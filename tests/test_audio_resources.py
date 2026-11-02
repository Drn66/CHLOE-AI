"""Audio ownership under device removal and task cancellation; no real audio."""
import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from main import JarvisLive


class AudioResourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.live = JarvisLive.__new__(JarvisLive)
        self.live.ui = Mock()
        self.live._out_latency = 0.2
        self.live._play_cursor = 0.0
        self.live._interrupted = False
        self.live._is_speaking = False
        self.live._turn_done_event = asyncio.Event()
        self.live.audio_in_queue = asyncio.Queue()
        self.live.set_speaking = Mock()
        self.stream = Mock(device=7, latency=0.2)
        self.resolve = patch("main.audio_devices.resolve", return_value=None)
        self.open = patch("main.sd.RawOutputStream", return_value=self.stream)
        self.resolve.start()
        self.open.start()
        self.addCleanup(self.resolve.stop)
        self.addCleanup(self.open.stop)

    async def test_device_removed_during_query_closes_open_stream(self):
        with patch("main.sd.query_devices", side_effect=RuntimeError("device removed")):
            with self.assertRaises(RuntimeError):
                await self.live._play_audio_stream()
        self.stream.start.assert_called_once()
        self.stream.close.assert_called_once()

    async def test_cancelling_idle_playback_closes_stream_once(self):
        with patch("main.sd.query_devices", return_value={"name": "test"}):
            task = asyncio.create_task(self.live._play_audio_stream())
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.stream.stop.assert_called_once()
        self.stream.close.assert_called_once()

    async def test_start_failure_closes_stream_once(self):
        self.stream.start.side_effect = RuntimeError("device removed")
        with self.assertRaises(RuntimeError):
            await self.live._play_audio_stream()
        self.stream.close.assert_called_once()

    async def test_stop_failure_still_closes_stream(self):
        self.stream.stop.side_effect = RuntimeError("device removed")
        with patch("main.sd.query_devices", side_effect=RuntimeError("query failed")):
            with self.assertRaises(RuntimeError):
                await self.live._play_audio_stream()
        self.stream.close.assert_called_once()

    async def test_cancellation_waits_for_active_write_before_closing(self):
        entered, release = threading.Event(), threading.Event()
        closed_during_write = []

        def write(_data):
            entered.set()
            release.wait(2.0)
            closed_during_write.append(self.stream.close.called)

        self.stream.write.side_effect = write
        self.live._visemes = Mock()
        self.live._echo = Mock()
        await self.live.audio_in_queue.put(bytes(2400))
        with patch("main.sd.query_devices", return_value={"name": "test"}):
            task = asyncio.create_task(self.live._play_audio_stream())
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(entered.is_set())
                task.cancel()
                await asyncio.sleep(0.01)
                self.stream.close.assert_not_called()
                task.cancel()
                task.cancel()
                await asyncio.sleep(0.01)
                self.stream.close.assert_not_called()
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(closed_during_write, [False])
        self.stream.close.assert_called_once()
