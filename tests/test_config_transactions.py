"""Configuration regressions: temporary data only, including real processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from memory import config_manager as config
from memory import config_store as store


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config" / "api_keys.json"
        self.patcher = patch.object(config, "CONFIG_FILE", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_missing_directory_is_created_and_unrelated_fields_survive(self):
        config.update_config({"unrelated": {"preserved": True}})
        config.save_api_keys("test-placeholder")
        config.save_assistant_config("CHLOE AI", "Test User")
        config.save_voice("Kore")
        self.assertTrue(self.read()["unrelated"]["preserved"])
        self.assertEqual(self.read()["voice_name"], "Kore")

    def test_actual_setters_do_not_overwrite_concurrent_changes(self):
        config.update_config({"untouched": "kept"})
        updates = [
            (config.save_voice, "Kore"),
            (config.save_wake_word_enabled, True),
            (config.save_push_to_talk_enabled, True),
            (config.save_input_device, "Test input"),
            (config.save_output_device, "Test output"),
            (config.save_media_resolution, "high"),
        ]
        with ThreadPoolExecutor(max_workers=len(updates)) as executor:
            futures = [executor.submit(function, value) for function, value in updates]
            for future in futures:
                future.result(timeout=10)
        result = self.read()
        self.assertEqual(result["voice_name"], "Kore")
        self.assertTrue(result["wake_word_enabled"])
        self.assertTrue(result["push_to_talk_enabled"])
        self.assertEqual(result["input_device"], "Test input")
        self.assertEqual(result["output_device"], "Test output")
        self.assertEqual(result["media_resolution"], "high")
        self.assertEqual(result["untouched"], "kept")

    def test_concurrent_nested_plugin_fields_are_preserved(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(config.save_plugin_config, "test", {f"field_{n}": n})
                       for n in range(40)]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(config.get_plugin_config("test"), {f"field_{n}": n for n in range(40)})

    def test_updates_from_distinct_processes_preserve_every_field(self):
        script = (
            "import sys;from pathlib import Path;"
            "from memory.config_manager import update_config;"
            "path=Path(sys.argv[1]);prefix=sys.argv[2];"
            "\ntry:\n"
            " for n in range(15): update_config({prefix+str(n):n},path=path)\n"
            "except Exception as exc:\n"
            " cause=exc.__context__ or exc\n"
            " print(type(cause).__name__,getattr(cause,'errno',None),"
            "getattr(cause,'winerror',None),file=sys.stderr)\n"
            " raise SystemExit(1)\n"
        )
        processes = []
        try:
            for number in range(4):
                processes.append(subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(self.path), f"process_{number}_"],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ))
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, f"isolated configuration worker failed: {stderr}")
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)
        self.assertEqual(self.read(), {f"process_{p}_{n}": n for p in range(4) for n in range(15)})

    def test_invalid_existing_data_is_preserved_and_error_is_sanitized(self):
        self.path.parent.mkdir()
        original = b'{"private-placeholder":"not-a-real-secret", broken'
        self.path.write_bytes(original)
        with self.assertRaises(config.ConfigUpdateError) as failure:
            config.save_voice("Kore")
        self.assertEqual(failure.exception.code, "arquivo_ilegivel")
        self.assertNotIn("not-a-real-secret", str(failure.exception))
        self.assertNotIn(str(self.path), str(failure.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(config.load_api_keys(), {})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_non_object_existing_data_is_preserved(self):
        self.path.parent.mkdir()
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(config.ConfigUpdateError):
            config.save_wake_word_enabled(True)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "[]")

    def test_replace_failure_preserves_file_and_removes_temporary(self):
        config.update_config({"original": True})
        before = self.path.read_bytes()
        with patch.object(store, "REPLACE_RETRY_SECONDS", 0), patch.object(
                store.os, "replace", side_effect=PermissionError("private I/O detail")):
            with self.assertRaises(config.ConfigUpdateError) as failure:
                config.save_voice("Kore")
        self.assertNotIn("private I/O detail", str(failure.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        config.save_voice("Kore")  # The failed transaction released its locks.
        self.assertEqual(self.read()["voice_name"], "Kore")

    @unittest.skipUnless(os.name == "nt", "Windows sharing denial behavior")
    def test_transient_windows_replace_denial_retries_same_complete_transaction(self):
        config.update_config({"original": True})
        native_replace = os.replace
        attempts = []

        def replace_after_denial(source, destination):
            attempts.append((source, destination))
            if len(attempts) <= 3:
                raise PermissionError(13, "synthetic transient sharing denial")
            return native_replace(source, destination)

        with patch.object(store.os, "replace", side_effect=replace_after_denial):
            config.save_voice("Kore")
        self.assertEqual(len(attempts), 4)
        self.assertEqual(len({source for source, _ in attempts}), 1)
        self.assertEqual(self.read(), {"original": True, "voice_name": "Kore"})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows sharing denial behavior")
    def test_permanent_windows_replace_denial_has_bounded_retry_and_preserves_file(self):
        config.update_config({"original": True})
        before = self.path.read_bytes()
        started = time.monotonic()
        with patch.object(store, "REPLACE_RETRY_SECONDS", 0.04), patch.object(
                store.os, "replace", side_effect=PermissionError(13, "synthetic permanent denial")) as replace:
            with self.assertRaises(config.ConfigUpdateError):
                config.save_voice("Kore")
        self.assertGreater(replace.call_count, 1)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_new_lock_file_is_initialized_only_after_native_lock_acquisition(self):
        native_lock = store._native_lock
        observed_sizes = []

        def check_lock(descriptor, *, unlock=False):
            if not unlock:
                observed_sizes.append(os.fstat(descriptor).st_size)
            return native_lock(descriptor, unlock=unlock)

        with patch.object(store, "_native_lock", side_effect=check_lock):
            config.update_config({"new": True})
        self.assertEqual(observed_sizes, [0])
        lock = self.path.with_name(f".{self.path.name}.lock")
        self.assertEqual(lock.read_bytes(), b"\0")

    def test_interrupted_flush_preserves_file_and_removes_temporary(self):
        config.update_config({"original": True})
        before = self.path.read_bytes()
        with patch.object(store.os, "fsync", side_effect=OSError("interrupted")):
            with self.assertRaises(config.ConfigUpdateError):
                config.update_config({"new": True})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_update_is_rejected_before_touching_disk(self):
        for fields in (None, ["invalid"], {1: True}, {"value": object()}, {"value": float("nan")}):
            with self.subTest(type=type(fields).__name__):
                with self.assertRaises(config.ConfigUpdateError):
                    config.update_config(fields)
        self.assertFalse(self.path.exists())

    def test_process_lock_timeout_is_bounded_and_preserves_file(self):
        config.update_config({"original": True})
        ready = Path(self.directory.name) / "ready"
        script = (
            "import sys;from pathlib import Path;"
            "from memory.config_store import _configuration_lock;"
            "path=Path(sys.argv[1]).resolve();ready=Path(sys.argv[2]);"
            "\nwith _configuration_lock(path):\n"
            " ready.write_text('ready');sys.stdin.readline()\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.path), str(ready)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "isolated lock worker did not become ready")
            started = time.monotonic()
            with patch.object(store, "LOCK_TIMEOUT_SECONDS", 0.05):
                with self.assertRaises(config.ConfigUpdateError) as failure:
                    config.save_voice("Kore")
            self.assertEqual(failure.exception.code, "ocupada")
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(self.read(), {"original": True})
        finally:
            if process.poll() is None:
                process.communicate(input="\n", timeout=5)
            else:
                process.communicate(timeout=5)
        config.save_voice("Kore")
        self.assertEqual(self.read()["voice_name"], "Kore")

    @unittest.skipIf(os.name == "nt", "POSIX file permissions require a POSIX host")
    def test_atomic_file_has_private_permissions_on_posix(self):
        config.update_config({"test": True})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_lock_file_contains_no_configuration_data(self):
        config.update_config({"test": True})
        lock = self.path.with_name(f".{self.path.name}.lock")
        self.assertEqual(lock.read_bytes(), b"\0")


if __name__ == "__main__":
    unittest.main()
