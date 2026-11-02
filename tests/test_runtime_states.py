"""Regressões para estado real de conexão, áudio e medições locais."""
import ctypes
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from actions import system_monitor as monitor
from main import JarvisLive


class InteractionStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.live = JarvisLive.__new__(JarvisLive)
        self.live.ui = Mock(muted=False)
        self.live.session = None
        self.live._speaking_lock = threading.Lock()
        self.live._out_latency = 0.2
        self.live._is_speaking = False
        self.live._awake = True
        self.live._response_pending = False

    def test_disconnected_never_claims_to_think_or_listen(self):
        self.live._response_pending = True
        self.live.set_speaking(False)
        self.live.ui.set_state.assert_called_with("DISCONNECTED")

    def test_connected_pending_response_reports_thinking(self):
        self.live.session = object()
        self.live._response_pending = True
        self.live._refresh_idle_state()
        self.live.ui.set_state.assert_called_with("THINKING")

    def test_sleep_is_preserved_when_audio_finishes(self):
        self.live.session = object()
        self.live._awake = False
        self.live.set_speaking(False)
        self.live.ui.set_state.assert_called_with("SLEEPING")

    def test_microphone_silence_does_not_create_an_audio_level(self):
        from main import _pcm_level
        self.assertEqual(_pcm_level([0] * 1024), 0.0)
        self.assertEqual(_pcm_level([40] * 1024), 0.0)

    async def test_finished_pcm_clears_speaking_without_server_turn_complete(self):
        self.live.session = object()
        self.live.audio_in_queue = asyncio.Queue()
        self.live._turn_done_event = asyncio.Event()
        self.live._play_cursor = 0.0
        self.live._interrupted = False
        self.live._visemes = Mock()
        self.live._echo = Mock()
        stream = Mock(device=3, latency=0.2)
        with patch("main.audio_devices.resolve", return_value=None), patch(
                "main.sd.RawOutputStream", return_value=stream), patch(
                "main.sd.query_devices", return_value={"name": "Saída de teste"}):
            await self.live.audio_in_queue.put(bytes(2400))
            task = asyncio.create_task(self.live._play_audio_stream())
            try:
                await asyncio.sleep(0.22)
                self.assertFalse(self.live._is_speaking)
                self.assertFalse(self.live._turn_done_event.is_set())
                self.live.ui.set_state.assert_called_with("LISTENING")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task


class MetricsTests(unittest.TestCase):
    def test_failed_nvml_call_is_not_reported_as_zero(self):
        library = Mock()
        library.nvmlDeviceGetUtilizationRates.return_value = 3
        with patch.object(monitor, "_nvml_lib", library), patch.object(
                monitor, "_nvml_device", ctypes.c_void_p(1)), patch.object(
                monitor, "_nvml_ok", True):
            self.assertEqual(monitor._nvml_gpu(), -1.0)

    def test_initial_snapshot_has_no_invented_zero_readings(self):
        with patch.object(threading.Thread, "start"):
            sampler = monitor.SystemMetrics()
        snap = sampler.snapshot()
        self.assertFalse(snap["ready"])
        for name in ("cpu", "mem", "net", "gpu", "tmp"):
            self.assertLess(snap[name], 0)

    def test_system_tool_reuses_shared_snapshot(self):
        snap = dict(cpu=12.3, mem=42.5, gpu=-1.0, tmp=-1.0,
                    ram_used_gb=6.8, ram_total_gb=16.0, process_count=10)
        sampler = SimpleNamespace(snapshot=lambda: snap)
        with patch.object(monitor, "get_metrics", return_value=sampler), patch.object(
                monitor.psutil, "cpu_percent") as poll:
            result = monitor.get_system_status()
        poll.assert_not_called()
        self.assertEqual(result["cpu_percent"], 12.3)
        self.assertIsNone(result["gpu_percent"])


if __name__ == "__main__":
    unittest.main()
