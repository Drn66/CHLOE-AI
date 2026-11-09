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
    def setUp(self):
        # Nenhum teste escreve no logs/launcher.log real.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(launcher, "LOG", Path(tmp.name) / "launcher.log")
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def _isolated_log(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "logs" / "launcher.log"
        for patcher in (patch.object(launcher, "LOG", log), patch.object(launcher, "_shown_signatures", set())):
            patcher.start()
            self.addCleanup(patcher.stop)
        windll = patch.object(launcher.ctypes, "windll").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(lambda: [h.close() for h in launcher.logging.getLogger("chloe.launcher.crash").handlers])
        return log, windll.user32.MessageBoxW

    def test_callback_exception_logs_safe_traceback_and_warns_once(self):
        log, dialog = self._isolated_log()

        def island_wheel_handler():
            raise RuntimeError("https://example.invalid/?key=PRIVATE_TEST_MARKER " + str(Path.home()))

        for _ in range(3):  # mesma falha repetida: registra sempre, avisa uma vez
            try:
                island_wheel_handler()
            except RuntimeError:
                launcher._ui_excepthook(*sys.exc_info())
        text = log.read_text(encoding="utf-8")
        self.assertEqual(text.count("ui_runtime_error"), 3)
        self.assertIn("thread=MainThread", text)
        # origem = função mais externa do projeto no traceback (num slot Qt, o próprio handler)
        self.assertIn("origem=LauncherTests.test_callback_exception_logs_safe_traceback_and_warns_once", text)
        self.assertIn("em LauncherTests.test_callback_exception_logs_safe_traceback_and_warns_once."
                      "<locals>.island_wheel_handler", text)
        self.assertIn("tests/test_windows_launcher.py:", text)
        self.assertIn("RuntimeError", text)
        for secret in ("PRIVATE_TEST_MARKER", "example.invalid", str(Path.home())):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, str(dialog.call_args_list))
        dialog.assert_called_once()
        self.assertIn("continua em execução", dialog.call_args.args[1])
        self.assertIn("ui_runtime_error", dialog.call_args.args[1])

    def test_worker_exception_is_logged_with_thread_and_specific_code(self):
        log, dialog = self._isolated_log()

        def fetch():
            raise TimeoutError("PRIVATE_TEST_MARKER")

        with patch.object(threading, "excepthook", launcher._thread_excepthook):
            worker = threading.Thread(target=fetch, name="fixture-worker")
            worker.start()
            worker.join()
        text = log.read_text(encoding="utf-8")
        self.assertIn("worker_io_error", text)
        self.assertIn("thread=fixture-worker", text)
        self.assertNotIn("PRIVATE_TEST_MARKER", text)
        dialog.assert_called_once()

    def test_codes_are_specific_and_from_a_closed_set(self):
        cases = {(RuntimeError, "ui"): "ui_runtime_error", (ModuleNotFoundError, "ui"): "ui_dependency_error",
                 (ConnectionError, "worker"): "worker_io_error", (KeyError, "async"): "async_logic_error",
                 (MemoryError, "ui"): "ui_memory_error", (Exception, "PRIVATE"): "ui_unexpected_error"}
        for (exc_type, origin), code in cases.items():
            self.assertEqual(launcher.classify(exc_type, origin), code)
            self.assertIn(code, launcher.RECOVERABLE_EVENTS)

    def test_run_application_installs_and_removes_hooks(self):
        asyncio_logger = launcher.logging.getLogger("asyncio")
        before = list(asyncio_logger.handlers)
        with patch.object(sys, "excepthook"), patch.object(threading, "excepthook"), \
                patch.object(launcher.runpy, "run_path",
                             side_effect=lambda *a, **k: self.assertIs(sys.excepthook, launcher._ui_excepthook)):
            self.assertEqual(launcher.run_application(), 0)
        self.assertEqual(asyncio_logger.handlers, before)

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
            self.assertEqual(launcher.existing_processes(supervisors=True), {12})

    def test_child_exit_code_and_stderr_are_logged_redacted(self):
        log, _ = self._isolated_log()
        stderr = (f"Traceback\n  File \"{Path.home()}\\x.py\", line 1\n"
                  "GET https://api.example.invalid/v1?key=PRIVATE_TEST_MARKER\n"
                  "api_key=PRIVATE_TEST_MARKER\nRuntimeError: wrapped C/C++ object deleted\n").encode()
        launcher.log_child_exit(3221225477, stderr)
        text = log.read_text(encoding="utf-8")
        self.assertIn("app_exit code=3221225477", text)
        self.assertIn("RuntimeError: wrapped C/C++ object deleted", text)
        self.assertIn("https://api.example.invalid/v1?<omitido>", text)
        for secret in ("PRIVATE_TEST_MARKER", str(Path.home())):
            self.assertNotIn(secret, text)

    def _supervise(self, app_pids, stale, restart):
        mutex = patch.object(launcher.ctypes, "WinDLL").start()
        mutex.return_value.CreateMutexW.return_value = 1
        patch.object(launcher.ctypes, "get_last_error", return_value=0).start()
        patch.object(launcher, "existing_processes",
                     side_effect=lambda supervisors=False: {90} if supervisors else set(app_pids)).start()
        patch.object(launcher, "stale_instances", return_value=stale).start()
        patch.object(launcher, "ask_restart_stale", return_value=restart).start()
        show = patch.object(launcher, "show_existing").start()
        term = patch.object(launcher, "terminate").start()
        run = patch.object(launcher.subprocess, "run", return_value=Mock(returncode=0, stderr=b"")).start()
        self.assertEqual(launcher.supervise(), 0)
        return show, term, run

    def test_stale_instance_is_restarted_after_confirmation(self):
        log, _ = self._isolated_log()
        show, term, run = self._supervise({9024}, {9024: 1.0}, restart=True)
        self.assertEqual([c.args[0] for c in term.call_args_list], [{90}, {9024}])  # launcher antigo primeiro
        show.assert_not_called()
        self.assertEqual(run.call_args.kwargs["stderr"], launcher.subprocess.PIPE)
        text = log.read_text(encoding="utf-8")
        for event in ("instance_already_running", "stale=[9024]", "stale_instance_closed", "app_launch", "app_exit code=0"):
            self.assertIn(event, text)

    def test_current_instance_is_only_focused(self):
        log, _ = self._isolated_log()
        show, term, run = self._supervise({9024}, {}, restart=True)
        show.assert_called_once_with({9024})
        term.assert_not_called()
        run.assert_not_called()
        self.assertIn("stale=[]", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
