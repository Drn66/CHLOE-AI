"""Voz e respostas: transcrição sem palavras perdidas, clima sem navegador, diagnóstico.

Executar com: python -B -m unittest tests.test_voice_fixes -v
Nenhum teste usa rede, microfone ou credenciais.
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import main
from actions import weather_report
from core.voice_diag import VoiceDiagnostics
from core.weather_service import DailyForecast, Location, WeatherData


def _feed(chunks):
    """Mesmo caminho do laço de recepção: limpa, filtra repetição, junta."""
    buf = []
    for raw in chunks:
        txt = main._clean_transcript(raw)
        if txt.strip() and not main._is_repeat_chunk(txt, buf):
            buf.append(txt)
    return main._join_transcript(buf)


class TranscriptTests(unittest.TestCase):
    def test_word_split_across_chunks_is_not_broken_apart(self):
        # Antes: strip() + " ".join → "Pronto, está tocan do."
        self.assertEqual(_feed(["Pronto", ", está", " tocan", "do", "."]), "Pronto, está tocando.")

    def test_repeated_words_and_phrases_are_kept(self):
        # Antes: o 2º "muito" (igual ao pedaço anterior) e a frase repetida sumiam.
        self.assertEqual(_feed(["Está", " muito", " muito", " quente."]), "Está muito muito quente.")
        text = _feed(["Em São Paulo", " faz 24 graus.", " Em São Paulo", " amanhã chove."])
        self.assertEqual(text, "Em São Paulo faz 24 graus. Em São Paulo amanhã chove.")

    def test_resent_tail_is_still_dropped(self):
        self.assertEqual(_feed(["Volume em quarenta", " por cento agora.", " por cento agora."]),
                         "Volume em quarenta por cento agora.")

    def test_whole_word_chunks_without_spaces_still_get_spaces(self):
        self.assertEqual(_feed(["Olá", "tudo", "bem?"]), "Olá tudo bem?")

    def test_control_characters_removed_but_spaces_kept(self):
        self.assertEqual(main._clean_transcript(" vinte\x07"), " vinte")


def _weather():
    loc = Location("São Paulo", "São Paulo", "BR", -23.5, -46.6)
    now = WeatherData(loc, 24.4, 26.2, 62, 11.0, 2, True, 5.0, None, datetime.now())
    daily = [DailyForecast("2026-11-08", 2, 27.0, 18.0, 30), DailyForecast("2026-11-09", 61, 22.0, 17.0, 80)]
    return loc, now, daily


class WeatherToolTests(unittest.TestCase):
    def test_answers_with_real_numbers_and_never_opens_a_browser(self):
        loc, now, daily = _weather()
        player = Mock()
        with patch.object(weather_report, "detect_location", return_value=loc), \
                patch.object(weather_report, "fetch_weather", return_value=now), \
                patch.object(weather_report, "fetch_daily", return_value=daily), \
                patch("webbrowser.open") as browser:
            result = weather_report.weather_action({}, player=player)
        browser.assert_not_called()
        self.assertIn("24°C", result)
        self.assertIn("Parcialmente nublado", result)
        self.assertIn("high 27°C", result)
        title, card = player.show_content.call_args.args
        self.assertIn("São Paulo", title)
        self.assertIn("Fonte: Open-Meteo", card)

    def test_named_city_and_tomorrow(self):
        loc, _, daily = _weather()
        with patch.object(weather_report, "geocode", return_value=loc) as geo, \
                patch.object(weather_report, "fetch_weather") as now, \
                patch.object(weather_report, "fetch_daily", return_value=daily):
            result = weather_report.weather_action({"city": "São Paulo", "time": "amanhã"})
        geo.assert_called_once_with("São Paulo")
        now.assert_not_called()
        self.assertIn("Tomorrow", result)
        self.assertIn("80% chance of rain", result)

    def test_offline_or_unknown_city_explains_instead_of_browsing(self):
        with patch.object(weather_report, "geocode", side_effect=LookupError("x")):
            self.assertIn("could not find", weather_report.weather_action({"city": "Xyz"}))
        with patch.object(weather_report, "detect_location", side_effect=OSError("offline")):
            self.assertIn("could not get the weather", weather_report.weather_action({}))

    def test_declaration_routes_any_weather_question_here(self):
        decl = weather_report.TOOL
        self.assertNotIn("required", decl["parameters"])           # cidade opcional
        self.assertIn("never opens a browser", decl["description"])
        status = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "system_status")
        self.assertIn("weather_report", status["description"])


class VoiceDiagnosticsTests(unittest.TestCase):
    def test_turn_line_has_numbers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            diag = VoiceDiagnostics(Path(tmp) / "voice.log")
            diag.note_audio(24000 * 2 * 3)                 # 3 s
            diag.note_chunk(" segredo do usuário")
            diag.gap_started(10.0)
            diag.gap_ended(10.31)
            diag.note_mic_held()
            diag.note_server_interrupted()
            line = diag.turn_complete()
            for h in list(diag._log.handlers):
                h.flush()
            text = (Path(tmp) / "voice.log").read_text(encoding="utf-8")
            for h in list(diag._log.handlers):
                diag._log.removeHandler(h)
                h.close()
        self.assertIn("audio=3.0s gaps=1 max_gap=310ms server_int=sim", line)
        self.assertIn("mic_held=1", line)
        self.assertIn("lead_space=1", line)
        self.assertNotIn("segredo", text)

    def test_empty_turns_are_not_logged_and_state_resets(self):
        diag = VoiceDiagnostics(None)
        self.assertIsNone(diag.turn_complete())
        diag.note_audio(100)
        diag.turn_complete()
        self.assertEqual(diag.audio_bytes, 0)


if __name__ == "__main__":
    unittest.main()
