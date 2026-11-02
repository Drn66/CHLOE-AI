"""Preferências em arquivos temporários; não lê nem altera a configuração real."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from memory import ui_preferences as preferences


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config" / "ui_preferences.json"
        self.patcher = patch.object(preferences, "PREFERENCES_FILE", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_first_launch_is_collapsed_and_greeting_is_silent(self):
        self.assertTrue(preferences.get_chat_collapsed())
        self.assertEqual(preferences.get_chat_width(), 340)
        self.assertEqual(preferences.get_startup_preferences(), {
            "enabled": True, "spoken": False, "include_time": True,
            "include_cpu": False, "include_ram": False, "include_gpu": False,
        })
        self.assertFalse(self.path.exists())

    def test_partial_changes_preserve_other_preferences_and_files(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"other": "preservar", "startup": {
            "enabled": False, "future_option": True,
        }}), encoding="utf-8")
        other_file = self.path.parent / "untouched.json"
        other_file.write_text("não modificar", encoding="utf-8")
        self.assertTrue(preferences.save_startup_preferences({"spoken": True}))
        self.assertTrue(preferences.save_chat_collapsed(False))
        self.assertFalse(preferences.get_startup_preferences()["enabled"])
        self.assertTrue(preferences.get_startup_preferences()["spoken"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["other"], "preservar")
        self.assertTrue(data["startup"]["future_option"])
        self.assertEqual(other_file.read_text(encoding="utf-8"), "não modificar")

    def test_invalid_existing_file_is_preserved(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{incompleto", encoding="utf-8")
        self.assertTrue(preferences.get_chat_collapsed())
        self.assertFalse(preferences.save_chat_collapsed(False))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{incompleto")

    def test_concurrent_updates_do_not_lose_independent_fields(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(preferences.save_chat_collapsed, False),
                executor.submit(preferences.save_chat_width, 420),
                executor.submit(preferences.save_startup_preferences, {"spoken": True}),
            ]
            self.assertTrue(all(future.result() for future in futures))
        self.assertFalse(preferences.get_chat_collapsed())
        self.assertEqual(preferences.get_chat_width(), 420)
        self.assertTrue(preferences.get_startup_preferences()["spoken"])
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_failed_replace_keeps_previous_file_and_removes_temporary(self):
        self.assertTrue(preferences.save_chat_collapsed(True))
        before = self.path.read_bytes()
        with patch.object(preferences.os, "replace", side_effect=OSError("falha")):
            self.assertFalse(preferences.save_chat_collapsed(False))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_values_do_not_enable_features(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "chat_collapsed": "false", "chat_width": "enorme",
            "startup": {"spoken": "true", "include_gpu": 1},
        }), encoding="utf-8")
        self.assertTrue(preferences.get_chat_collapsed())
        self.assertEqual(preferences.get_chat_width(), 340)
        self.assertFalse(preferences.get_startup_preferences()["spoken"])
        self.assertFalse(preferences.get_startup_preferences()["include_gpu"])


if __name__ == "__main__":
    unittest.main()
