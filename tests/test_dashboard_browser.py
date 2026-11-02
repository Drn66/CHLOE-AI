"""Real Chromium DOM/events with local routes and synthetic media/session data.

No TCP server, real microphone, user browser profile or external request is used.
Install the optional browser with `python -m playwright install chromium`.
"""
import json
from pathlib import Path
import unittest
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://dashboard.test"

# All browser media and sockets are fixtures. The DOM, script, CryptoJS,
# button interactions, fetch, Promise scheduling and rendering remain real.
BROWSER_FIXTURES = r"""
sessionStorage.setItem('jarvis_token', 'fixture-token');
sessionStorage.setItem('jarvis_key', 'fixture-session');
if (!sessionStorage.getItem('jarvis_device_id')) sessionStorage.setItem('jarvis_device_id', 'fixture-device');
window.fixture = { permissionCalls: 0, pending: [], streams: [], contexts: [], sockets: [], nodes: [], autoGrant: false };
fixture.makeStream = () => {
  const tracks = [0, 1].map(() => ({ stopped: 0, stop() { this.stopped++; } }));
  const stream = { getTracks: () => tracks };
  fixture.streams.push(stream); return stream;
};
fixture.grant = () => fixture.pending.shift().resolve(fixture.makeStream());
fixture.node = () => {
  const node = { port: {}, disconnected: 0, connect() {}, disconnect() { this.disconnected++; } };
  fixture.nodes.push(node); return node;
};
Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
  getUserMedia() {
    fixture.permissionCalls++;
    return fixture.autoGrant ? Promise.resolve(fixture.makeStream()) : new Promise((resolve, reject) => fixture.pending.push({ resolve, reject }));
  }
} });
window.AudioContext = class {
  constructor() {
    this.state = 'running'; this.sampleRate = 16000; this.closed = 0; this.destination = {};
    this.audioWorklet = { addModule: async () => {} }; fixture.contexts.push(this);
  }
  close() { this.closed++; return Promise.resolve(); }
  createMediaStreamSource() { return fixture.node(); }
  createScriptProcessor() { return fixture.node(); }
};
window.AudioWorkletNode = function () { return fixture.node(); };
window.WebSocket = class {
  constructor(url) {
    this.url = url; this.readyState = 0; this.closed = 0; fixture.sockets.push(this);
    queueMicrotask(() => { if (this.readyState === 0) { this.readyState = 1; this.onopen?.(); } });
  }
  send() {}
  close() { this.closed++; this.readyState = 3; this.onclose?.({}); }
};
"""


class DashboardChromiumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("Playwright is not installed; Chromium UI regressions were not executed")
        cls.playwright = sync_playwright().start()
        if not Path(cls.playwright.chromium.executable_path).is_file():
            cls.playwright.stop()
            raise unittest.SkipTest("Playwright Chromium is not installed; UI regressions were not executed")
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 430, "height": 900})
        self.addCleanup(self.context.close)
        self.context.add_init_script(BROWSER_FIXTURES)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.javascript_errors = []
        self.external_requests = []
        self.command_bodies = []
        self.command_routes = []
        self.command_results = [(200, "delivered")]
        self.hold_command = False
        self.page.on("pageerror", lambda error: self.javascript_errors.append(str(error)))
        self.page.route("**/*", self.route)
        self.page.goto(ORIGIN + "/", wait_until="load")
        self.assertEqual(self.page.locator("#mic-btn").get_attribute("data-state"), "idle")

    def tearDown(self):
        self.assertEqual(self.javascript_errors, [], "Browser JavaScript errors")
        self.assertEqual(self.external_requests, [], "External network was attempted")

    def test_expired_session_offers_visible_reconnection_without_erasing_draft(self):
        self.command_results = [(401, "failed")]
        self.page.locator("#inp").fill("synthetic command")
        self.page.locator("#send-btn").click()
        self.page.wait_for_selector("#reconnect-device")
        self.assertTrue(self.page.locator("#reconnect-device").is_visible())
        self.assertEqual(self.page.locator("#reconnect-device").get_attribute("href"), "/login")
        self.assertEqual(self.page.locator("#inp").input_value(), "synthetic command")

    def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.netloc != "dashboard.test":
            self.external_requests.append(parsed.netloc)
            route.abort()
        elif parsed.path == "/":
            html = (ROOT / "dashboard" / "static" / "app.html").read_text(encoding="utf-8")
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=html.replace("__IP__", "dashboard.test").replace("__PORT__", "443"))
        elif parsed.path == "/static/crypto.js":
            route.fulfill(status=200, content_type="application/javascript",
                          body=(ROOT / "dashboard" / "static" / "crypto-js.min.js").read_text(encoding="utf-8"))
        elif parsed.path == "/api/command":
            body = request.post_data_json
            self.command_bodies.append(body)
            if self.hold_command:
                self.command_routes.append(route)
            else:
                status, state = self.command_results.pop(0)
                route.fulfill(status=status, content_type="application/json", body=json.dumps({
                    "ok": state == "delivered", "id": body["id"], "status": state,
                }))
        else:
            route.fulfill(status=404, content_type="application/json", body='{"error":"fixture route unavailable"}')

    def wait_state(self, state):
        self.page.locator(f'.msg-u[data-state="{state}"]').wait_for()

    def click_recording_microphone(self):
        # The existing recording pulse continuously scales this button, so a
        # locator's stability wait never completes. Use a real pointer click at
        # its centre, as a user would, without forcing DOM events or disabling CSS.
        box = self.page.locator("#mic-btn").bounding_box()
        self.assertIsNotNone(box)
        self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    def test_503_visible_error_preserves_draft_and_retry_confirms_same_command(self):
        self.command_results = [(503, "failed"), (200, "delivered")]
        self.page.locator("#inp").fill("Mensagem sintética para teste")
        self.page.locator("#send-btn").click()
        self.wait_state("failed")
        self.assertEqual(self.page.locator("#inp").input_value(), "Mensagem sintética para teste")
        self.assertIn("preservado", self.page.locator(".delivery-status").inner_text())
        self.assertTrue(self.page.locator(".delivery-retry").is_visible())
        self.page.locator(".delivery-retry").click()
        self.wait_state("delivered")
        self.assertEqual(self.page.locator("#inp").input_value(), "")
        self.assertEqual(self.page.locator(".msg-u").count(), 1)
        self.assertEqual(len(self.command_bodies), 2)
        self.assertEqual(self.command_bodies[0]["id"], self.command_bodies[1]["id"])
        self.assertEqual(self.command_bodies[0]["created_at"], self.command_bodies[1]["created_at"])
        self.assertIn("enc", self.command_bodies[0])
        self.assertNotIn("text", self.command_bodies[0])

    def test_double_microphone_click_cancels_pending_permission_and_stops_late_tracks(self):
        self.page.locator("#mic-btn").dblclick(delay=10)
        self.assertEqual(self.page.locator("#mic-btn").get_attribute("data-state"), "stopping")
        self.assertTrue(self.page.locator("#mic-btn").is_disabled())
        self.assertEqual(self.page.evaluate("fixture.permissionCalls"), 1)
        self.page.evaluate("fixture.grant()")
        self.page.wait_for_function("document.getElementById('mic-btn').dataset.state === 'idle'")
        self.assertEqual(self.page.evaluate("fixture.streams.flatMap(s => s.getTracks().map(t => t.stopped))"), [1, 1])
        self.assertEqual(self.page.evaluate("fixture.contexts.length"), 0)
        self.assertEqual(self.page.evaluate("fixture.sockets.filter(s => s.url.includes('phone-audio')).length"), 0)
        self.assertFalse(self.page.locator("#mic-btn").is_disabled())

    def test_new_draft_is_preserved_while_previous_delivery_is_pending(self):
        self.hold_command = True
        self.page.locator("#inp").fill("Primeiro rascunho sintético")
        self.page.locator("#send-btn").click()
        self.wait_state("pending")
        self.page.wait_for_function("document.getElementById('send-btn').disabled")
        self.assertEqual(len(self.command_bodies), 1)
        self.page.locator("#inp").fill("Próximo rascunho sintético")
        self.page.locator("#inp").press("Enter")
        self.assertEqual(len(self.command_bodies), 1)
        self.command_routes[0].fulfill(status=200, content_type="application/json", body=json.dumps({
            "ok": True, "id": self.command_bodies[0]["id"], "status": "delivered",
        }))
        self.wait_state("delivered")
        self.assertEqual(self.page.locator("#inp").input_value(), "Próximo rascunho sintético")
        self.assertEqual(self.page.locator(".msg-u").count(), 1)

    def test_multiple_live_microphone_cycles_release_every_resource(self):
        self.page.evaluate("fixture.autoGrant = true")
        for _ in range(3):
            self.page.locator("#mic-btn").click()
            self.page.wait_for_function("document.getElementById('mic-btn').dataset.state === 'recording'")
            self.assertEqual(self.page.locator("#mic-btn").get_attribute("aria-pressed"), "true")
            self.click_recording_microphone()
            self.page.wait_for_function("document.getElementById('mic-btn').dataset.state === 'idle'")
        self.assertEqual(self.page.evaluate("fixture.streams.flatMap(s => s.getTracks().map(t => t.stopped))"), [1] * 6)
        self.assertEqual(self.page.evaluate("fixture.contexts.map(c => c.closed)"), [1] * 3)
        self.assertEqual(self.page.evaluate("fixture.sockets.filter(s => s.url.includes('phone-audio')).map(s => s.closed)"), [1] * 3)
        self.assertTrue(self.page.evaluate("fixture.nodes.every(n => n.disconnected > 0)"))

    def test_unconfirmed_delivery_keeps_draft_and_status_query_uses_same_identity(self):
        self.command_results = [(202, "unknown"), (202, "unknown")]
        self.page.locator("#inp").fill("Comando sintético sem confirmação")
        self.page.locator("#send-btn").click()
        self.wait_state("unknown")
        self.assertIn("não confirmada", self.page.locator(".delivery-status").inner_text())
        self.assertEqual(self.page.locator(".delivery-retry").inner_text(), "Consultar envio")
        self.page.locator(".delivery-retry").click()
        self.page.wait_for_function("document.querySelector('.msg-u').dataset.state === 'unknown' && !document.getElementById('send-btn').disabled")
        self.assertEqual(self.page.locator("#inp").input_value(), "Comando sintético sem confirmação")
        self.assertEqual(len(self.command_bodies), 2)
        self.assertEqual(self.command_bodies[0]["id"], self.command_bodies[1]["id"])
        self.assertEqual(self.page.locator(".msg-u").count(), 1)

    def test_websocket_loss_and_reload_restore_uncertain_delivery_without_a_new_identity(self):
        self.command_results = [(202, "unknown"), (200, "delivered")]
        self.page.locator("#inp").fill("Envio sintético recuperável")
        self.page.locator("#send-btn").click()
        self.wait_state("unknown")
        first = dict(self.command_bodies[0])
        self.page.evaluate("fixture.sockets[0].onclose({})")
        self.assertIn("recuperados", self.page.locator("#feed").inner_text())
        self.page.reload(wait_until="load")
        self.wait_state("unknown")
        self.assertEqual(self.page.locator("#inp").input_value(), "Envio sintético recuperável")
        self.assertEqual(len(self.command_bodies), 1, "Restoration must not execute automatically")
        self.assertEqual(self.page.locator(".msg-u").count(), 1)
        self.page.locator(".delivery-retry").click()
        self.wait_state("delivered")
        self.assertEqual(self.command_bodies[1]["id"], first["id"])
        self.assertEqual(self.command_bodies[1]["created_at"], first["created_at"])
        self.assertEqual(len({body["id"] for body in self.command_bodies}), 1)
        self.assertEqual(self.page.locator(".msg-u").count(), 1)

    def test_multiple_uncertain_messages_restore_and_oldest_can_be_queried(self):
        self.command_results = [(202, "unknown"), (202, "unknown"), (200, "delivered")]
        self.page.locator("#inp").fill("Primeira pendência sintética")
        self.page.locator("#send-btn").click()
        self.wait_state("unknown")
        self.page.locator("#inp").fill("Segunda pendência sintética")
        self.page.locator("#send-btn").click()
        self.page.wait_for_function("document.querySelectorAll('.msg-u[data-state=unknown]').length === 2")
        first = dict(self.command_bodies[0])
        self.page.reload(wait_until="load")
        self.assertEqual(self.page.locator(".msg-u").count(), 2)
        self.assertEqual(self.page.locator("#inp").input_value(), "Segunda pendência sintética")
        self.page.locator(".delivery-retry").first.click()
        self.wait_state("delivered")
        self.assertEqual(self.command_bodies[2]["id"], first["id"])
        self.assertEqual(self.command_bodies[2]["created_at"], first["created_at"])
        self.assertEqual(self.page.locator("#inp").input_value(), "Segunda pendência sintética")

    def test_storage_quota_failure_is_visible_preserves_ram_draft_and_prevents_new_send(self):
        self.page.evaluate("() => { Storage.prototype.setItem = function() { throw new DOMException('fixture quota', 'QuotaExceededError'); }; }")
        self.page.locator("#inp").fill("Rascunho sintético somente em memória")
        self.page.locator("#send-btn").click()
        self.wait_state("failed")
        self.assertEqual(self.page.locator("#inp").input_value(), "Rascunho sintético somente em memória")
        self.assertIn("mantenha a aba aberta", self.page.locator("#recovery-notice").inner_text())
        self.assertEqual(self.command_bodies, [])
        self.assertFalse(self.page.locator("#send-btn").is_disabled())

    def test_different_device_cannot_replay_restored_commands(self):
        self.command_results = [(202, "unknown")]
        self.page.locator("#inp").fill("Pendência sintética de outro dispositivo")
        self.page.locator("#send-btn").click()
        self.wait_state("unknown")
        self.page.evaluate("sessionStorage.setItem('jarvis_device_id', 'different-fixture-device')")
        self.page.reload(wait_until="load")
        self.assertIn("outro dispositivo", self.page.locator("#recovery-notice").inner_text())
        self.page.locator(".msg-u .delivery-retry").click()
        self.page.locator("#send-btn").click()
        self.assertEqual(len(self.command_bodies), 1)
        self.assertEqual(self.page.locator("#inp").input_value(), "Pendência sintética de outro dispositivo")


if __name__ == "__main__":
    unittest.main()
