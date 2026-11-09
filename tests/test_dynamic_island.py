"""Ilha Dinâmica: widget isolado e integração com a janela principal (Qt offscreen).

Executar com: python -B -m unittest discover -s tests -p test_dynamic_island.py -v
Sem rede, sem WinRT e sem credenciais: os serviços de mídia/clima são substituídos.
"""

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.media_service import friendly_source
from core.weather_service import Location, WeatherData
from frontend.dynamic_island import DynamicIsland, T
from memory import config_manager, ui_preferences
import ui


def _media(playing=True, title="Faixa de teste"):
    return SimpleNamespace(title=title, artist="Artista", source_name="Spotify", is_playing=playing,
                           position=30.0, duration=200.0, thumbnail=None)


class IslandWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.island = DynamicIsland()
        self.addCleanup(self.island.deleteLater)
        self.addCleanup(self.island.hide)

    def test_starts_compact_and_expands_to_design_size(self):
        self.island.show()
        self.assertEqual(self.island.size(), T.COMPACT)
        self.island.expand()
        QTest.qWait(T.EXPAND_MS + 120)
        self.assertEqual(self.island.size(), T.EXPANDED)
        self.island.collapse()
        QTest.qWait(T.COLLAPSE_MS + 120)
        self.assertEqual(self.island.size(), T.COMPACT)

    def test_playing_media_becomes_the_focus_on_expand(self):
        self.island.update_media(_media(playing=True))
        self.island.show()
        self.island.expand()
        self.assertEqual(self.island.current_floor, DynamicIsland.FLOOR_MEDIA)

    def test_paused_media_does_not_steal_focus(self):
        self.island.update_media(_media(playing=False))
        self.island.show()
        self.island.expand()
        self.assertEqual(self.island.current_floor, DynamicIsland.FLOOR_ASSISTANT)

    def test_floors_are_clamped(self):
        self.island.show()
        self.island.expand()
        self.island.go_to_floor(99)
        self.assertEqual(self.island.current_floor, 2)
        self.island.go_to_floor(-5)
        self.assertEqual(self.island.current_floor, 0)

    def test_floor_changes_after_a_finished_slide_do_not_touch_deleted_animations(self):
        """Regressão: o slide usava DeleteWhenStopped e a troca seguinte chamava
        .state() no grupo já apagado pelo Qt (RuntimeError → uncaught_error)."""
        errors = []
        with patch("sys.excepthook", lambda exc_type, *_: errors.append(exc_type)):
            self.island.show()
            self.island.expand()
            for floor in (1, 2, 1, 0, 2, 0):
                self.island.go_to_floor(floor)
                QTest.qWait(T.SLIDE_MS + 120)   # slide termina e o deleteLater é processado
                self.assertEqual(self.island.stack.currentIndex(), floor)
                self.assertEqual(self.island.current_floor, floor)
            for floor in (1, 2, 1, 0):          # trocas interrompendo o slide em andamento
                self.island.go_to_floor(floor)
                QTest.qWait(30)
            QTest.qWait(T.SLIDE_MS + 120)
            self.assertEqual(self.island.stack.currentIndex(), 0)
            # Expandir com mídia tocando usa a troca imediata (set_index → _stop).
            self.island.collapse()
            QTest.qWait(T.COLLAPSE_MS + 120)
            self.island.update_media(_media(playing=True))
            self.island.expand()
            self.assertEqual(self.island.stack.currentIndex(), DynamicIsland.FLOOR_MEDIA)
        self.assertEqual(errors, [])

    def test_hover_cycles_do_not_accumulate_animation_objects(self):
        from PyQt6.QtCore import QAbstractAnimation
        self.island.show()
        for _ in range(15):
            self.island.expand()
            QTest.qWait(10)
            self.island.collapse()
            QTest.qWait(10)
        QTest.qWait(T.COLLAPSE_MS + 120)
        live = self.island.findChildren(QAbstractAnimation)
        self.assertLess(len(live), 12)   # antes: ~4 objetos novos a cada hover, nunca liberados

    def test_mic_button_only_requests_the_change(self):
        requested = []
        self.island.mic_toggled.connect(requested.append)
        self.island.assistant.mic.click()
        self.assertEqual(requested, [True])
        self.assertFalse(self.island.is_muted)       # o app ainda não confirmou
        self.island.set_assistant_state("muted")
        self.island.assistant.mic.click()
        self.assertEqual(requested, [True, False])

    def test_unknown_state_falls_back_to_listening(self):
        self.island.set_assistant_state("algo-novo")
        self.assertEqual(self.island.assistant.status.text(), "OUVINDO")

    def test_weather_floor_renders_data(self):
        loc = Location("São Paulo", "São Paulo", "BR", -23.5, -46.6)
        data = WeatherData(loc, 24.4, 26.2, 68, 14.3, 2, True, 5.0, None, datetime.now())
        self.island.update_weather(data)
        self.assertEqual(self.island.weather.temp.text(), "24°")
        self.assertEqual(self.island.weather.condition.text(), "Parcialmente nublado")
        self.assertEqual(self.island.weather.city.full_text(), "São Paulo, SP")
        self.assertIn("UV 5", self.island.weather.chip_extra.text())

    def test_live_stream_without_duration_hides_remaining_time(self):
        info = _media()
        info.duration = 0
        self.island.update_media(info)
        self.assertTrue(self.island.media.rem.isHidden())


class ServiceHelpersTests(unittest.TestCase):
    def test_friendly_source_names(self):
        self.assertEqual(friendly_source("Spotify.exe"), "Spotify")
        self.assertEqual(friendly_source("Chrome"), "Chrome")
        self.assertEqual(friendly_source("MSEdge"), "Edge")
        self.assertEqual(friendly_source("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "Mídia do Windows")
        self.assertEqual(friendly_source("foobar2000.exe"), "foobar2000")

    def test_weather_labels(self):
        loc = Location("Lisboa", "Lisboa", "PT", 38.7, -9.1)
        data = WeatherData(loc, 18, 18, 70, 10, 95, False, 9.0, 55, datetime.now())
        self.assertEqual(data.kind, "storm")
        self.assertEqual(data.uv_label, "Muito alto")
        self.assertEqual(data.aqi_label, "Moderada")
        self.assertEqual(loc.label, "Lisboa, Lisboa")


class IslandIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setFont(QFont("Segoe UI", 10))

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        config_dir = Path(directory) / "config"
        for owner, field, value in (
            (config_manager, "CONFIG_DIR", config_dir),
            (config_manager, "CONFIG_FILE", config_dir / "api_keys.json"),
            (ui_preferences, "PREFERENCES_FILE", config_dir / "ui_preferences.json"),
            (ui, "CONFIG_DIR", config_dir),
            (ui, "API_FILE", config_dir / "api_keys.json"),
            (ui, "HAVE_VIDEO", False),
        ):
            stack.enter_context(patch.object(owner, field, value))
        stack.enter_context(patch.object(ui, "_read_full_config", return_value={}))
        stack.enter_context(patch.object(config_manager, "load_api_keys", return_value={}))
        stack.enter_context(patch.object(ui.MainWindow, "_check_config", return_value=True))
        stack.enter_context(patch.object(ui.MainWindow, "_check_autostart", return_value=False))
        stack.enter_context(patch.object(ui.MainWindow, "_warm_wake_state"))
        # Nada de rede nem WinRT nos testes.
        self.ensure = stack.enter_context(patch.object(ui.MainWindow, "_ensure_island_services"))
        metrics = Mock()
        metrics.snapshot.return_value = {"cpu": 1.0, "mem": 1.0, "gpu": -1.0, "tmp": -1.0,
                                         "net": 0.0, "ram_used_gb": 1.0, "ram_total_gb": 16.0}
        stack.enter_context(patch.object(ui, "_metrics", metrics))

        self.win = ui.MainWindow("")
        self.win.resize(1180, 780)
        self.win.show()
        self.app.processEvents()
        self.island = self.win._island
        self.addCleanup(self._close)

    def _close(self):
        for owner in (self.win, self.island):
            for timer in owner.findChildren(QTimer):
                timer.stop()
        self.island.hide()
        self.win.close()
        self.island.deleteLater()
        self.win.deleteLater()
        self.app.processEvents()

    def test_header_has_island_button_and_island_exists(self):
        self.assertIsNotNone(self.island)
        self.assertTrue(self.win._island_btn.isEnabled())
        self.assertFalse(self.island.isVisible())

    def test_island_mode_round_trip(self):
        self.win._island_btn.click()
        self.app.processEvents()
        self.ensure.assert_called_once()
        self.assertFalse(self.win.isVisible())
        self.assertTrue(self.island.isVisible())
        self.island.restore_requested.emit()
        QTest.qWait(320)
        self.assertTrue(self.win.isVisible())
        self.assertFalse(self.island.isVisible())

    def test_hud_states_reach_the_island(self):
        self.win._apply_connection_state("CONNECTED")
        self.win._playback_available = True
        for hud, island in (("THINKING", "thinking"), ("SPEAKING", "speaking"),
                            ("SLEEPING", "sleeping"), ("LISTENING", "listening")):
            self.win._apply_state(hud)
            self.assertEqual(self.island._state, island, hud)
        self.win._apply_connection_state("DISCONNECTED")
        self.assertEqual(self.island._state, "offline")

    def test_assistant_answers_show_on_the_island_and_system_lines_do_not(self):
        self.win._log_sig.emit("CHLOE AI: Amanhã vai chover à tarde.")
        self.win._log_sig.emit("SYS: Microphone active.")
        self.app.processEvents()
        self.assertEqual(self.island.assistant._full_response, "Amanhã vai chover à tarde.")

    def test_island_mic_toggles_the_real_mute(self):
        self.win._apply_connection_state("CONNECTED")
        with patch("memory.config_manager._save_flag"):
            self.island.assistant.mic.click()
            self.assertTrue(self.win._muted)
            self.assertTrue(self.island.is_muted)
            self.island.assistant.mic.click()
            self.assertFalse(self.win._muted)
            self.assertFalse(self.island.is_muted)

    def test_text_typed_on_the_island_goes_to_the_assistant(self):
        callback = Mock(return_value=True)
        self.win.on_text_command = callback
        self.island.message_submitted.emit("que horas são?")
        for _ in range(50):
            QTest.qWait(20)
            if not self.win._island_text_pending:
                break
        callback.assert_called_once_with("que horas são?")
        self.assertEqual(self.island.assistant._full_response, "Você: que horas são?")

    def test_text_without_api_key_explains_instead_of_failing(self):
        self.win.on_text_command = None
        self.island.message_submitted.emit("oi")
        self.assertIn("chave de API", self.island.assistant._full_response)


if __name__ == "__main__":
    unittest.main()
