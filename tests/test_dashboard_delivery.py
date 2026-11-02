"""ASGI -> real dashboard queue -> main relay; Gemini and audio are simulated."""
import asyncio
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import main
from dashboard import server


class DashboardDeliveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.object(server, "_local_ip", return_value="127.0.0.1"):
            self.dashboard = server.DashboardServer()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.dashboard.app),
                                        base_url="https://localhost")
        self.addAsyncCleanup(self.client.aclose)
        login = await self.client.post("/login", json={"pin": self.dashboard.new_key()})
        self.login = login.json()
        self.headers = {"Authorization": "Bearer " + self.login["token"]}
        self.live = main.JarvisLive.__new__(main.JarvisLive)
        self.live._dashboard = self.dashboard
        self.live.session = SimpleNamespace(send_client_content=AsyncMock(), send_realtime_input=AsyncMock())
        self.live.ui = Mock(muted=False)
        self.live._wake_enabled = False
        self.live._awake = True
        self.live._session_log = []
        self.live._refresh_idle_state = Mock()
        self.live._speaking_lock = threading.Lock()
        self.live._is_speaking = False
        self.live.out_queue = asyncio.Queue(maxsize=10)

    async def command(self, identity="test"):
        with patch.object(server, "_COMMAND_WAIT_SECONDS", 0.001):
            response = await self.client.post("/api/command", headers=self.headers,
                                              json={"id": identity, "text": "test message", "created_at": time.time()})
        self.assertEqual(response.json()["status"], "pending")
        return self.dashboard._command_queue.get_nowait()

    async def cancel(self, task):
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_http_ack_waits_for_gemini_transport_acceptance(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def transport(**kwargs):
            entered.set()
            await release.wait()
        self.live.session.send_client_content.side_effect = transport
        relay = asyncio.create_task(self.live._process_dashboard_commands())
        try:
            response_task = asyncio.create_task(self.client.post("/api/command", headers=self.headers,
                json={"id": "accepted", "text": "test message", "created_at": time.time()}))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertFalse(response_task.done())
            queued = self.dashboard._commands[(self.login["device_id"], "accepted")]
            self.assertEqual(queued.status, "pending")
            release.set()
            response = await asyncio.wait_for(response_task, 1)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "delivered")
            self.live.session.send_client_content.assert_awaited_once_with(
                turns={"role": "user", "parts": [{"text": "test message"}]}, turn_complete=True)
        finally:
            await self.cancel(relay)

    async def test_missing_gemini_session_is_a_definite_failure(self):
        command = await self.command()
        self.live.session = None
        with patch.object(main, "_DASHBOARD_SESSION_TIMEOUT", 0):
            await self.live._deliver_dashboard_command(command)
        self.assertEqual(command.status, "failed")
        self.assertFalse(command.started)

    async def test_transport_error_is_unknown_and_retry_does_not_duplicate(self):
        command = await self.command("uncertain")
        self.live.session.send_client_content.side_effect = ConnectionError("private transport information")
        await self.live._deliver_dashboard_command(command)
        self.assertEqual(command.status, "unknown")
        response = await self.client.post("/api/command", headers=self.headers,
            json={"id": "uncertain", "text": "test message", "created_at": time.time()})
        self.assertEqual(response.json()["status"], "unknown")
        self.assertTrue(self.dashboard._command_queue.empty())
        self.live.session.send_client_content.assert_awaited_once()
        self.assertNotIn("private transport information", str(self.live.ui.write_log.call_args_list))

    async def test_delivered_retry_returns_same_ack_without_second_transport(self):
        command = await self.command("once")
        await self.live._deliver_dashboard_command(command)
        response = await self.client.post("/api/command", headers=self.headers,
            json={"id": "once", "text": "test message", "created_at": time.time()})
        self.assertEqual(response.json()["status"], "delivered")
        self.assertTrue(self.dashboard._command_queue.empty())
        self.live.session.send_client_content.assert_awaited_once()

    async def test_revocation_while_waiting_for_session_prevents_forwarding(self):
        command = await self.command()
        self.live.session = None
        pending = asyncio.create_task(self.live._deliver_dashboard_command(command))
        await asyncio.sleep(0)
        await self.dashboard._revoke(self.login["device_id"])
        await asyncio.wait_for(pending, 1)
        self.assertEqual(command.status, "failed")
        self.assertFalse(command.started)
        self.assertFalse(self.dashboard.begin_command(command))

    async def test_revocation_between_begin_and_forward_task_prevents_transport(self):
        command = await self.command()
        real_wait_for = asyncio.wait_for
        async def revoke_before_scheduled(coroutine, timeout):
            self.assertTrue(command.started)
            await self.dashboard._revoke(self.login["device_id"])
            return await real_wait_for(coroutine, timeout=timeout)
        with patch.object(main.asyncio, "wait_for", side_effect=revoke_before_scheduled):
            await self.live._deliver_dashboard_command(command)
        self.live.session.send_client_content.assert_not_awaited()
        self.assertNotEqual(command.status, "delivered")

    async def test_revoked_phone_frame_already_in_output_queue_is_rejected(self):
        frame = {"data": b"\0\0", "mime_type": "audio/pcm", "_token": self.login["token"]}
        await self.live.out_queue.put(frame)
        await self.dashboard._revoke(self.login["device_id"])
        sender = asyncio.create_task(self.live._send_realtime())
        try:
            for _ in range(20):
                await asyncio.sleep(0)
                if self.live.out_queue.empty():
                    break
            self.assertTrue(self.live.out_queue.empty())
            self.live.session.send_realtime_input.assert_not_awaited()
        finally:
            await self.cancel(sender)

    async def test_legitimate_phone_frame_reaches_existing_gemini_audio_payload(self):
        sent = asyncio.Event()
        async def transport(**kwargs):
            sent.set()
        self.live.session.send_realtime_input.side_effect = transport
        frame = {"data": b"\0\0", "mime_type": "audio/pcm", "_token": self.login["token"]}
        await self.live.out_queue.put(frame)
        sender = asyncio.create_task(self.live._send_realtime())
        try:
            await asyncio.wait_for(sent.wait(), 1)
            payload = self.live.session.send_realtime_input.call_args.kwargs["audio"]
            self.assertEqual(payload.data, b"\0\0")
            self.assertEqual(payload.mime_type, "audio/pcm")
        finally:
            await self.cancel(sender)


if __name__ == "__main__":
    unittest.main()
