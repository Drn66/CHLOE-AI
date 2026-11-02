"""The UI and greeting consume per-sensor validity rather than a global flag."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import ui
from main import JarvisLive


class MonitorPresentationTests(unittest.TestCase):
    def snapshot(self):
        return dict(cpu=88.0, mem=42.0, gpu=10.0, tmp=-1.0, net=-1.0,
                    ready=False, ram_used_gb=4.2, ram_total_gb=10.0, process_count=None,
                    sensors={
                        "cpu": {"status": "stale", "age_seconds": 10.0, "collected_at": 1700000000.0},
                        "mem": {"status": "valid", "age_seconds": 0.0, "collected_at": 1700000010.0},
                        "gpu": {"status": "valid", "age_seconds": 0.0, "collected_at": 1700000010.0},
                    })

    def test_ui_marks_stale_cpu_and_preserves_current_independent_sensors(self):
        window = SimpleNamespace(_system_labels={key: Mock() for key in ("cpu", "mem", "gpu")},
                                 _uptime_lbl=Mock(), _proc_lbl=Mock(),
                                 **{f"_bar_{key}": Mock() for key in ("cpu", "mem", "gpu", "tmp", "net")})
        metrics = SimpleNamespace(snapshot=self.snapshot)
        with patch.object(ui, "_metrics", metrics), patch.object(ui.psutil, "boot_time", return_value=0):
            ui.MainWindow._update_metrics(window)
        window._system_labels["cpu"].setText.assert_called_once_with("CPU · Desatualizado")
        window._bar_cpu.set_value.assert_called_once_with(0, "Desatualizado")
        window._system_labels["mem"].setText.assert_called_once_with("RAM · 42%")
        window._system_labels["gpu"].setText.assert_called_once_with("GPU · 10%")
        self.assertIn("Última coleta válida", window._system_labels["cpu"].setToolTip.call_args.args[0])

    def test_greeting_does_not_drop_fresh_sensors_when_ready_is_false(self):
        preferences = dict(include_time=False, include_cpu=True, include_ram=True, include_gpu=True)
        text = JarvisLive._startup_text(preferences, self.snapshot())
        self.assertIn("CPU: Indisponível", text)
        self.assertIn("RAM: 42%", text)
        self.assertIn("GPU: 10%", text)
        self.assertNotIn("88%", text)
