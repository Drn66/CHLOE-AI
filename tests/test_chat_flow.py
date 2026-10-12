"""Regression tests for text delivery and optional local audio hardware.

Run with: python -B -m unittest discover -s tests -v
No credentials or network calls are used by this suite.
"""
import asyncio
import os
import time
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from frontend.chat import CommandInput
from main import JarvisLive, sd
from ui import MainWindow


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assistant = JarvisLive.__new__(JarvisLive)
        self.assistant.ui = Mock()
        self.assistant.ui.muted = False
        self.assistant._loop = None
        self.assistant.session = None
        self.assistant._wake_enabled = False
        self.assistant._awake = True
        self.assistant._session_log = []

    async def send(self):
        self.assistant._loop = asyncio.get_running_loop()
        return await asyncio.to_thread(self.assistant._on_text_command, "Olá")

    async def test_disconnected_send_reports_failure(self):
        self.assertFalse(await self.send())
        self.assistant.ui.write_log.assert_called_once()

    async def test_success_uses_existing_live_payload(self):
        send = AsyncMock()
        self.assistant.session = SimpleNamespace(send_client_content=send)
        self.assertTrue(await self.send())
        send.assert_awaited_once_with(
            turns={"role": "user", "parts": [{"text": "Olá"}]},
            turn_complete=True,
        )
        self.assertEqual(self.assistant._session_log, ["User: Olá"])

    async def test_transport_failure_is_not_silently_discarded(self):
        self.assistant.session = SimpleNamespace(
            send_client_content=AsyncMock(side_effect=ConnectionError("private transport details")))
        self.assertFalse(await self.send())
        self.assertEqual(self.assistant._session_log, [])
        self.assertNotIn("private transport details", self.assistant.ui.write_log.call_args.args[0])

    async def test_sleep_gate_is_preserved(self):
        send = AsyncMock()
        self.assistant.session = SimpleNamespace(send_client_content=send)
        self.assistant._wake_enabled = True
        self.assistant._awake = False
        self.assertFalse(await self.send())
        send.assert_not_awaited()

    async def test_missing_microphone_does_not_cancel_sibling_task(self):
        survived = asyncio.Event()
        async def receive():
            await asyncio.sleep(0.02)
            survived.set()
        with patch("main.audio_devices.resolve", return_value=None), patch(
            "main.sd.InputStream", side_effect=sd.PortAudioError("No input")):
            async with asyncio.TaskGroup() as group:
                group.create_task(self.assistant._listen_audio())
                group.create_task(receive())
        self.assertTrue(survived.is_set())
        self.assistant.ui.set_microphone_available.assert_called_once_with(False)

    async def test_missing_speaker_drains_audio_and_remains_cancellable(self):
        self.assistant.audio_in_queue = asyncio.Queue()
        self.assistant._play_audio_stream = AsyncMock(side_effect=sd.PortAudioError("No output"))
        self.assistant.set_speaking = Mock()
        task = asyncio.create_task(self.assistant._play_audio())
        try:
            await self.assistant.audio_in_queue.put(b"audio")
            await asyncio.sleep(0.02)
            self.assertTrue(self.assistant.audio_in_queue.empty())
            self.assertFalse(task.done())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_write_failure_reports_output_error_and_closes_stream(self):
        self.assistant.audio_in_queue = asyncio.Queue()
        self.assistant._turn_done_event = asyncio.Event()
        self.assistant.set_speaking = Mock()
        stream = Mock(device=3, latency=0.2)
        stream.write.side_effect = RuntimeError("Playback failed")
        with patch("main.audio_devices.resolve", return_value=None), patch(
            "main.sd.RawOutputStream", return_value=stream), patch(
            "main.sd.query_devices", return_value={"name": "Test output"}):
            await self.assistant.audio_in_queue.put(bytes(2400))
            task = asyncio.create_task(self.assistant._play_audio())
            try:
                for _ in range(100):
                    await asyncio.sleep(0.005)
                    if stream.close.called:
                        break
                stream.write.assert_called_once()
                stream.close.assert_called_once()
                self.assertFalse(self.assistant.ui.set_playback_status.call_args.args[0])
                self.assertFalse(task.done())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_speaking_ends_even_with_microphone_muted(self):
        self.assistant._speaking_lock = threading.Lock()
        self.assistant._out_latency = 0.2
        self.assistant.ui.muted = True
        self.assistant.set_speaking(True)
        self.assistant.ui.set_state.assert_called_with("SPEAKING")
        self.assistant.set_speaking(False)
        self.assistant.ui.set_state.assert_called_with("MUTED")

    async def test_muting_releases_microphone_and_unmuting_reopens_it(self):
        self.assistant.ui.muted = True
        stream = MagicMock()
        with patch("main.audio_devices.resolve", return_value=None), patch(
            "main.sd.InputStream", return_value=stream) as open_input:
            task = asyncio.create_task(self.assistant._listen_audio())
            try:
                await asyncio.sleep(0.15)
                open_input.assert_not_called()
                self.assistant.ui.muted = False
                await asyncio.sleep(0.15)
                stream.__enter__.assert_called_once()
                self.assistant.ui.muted = True
                await asyncio.sleep(0.15)
                stream.__exit__.assert_called_once()
                self.assistant.ui.muted = False
                await asyncio.sleep(0.15)
                self.assertEqual(open_input.call_count, 2)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task


class Composer(QObject):
    _text_sent_sig = pyqtSignal(str, bool)
    _log_sig = pyqtSignal(str)
    _send = MainWindow._send
    _text_sent = MainWindow._text_sent

    def __init__(self, callback):
        super().__init__()
        self.on_text_command = callback
        self._input = CommandInput()
        self._log = Mock()
        self._text_sent_sig.connect(self._text_sent)


class ComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def finish(self, composer):
        deadline = time.monotonic() + 2
        while getattr(composer, "_text_pending", False) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(composer._text_pending)

    def test_failed_send_keeps_draft_and_does_not_claim_delivery(self):
        composer = Composer(lambda text: False)
        composer._input.setPlainText("Olá")
        composer._send()
        self.finish(composer)
        self.assertEqual(composer._input.text(), "Olá")
        composer._log.append_log.assert_not_called()

    def test_success_clears_draft_and_logs_once(self):
        composer = Composer(lambda text: True)
        composer._input.setPlainText("Olá")
        composer._send()
        composer._send()  # a repeated Enter before delivery must not resend
        self.finish(composer)
        self.assertEqual(composer._input.text(), "")
        composer._log.append_log.assert_called_once_with("You: Olá")

    def test_delivery_does_not_erase_next_draft(self):
        composer = Composer(lambda text: True)
        composer._input.setPlainText("Olá")
        composer._send()
        composer._input.setPlainText("Próxima pergunta")
        self.finish(composer)
        self.assertEqual(composer._input.text(), "Próxima pergunta")


if __name__ == "__main__":
    unittest.main()
