"""Exercise actual shortcut/registry wiring without changing the user's system."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import launch_chloe as launcher
from core.startup import windows_launch_command
import ui


class StartupCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="chloe space ")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        executable = self.base / ".venv" / "Scripts" / "pythonw.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        (self.base / "launch_chloe.py").touch()
        self.command = windows_launch_command(self.base)
        log = patch.object(launcher, "LOG", self.base / "logs" / "launcher.log")  # nunca o log real
        log.start()
        self.addCleanup(log.stop)

    def test_command_uses_supervisor_and_quotes_paths_with_spaces(self):
        self.assertEqual(self.command.script.name, "launch_chloe.py")
        self.assertEqual(self.command.cwd, self.base.resolve())
        self.assertEqual(self.command.command_line,
                         subprocess.list2cmdline([str(self.command.executable), str(self.command.script)]))
        self.assertIn('"', self.command.command_line)

    def test_incomplete_environment_fails_before_registering(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                windows_launch_command(Path(directory))

    def test_shortcut_and_autostart_share_the_identical_command(self):
        registry = Mock()
        window = SimpleNamespace(_check_autostart=Mock(return_value=False),
                                 _update_autostart_btn=Mock(), _log=Mock(),
                                 _get_desktop_dir=Mock(return_value=self.base),
                                 _create_lnk_windows=Mock(), _build_jarvis_icon=Mock())
        with patch.object(ui, "windows_launch_command", return_value=self.command), patch.object(
            ui, "_OS", "Windows"
        ), patch.object(ui.platform, "system", return_value="Windows"), patch.dict(
            sys.modules, {"winreg": registry}
        ):
            ui.MainWindow._toggle_autostart(window)
            ui.MainWindow._create_desktop_shortcut(window)
        saved_command = registry.SetValueEx.call_args.args[-1]
        shortcut = window._create_lnk_windows.call_args.args
        self.assertEqual(saved_command, self.command.command_line)
        self.assertEqual(shortcut[1:4], (str(self.command.executable), str(self.command.script), str(self.command.cwd)))
        registry.CloseKey.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows supervisor")
    def test_existing_instance_never_starts_another_worker(self):
        kernel = Mock()
        with patch.object(launcher.ctypes, "WinDLL", return_value=kernel), patch.object(
            launcher.ctypes, "get_last_error", return_value=183
        ), patch.object(launcher, "existing_processes", return_value=set()), patch.object(
            launcher, "show_existing"
        ) as show, patch.object(launcher.subprocess, "run") as spawn, patch(
            "psutil.Process", return_value=Mock(parents=Mock(return_value=[]))
        ):
            self.assertEqual(launcher.supervise(), 0)
        spawn.assert_not_called()
        show.assert_called_once()
        kernel.CloseHandle.assert_called_once()

    def test_worker_marks_main_as_supervised_to_prevent_recursive_launch(self):
        with patch.object(sys, "excepthook"), patch.object(launcher.threading, "excepthook"), patch.object(
            launcher.runpy, "run_path"
        ) as run:
            self.assertEqual(launcher.run_application(), 0)
        self.assertTrue(run.call_args.kwargs["init_globals"]["_CHLOE_SUPERVISED"])
