"""Independent sensor failures must never preserve misleading current values."""
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from actions import system_monitor as monitor


_START_THREAD = threading.Thread.start
_GPU_PROVIDER = monitor._get_gpu_usage


class MetricFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("threading.Thread.start"))
        self.clock = self.stack.enter_context(patch.object(monitor.time, "monotonic", return_value=100.0))
        self.cpu = self.stack.enter_context(patch.object(monitor.psutil, "cpu_percent", return_value=20.0))
        self.ram = self.stack.enter_context(patch.object(monitor.psutil, "virtual_memory", return_value=
            SimpleNamespace(percent=40.0, used=4 * 1024**3, total=10 * 1024**3)))
        self.net = self.stack.enter_context(patch.object(monitor.psutil, "net_io_counters", return_value=
            SimpleNamespace(bytes_sent=100, bytes_recv=200)))
        self.gpu = self.stack.enter_context(patch.object(monitor, "_get_gpu_usage", return_value=88.0))
        self.temp = self.stack.enter_context(patch.object(monitor, "_get_cpu_temp", return_value=95.0))
        self.processes = self.stack.enter_context(patch.object(monitor.psutil, "pids", return_value=[1, 2]))
        self.sampler = monitor.SystemMetrics()
        self.addCleanup(self.sampler.stop)

    def test_cpu_failure_preserves_independent_ram_and_gpu(self):
        self.cpu.side_effect = OSError("sensor")
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertLess(snap["cpu"], 0)
        self.assertEqual(snap["mem"], 40.0)
        self.assertEqual(snap["gpu"], 88.0)

    def test_ram_failure_preserves_cpu(self):
        self.ram.side_effect = OSError("sensor")
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertEqual(snap["cpu"], 20.0)
        self.assertLess(snap["mem"], 0)
        self.assertIsNone(snap["ram_used_gb"])

    def test_gpu_failure_does_not_suppress_temperature_or_processes(self):
        self.gpu.side_effect = OSError("sensor")
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertLess(snap["gpu"], 0)
        self.assertEqual(snap["tmp"], 95.0)
        self.assertEqual(snap["process_count"], 2)

    def test_temperature_failure_preserves_gpu(self):
        self.temp.side_effect = OSError("sensor")
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertEqual(snap["gpu"], 88.0)
        self.assertLess(snap["tmp"], 0)

    def test_network_and_process_failures_are_independent(self):
        self.net.side_effect = OSError("sensor")
        self.processes.side_effect = OSError("sensor")
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertLess(snap["net"], 0)
        self.assertIsNone(snap["process_count"])
        self.assertEqual(snap["cpu"], 20.0)
        self.assertEqual(snap["tmp"], 95.0)

    def test_stale_values_are_not_returned_as_current_or_used_for_alerts(self):
        self.sampler._update()
        self.clock.return_value = 130.0
        snap = self.sampler.snapshot()
        self.assertFalse(snap["ready"])
        for key in ("cpu", "mem", "gpu", "tmp", "net"):
            self.assertLess(snap[key], 0)
        self.assertIsNone(snap["process_count"])
        with patch.object(monitor, "get_metrics", return_value=self.sampler):
            self.assertIsNone(monitor.SystemMonitor().check())

    def test_success_has_per_sensor_validity_and_timestamp(self):
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertEqual(snap["sensors"]["gpu"]["status"], "valid")
        self.assertIsInstance(snap["sensors"]["gpu"]["collected_at"], (int, float))

    def test_failure_after_success_invalidates_only_affected_sensor_and_can_recover(self):
        self.sampler._update()
        before = self.sampler.snapshot()["sensors"]["gpu"]["collected_at"]
        self.clock.return_value = 102.0
        self.gpu.side_effect = OSError("removed")
        self.sampler._slow_tick = 0
        self.sampler._update()
        snap = self.sampler.snapshot()
        self.assertLess(snap["gpu"], 0)
        self.assertEqual(snap["sensors"]["gpu"]["status"], "unavailable")
        self.assertEqual(snap["sensors"]["gpu"]["collected_at"], before)
        self.assertEqual(snap["tmp"], 95.0)
        self.assertTrue(snap["ready"])
        self.gpu.side_effect = None
        self.gpu.return_value = 17.0
        self.sampler._slow_tick = 0
        self.sampler._update()
        self.assertEqual(self.sampler.snapshot()["gpu"], 17.0)
        self.assertEqual(self.sampler.snapshot()["sensors"]["gpu"]["status"], "valid")

    def test_fast_sensor_expiry_does_not_invalidate_current_slow_sensors(self):
        self.sampler._update()
        self.clock.return_value = 107.0
        snap = self.sampler.snapshot()
        self.assertFalse(snap["ready"])
        self.assertLess(snap["cpu"], 0)
        self.assertIsNone(snap["ram_used_gb"])
        self.assertEqual(snap["sensors"]["cpu"]["status"], "stale")
        self.assertEqual(snap["gpu"], 88.0)
        self.assertEqual(snap["sensors"]["gpu"]["status"], "valid")
        self.assertEqual(snap["sensors"]["gpu"]["age_seconds"], 7.0)

    def test_nonfinite_and_out_of_range_values_are_unavailable(self):
        for invalid in (float("nan"), float("inf"), -1, 101, True):
            with self.subTest(value=invalid):
                self.cpu.return_value = invalid
                self.gpu.return_value = invalid
                self.sampler._slow_tick = 0
                self.sampler._update()
                snap = self.sampler.snapshot()
                self.assertLess(snap["cpu"], 0)
                self.assertLess(snap["gpu"], 0)
                self.assertEqual(snap["mem"], 40.0)

    def test_network_rate_requires_two_current_samples_and_recovers_after_failure(self):
        self.sampler._update()
        self.assertLess(self.sampler.snapshot()["net"], 0)
        self.clock.return_value = 102.0
        self.net.return_value = SimpleNamespace(bytes_sent=100 + 2 * 1024**2, bytes_recv=200)
        self.sampler._update()
        self.assertAlmostEqual(self.sampler.snapshot()["net"], 1.0)
        self.net.side_effect = OSError("network unavailable")
        self.clock.return_value = 104.0
        self.sampler._update()
        self.assertLess(self.sampler.snapshot()["net"], 0)
        self.net.side_effect = None
        self.clock.return_value = 106.0
        self.sampler._update()
        self.assertLess(self.sampler.snapshot()["net"], 0)
        self.clock.return_value = 108.0
        self.net.return_value = SimpleNamespace(bytes_sent=100 + 4 * 1024**2, bytes_recv=200)
        self.sampler._update()
        self.assertAlmostEqual(self.sampler.snapshot()["net"], 1.0)

    def test_system_tool_rejects_stale_sensor_metadata_even_with_positive_adapter_values(self):
        self.sampler._update()
        snap = self.sampler.snapshot()
        snap["sensors"]["gpu"]["status"] = "stale"
        snap["sensors"]["tmp"]["status"] = "unavailable"
        adapter = SimpleNamespace(snapshot=lambda: snap)
        with patch.object(monitor, "get_metrics", return_value=adapter), patch.object(
                monitor.psutil, "boot_time", return_value=0):
            result = monitor.get_system_status()
        self.assertEqual(result["cpu_percent"], 20.0)
        self.assertIsNone(result["gpu_percent"])
        self.assertIsNone(result["cpu_temp_c"])

    def test_alerts_require_distinct_cpu_samples(self):
        self.cpu.return_value = 97.0
        self.gpu.return_value = 0.0
        self.temp.return_value = 40.0
        self.sampler._update()
        checker = monitor.SystemMonitor()
        with patch.object(monitor, "get_metrics", return_value=self.sampler):
            for _ in range(10):
                self.assertIsNone(checker.check())
            self.assertEqual(checker._cpu_streak, 1)
            self.clock.return_value = 102.0
            self.sampler._update()
            self.assertIsNone(checker.check())
            self.assertEqual(checker._cpu_streak, 2)
            self.clock.return_value = 104.0
            self.sampler._update()
            self.assertIn("CPU usage", checker.check())

    def test_alerts_reject_invalid_metadata_from_external_adapter(self):
        self.sampler._update()
        snap = self.sampler.snapshot()
        snap["sensors"]["tmp"]["status"] = "stale"
        snap["sensors"]["gpu"]["status"] = "unavailable"
        adapter = SimpleNamespace(snapshot=lambda: snap)
        with patch.object(monitor, "get_metrics", return_value=adapter):
            self.assertIsNone(monitor.SystemMonitor().check())

    def test_snapshot_cannot_mutate_sampler_metadata(self):
        self.sampler._update()
        snapshot = self.sampler.snapshot()
        snapshot["sensors"]["gpu"]["status"] = "stale"
        self.assertEqual(self.sampler.snapshot()["sensors"]["gpu"]["status"], "valid")

    def test_unexpected_sampler_failure_invalidates_entire_unknown_batch(self):
        self.sampler._update()
        with patch.object(self.sampler, "_update", side_effect=RuntimeError("sampler failure")), patch.object(
                self.sampler._stop_event, "wait", side_effect=lambda _: self.sampler._stop_event.set()):
            self.sampler._loop()
        snapshot = self.sampler.snapshot()
        self.assertFalse(snapshot["ready"])
        for key in ("cpu", "mem", "gpu", "tmp", "net"):
            self.assertLess(snapshot[key], 0)
            self.assertEqual(snapshot["sensors"][key]["status"], "unavailable")
        self.assertIsNone(snapshot["process_count"])

    def test_stop_interrupts_wait_and_thread_exits(self):
        entered = threading.Event()
        sampler = self.sampler
        sampler._update = entered.set
        worker = threading.Thread(target=sampler._loop)
        # setUp suppresses real starts for sampler construction; start only this
        # isolated event-only worker, with all hardware providers still mocked.
        sampler._thread = worker
        _START_THREAD(worker)
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(sampler.stop(timeout=1))
        self.assertFalse(worker.is_alive())
        self.assertTrue(sampler._stop_event.is_set())

    def test_pynvml_bad_cache_is_retried_after_cooldown(self):
        library = Mock()
        library.nvmlDeviceGetHandleByIndex.return_value = object()
        library.nvmlDeviceGetUtilizationRates.side_effect = [SimpleNamespace(gpu=float("nan")),
                                                          SimpleNamespace(gpu=12.0)]
        with patch.object(monitor, "_pynvml", library), patch.object(
                monitor, "_pynvml_device", object()), patch.object(
                monitor, "_pynvml_ok", True), patch.object(
                monitor, "_pynvml_retry_at", 0.0), patch.object(monitor, "_nvml_gpu", return_value=-1.0):
            self.assertEqual(_GPU_PROVIDER(), -1.0)
            self.assertFalse(monitor._pynvml_ok)
            self.clock.return_value = 110.0
            self.assertEqual(_GPU_PROVIDER(), -1.0)
            self.assertEqual(library.nvmlDeviceGetUtilizationRates.call_count, 1)
            self.clock.return_value = 131.0
            self.assertEqual(_GPU_PROVIDER(), 12.0)
            library.nvmlDeviceGetHandleByIndex.assert_called_once()
