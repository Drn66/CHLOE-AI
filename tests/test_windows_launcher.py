"""Launcher regressions; no credentials, network calls or real dialogs."""
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import launch_chloe as launcher


class LauncherTests(unittest.TestCase):
    def test_diagnostics_do_not_include_arbitrary_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "launcher.log"
            with patch.object(launcher, "LOG", log), patch.object(launcher.ctypes, "windll") as api:
                launcher.report_failure("PRIVATE_TEST_MARKER", "PRIVATE_TEST_MARKER")
                self.assertNotIn("PRIVATE_TEST_MARKER", log.read_text())
                self.assertNotIn("PRIVATE_TEST_MARKER", str(api.user32.MessageBoxW.call_args))
                self.assertIn("launcher_failed", log.read_text())

    def test_import_failure_and_prints_are_not_exposed(self):
        captured = io.StringIO()

        def fail(*args, **kwargs):
            print("PRIVATE_TEST_MARKER")
            print("PRIVATE_TEST_MARKER", file=sys.stderr)
            raise RuntimeError("PRIVATE_TEST_MARKER")

        with patch.object(sys, "stdout", captured), patch.object(sys, "stderr", captured), \
                patch.object(sys, "excepthook"), patch.object(threading, "excepthook"), \
                patch.object(launcher.runpy, "run_path", side_effect=fail):
            self.assertEqual(launcher.run_application(), 1)
        self.assertEqual(captured.getvalue(), "")

    def test_clean_application_close_is_success(self):
        with patch.object(sys, "excepthook"), patch.object(threading, "excepthook"), \
                patch.object(launcher.runpy, "run_path", side_effect=SystemExit(0)):
            self.assertEqual(launcher.run_application(), 0)

    def test_detects_direct_main_and_child_but_not_competing_launchers(self):
        def process(pid, args):
            return Mock(info={"pid": pid, "name": "pythonw.exe", "cmdline": args,
                              "cwd": str(launcher.BASE)})

        processes = [process(11, ["pythonw.exe", "main.py"]),
                     process(12, ["pythonw.exe", "launch_chloe.py"]),
                     process(13, ["pythonw.exe", "launch_chloe.py", "--run-app"]),
                     process(14, ["pythonw.exe", str(launcher.BASE.parent / "main.py")])]
        with patch("psutil.process_iter", return_value=processes):
            self.assertEqual(launcher.existing_processes(), {11, 13})


if __name__ == "__main__":
    unittest.main()
