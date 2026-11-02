"""Saudação inicial isolada: nenhuma credencial, API ou saída de áudio real."""

import asyncio
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from main import JarvisLive
from memory.config_manager import get_brief_enabled


DEFAULTS = {
    "enabled": True, "spoken": False, "include_time": True,
    "include_cpu": False, "include_ram": False, "include_gpu": False,
}


class StartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assistant = JarvisLive.__new__(JarvisLive)
        self.assistant.ui = Mock()
        self.assistant.ui.muted = False
        self.assistant.ui.system_snapshot.return_value = {
            "ready": True, "cpu": 12.0, "mem": 47.0, "gpu": None,
        }
        self.send = AsyncMock()
        self.assistant.session = SimpleNamespace(send_client_content=self.send)
        self.assistant._startup_done = False
        self.assistant._startup_user_interacted = False
        self.assistant._awake = True
        self.assistant._response_pending = False
        self.assistant._is_speaking = False
        self.assistant._ptt_held = False
        self.assistant._playback_status_known = asyncio.Event()
        self.assistant._playback_available = False
        self.preferences = dict(DEFAULTS)
        self.preference_patch = patch("main.get_startup_preferences", side_effect=lambda: self.preferences)
        self.preference_patch.start()
        self.addCleanup(self.preference_patch.stop)

    async def test_silent_greeting_is_local_and_not_repeated_after_reconnect(self):
        with patch("main.load_memory") as memory, patch("main.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 12, 9, 7)
            await self.assistant._send_startup_greeting()
            self.assistant.session = SimpleNamespace(send_client_content=self.send)
            await self.assistant._send_startup_greeting()
        self.assistant.ui.show_startup_greeting.assert_called_once_with(
            "Olá! CHLOE AI conectada. Agora são 09:07.")
        self.assistant.ui.system_snapshot.assert_not_called()
        self.send.assert_not_awaited()
        memory.assert_not_called()

    async def test_disabled_greeting_does_not_send_or_sample(self):
        self.preferences["enabled"] = False
        await self.assistant._send_startup_greeting()
        self.send.assert_not_awaited()
        self.assistant.ui.show_startup_greeting.assert_not_called()
        self.assistant.ui.system_snapshot.assert_not_called()

    async def test_spoken_greeting_uses_existing_gemini_session_once(self):
        self.preferences.update(spoken=True, include_time=False, include_cpu=True,
                                include_ram=True, include_gpu=True)
        self.assistant._playback_available = True
        self.assistant._playback_status_known.set()
        await self.assistant._send_startup_greeting()
        await self.assistant._send_startup_greeting()
        self.send.assert_awaited_once()
        payload = self.send.call_args.kwargs
        self.assertEqual(payload["turns"]["role"], "user")
        self.assertTrue(payload["turn_complete"])
        prompt = payload["turns"]["parts"][0]["text"]
        self.assertIn("CPU: 12%; RAM: 47%; GPU: Indisponível", prompt)
        self.assertIn("Não chame ferramentas", prompt)
        self.assertTrue(self.assistant._response_pending)

    async def test_spoken_greeting_waits_for_output_initialization(self):
        self.preferences["spoken"] = True
        task = asyncio.create_task(self.assistant._send_startup_greeting())
        await asyncio.sleep(0)
        self.send.assert_not_awaited()
        self.assistant._playback_available = True
        self.assistant._playback_status_known.set()
        await asyncio.wait_for(task, timeout=1)
        self.send.assert_awaited_once()

    async def test_unavailable_output_falls_back_to_silent_greeting(self):
        self.preferences["spoken"] = True
        self.assistant._playback_status_known.set()
        await self.assistant._send_startup_greeting()
        self.send.assert_not_awaited()
        self.assistant.ui.show_startup_greeting.assert_called_once()
        self.assertIn("em silêncio", self.assistant.ui.write_log.call_args.args[0])

    async def test_output_timeout_is_bounded_and_silent(self):
        self.preferences["spoken"] = True
        with patch("main._STARTUP_READY_TIMEOUT", 0):
            await self.assistant._send_startup_greeting()
        self.send.assert_not_awaited()
        self.assistant.ui.show_startup_greeting.assert_called_once()

    async def test_metrics_wait_for_real_cached_sample(self):
        self.preferences.update(include_time=False, include_cpu=True, include_ram=True)
        self.assistant.ui.system_snapshot.side_effect = [
            {"ready": False, "cpu": 0.0, "mem": 0.0},
            {"ready": True, "cpu": 21.0, "mem": 54.0},
        ]
        await self.assistant._send_startup_greeting()
        self.assistant.ui.show_startup_greeting.assert_called_once_with(
            "Olá! CHLOE AI conectada. CPU: 21%; RAM: 54%.")

    async def test_uninitialized_or_invalid_metrics_are_never_presented_as_real(self):
        self.preferences.update(include_time=False, include_cpu=True, include_gpu=True)
        self.assistant.ui.system_snapshot.return_value = {"ready": False, "cpu": 0, "gpu": 0}
        with patch("main._STARTUP_READY_TIMEOUT", 0):
            await self.assistant._send_startup_greeting()
        self.assistant.ui.show_startup_greeting.assert_called_once_with(
            "Olá! CHLOE AI conectada. CPU: Indisponível; GPU: Indisponível.")
        for value in (-1, float("nan"), float("inf"), True, "30", 101):
            with self.subTest(value=value):
                text = JarvisLive._startup_text(self.preferences, {"ready": True, "gpu": value})
                self.assertIn("GPU: Indisponível", text)

    async def test_user_interaction_during_readiness_wait_cancels_greeting(self):
        self.preferences["spoken"] = True
        task = asyncio.create_task(self.assistant._send_startup_greeting())
        await asyncio.sleep(0)
        self.assistant._startup_user_interacted = True
        await asyncio.wait_for(task, timeout=1)
        self.send.assert_not_awaited()
        self.assistant.ui.show_startup_greeting.assert_not_called()
        self.assertTrue(self.assistant._startup_done)

    async def test_sleeping_session_never_wakes_for_greeting(self):
        self.assistant._awake = False
        await self.assistant._send_startup_greeting()
        self.assistant._awake = True
        await self.assistant._send_startup_greeting()
        self.send.assert_not_awaited()
        self.assistant.ui.show_startup_greeting.assert_not_called()

    async def test_send_failure_does_not_repeat_or_expose_transport_details(self):
        self.preferences["spoken"] = True
        self.assistant._playback_available = True
        self.assistant._playback_status_known.set()
        self.send.side_effect = RuntimeError("detalhe privado do transporte")
        await self.assistant._send_startup_greeting()
        self.assistant.session = SimpleNamespace(send_client_content=self.send)
        await self.assistant._send_startup_greeting()
        self.send.assert_awaited_once()
        self.assertFalse(self.assistant._response_pending)
        self.assertNotIn("detalhe privado", str(self.assistant.ui.write_log.call_args))

    async def test_disconnected_session_waits_until_connected(self):
        self.assistant.session = None
        await self.assistant._send_startup_greeting()
        self.assertFalse(self.assistant._startup_done)
        self.assistant.ui.show_startup_greeting.assert_not_called()

    def test_legacy_news_preference_is_disabled_by_default(self):
        with patch("memory.config_manager.load_api_keys", return_value={}):
            self.assertFalse(get_brief_enabled())


if __name__ == "__main__":
    unittest.main()
