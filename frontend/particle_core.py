"""Software-rendered particle sphere driven by the existing audio envelope."""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPen, QRadialGradient, QBrush, QPixmap, QIcon, QPainter

from frontend.theme import C, LISTEN_COLOR, SPEAK_COLOR


def make_icon():
    """Original dotted-sphere icon, generated in memory."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(C.BG))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(190):
        y = 1 - 2 * (i + 0.5) / 190
        a = i * math.pi * (3 - math.sqrt(5))
        ring = math.sqrt(1 - y*y)
        z = ring * math.sin(a)
        p.setBrush(QColor(LISTEN_COLOR if z < 0.5 else SPEAK_COLOR))
        p.drawEllipse(QPointF(32 + ring * math.cos(a) * 25, 32 + y * 25), 0.8, 0.8)
    p.end()
    return QIcon(pm)


class ParticleField:
    def __init__(self, count=1100):
        golden = math.pi * (3 - math.sqrt(5))
        self.points = []
        for i in range(count):
            y = 1 - 2 * (i + 0.5) / count
            ring = math.sqrt(1 - y * y)
            a = i * golden
            self.points.append((ring * math.cos(a), y, ring * math.sin(a), i))
        self.pink = 0.0

    def paint(self, p, width, height, phase, amp, state, speaking, muted,
              reduced_motion=False):
        active = speaking or (not muted and state not in ("SLEEPING", "INITIALISING"))
        amp = max(0.0, min(1.0, amp)) if active else 0.0
        target = 1.0 if speaking else 0.0
        self.pink += (target - self.pink) * 0.13
        blue, pink = QColor(LISTEN_COLOR), QColor(SPEAK_COLOR)
        col = QColor(*[round(a + (b - a) * self.pink) for a, b in
                       zip(blue.getRgb()[:3], pink.getRgb()[:3])])
        t = 0.0 if reduced_motion else phase
        cx, cy = width / 2, height * 0.46
        base_radius = max(35, min(width * 0.34, height * 0.29))
        pulse = 1 if reduced_motion else 1 + 0.009 * math.sin(t * 1.2) + amp * 0.055
        radius = base_radius * pulse
        glow = QRadialGradient(cx, cy, radius * 1.6)
        glow.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 22 + int(amp * 15)))
        glow.setColorAt(0.65, QColor(col.red(), col.green(), col.blue(), 8))
        glow.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), radius * 1.6, radius * 1.6)
        angle = t * 0.075
        ca, sa = math.cos(angle), math.sin(angle)
        projected = []
        for x, y, z, i in self.points:
            xx, zz = x * ca + z * sa, z * ca - x * sa
            yy, zz = y * 0.94 - zz * 0.342, y * 0.342 + zz * 0.94
            wave = 1 + (0 if reduced_motion else amp * 0.018 * math.sin(i * 0.12 + t * 2))
            perspective = 3.8 / (3.8 - zz)
            projected.append((zz, cx + xx * radius * perspective * wave,
                              cy + yy * radius * perspective * wave, i))
        for z, x, y, i in sorted(projected):
            depth = (z + 1) / 2
            alpha = int((38 + depth * 185) * (0.48 if not speaking and (muted or state == "SLEEPING") else 1))
            size = 0.7 + depth * 0.8 + (i % 7 == 0) * 0.45
            p.setBrush(QColor(col.red(), col.green(), col.blue(), alpha))
            p.drawEllipse(QPointF(x, y), size, size)
            if depth > 0.8 and i % 13 == 0:
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 18))
                p.drawEllipse(QPointF(x, y), size * 3, size * 3)
        if speaking:
            label, detail = "CHLOE falando", "Uma ideia de cada vez"
        elif muted:
            label, detail = "Microfone desativado", "Você ainda pode escrever uma mensagem"
        elif state in ("THINKING", "PROCESSING"):
            label, detail = "Pensando", "Preparando uma resposta para você"
        elif state == "LISTENING":
            label = "Ouvindo você" if amp > 0.04 else "Pronta para ouvir"
            detail = "Fale naturalmente ou escreva ao lado"
        elif state == "SLEEPING":
            label, detail = "Em repouso", "Use os controles para despertar"
        else:
            label, detail = "Seu espaço de inteligência", "Configure sua chave para começar"
        # The caption is anchored to the layout, never to the audio envelope.
        sy = min(cy + base_radius * 1.35, height - 80)
        compact = height < 300
        if compact:
            sy = height - 30
        p.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        p.setPen(QPen(QColor(SPEAK_COLOR if speaking else LISTEN_COLOR)))
        p.drawText(QRectF(8, sy, width - 16, 30), Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QColor(C.TEXT_DIM))
        if not compact:
            p.drawText(QRectF(8, sy + 36, width - 16, 28), Qt.AlignmentFlag.AlignCenter, detail)
