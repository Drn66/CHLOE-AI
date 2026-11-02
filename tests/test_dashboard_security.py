"""Security regressions: local ASGI only; no OS firewall or real credentials."""
import asyncio
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import ssl
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect
from dashboard import server


class DashboardSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ip = patch.object(server, "_local_ip", return_value="127.0.0.1")
        self.ip.start()
        self.addCleanup(self.ip.stop)
        self.dashboard = server.DashboardServer()
        self.dashboard._uploads_dir = Path(self.directory.name)
        self.client = TestClient(self.dashboard.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        waiting = patch.object(server, "_COMMAND_WAIT_SECONDS", 0.001)
        waiting.start()
        self.addCleanup(waiting.stop)

    def login(self):
        response = self.client.post("/login", json={"pin": self.dashboard.new_key()})
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def headers(login):
        return {"Authorization": "Bearer " + login["token"]}

    def test_revoked_bearer_cannot_enqueue_commands(self):
        login = self.login()
        headers = self.headers(login)
        self.assertEqual(self.client.post("/api/revoke-devices", headers=headers).status_code, 200)
        response = self.client.post("/api/command", headers=headers, json={"text": "test", "id": "revoke-test"})
        self.assertEqual(response.status_code, 401)
        self.assertTrue(self.dashboard._command_queue.empty())

    def test_tls_failure_never_creates_a_listening_server(self):
        with patch.object(server, "_ensure_certs", return_value=False), patch.object(
            self.dashboard, "_ssl_enabled", return_value=False
        ), patch.object(server, "_ensure_network_access"), patch.object(
            server.uvicorn, "Config"
        ) as config, patch.object(server.uvicorn, "Server", return_value=Mock(serve=AsyncMock())):
            asyncio.run(self.dashboard.serve())
        config.assert_not_called()

    def test_targeted_revoke_preserves_other_device_and_blocks_relogin(self):
        admin, target = self.login(), self.login()
        target_headers = self.headers(target)
        self.client.post("/api/revoke-devices", headers=self.headers(admin),
                         json={"device_id": target["device_id"]})
        self.assertEqual(self.client.get("/api/files", headers=target_headers).status_code, 401)
        self.assertEqual(self.client.get("/api/files", headers=self.headers(admin)).status_code, 200)
        self.assertEqual(self.client.post("/api/device-login", json={
            "device_token": target["device_token"]}).status_code, 401)
        self.assertEqual(self.client.post("/api/device-login", json={
            "device_token": admin["device_token"]}).status_code, 200)

    def test_revocation_closes_existing_websocket_and_blocks_new_one(self):
        login = self.login()
        with self.client.websocket_connect("/ws?token=" + login["token"]) as websocket:
            self.client.post("/api/revoke-devices", headers=self.headers(login))
            with self.assertRaises(WebSocketDisconnect):
                websocket.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws?token=" + login["token"]):
                pass

    def test_revocation_closes_phone_capture_and_discards_queued_audio(self):
        login = self.login()
        with self.client.websocket_connect("/ws/phone-audio?token=" + login["token"]) as websocket:
            websocket.send_bytes(b"\x00\x00")
            self.client.post("/api/revoke-devices", headers=self.headers(login))
            with self.assertRaises(WebSocketDisconnect):
                websocket.receive_bytes()
        self.assertTrue(self.dashboard._phone_audio_queue.empty())
        self.assertFalse(self.dashboard.phone_audio_authorized({"_token": login["token"]}))

    def test_revoke_prevents_execution_of_already_queued_command(self):
        login = self.login()
        response = self.client.post("/api/command", headers=self.headers(login),
                                    json={"id": "queued-test", "text": "test"})
        self.assertEqual(response.json()["status"], "pending")
        command = self.dashboard._command_queue.get_nowait()
        self.client.post("/api/revoke-devices", headers=self.headers(login))
        self.assertFalse(self.dashboard.begin_command(command))
        self.assertEqual(command.status, "failed")

    def test_expired_session_cannot_access_any_protected_transport(self):
        login = self.login()
        self.dashboard._tokens[login["token"]].expires_at = 0
        for route in ("/api/command", "/api/wake", "/api/revoke-devices"):
            with self.subTest(route=route):
                self.assertEqual(self.client.post(route, headers=self.headers(login), json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/files", headers=self.headers(login)).status_code, 401)
        self.assertEqual(self.client.get("/uploads/a?token=" + login["token"]).status_code, 401)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/phone-audio?token=" + login["token"]):
                pass

    def test_bad_json_types_return_controlled_errors(self):
        login = self.login()
        for value in ([], None, "text", 1):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/command", headers=self.headers(login), json=value).status_code, 400)
                self.assertEqual(self.client.post("/api/device-login", json=value).status_code, 400)
        for value in ([], {}, 3):
            self.assertEqual(self.client.post("/api/command", headers=self.headers(login),
                                             json={"text": value}).status_code, 400)

    def test_no_authentication_bypass_on_upload_or_download(self):
        (Path(self.directory.name) / "private.txt").write_text("private", encoding="utf-8")
        self.assertEqual(self.client.get("/api/files").status_code, 401)
        self.assertEqual(self.client.get("/uploads/private.txt").status_code, 401)
        self.assertEqual(self.client.post("/api/upload", files={"file": ("a.txt", b"a")}).status_code, 401)

    def test_pin_is_one_time_and_rate_limited(self):
        pin = self.dashboard.new_key()
        self.assertEqual(self.client.post("/login", json={"pin": pin}).status_code, 200)
        self.assertEqual(self.client.post("/login", json={"pin": pin}).status_code, 401)
        for _ in range(8):
            self.client.post("/login", json={"pin": "INVALID"})
        self.assertEqual(self.client.post("/login", json={"pin": "INVALID"}).status_code, 429)

    def test_auth_is_rechecked_after_awaiting_request_body(self):
        login = self.login()
        async def scenario():
            receiving, release = asyncio.Event(), asyncio.Event()
            async def receive():
                receiving.set()
                await release.wait()
                return {"type": "http.request", "body": json.dumps({"id": "race", "text": "test"}).encode(),
                        "more_body": False}
            request = Request({"type": "http", "headers": [
                (b"authorization", ("Bearer " + login["token"]).encode())]}, receive)
            endpoint = next(route.endpoint for route in self.dashboard.app.routes if route.path == "/api/command")
            pending = asyncio.create_task(endpoint(request))
            await receiving.wait()
            await self.dashboard._revoke(login["device_id"])
            release.set()
            response = await pending
            self.assertEqual(response.status_code, 401)
            self.assertTrue(self.dashboard._command_queue.empty())
        self.client.portal.call(scenario)

    def test_real_consumer_completion_is_the_delivery_ack(self):
        login = self.login()
        async def consumer():
            queued = await self.dashboard.next_command(timeout=1)
            self.assertTrue(self.dashboard.begin_command(queued))
            self.dashboard.complete_command(queued, True)
        consumer_task = self.client.portal.start_task_soon(consumer)
        with patch.object(server, "_COMMAND_WAIT_SECONDS", 1):
            response = self.client.post("/api/command", headers=self.headers(login),
                                        json={"id": "delivered", "text": "test"})
        consumer_task.result(timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "delivered")
        again = self.client.post("/api/command", headers=self.headers(login),
                                 json={"id": "delivered", "text": "test"})
        self.assertEqual(again.json()["status"], "delivered")
        self.assertTrue(self.dashboard._command_queue.empty())

    def test_concurrent_retries_have_only_one_queued_attempt(self):
        login = self.login()
        def send(_):
            return self.client.post("/api/command", headers=self.headers(login),
                                    json={"id": "concurrent", "text": "test"}).json()["status"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(send, range(16)))
        self.assertEqual(set(statuses), {"pending"})
        self.assertEqual(self.dashboard._command_queue.qsize(), 1)

    def test_unknown_delivery_is_never_automatically_requeued(self):
        login = self.login()
        body = {"id": "unknown", "text": "test", "created_at": time.time()}
        self.client.post("/api/command", headers=self.headers(login), json=body)
        async def consume():
            command = await self.dashboard.next_command(timeout=1)
            self.assertTrue(self.dashboard.begin_command(command))
            self.dashboard.complete_command(command, False, uncertain=True)
        self.client.portal.call(consume)
        again = self.client.post("/api/command", headers=self.headers(login), json=body)
        self.assertEqual(again.json()["status"], "unknown")
        self.assertTrue(self.dashboard._command_queue.empty())

    def test_definite_failure_can_be_retried_with_the_same_id(self):
        login = self.login()
        body = {"id": "failed", "text": "test"}
        self.client.post("/api/command", headers=self.headers(login), json=body)
        async def consume():
            command = await self.dashboard.next_command(timeout=1)
            self.dashboard.complete_command(command, False)
        self.client.portal.call(consume)
        retry = self.client.post("/api/command", headers=self.headers(login), json=body)
        self.assertEqual(retry.json()["status"], "pending")
        self.assertEqual(self.dashboard._command_queue.qsize(), 1)

    def test_id_cannot_be_reused_for_different_text_or_another_device(self):
        first, second = self.login(), self.login()
        self.client.post("/api/command", headers=self.headers(first), json={"id": "same", "text": "first"})
        different = self.client.post("/api/command", headers=self.headers(first),
                                     json={"id": "same", "text": "different"})
        self.assertEqual(different.status_code, 409)
        other_device = self.client.post("/api/command", headers=self.headers(second),
                                        json={"id": "same", "text": "different"})
        self.assertEqual(other_device.json()["status"], "pending")
        self.assertEqual(self.dashboard._command_queue.qsize(), 2)

    def test_expired_dedup_retry_is_unknown_instead_of_a_fresh_send(self):
        login = self.login()
        response = self.client.post("/api/command", headers=self.headers(login), json={
            "id": "too-old", "text": "test", "created_at": time.time() - server._COMMAND_TTL - 1})
        self.assertEqual(response.json()["status"], "unknown")
        self.assertTrue(self.dashboard._command_queue.empty())

    def test_command_queue_is_bounded_and_full_error_is_explicit(self):
        login = self.login()
        with patch.object(server, "_COMMAND_WAIT_SECONDS", 0):
            for index in range(100):
                self.client.post("/api/command", headers=self.headers(login),
                                 json={"id": str(index), "text": "test"})
            response = self.client.post("/api/command", headers=self.headers(login),
                                        json={"id": "full", "text": "test"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(self.dashboard._command_queue.qsize(), 100)

    def test_authorized_upload_download_and_notification_are_preserved(self):
        login = self.login()
        response = self.client.post("/api/upload", headers=self.headers(login),
                                    files={"file": ("example.txt", b"example")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["size"], 7)
        self.assertEqual(self.dashboard._history[-1]["type"], "file_received")
        download = self.client.get("/uploads/example.txt?token=" + login["token"])
        self.assertEqual(download.content, b"example")
        names = self.client.get("/api/files", headers=self.headers(login)).json()["files"]
        self.assertEqual(names, [{"name": "example.txt", "size": 7}])

    def test_malformed_websocket_frames_close_without_server_exception(self):
        login = self.login()
        with self.client.websocket_connect("/ws?token=" + login["token"]) as websocket:
            websocket.send_bytes(b"unexpected binary")
            with self.assertRaises(WebSocketDisconnect):
                websocket.receive_json()
        with self.client.websocket_connect("/ws/phone-audio?token=" + login["token"]) as websocket:
            websocket.send_text("unexpected text")
            with self.assertRaises(WebSocketDisconnect):
                websocket.receive_bytes()

    def test_streaming_download_stops_after_revocation(self):
        login = self.login()
        (Path(self.directory.name) / "test.bin").write_bytes(b"a" * 131072)
        async def scenario():
            endpoint = next(route.endpoint for route in self.dashboard.app.routes if route.path == "/uploads/{filename}")
            response = await endpoint("test.bin", login["token"])
            self.assertEqual(len(await anext(response.body_iterator)), 65536)
            await self.dashboard._revoke(login["device_id"])
            with self.assertRaises(StopAsyncIteration):
                await anext(response.body_iterator)
        self.client.portal.call(scenario)

    def test_stop_is_bounded_even_if_a_peer_never_acknowledges_close(self):
        login = self.login()
        async def never_closes(**kwargs):
            await asyncio.Event().wait()
        websocket = Mock(close=AsyncMock(side_effect=never_closes))
        self.dashboard._connections[websocket] = login["token"]
        self.dashboard._clients.add(websocket)
        started = time.monotonic()
        self.client.portal.call(self.dashboard.stop)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(self.dashboard._session(login["token"]))
        self.assertFalse(self.dashboard._connections)
        self.assertFalse(self.dashboard._clients)


class TLSAndNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.certs = self.base / "config" / "certs"
        self.certs.mkdir(parents=True)
        patcher = patch.object(server, "BASE_DIR", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pair(self, *, expired=False, future=False, mismatch=False):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=1) if future else now - timedelta(days=2)
        end = now - timedelta(days=1) if expired else now + timedelta(days=2)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(start).not_valid_after(end).sign(self.key, hashes.SHA256()))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if mismatch else self.key
        (self.certs / "jarvis.key").write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (self.certs / "jarvis.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    def test_missing_invalid_expired_future_or_mismatched_tls_pair_is_rejected(self):
        self.assertIsNone(server._tls_context())
        (self.certs / "jarvis.crt").write_text("invalid", encoding="ascii")
        self.assertIsNone(server._tls_context())
        for options in ({"expired": True}, {"future": True}, {"mismatch": True}):
            with self.subTest(options=options):
                self.pair(**options)
                self.assertIsNone(server._tls_context())
                self.assertFalse(server._ensure_certs())

    def test_valid_certificate_uses_validated_context_and_only_tls_listener(self):
        self.pair()
        context = server._tls_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        with patch.object(server, "_local_ip", return_value="127.0.0.1"):
            dashboard = server.DashboardServer()
        with patch.object(server.uvicorn, "Config") as config, patch.object(
            server.uvicorn, "Server", return_value=Mock(serve=AsyncMock())
        ) as listener, patch.object(server, "_ensure_network_access"):
            asyncio.run(dashboard.serve())
        self.assertEqual(config.call_count, 1)
        self.assertEqual(config.call_args.kwargs["host"], "0.0.0.0")
        self.assertIn("ssl_certfile", config.call_args.kwargs)
        self.assertIsInstance(config.return_value.ssl, ssl.SSLContext)
        listener.return_value.serve.assert_awaited_once()

    def test_network_setup_never_mutates_any_os_or_requires_elevation(self):
        with patch("subprocess.run") as command:
            for scenario in ("public", "private", "unprivileged", "existing"):
                with self.subTest(scenario=scenario):
                    server._ensure_network_access(8000)
            command.assert_not_called()
        commands = server.windows_firewall_commands(8000)
        self.assertIn("profile=private", commands["add"])
        self.assertIn("remoteip=LocalSubnet", commands["add"])
        self.assertIn("localport=8000", commands["add"])
        self.assertIn("protocol=TCP", commands["add"])
        self.assertFalse(any(arg.startswith("program=") for arg in commands["add"]))
        self.assertIn("show", commands["query"])
        self.assertIn("delete", commands["remove"])
        self.assertEqual(commands, server.windows_firewall_commands(8000))
        for invalid in (True, "8000", 0, 65536):
            with self.assertRaises(ValueError):
                server.windows_firewall_commands(invalid)


if __name__ == "__main__":
    unittest.main()
