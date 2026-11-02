"""Independent QA regressions for interrupted delivery ownership.

Only synthetic sessions and an in-memory transport are used. These tests close
coverage gaps for timeout and cancellation after the remote send has begun.
"""
import asyncio
import json
from pathlib import Path
import time
import unittest
from urllib.parse import urlparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import main
from dashboard import server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class InterruptedDeliveryQATests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.object(server, "_local_ip", return_value="127.0.0.1"):
            self.dashboard = server.DashboardServer()
        self.login = self.dashboard._pair(self.dashboard.new_key())
        self.live = main.JarvisLive.__new__(main.JarvisLive)
        self.live._dashboard = self.dashboard
        self.live.ui = Mock()
        self.live._wake_enabled = False
        self.live._awake = True
        self.entered = asyncio.Event()

        async def transport(**_kwargs):
            self.entered.set()
            await asyncio.Event().wait()

        self.live.session = SimpleNamespace(send_client_content=transport)
        self.command, code = self.dashboard._queue_command(
            self.login["token"], "synthetic command", "qa-interruption", time.time())
        self.assertEqual(code, 200)
        self.assertIs(self.dashboard._command_queue.get_nowait(), self.command)

    def assert_unknown_without_second_attempt(self):
        self.assertEqual(self.command.status, "unknown")
        self.assertTrue(self.command.done.is_set())
        previous, _code = self.dashboard._queue_command(
            self.login["token"], self.command.text, self.command.id, time.time())
        self.assertIs(previous, self.command)
        self.assertTrue(self.dashboard._command_queue.empty())

    async def test_transport_timeout_retains_unknown_instead_of_retrying(self):
        with patch.object(main, "_DASHBOARD_SEND_TIMEOUT", 0.01):
            await self.live._deliver_dashboard_command(self.command)
        self.assertTrue(self.entered.is_set())
        self.assert_unknown_without_second_attempt()

    async def test_cancellation_after_transport_started_resolves_pending_as_unknown(self):
        delivering = asyncio.create_task(self.live._deliver_dashboard_command(self.command))
        await asyncio.wait_for(self.entered.wait(), timeout=1)
        delivering.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await delivering
        self.assert_unknown_without_second_attempt()


class EncryptedBrowserServerQATests(unittest.TestCase):
    """Join the previously separate browser and ASGI contract checks."""

    def test_real_browser_encryption_reaches_real_endpoint_and_delivery_ack(self):
        if sync_playwright is None:
            self.skipTest("Playwright is required for the browser/ASGI contract check")
        from test_dashboard_browser import BROWSER_FIXTURES

        root = Path(__file__).resolve().parents[1]
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                self.skipTest("Chromium is required for the browser/ASGI contract check")
            browser = playwright.chromium.launch(headless=True)
            try:
                with patch.object(server, "_local_ip", return_value="127.0.0.1"):
                    dashboard = server.DashboardServer()
                with TestClient(dashboard.app) as client:
                    login = client.post("/login", json={"pin": dashboard.new_key()}).json()
                    live = main.JarvisLive.__new__(main.JarvisLive)
                    transport = AsyncMock()
                    live.session = SimpleNamespace(send_client_content=transport)
                    live._dashboard = dashboard
                    live.ui = Mock()
                    live._wake_enabled = False
                    live._awake = True
                    live._session_log = []
                    live._refresh_idle_state = Mock()
                    consumer = client.portal.start_task_soon(live._process_dashboard_commands)
                    context = browser.new_context()
                    try:
                        context.add_init_script(
                            BROWSER_FIXTURES + "\n" +
                            "sessionStorage.setItem('jarvis_token', " + json.dumps(login["token"]) + ");"
                            "sessionStorage.setItem('jarvis_key', " + json.dumps(login["key"]) + ");"
                        )
                        page = context.new_page()
                        errors, requests, bodies = [], [], []
                        page.on("pageerror", lambda error: errors.append(type(error).__name__))

                        def route_request(route):
                            request = route.request
                            parsed = urlparse(request.url)
                            if parsed.netloc != "dashboard.test":
                                requests.append(parsed.netloc)
                                route.abort()
                            elif parsed.path == "/":
                                html = (root / "dashboard/static/app.html").read_text(encoding="utf-8")
                                route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                              body=html.replace("__IP__", "dashboard.test").replace("__PORT__", "443"))
                            elif parsed.path == "/static/crypto.js":
                                route.fulfill(status=200, content_type="application/javascript",
                                              body=(root / "dashboard/static/crypto-js.min.js").read_text(encoding="utf-8"))
                            elif parsed.path == "/api/command":
                                body = request.post_data_json
                                bodies.append(body)
                                response = client.post("/api/command", json=body, headers={
                                    "Authorization": request.headers.get("authorization", ""),
                                })
                                route.fulfill(status=response.status_code, content_type="application/json",
                                              body=response.text)
                            else:
                                route.fulfill(status=404, content_type="application/json", body="{}")

                        page.route("**/*", route_request)
                        page.goto("https://dashboard.test/", wait_until="load")
                        message = "Mensagem sintética de integração — ação 42"
                        page.locator("#inp").fill(message)
                        page.locator("#send-btn").click()
                        page.locator('.msg-u[data-state="delivered"]').wait_for(timeout=5000)
                        self.assertEqual(page.locator("#inp").input_value(), "")
                        self.assertEqual(len(bodies), 1)
                        self.assertIn("enc", bodies[0])
                        self.assertNotIn("text", bodies[0])
                        self.assertIn("id", bodies[0])
                        self.assertIn("created_at", bodies[0])
                        transport.assert_awaited_once_with(
                            turns={"role": "user", "parts": [{"text": message}]}, turn_complete=True)
                        self.assertEqual(errors, [])
                        self.assertEqual(requests, [])
                    finally:
                        context.close()
                        consumer.cancel()
            finally:
                browser.close()
