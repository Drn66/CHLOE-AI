"""Real TLS/HTTPS/WSS smoke test, restricted to an ephemeral loopback socket."""
import asyncio
from contextlib import ExitStack
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from websockets.asyncio.client import connect

from dashboard import server


class DashboardTLSSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.base = Path(self.directory)
        self.stack.enter_context(patch.object(server, "BASE_DIR", self.base))
        self.stack.enter_context(patch.object(server, "_local_ip", return_value="127.0.0.1"))
        self.assertTrue(server._ensure_certs())
        self.dashboard = server.DashboardServer()
        self.dashboard._uploads_dir = self.base / "uploads"
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(16)
        self.socket.setblocking(False)
        self.port = self.socket.getsockname()[1]
        self.addCleanup(self.socket.close)
        real_config, real_server = uvicorn.Config, uvicorn.Server
        bound_socket = self.socket
        class LoopbackServer(real_server):
            async def serve(instance, sockets=None):
                return await super().serve(sockets=[bound_socket])
            async def shutdown(instance, sockets=None):
                return await super().shutdown(sockets=[bound_socket])
        def loopback_config(*args, **kwargs):
            kwargs.update(host="127.0.0.1", port=0, log_level="critical", log_config=None,
                          lifespan="off", proxy_headers=False, ws="websockets-sansio")
            return real_config(*args, **kwargs)
        self.stack.enter_context(patch.object(server.uvicorn, "Config", side_effect=loopback_config))
        self.stack.enter_context(patch.object(server.uvicorn, "Server", LoopbackServer))
        self.serving = asyncio.create_task(self.dashboard.serve())
        self.addAsyncCleanup(self.stop_server)
        for _ in range(100):
            if self.dashboard._server is not None and self.dashboard._server.started:
                break
            if self.serving.done():
                self.fail("The loopback TLS server stopped before startup.")
            await asyncio.sleep(0.01)
        self.assertIsNotNone(self.dashboard._server, "Loopback TLS server did not start.")
        self.http_server = self.dashboard._server
        self.assertTrue(self.http_server.started, "Loopback TLS server did not start.")
        self.trust = ssl.create_default_context(cafile=str(self.base / "config" / "certs" / "jarvis.crt"))
        self.client = httpx.AsyncClient(base_url=f"https://127.0.0.1:{self.port}",
                                        verify=self.trust, trust_env=False, timeout=3)
        self.addAsyncCleanup(self.client.aclose)

    async def stop_server(self):
        await self.dashboard.stop()
        try:
            await asyncio.wait_for(self.serving, 1)
        except asyncio.CancelledError:
            pass

    async def test_https_and_authenticated_wss_use_real_tls(self):
        self.assertEqual(self.socket.getsockname()[0], "127.0.0.1")
        page = await self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn("no-store", page.headers["cache-control"])
        login = await self.client.post("/login", json={"pin": self.dashboard.new_key()})
        self.assertEqual(login.status_code, 200)
        token = login.json()["token"]
        try:
            async with connect(f"wss://127.0.0.1:{self.port}/ws?token={token}",
                               ssl=self.trust, proxy=None, open_timeout=3) as websocket:
                self.assertIsNotNone(websocket.transport.get_extra_info("ssl_object"))
                await self.dashboard.broadcast({"type": "sys", "text": "TLS integration test"})
                payload = await asyncio.wait_for(websocket.recv(), 1)
                self.assertIn("TLS integration test", payload)
        except Exception as exc:
            # Never include the token-bearing URI from a transport exception.
            raise AssertionError(f"WSS smoke test failed ({type(exc).__name__}).") from None

    async def test_plain_http_cannot_retrieve_login_or_authenticated_data(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(b"GET /login HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            try:
                data = await asyncio.wait_for(reader.read(4096), 1)
            except (ConnectionResetError, asyncio.TimeoutError):
                data = b""
            self.assertEqual(data, b"", "Plain HTTP unexpectedly returned application data.")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass

    async def test_stop_closes_real_listener_invalidates_sessions_and_is_idempotent(self):
        login = await self.client.post("/login", json={"pin": self.dashboard.new_key()})
        token = login.json()["token"]
        await asyncio.wait_for(self.dashboard.stop(), timeout=3.1)
        await self.dashboard.stop()
        self.assertTrue(self.serving.done())
        self.assertFalse(self.dashboard.available)
        self.assertIsNone(self.dashboard._session(token))
        self.assertFalse(self.dashboard._connections)
        with self.assertRaises(OSError):
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.port), 0.5)
            writer.close()
            await writer.wait_closed()

    async def test_cancelled_serve_task_still_closes_real_listener(self):
        self.serving.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.serving, 2)
        self.assertFalse(self.dashboard.available)
        self.assertIsNone(self.dashboard._server)
        with self.assertRaises(OSError):
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.port), 0.5)
            writer.close()
            await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
