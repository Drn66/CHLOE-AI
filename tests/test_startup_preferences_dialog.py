"""Diálogo da saudação em Qt offscreen e preferências exclusivamente temporárias."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from frontend import startup_preferences as dialog_module
from memory import ui_preferences


class StartupPreferencesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "ui_preferences.json"
        patcher = patch.object(ui_preferences, "PREFERENCES_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dialogs = []
        self.addCleanup(self.close_dialogs)

    def close_dialogs(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def dialog(self):
        dialog = dialog_module.StartupPreferencesDialog()
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def button(self, dialog, text):
        return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)

    def test_cancel_discards_changed_fields_without_creating_preferences(self):
        with patch.object(dialog_module, "save_startup_preferences") as save:
            dialog = self.dialog()
            dialog.mode.setCurrentIndex(1)
            dialog.fields["include_gpu"].setChecked(True)
            self.button(dialog, "Cancelar").click()
            save.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(self.path.exists())

    def test_save_passes_only_startup_fields_and_preserves_other_preferences(self):
        self.path.write_text(json.dumps({"chat_collapsed": False, "chat_width": 420}), encoding="utf-8")
        with patch.object(dialog_module, "save_startup_preferences", wraps=ui_preferences.save_startup_preferences) as save:
            dialog = self.dialog()
            dialog.enabled.setChecked(True)
            dialog.mode.setCurrentIndex(1)
            dialog.fields["include_time"].setChecked(False)
            dialog.fields["include_cpu"].setChecked(True)
            dialog.fields["include_ram"].setChecked(True)
            dialog.fields["include_gpu"].setChecked(True)
            self.button(dialog, "Salvar").click()
            expected = {
                "enabled": True, "spoken": True, "include_time": False,
                "include_cpu": True, "include_ram": True, "include_gpu": True,
            }
            save.assert_called_once_with(expected)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored, {"chat_collapsed": False, "chat_width": 420, "startup": expected})

    def test_failed_save_keeps_dialog_open_and_shows_generic_error(self):
        with patch.object(dialog_module, "save_startup_preferences", return_value=False), patch.object(
            dialog_module.QMessageBox, "warning"
        ) as warning:
            dialog = self.dialog()
            accepted = Mock()
            dialog.accepted.connect(accepted)
            self.button(dialog, "Salvar").click()
            accepted.assert_not_called()
            self.assertTrue(dialog.isVisible())
            warning.assert_called_once_with(
                dialog, "Preferências não salvas",
                "Não foi possível salvar as preferências locais. Verifique o acesso à pasta do projeto.",
            )
        self.assertFalse(self.path.exists())

    def test_disabling_greeting_disables_options_and_preserves_selections(self):
        ui_preferences.save_startup_preferences({"enabled": False, "spoken": True, "include_cpu": True})
        dialog = self.dialog()
        self.assertFalse(dialog.enabled.isChecked())
        for widget in (dialog.mode, *dialog.fields.values()):
            self.assertFalse(widget.isEnabled())
        self.assertTrue(dialog.mode.currentData())
        self.assertTrue(dialog.fields["include_cpu"].isChecked())
        dialog.enabled.setChecked(True)
        for widget in (dialog.mode, *dialog.fields.values()):
            self.assertTrue(widget.isEnabled())
        self.assertTrue(dialog.mode.currentData())
        self.assertTrue(dialog.fields["include_cpu"].isChecked())
        self.button(dialog, "Cancelar").click()
        self.assertFalse(ui_preferences.get_startup_preferences()["enabled"])


if __name__ == "__main__":
    unittest.main()
