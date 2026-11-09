"""Esfera de partículas: cores por estado, sem saltos, igual na janela e na Ilha.

Executar com: python -B -m unittest tests.test_particle_orb -v
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from frontend.particle_core import (  # noqa: E402
    LISTEN_HOLD, MODE_COLORS, ParticleField, ParticleOrb, VOICE_THRESHOLD,
)


def _render(field, t, mode, amp=0.0, reduced=False):
    img = QImage(200, 200, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    col = field.draw_sphere(p, 100, 100, 70, t, amp, mode, reduced)
    p.end()
    return col


class ModeTests(unittest.TestCase):
    def test_who_is_talking_sets_the_mode(self):
        f = ParticleField(50)
        self.assertEqual(f.resolve_mode(0, "LISTENING", False, False, 0.0), "rest")
        self.assertEqual(f.resolve_mode(0, "LISTENING", False, False, 0.5), "listen")
        self.assertEqual(f.resolve_mode(0, "SPEAKING", True, False, 0.5), "speak")
        self.assertEqual(f.resolve_mode(0, "THINKING", False, False, 0.0), "think")
        self.assertEqual(f.resolve_mode(0, "PROCESSING", False, False, 0.0), "think")

    def test_muted_offline_and_sleeping_stay_grey_even_with_sound(self):
        f = ParticleField(50)
        self.assertEqual(f.resolve_mode(0, "LISTENING", False, True, 0.9), "rest")
        for state in ("DISCONNECTED", "CONNECTING", "SLEEPING", "MIC_UNAVAILABLE"):
            self.assertEqual(f.resolve_mode(0, state, False, False, 0.9), "rest")

    def test_blue_holds_briefly_between_words(self):
        f = ParticleField(50)
        f.resolve_mode(1.0, "LISTENING", False, False, VOICE_THRESHOLD + 0.1)
        self.assertEqual(f.resolve_mode(1.0 + LISTEN_HOLD / 2, "LISTENING", False, False, 0.0), "listen")
        self.assertEqual(f.resolve_mode(1.0 + LISTEN_HOLD + 0.01, "LISTENING", False, False, 0.0), "rest")


class DrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_colour_eases_instead_of_jumping(self):
        f = ParticleField(80)
        _render(f, 0.0, "rest")
        first = _render(f, 0.033, "speak")        # um quadro depois da troca
        self.assertNotEqual(first.name(), QColor(MODE_COLORS["speak"]).name())
        for i in range(2, 60):
            last = _render(f, i * 0.033, "speak")
        self.assertEqual(last.name(), QColor(MODE_COLORS["speak"]).name())

    def test_each_mode_reaches_its_design_colour(self):
        for mode, hex_colour in MODE_COLORS.items():
            f = ParticleField(40)
            col = _render(f, 0.0, mode, reduced=True)   # sem transição
            self.assertEqual(col.name(), QColor(hex_colour).name(), mode)

    def test_rotation_is_integrated_so_changing_speed_never_jumps(self):
        f = ParticleField(40)
        _render(f, 10.0, "rest")
        _render(f, 10.033, "rest")
        before = f._angle
        _render(f, 10.066, "think")                # velocidade maior a partir daqui
        self.assertLess(f._angle - before, 0.02)

    def test_reduced_motion_freezes_rotation(self):
        f = ParticleField(40)
        for i in range(10):
            _render(f, i * 0.1, "speak", amp=1.0, reduced=True)
        self.assertEqual(f._angle, 0.0)


class IslandOrbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_island_orb_follows_assistant_state_and_voice(self):
        orb = ParticleOrb(48)
        self.addCleanup(orb.deleteLater)
        orb.show()
        orb.set_state("speaking")
        QTest.qWait(80)
        self.assertEqual(orb.mode, "speak")
        orb.set_state("thinking")
        QTest.qWait(80)
        self.assertEqual(orb.mode, "think")
        orb.set_state("listening")
        QTest.qWait(80)
        self.assertEqual(orb.mode, "rest")
        orb.set_level(0.8)                         # você começou a falar
        QTest.qWait(120)
        self.assertEqual(orb.mode, "listen")
        orb.set_state("muted")
        QTest.qWait(80)
        self.assertEqual(orb.mode, "rest")

    def test_old_voice_received_while_hidden_does_not_turn_it_blue_on_show(self):
        orb = ParticleOrb(48)
        self.addCleanup(orb.deleteLater)
        orb.set_state("listening")
        orb.set_level(0.9)                         # chegou com a Ilha recolhida
        QTest.qWait(1200)                          # ... e já passou
        orb.show()
        QTest.qWait(40)
        self.assertEqual(orb.mode, "rest")

    def test_mode_is_known_and_announced_even_while_hidden(self):
        orb = ParticleOrb(24)                      # nunca mostrada (capa da música no lugar)
        self.addCleanup(orb.deleteLater)
        seen = []
        orb.mode_changed.connect(seen.append)
        orb.set_state("speaking")
        orb.set_state("thinking")
        self.assertEqual(seen, ["speak", "think"])

    def test_set_level_ignores_garbage(self):
        orb = ParticleOrb(24)
        self.addCleanup(orb.deleteLater)
        orb.set_level("nada")
        orb.set_level(None)
        orb.set_level(7)
        self.assertEqual(orb._target, 1.0)


if __name__ == "__main__":
    unittest.main()
