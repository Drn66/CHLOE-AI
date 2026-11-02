"""Observable Qt setup recovery with temporary persistence and synthetic input."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import ui
from memory import config_manager, config_store


class SetupRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "missing" / "api_keys.json"
        self.path_patch = patch.object(ui, "API_FILE", self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.config_patch = patch.object(config_manager, "CONFIG_FILE", self.path)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.form = ui.SetupOverlay()
        self.form.resize(500, 500)
        self.window = SimpleNamespace(_overlay=self.form, _ready=False, _log=Mock(),
                                      _style_mute_btn=Mock(), _apply_state=Mock())
        self.form.done.connect(lambda key, os_name: ui.MainWindow._on_setup_done(self.window, key, os_name))
        self.form.show()
        self.app.processEvents()
        self.addCleanup(self.close_form)

    def close_form(self):
        self.form.close()
        self.form.deleteLater()
        self.app.processEvents()

    def submit(self):
        QTest.mouseClick(self.form._submit_btn, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def test_empty_key_has_visible_validation_and_no_file(self):
        self.submit()
        self.assertTrue(self.form._error_label.isVisible())
        self.assertIn("Informe", self.form._error_label.text())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.window._ready)

    def test_permission_failure_preserves_form_and_allows_retry(self):
        self.form._key_input.setText("synthetic-placeholder")
        with patch.object(config_store.os, "replace", side_effect=PermissionError("PRIVATE_TEST_MARKER")):
            self.submit()
        self.assertFalse(self.window._ready)
        self.assertTrue(self.form.isVisible())
        self.assertTrue(self.form._submit_btn.isEnabled())
        self.assertEqual(self.form._key_input.text(), "synthetic-placeholder")
        self.assertIn("tente novamente", self.form._error_label.text())
        self.assertNotIn("PRIVATE_TEST_MARKER", self.form._error_label.text())
        self.assertNotIn("synthetic-placeholder", str(self.window._log.append_log.call_args_list))
        self.submit()
        self.assertTrue(self.window._ready)
        self.assertFalse(self.form.isVisible())
        self.assertEqual(json.loads(self.path.read_text())["gemini_api_key"], "synthetic-placeholder")

    def test_interrupted_write_keeps_previous_file_and_form(self):
        config_manager.update_config({"unrelated": True}, path=self.path)
        before = self.path.read_bytes()
        self.form._key_input.setText("synthetic-placeholder")
        with patch.object(config_store.os, "fsync", side_effect=OSError("interrupted")):
            self.submit()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.window._ready)
        self.assertTrue(self.form._error_label.isVisible())
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_unreadable_configuration_is_preserved_for_recovery(self):
        self.path.parent.mkdir()
        self.path.write_text("{broken", encoding="utf-8")
        self.form._key_input.setText("synthetic-placeholder")
        self.submit()
        self.assertFalse(self.window._ready)
        self.assertEqual(self.path.read_text(), "{broken")
        self.assertTrue(self.form._error_label.isVisible())

    def test_missing_directory_and_successful_save_preserve_other_fields(self):
        config_manager.update_config({"unrelated": True}, path=self.path)
        self.form._key_input.setText("synthetic-placeholder")
        self.submit()
        saved = json.loads(self.path.read_text())
        self.assertTrue(saved["unrelated"])
        self.assertTrue(self.window._ready)
        self.assertFalse(self.form.isVisible())

    def test_audio_device_failure_preserves_selection_and_does_not_reconnect(self):
        with patch("core.audio_devices.list_devices", return_value=["Test device"]):
            overlay = ui.AudioDeviceOverlay()
        try:
            overlay._in_box.setCurrentIndex(1)
            overlay._out_box.setCurrentIndex(1)
            overlay.show()
            self.app.processEvents()
            picked = Mock()
            overlay.picked.connect(picked)
            with patch.object(ui, "update_config", side_effect=PermissionError()), patch.object(
                ui, "_configuration_warning"
            ) as warning:
                overlay._apply()
            self.assertTrue(overlay.isVisible())
            self.assertEqual(overlay._in_box.currentData(), "Test device")
            picked.assert_not_called()
            warning.assert_called_once()
            overlay._apply()
            saved = json.loads(self.path.read_text())
            self.assertEqual((saved["input_device"], saved["output_device"]), ("Test device", "Test device"))
            picked.assert_called_once()
        finally:
            overlay.close()
            overlay.deleteLater()
            self.app.processEvents()

    def test_customization_failure_does_not_change_runtime_or_claim_saved(self):
        config_manager.update_config({"assistant_name": "Before", "voice_name": "Aoede"}, path=self.path)
        window = SimpleNamespace(_assistant_name="Before", _log=Mock(), on_voice_change=Mock())
        with patch.object(ui, "update_config", side_effect=PermissionError()), patch.object(
            ui, "_configuration_warning"
        ) as warning:
            ui.MainWindow._apply_name_update(window, "After", "Synthetic user", "#22aaff", "Kore")
        self.assertEqual(window._assistant_name, "Before")
        window.on_voice_change.assert_not_called()
        warning.assert_called_once()
        saved = json.loads(self.path.read_text())
        self.assertEqual((saved["assistant_name"], saved["voice_name"]), ("Before", "Aoede"))
