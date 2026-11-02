"""Regressões da interface em Qt offscreen, sem API ou configuração pessoal.

Executar com: python -B -m unittest discover -s tests -p test_desktop_layout.py -v
As dimensões são pixels lógicos do Qt; não substituem inspeção visual no Windows.
"""

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QFont, QShortcut
from PyQt6.QtWidgets import QApplication

from memory import config_manager, ui_preferences
import ui


class DesktopLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setFont(QFont("Segoe UI", 10))

    def setUp(self):
        self.isolation = ExitStack()
        self.addCleanup(self.isolation.close)
        self.directory = self.isolation.enter_context(tempfile.TemporaryDirectory())
        self.config_dir = Path(self.directory) / "config"
        self.preferences_path = self.config_dir / "ui_preferences.json"
        for owner, field, value in (
            (config_manager, "CONFIG_DIR", self.config_dir),
            (config_manager, "CONFIG_FILE", self.config_dir / "api_keys.json"),
            (ui_preferences, "PREFERENCES_FILE", self.preferences_path),
            (ui, "CONFIG_DIR", self.config_dir),
            (ui, "API_FILE", self.config_dir / "api_keys.json"),
            (ui, "HAVE_VIDEO", False),
        ):
            self.isolation.enter_context(patch.object(owner, field, value))
        # Nenhuma chave é necessária: a janela recebe configuração vazia e
        # o teste da presença de credenciais é substituído antes de construí-la.
        self.isolation.enter_context(patch.object(ui, "_read_full_config", return_value={}))
        self.isolation.enter_context(patch.object(config_manager, "load_api_keys", return_value={}))
        self.isolation.enter_context(patch.object(ui.MainWindow, "_check_config", return_value=True))
        self.isolation.enter_context(patch.object(ui.MainWindow, "_check_autostart", return_value=False))
        self.isolation.enter_context(patch.object(ui.MainWindow, "_warm_wake_state"))
        self.metrics = Mock()
        self.metrics.snapshot.return_value = {
            "cpu": 23.0, "mem": 42.0, "gpu": -1.0, "tmp": -1.0,
            "net": 0.0, "ram_used_gb": 6.72, "ram_total_gb": 16.0,
        }
        self.isolation.enter_context(patch.object(ui, "_metrics", self.metrics))
        self.windows = []
        self.addCleanup(self.close_windows)

    def close_windows(self):
        for window in self.windows:
            for timer in window.findChildren(QTimer):
                timer.stop()
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def window(self, width=1180, height=780):
        window = ui.MainWindow("")
        self.windows.append(window)
        window.resize(width, height)
        window.show()
        self.app.processEvents()
        return window

    def settle(self):
        self.app.processEvents()

    def test_first_launch_collapses_chat_and_gives_sphere_available_width(self):
        window = self.window()
        self.assertTrue(window._chat_collapsed)
        self.assertFalse(window._right_panel.isVisible())
        self.assertTrue(window._chat_toggle_btn.isVisible())
        self.assertEqual(window._chat_toggle_btn.accessibleName(), "Mostrar conversa")
        self.assertGreaterEqual(window.hud.width(), window._main_body.width() - 2)
        center = window.hud.mapTo(window._main_body, QPoint(window.hud.width() // 2, 0))
        self.assertAlmostEqual(center.x(), window._main_body.width() / 2, delta=2)
        self.assertFalse(self.preferences_path.exists())

    def test_chat_restore_is_about_340_pixels_and_accepts_user_resize(self):
        window = self.window()
        window._chat_toggle_btn.click()
        self.settle()
        self.assertFalse(window._chat_collapsed)
        self.assertTrue(window._right_panel.isVisible())
        self.assertAlmostEqual(window._right_panel.width(), 340, delta=8)
        self.assertEqual(window._main_body.orientation(), Qt.Orientation.Horizontal)
        self.assertFalse(ui_preferences.get_chat_collapsed())

        # moveSplitter percorre o mesmo caminho de redimensionamento do arraste.
        window._main_body.moveSplitter(window._main_body.width() - 426, 1)
        self.settle()
        resized = window._right_panel.width()
        self.assertGreater(resized, 395)
        self.assertAlmostEqual(ui_preferences.get_chat_width(), resized, delta=2)
        window._chat_toggle_btn.click()
        window._chat_toggle_btn.click()
        self.settle()
        self.assertAlmostEqual(window._right_panel.width(), resized, delta=2)

    def test_collapse_preserves_history_draft_and_attachment_reference(self):
        window = self.window()
        window._set_chat_visible(True, persist=False)
        window._log.append_log("You: Mensagem de teste")
        window._log.append_log("CHLOE AI: Resposta de teste")
        window._input.setPlainText("Rascunho ainda não enviado\nsegunda linha")
        window._current_file = str(Path(self.directory) / "anexo-de-teste.txt")
        self.settle()
        history = window._log.toPlainText()
        draft = window._input.toPlainText()
        attachment = window._current_file
        for _ in range(3):
            window._set_chat_visible(False, persist=False)
            window._set_chat_visible(True, persist=False)
        self.settle()
        self.assertEqual(window._log.toPlainText(), history)
        self.assertEqual(window._input.toPlainText(), draft)
        self.assertEqual(window._current_file, attachment)
        self.assertTrue(window._drop_zone.isVisible())
        self.assertTrue(window._input.isVisible())

    def test_collapsed_chat_keeps_audio_controls_and_shortcuts(self):
        window = self.window()
        self.assertTrue(window._mute_btn.isVisible())
        self.assertTrue(window._interrupt_btn.isVisible())
        self.assertGreaterEqual(window._mute_btn.height(), 40)
        self.assertGreaterEqual(window._interrupt_btn.height(), 40)
        keys = {shortcut.key().toString() for shortcut in window.findChildren(QShortcut)}
        self.assertTrue({"F4", "F11", "Esc"}.issubset(keys))
        interrupt = Mock()
        window.on_interrupt = interrupt
        window._interrupt_btn.click()
        interrupt.assert_called_once_with()

    def test_disconnected_and_connecting_guard_against_stale_interaction_state(self):
        window = self.window()
        for connection, caption in (("DISCONNECTED", "Sem conexão"), ("CONNECTING", "Conectando…")):
            with self.subTest(connection=connection):
                window._apply_connection_state(connection)
                for stale_state in ("THINKING", "SPEAKING", "LISTENING"):
                    window._apply_state(stale_state)
                    self.assertEqual(window.hud.state, connection)
                    self.assertFalse(window.hud.speaking)
                self.assertEqual(window._sub_lbl.text(), caption)
                self.assertEqual(window._playback_label.text(), "Voz · aguardando conexão")
        window._apply_connection_state("CONNECTED")
        window._apply_state("THINKING")
        self.assertEqual(window.hud.state, "THINKING")
        window._apply_connection_state("DISCONNECTED")
        self.assertEqual(window.hud.state, "DISCONNECTED")

    def test_gpu_unavailable_is_explicit_and_measurements_use_snapshot(self):
        window = self.window()
        self.assertEqual(window._system_labels["gpu"].text(), "GPU · Indisponível")
        self.assertEqual(window._system_labels["cpu"].text(), "CPU · 23%")
        self.assertEqual(window._system_labels["mem"].text(), "RAM · 42%")
        self.metrics.snapshot.return_value["gpu"] = 0.0
        window._update_metrics()
        self.assertEqual(window._system_labels["gpu"].text(), "GPU · 0%")
        self.assertGreaterEqual(window._metric_tmr.interval(), 2000)

    def test_end_of_playback_clears_speech_envelope_and_visemes(self):
        window = self.window()
        window._apply_connection_state("CONNECTED")
        window._set_playback_status(True, "Saída de teste")
        window._apply_state("SPEAKING")
        self.assertTrue(window.hud.speaking)
        window.hud._live_amp = 0.7
        window.hud._amp_disp = 0.5
        window.hud._visemes = ([], 0.0, 0.02)
        window._apply_state("LISTENING")
        self.assertEqual(window.hud.state, "LISTENING")
        self.assertFalse(window.hud.speaking)
        self.assertEqual(window.hud._live_amp, 0.0)
        self.assertEqual(window.hud._amp_disp, 0.0)
        self.assertIsNone(window.hud._visemes)
        self.assertIn("Voz pronta", window._playback_label.text())

    def test_microphone_unavailable_updates_idle_state_and_recovers(self):
        window = self.window()
        window._apply_connection_state("CONNECTED")
        self.assertEqual(window.hud.state, "LISTENING")
        window._set_microphone_available(False)
        self.assertEqual(window.hud.state, "MIC_UNAVAILABLE")
        self.assertIn("Microfone indisponível", window._mute_btn.text())
        window._set_microphone_available(True)
        self.assertEqual(window.hud.state, "LISTENING")
        self.assertIn("Microfone ativado", window._mute_btn.text())

    def test_muting_during_thinking_preserves_interaction_state(self):
        window = self.window()
        window._apply_connection_state("CONNECTED")
        window._apply_state("THINKING")
        window._set_muted(True)
        self.assertTrue(window.hud.muted)
        self.assertEqual(window.hud.state, "THINKING")
        window._set_muted(False)
        self.assertFalse(window.hud.muted)
        self.assertEqual(window.hud.state, "THINKING")

    def test_chat_messages_keep_role_labels_on_separate_lines(self):
        window = self.window()
        window._log.append_log("You: Primeiro texto")
        window._log.append_log("SYS: Segundo texto")
        window._log.append_log("CHLOE AI: Terceiro texto")
        self.settle()
        text = window._log.toPlainText()
        self.assertIn("Primeiro texto\nSISTEMA", text)
        self.assertIn("Segundo texto\nCHLOE AI", text)
        self.assertNotIn("textoSISTEMA", text)
        self.assertNotIn("textoCHLOE AI", text)

    def test_controls_fit_logical_window_sizes_with_chat_open_and_closed(self):
        window = self.window()
        for width, height in ((1180, 780), (820, 600)):
            for visible in (False, True):
                with self.subTest(width=width, height=height, chat=visible):
                    window.resize(width, height)
                    window._set_chat_visible(visible, persist=False)
                    self.settle()
                    self.assertEqual(window.width(), width)
                    self.assertEqual(window.height(), height)
                    for widget in (window._chat_toggle_btn, window._mute_btn, window._interrupt_btn,
                                   *window._system_labels.values()):
                        point = widget.mapTo(window.centralWidget(), QPoint(0, 0))
                        self.assertGreaterEqual(point.x(), 0)
                        self.assertGreaterEqual(point.y(), 0)
                        self.assertLessEqual(point.x() + widget.width(), window.centralWidget().width())
                        self.assertLessEqual(point.y() + widget.height(), window.centralWidget().height())
                    if visible:
                        self.assertGreaterEqual(window._right_panel.width(), 300)
                        self.assertGreaterEqual(window.hud.width(), 260)


if __name__ == "__main__":
    unittest.main()
