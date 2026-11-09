"""Esfera de partículas da CHLOE: a mesma na janela completa e na Ilha Dinâmica.

Pontos numa esfera de Fibonacci, girando devagar com leve inclinação e
perspectiva; os de trás ficam mais apagados. A cor diz quem está falando:

    rest    cinza  — ninguém falando (também mudo, offline, conectando)
    listen  azul   — você está falando (nível do microfone acima do ruído)
    speak   rosa   — a CHLOE está falando; pulsa até 6,5% com a voz
    think   roxo   — pensando; gira um pouco mais rápido e respira

A troca de cor é suave (≈ 150 ms) e o giro é integrado no tempo, então mudar
de estado nunca dá saltos.
"""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QWidget

from frontend.theme import C, FONT_FAMILIES, LISTEN_COLOR, REST_COLOR, SPEAK_COLOR, THINK_COLOR

MODES = ("rest", "listen", "speak", "think")
MODE_COLORS = {"rest": REST_COLOR, "listen": LISTEN_COLOR, "speak": SPEAK_COLOR, "think": THINK_COLOR}
_SPEED = {"rest": 0.12, "listen": 0.12, "speak": 0.12, "think": 0.32}   # rad/s
VOICE_THRESHOLD = 0.04      # nível do microfone que conta como "você está falando"
LISTEN_HOLD = 0.35          # s em azul depois que a voz some (evita piscar entre palavras)


def ui_font(point_size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def make_icon():
    """Ícone da esfera pontilhada, gerado em memória."""
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
        self._rgb = [float(v) for v in QColor(REST_COLOR).getRgb()[:3]]
        self._angle = 0.0
        self._last_t = None
        self._listen_until = -1.0
        self.mode = "rest"

    # ── estado → modo ───────────────────────────────────────────────────────
    def resolve_mode(self, t: float, state: str, speaking: bool, muted: bool, amp: float) -> str:
        """Modo da esfera a partir do estado da HUD; `t` é o tempo integrado."""
        if speaking:
            mode = "speak"
        elif state in ("THINKING", "PROCESSING"):
            mode = "think"
        elif not muted and state == "LISTENING" and (amp > VOICE_THRESHOLD or t < self._listen_until):
            if amp > VOICE_THRESHOLD:
                self._listen_until = t + LISTEN_HOLD
            mode = "listen"
        else:
            mode = "rest"
        if mode != "listen":
            self._listen_until = -1.0
        return mode

    # ── desenho ─────────────────────────────────────────────────────────────
    def draw_sphere(self, p: QPainter, cx: float, cy: float, base_radius: float, t: float,
                    amp: float, mode: str, reduced_motion: bool = False, dot_scale: float = 1.0,
                    glow_scale: float = 1.6) -> QColor:
        """Desenha a esfera e devolve a cor atual (já em transição)."""
        mode = mode if mode in MODES else "rest"
        self.mode = mode
        dt = 0.0 if self._last_t is None else max(0.0, min(0.2, t - self._last_t))
        self._last_t = t

        target = QColor(MODE_COLORS[mode]).getRgb()[:3]
        k = 1.0 if reduced_motion else 1.0 - math.exp(-dt / 0.15)
        self._rgb = [a + (b - a) * k for a, b in zip(self._rgb, target)]
        r, g, b = (round(v) for v in self._rgb)

        amp = max(0.0, min(1.0, amp)) if mode in ("listen", "speak") else 0.0
        breathe = 1.0 + (0.025 * math.sin(t * 2.2) if mode == "think" and not reduced_motion else 0.0)
        if not reduced_motion:
            self._angle += dt * _SPEED[mode]
        pulse = 1.0 if reduced_motion else (1 + amp * 0.065) * breathe
        radius = base_radius * pulse
        dim = 0.48 if mode == "rest" else 1.0

        glow = QRadialGradient(cx, cy, radius * glow_scale)
        ga = (22 + amp * 15) * (0.6 if mode == "rest" else 1.0)
        glow.setColorAt(0, QColor(r, g, b, int(ga)))
        glow.setColorAt(0.65, QColor(r, g, b, int(ga * 0.36)))
        glow.setColorAt(1, QColor(r, g, b, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), radius * glow_scale, radius * glow_scale)

        ca, sa = math.cos(self._angle), math.sin(self._angle)
        wave_t = 0.0 if reduced_motion else t
        projected = []
        for x, y, z, i in self.points:
            xx, zz = x * ca + z * sa, z * ca - x * sa
            yy, zz = y * 0.94 - zz * 0.342, y * 0.342 + zz * 0.94
            wave = 1 + amp * 0.018 * math.sin(i * 0.12 + wave_t * 2)
            perspective = 3.8 / (3.8 - zz)
            projected.append((zz, cx + xx * radius * perspective * wave,
                              cy + yy * radius * perspective * wave, i))
        for z, x, y, i in sorted(projected):
            depth = (z + 1) / 2
            alpha = int((38 + depth * 185) * dim)
            size = (0.7 + depth * 0.8 + (i % 7 == 0) * 0.45) * dot_scale
            p.setBrush(QColor(r, g, b, alpha))
            p.drawEllipse(QPointF(x, y), size, size)
            if depth > 0.8 and i % 13 == 0:
                p.setBrush(QColor(r, g, b, int(18 * dim)))
                p.drawEllipse(QPointF(x, y), size * 3, size * 3)
        return QColor(r, g, b)

    def paint(self, p, width, height, phase, amp, state, speaking, muted,
              reduced_motion=False):
        """Esfera grande da janela completa, com a legenda do estado."""
        mode = self.resolve_mode(phase, state, speaking, muted, amp)
        cx, cy = width / 2, height * 0.44
        base_radius = max(35, min(width * 0.30, height * 0.27))
        col = self.draw_sphere(p, cx, cy, base_radius, phase, amp, mode, reduced_motion)

        if state in ("DISCONNECTED", "INITIALISING"):
            eyebrow, label, detail = "Offline", "Sem conexão", "Conecte a API para conversar"
        elif state == "CONNECTING":
            eyebrow, label, detail = "Conectando", "Conectando…", "Aguardando o Gemini"
        elif speaking:
            eyebrow, label, detail = "Falando", "CHLOE falando", "Esc interrompe a resposta"
        elif state == "MIC_UNAVAILABLE":
            eyebrow, label, detail = "Microfone", "Microfone indisponível", "Você pode escrever uma mensagem"
        elif state in ("THINKING", "PROCESSING"):
            eyebrow, label, detail = "Pensando", "Pensando…", "Preparando uma resposta para você"
        elif state == "SLEEPING":
            eyebrow, label, detail = "Repouso", "Em repouso", "Use os controles para despertar"
        elif muted:
            eyebrow, label, detail = "Mudo", "Microfone desativado", "F4 liga o microfone · você ainda pode escrever"
        elif state == "LISTENING":
            if mode == "listen":
                eyebrow, label = "Ouvindo", "Ouvindo você"
            else:
                eyebrow, label = "Pronta", "Pode falar — estou ouvindo."
            detail = "F4 silencia o microfone · Esc interrompe a resposta"
        else:
            eyebrow, label, detail = "Pronta", "Pronta", "Abra a conversa para escrever"

        # A legenda fica presa ao layout, nunca ao pulso da voz.
        compact = height < 300
        sy = height - 34 if compact else min(cy + base_radius * 1.45, height - 96)
        eyebrow_font = ui_font(8.5, QFont.Weight.Bold)
        eyebrow_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
        eyebrow_font.setCapitalization(QFont.Capitalization.AllUppercase)
        if not compact:
            p.setFont(eyebrow_font)
            p.setPen(QPen(col if mode != "rest" else QColor(C.TEXT_DIM)))
            p.drawText(QRectF(8, sy, width - 16, 18), Qt.AlignmentFlag.AlignCenter, eyebrow)
            sy += 22
        p.setFont(ui_font(13 if compact else 17, QFont.Weight.Light))
        p.setPen(QColor("#f5f5f7"))
        p.drawText(QRectF(8, sy, width - 16, 30), Qt.AlignmentFlag.AlignCenter, label)
        if not compact:
            p.setFont(ui_font(9.5))
            p.setPen(QColor("#a1a1aa"))
            p.drawText(QRectF(8, sy + 34, width - 16, 22), Qt.AlignmentFlag.AlignCenter, detail)


# Estados da Ilha (ver DynamicIsland.set_assistant_state) → estado da HUD.
_ISLAND_TO_HUD = {
    "speaking": ("SPEAKING", True, False), "thinking": ("THINKING", False, False),
    "listening": ("LISTENING", False, False), "muted": ("LISTENING", False, True),
}


class ParticleOrb(QWidget):
    """Esfera de partículas compacta (Ilha). `set_level()` só grava um float:
    pode ser chamado das threads de áudio, como a HUD."""

    def __init__(self, diameter: int, parent=None) -> None:
        super().__init__(parent)
        self._d = int(diameter)
        self.setFixedSize(self._d, self._d)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        count = 1100 if self._d >= 200 else 420 if self._d >= 80 else 220 if self._d >= 40 else 110
        self._field = ParticleField(count)
        self._dot_scale = max(0.45, self._d / 320)
        self._state = "listening"
        self._t = 0.0
        self._level = 0.0
        self._target = 0.0
        self.reduced_motion = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    @property
    def mode(self) -> str:
        return self._field.mode

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def set_level(self, level: float) -> None:
        try:
            lv = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        if lv > self._target:
            self._target = lv

    def showEvent(self, e):  # noqa: N802
        # Escondida, a esfera não decai o nível: descarta voz "congelada" de antes.
        self._level = self._target = 0.0
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        self._t += 0.033
        self._level += (self._target - self._level) * 0.35
        self._target *= 0.86
        self.update()

    def paintEvent(self, _):  # noqa: N802
        hud_state, speaking, muted = _ISLAND_TO_HUD.get(self._state, ("SLEEPING", False, False))
        mode = self._field.resolve_mode(self._t, hud_state, speaking, muted, self._level)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self._d / 2
        self._field.draw_sphere(p, c, c, self._d * 0.40, self._t, self._level, mode,
                                self.reduced_motion, self._dot_scale, glow_scale=1.2)
        p.end()
