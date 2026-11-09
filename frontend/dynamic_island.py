"""
dynamic_island.py — Ilha Dinâmica da CHLOE AI (PyQt6 · Windows 10/11)

Janela flutuante em formato de pílula no topo central da tela:

* Estado compacto (210×36): orbe + relógio, ou capa + equalizador se houver mídia tocando.
* Estado expandido (440×130) ao passar o mouse; contrai 400 ms depois que o cursor sai.
* Três "andares" trocados pela rodinha do mouse, com slide vertical:
    0 — Assistente (orbe, última resposta, campo de texto, microfone)
    1 — Clima (Open-Meteo)
    2 — Mídia do sistema (GSMTC). Vira o foco automático ao expandir quando algo toca.
* Não rouba o foco (WS_EX_NOACTIVATE); só aceita teclado quando o usuário clica no campo.

A ilha é só a camada de visual. Os dados chegam pelos slots públicos:
    set_response(), set_assistant_state(), set_voice_level(), update_weather(), update_media()
e as ações do usuário saem pelos sinais:
    message_submitted, mic_toggled, media_command, restore_requested, quit_requested, floor_changed

Na CHLOE ela é criada e ligada por `ui.MainWindow` (botão "Ilha" no topo).
Rode `python -m frontend.dynamic_island` para uma demonstração com dados de exemplo.
"""
from __future__ import annotations

import math
import sys
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTime,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
    QRadialGradient,
    QScreen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QMenu,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.particle_core import ParticleOrb

if TYPE_CHECKING:  # apenas para dicas de tipo; a ilha não depende dos serviços
    from core.media_service import MediaInfo
    from core.weather_service import WeatherData


# ════════════════════════════════════════════════════════════════════════════
#  Tokens de design (os mesmos do canvas de especificação)
# ════════════════════════════════════════════════════════════════════════════
class T:
    BG = QColor(10, 10, 12, 235)          # #0A0A0C @ 92%
    BORDER = QColor(255, 255, 255, 20)    # branco @ 8%
    TEXT = "#F4F4F6"
    TEXT_2 = "#A1A1AA"
    TEXT_3 = "#8B8B95"
    SOFT = "#E4E4E7"
    CYAN = "#22D3EE"
    BLUE = "#2563EB"
    RED = "#FB7185"
    GREEN = "#34D399"
    SKY = "#38BDF8"
    AMBER = "#FBBF24"

    COMPACT = QSize(210, 36)
    EXPANDED = QSize(440, 130)
    TOP_MARGIN = 10
    MAX_RADIUS = 28
    RAIL_W = 40                           # trilho direito (⤢ + pontos)

    EXPAND_MS = 240
    COLLAPSE_MS = 220
    LEAVE_GRACE_MS = 400
    SLIDE_MS = 280
    WHEEL_LOCK_MS = 320


UI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"]
ICON_FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
EMOJI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Segoe UI Emoji"]


class Glyph:
    """Pontos de código comuns ao Segoe Fluent Icons (Win11) e Segoe MDL2 Assets (Win10)."""
    PREV = ""
    NEXT = ""
    PLAY = ""
    PAUSE = ""
    MIC = ""
    MIC_OFF = ""
    EXPAND = ""
    PIN = ""
    MUSIC = ""


def ui_font(px: int, weight: QFont.Weight = QFont.Weight.Normal, families=None) -> QFont:
    f = QFont()
    f.setFamilies(families or UI_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def icon_font(px: int) -> QFont:
    f = QFont()
    f.setFamilies(ICON_FAMILIES)
    f.setPixelSize(px)
    return f


def make_label(text: str = "", px: int = 13, color: str = T.TEXT,
               weight: QFont.Weight = QFont.Weight.Normal, families=None) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(ui_font(px, weight, families))
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    return lbl


def icon_button(glyph: str, size: int, kind: str = "ghost", tooltip: str = "") -> QPushButton:
    """Botão tátil minimalista. NoFocus: clicar não ativa a janela nem rouba o foco."""
    b = QPushButton(glyph)
    b.setFont(icon_font(max(10, int(size * 0.40))))
    b.setFixedSize(size, size)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if tooltip:
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip)
    r = size // 2
    if kind == "solid":
        b.setStyleSheet(
            f"QPushButton {{ background: #F4F4F6; color: #0A0A0C; border: none; border-radius: {r}px; }}"
            f"QPushButton:hover {{ background: #FFFFFF; }}"
            f"QPushButton:pressed {{ background: #D4D4D8; }}"
        )
    else:
        radius = r if kind == "ghost" else 8
        b.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {T.SOFT}; border: none; border-radius: {radius}px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,26); }}"
            f"QPushButton:pressed {{ background: rgba(255,255,255,46); }}"
        )
    return b


def round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ════════════════════════════════════════════════════════════════════════════
#  Win32: não roubar foco
# ════════════════════════════════════════════════════════════════════════════
class _Win32:
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080

    def __init__(self) -> None:
        self.ok = sys.platform == "win32"
        if not self.ok:
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        get = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_ = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get.restype = ctypes.c_ssize_t
        get.argtypes = [wintypes.HWND, ctypes.c_int]
        set_.restype = ctypes.c_ssize_t
        set_.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._get, self._set = get, set_

    def set_no_activate(self, widget: QWidget, enabled: bool) -> None:
        if not self.ok:
            return
        hwnd = int(widget.winId())
        style = self._get(hwnd, self.GWL_EXSTYLE)
        if enabled:
            style |= self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW
        else:
            style &= ~self.WS_EX_NOACTIVATE
        self._set(hwnd, self.GWL_EXSTYLE, style)


# ════════════════════════════════════════════════════════════════════════════
#  Widgets de pintura
# ════════════════════════════════════════════════════════════════════════════
class Equalizer(QWidget):
    """Micro-equalizador de 4 barras verdes/azuis; congela quando pausado."""

    COLORS = [QColor(T.GREEN), QColor(T.SKY), QColor(T.GREEN), QColor(T.SKY)]
    PHASES = [0.20, 0.55, 0.10, 0.70]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 16)
        self._t = 0.0
        self._playing = True
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.update()

    def showEvent(self, e):  # noqa: N802
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        if self._playing:
            self._t += 0.04
            self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        H = self.height()
        for i, (col, ph) in enumerate(zip(self.COLORS, self.PHASES)):
            k = 0.5 + 0.5 * math.sin(2 * math.pi * (self._t / 0.9 + ph))
            h = H * (0.3 + 0.7 * k) if self._playing else H * 0.25
            p.setBrush(col)
            p.drawRoundedRect(QRectF(i * 5, H - h, 3, h), 1.5, 1.5)


class CoverArt(QWidget):
    """Capa do álbum / miniatura do vídeo, recortada em cantos arredondados (ou círculo)."""

    def __init__(self, size: int, radius: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._radius = radius
        self._img: Optional[QImage] = None
        self._key: Optional[int] = None

    def set_image(self, data: Optional[bytes]) -> None:
        key = hash(data) if data else None
        if key == self._key:
            return
        self._key = key
        img = QImage.fromData(data) if data else None
        self._img = img if img is not None and not img.isNull() else None
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.setClipPath(path)
        if self._img is not None:
            iw, ih = self._img.width(), self._img.height()
            side = min(iw, ih)
            src = QRectF((iw - side) / 2, (ih - side) / 2, side, side)   # recorte quadrado central
            p.drawImage(rect, self._img, src)
        else:
            g = QLinearGradient(rect.topLeft(), rect.bottomRight())
            g.setColorAt(0, QColor("#4338CA"))
            g.setColorAt(1, QColor("#0E7490"))
            p.fillRect(rect, g)
            p.setPen(QColor(255, 255, 255, 215))
            p.setFont(icon_font(max(8, int(self.width() * 0.42))))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, Glyph.MUSIC)


class MarqueeLabel(QWidget):
    """Texto de uma linha que rola (marquee) quando não cabe na largura."""

    GAP = 36
    PAUSE_TICKS = 50  # ≈1,5 s parado no início de cada volta

    def __init__(self, font: QFont, color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._font = font
        self._color = QColor(color)
        self._text = ""
        self._offset = 0.0
        self._pause = self.PAUSE_TICKS
        fm = QFontMetrics(font)
        self.setFixedHeight(fm.height() + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(40)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def setText(self, text: str) -> None:  # noqa: N802
        if text == self._text:
            return
        self._text = text
        self.setAccessibleName(text)
        self._offset = 0.0
        self._pause = self.PAUSE_TICKS
        self._sync()
        self.update()

    def _text_width(self) -> int:
        return QFontMetrics(self._font).horizontalAdvance(self._text)

    def _needs_scroll(self) -> bool:
        return self._text_width() > self.width()

    def _sync(self) -> None:
        if self.isVisible() and self._needs_scroll():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0.0

    def resizeEvent(self, e):  # noqa: N802
        self._sync()
        super().resizeEvent(e)

    def showEvent(self, e):  # noqa: N802
        self._sync()
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        if self._pause > 0:
            self._pause -= 1
            return
        self._offset += 1.0
        if self._offset >= self._text_width() + self.GAP:
            self._offset = 0.0
            self._pause = self.PAUSE_TICKS
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._font)
        p.setPen(self._color)
        r = self.rect()
        if not self._needs_scroll():
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
            return
        cycle = self._text_width() + self.GAP
        x = -self._offset
        flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        p.drawText(QRectF(x, 0, cycle, r.height()), flags, self._text)
        p.drawText(QRectF(x + cycle, 0, cycle, r.height()), flags, self._text)


class ThinProgress(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = 0.0

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        p.setBrush(QColor(255, 255, 255, 31))
        p.drawRoundedRect(r, 2, 2)
        if self._value > 0:
            p.setBrush(QColor(T.CYAN))
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(4.0, r.width() * self._value), r.height()), 2, 2)


class PageDots(QWidget):
    """Paginação vertical discreta: o ponto ativo vira um traço ciano."""

    def __init__(self, count: int = 3, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._count = count
        self._index = 0
        self.setFixedSize(6, 14 + (count - 1) * 4 + (count - 1) * 5)

    def set_index(self, index: int) -> None:
        self._index = index
        self.setAccessibleName(f"Andar {index + 1} de {self._count}")
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        y = 0.0
        for i in range(self._count):
            active = i == self._index
            h = 14 if active else 4
            p.setBrush(QColor(T.CYAN) if active else QColor(255, 255, 255, 72))
            p.drawRoundedRect(QRectF(1, y, 4, h), 2, 2)
            y += h + 5


class WeatherIcon(QWidget):
    """Ícone vetorial do tempo: clear, partly, cloudy, fog, rain, snow, storm (dia/noite)."""

    def __init__(self, size: int = 46, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._kind = "partly"
        self._day = True

    def set_condition(self, kind: str, is_day: bool = True) -> None:
        self._kind, self._day = kind, is_day
        self.update()

    @staticmethod
    def _cloud(r: QRectF) -> QPainterPath:
        w, h = r.width(), r.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(r.left(), r.top() + h * 0.45, w, h * 0.55), h * 0.27, h * 0.27)
        path.addEllipse(QRectF(r.left() + w * 0.10, r.top() + h * 0.22, w * 0.42, h * 0.62))
        path.addEllipse(QRectF(r.left() + w * 0.34, r.top(), w * 0.52, h * 0.82))
        return path.simplified()

    def _sun_or_moon(self, p: QPainter, c: QPointF, radius: float) -> None:
        amber = QColor(T.AMBER)
        if not self._day:
            moon = QPainterPath()
            moon.addEllipse(c, radius * 1.1, radius * 1.1)
            cut = QPainterPath()
            cut.addEllipse(QPointF(c.x() + radius * 0.6, c.y() - radius * 0.45), radius * 0.95, radius * 0.95)
            p.setPen(QPen(QColor("#C7D2FE"), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(moon.subtracted(cut))
            return
        p.setPen(round_pen(amber, 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, radius, radius)
        for i in range(8):
            a = i * math.pi / 4
            p1 = QPointF(c.x() + math.cos(a) * radius * 1.45, c.y() + math.sin(a) * radius * 1.45)
            p2 = QPointF(c.x() + math.cos(a) * radius * 1.85, c.y() + math.sin(a) * radius * 1.85)
            p.drawLine(p1, p2)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = float(self.width())
        k = self._kind
        cloud_pen = QPen(QColor(T.SOFT), 1.6)
        cloud_fill = QColor(10, 10, 12)

        if k == "clear":
            self._sun_or_moon(p, QPointF(s / 2, s / 2), s * 0.2)
            return
        if k == "partly":
            self._sun_or_moon(p, QPointF(s * 0.36, s * 0.36), s * 0.14)
            cloud = self._cloud(QRectF(s * 0.28, s * 0.42, s * 0.66, s * 0.42))
        else:
            cloud = self._cloud(QRectF(s * 0.12, s * 0.16, s * 0.76, s * 0.48))
        p.setPen(cloud_pen)
        p.setBrush(cloud_fill)
        p.drawPath(cloud)

        if k == "rain":
            p.setPen(round_pen(QColor(T.SKY), 1.8))
            for x in (0.32, 0.50, 0.68):
                p.drawLine(QPointF(s * x, s * 0.74), QPointF(s * (x - 0.05), s * 0.90))
        elif k == "snow":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E0F2FE"))
            for x, y in ((0.32, 0.78), (0.50, 0.88), (0.68, 0.78)):
                p.drawEllipse(QPointF(s * x, s * y), s * 0.035, s * 0.035)
        elif k == "fog":
            p.setPen(round_pen(QColor(T.TEXT_2), 1.8))
            for i, y in enumerate((0.76, 0.86)):
                p.drawLine(QPointF(s * (0.22 + 0.06 * i), s * y), QPointF(s * (0.78 - 0.06 * i), s * y))
        elif k == "storm":
            bolt = QPolygonF([QPointF(s * 0.52, s * 0.60), QPointF(s * 0.40, s * 0.80), QPointF(s * 0.50, s * 0.80),
                              QPointF(s * 0.44, s * 0.96), QPointF(s * 0.62, s * 0.72), QPointF(s * 0.52, s * 0.72)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.AMBER))
            p.drawPolygon(bolt)


class ElidedLabel(QLabel):
    """QLabel de uma linha que corta com "…" na largura real disponível."""

    def __init__(self, text: str = "", px: int = 13, color: str = T.TEXT,
                 weight: QFont.Weight = QFont.Weight.Normal) -> None:
        super().__init__()
        self.setFont(ui_font(px, weight))
        self.setStyleSheet(f"color: {color}; background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)
        self._full = ""
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self.setAccessibleName(self._full)
        self._refresh()

    def full_text(self) -> str:
        return self._full

    def _refresh(self) -> None:
        width = self.width() if self.width() > 20 else 400
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._refresh()


def vline() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet("background: rgba(255,255,255,20); border: none;")
    return f


# ════════════════════════════════════════════════════════════════════════════
#  QStackedWidget com slide vertical
# ════════════════════════════════════════════════════════════════════════════
class VerticalSlideStack(QStackedWidget):
    """Troca de página deslizando para cima/baixo (sentido segue o índice)."""

    def __init__(self, parent: Optional[QWidget] = None, duration: int = T.SLIDE_MS) -> None:
        super().__init__(parent)
        self._duration = duration
        self._group: Optional[QParallelAnimationGroup] = None
        self._pending: Optional[tuple[int, QWidget]] = None

    def set_index(self, index: int) -> None:
        """Troca imediata, sem animação (usada ao expandir com mídia tocando)."""
        self._stop()
        self.setCurrentIndex(index)
        self.currentWidget().move(0, 0)

    def slide_to(self, index: int) -> None:
        self._stop()
        cur_i = self.currentIndex()
        if index == cur_i or not (0 <= index < self.count()):
            return
        cur, nxt = self.currentWidget(), self.widget(index)
        w, h = self.width(), self.height()
        d = 1 if index > cur_i else -1
        nxt.setGeometry(0, d * h, w, h)
        nxt.show()
        nxt.raise_()

        group = QParallelAnimationGroup(self)
        for widget, start, end in ((cur, QPoint(0, 0), QPoint(0, -d * h)), (nxt, QPoint(0, d * h), QPoint(0, 0))):
            a = QPropertyAnimation(widget, b"pos")
            a.setDuration(self._duration)
            a.setStartValue(start)
            a.setEndValue(end)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(a)
        self._pending = (index, cur)
        group.finished.connect(self._finish)
        self._group = group
        # KeepWhenStopped: o grupo só é liberado em _stop(), que é quem guarda a
        # referência. Com DeleteWhenStopped o Qt apagava o objeto C++ ao fim do
        # slide e a troca de andar seguinte chamava .state() num wrapper já
        # destruído (RuntimeError a cada navegação pela rodinha).
        group.start()

    def _stop(self) -> None:
        group, self._group = self._group, None
        if group is not None:
            group.stop()          # não emite finished; inofensivo se já terminou
            group.deleteLater()
        self._finish()

    def _finish(self) -> None:
        if self._pending is None:
            return
        index, old = self._pending
        self._pending = None
        self.setCurrentIndex(index)
        old.move(0, 0)
        self.currentWidget().move(0, 0)


# ════════════════════════════════════════════════════════════════════════════
#  Estado compacto
# ════════════════════════════════════════════════════════════════════════════
class CompactView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # — repouso: orbe + "Chloe" + relógio
        self.idle = QWidget(self)
        il = QHBoxLayout(self.idle)
        il.setContentsMargins(6, 0, 14, 0)
        il.setSpacing(4)
        self.orb = ParticleOrb(24)
        il.addWidget(self.orb)
        il.addWidget(make_label("Chloe", 12, T.SOFT, QFont.Weight.Medium))
        il.addStretch(1)
        self.clock = make_label("", 13, T.TEXT, QFont.Weight.DemiBold)
        il.addWidget(self.clock)

        # — mídia ativa: capa circular + artista + equalizador
        self.media = QWidget(self)
        ml = QHBoxLayout(self.media)
        ml.setContentsMargins(6, 0, 14, 0)
        ml.setSpacing(8)
        self.cover = CoverArt(24, 12)
        ml.addWidget(self.cover)
        self.artist = make_label("", 12, T.TEXT_2)
        ml.addWidget(self.artist)
        ml.addStretch(1)
        self.eq = Equalizer()
        ml.addWidget(self.eq)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.idle)
        lay.addWidget(self.media)
        self.media.hide()

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _update_clock(self) -> None:
        self.clock.setText(QTime.currentTime().toString("HH:mm"))

    def set_media(self, info: Optional["MediaInfo"]) -> None:
        active = info is not None and info.is_playing
        self.idle.setVisible(not active)
        self.media.setVisible(active)
        if active:
            self.cover.set_image(info.thumbnail)
            fm = self.artist.fontMetrics()
            self.artist.setText(fm.elidedText(info.artist or info.source_name, Qt.TextElideMode.ElideRight, 110))
            self.eq.set_playing(True)

    def set_state(self, state: str) -> None:
        self.orb.set_state(state)


# ════════════════════════════════════════════════════════════════════════════
#  Andar 1 — Assistente
# ════════════════════════════════════════════════════════════════════════════
class FocusLineEdit(QLineEdit):
    clicked = pyqtSignal()
    focus_lost = pyqtSignal()
    escape = pyqtSignal()

    def mousePressEvent(self, e):  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        super().focusOutEvent(e)
        self.focus_lost.emit()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(e)


class AssistantFloor(QWidget):
    message_submitted = pyqtSignal(str)
    mic_clicked = pyqtSignal()

    MIC = {
        #              cor        rgb fundo         rótulo        glifo
        "listening": (T.CYAN, (34, 211, 238), "Ouvindo", Glyph.MIC),
        "muted": (T.RED, (244, 63, 94), "Mutado", Glyph.MIC_OFF),
        "speaking": (T.GREEN, (52, 211, 153), "Falando", Glyph.MIC),
        "thinking": ("#60A5FA", (96, 165, 250), "Pensando", Glyph.MIC),
        "sleeping": (T.TEXT_2, (161, 161, 170), "Repouso", Glyph.MIC),
        "offline": (T.TEXT_2, (161, 161, 170), "Offline", Glyph.MIC_OFF),
        "connecting": (T.TEXT_2, (161, 161, 170), "Conectando", Glyph.MIC_OFF),
        "unavailable": (T.TEXT_2, (161, 161, 170), "Sem mic", Glyph.MIC_OFF),
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full_response = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 12, T.RAIL_W + 4, 14)
        root.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(0)
        left.addStretch(1)
        self.orb = ParticleOrb(52)
        left.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = make_label("OUVINDO", 10, T.CYAN, QFont.Weight.DemiBold)
        f = self.status.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.4)
        self.status.setFont(f)
        left.addWidget(self.status, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(1)
        root.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.response = make_label("", 13, T.SOFT)
        self.response.setWordWrap(True)
        self.response.setFixedHeight(36)
        self.response.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.response.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        right.addWidget(self.response)
        right.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = FocusLineEdit()
        self.input.setObjectName("chloeAsk")
        self.input.setPlaceholderText("Pergunte algo à Chloe...")
        self.input.setAccessibleName("Pergunte algo à Chloe")
        self.input.setFont(ui_font(13))
        self.input.setFixedHeight(34)
        pal = self.input.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(T.TEXT_3))
        pal.setColor(QPalette.ColorRole.Text, QColor(T.TEXT))
        self.input.setPalette(pal)
        self.input.returnPressed.connect(self._submit)
        row.addWidget(self.input, 1)

        self.mic = QPushButton()
        self.mic.setFixedSize(34, 34)
        self.mic.setFont(icon_font(15))
        self.mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mic.clicked.connect(self.mic_clicked)
        row.addWidget(self.mic)
        right.addLayout(row)
        root.addLayout(right, 1)

        self.set_mic_state("listening")
        self.set_response("Olá! Passe o mouse aqui quando precisar de mim.")

    def _submit(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.message_submitted.emit(text)

    def set_response(self, text: str) -> None:
        self._full_response = text
        self.response.setToolTip(text)
        self._elide()

    def _elide(self) -> None:
        fm = self.response.fontMetrics()
        avail = max(60, self.response.width() * 2 - 28)   # ≈ duas linhas
        self.response.setText(fm.elidedText(self._full_response, Qt.TextElideMode.ElideRight, avail))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def set_mic_state(self, state: str) -> None:
        color, (r, g, b), label, glyph = self.MIC.get(state, self.MIC["listening"])
        self.mic.setText(glyph)
        self.mic.setToolTip("Microfone mutado — clique para ativar" if state == "muted"
                            else "Microfone ativo — clique para silenciar")
        self.mic.setAccessibleName(self.mic.toolTip())
        self.mic.setStyleSheet(
            f"QPushButton {{ background: rgba({r},{g},{b},38); color: {color}; border: none; border-radius: 10px; }}"
            f"QPushButton:hover {{ background: rgba({r},{g},{b},64); }}"
            f"QPushButton:pressed {{ background: rgba({r},{g},{b},90); }}"
        )
        self.status.setText(label.upper())
        self.status.setStyleSheet(f"color: {color}; background: transparent;")
        self.orb.set_state(state)


# ════════════════════════════════════════════════════════════════════════════
#  Andar 2 — Clima
# ════════════════════════════════════════════════════════════════════════════
class WeatherFloor(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, T.RAIL_W + 4, 14)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.icon = WeatherIcon(46)
        top.addWidget(self.icon)

        temp_box = QHBoxLayout()
        temp_box.setSpacing(1)
        self.temp = make_label("--°", 40, T.TEXT, QFont.Weight.DemiBold)
        tf = self.temp.font()
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -1.5)
        self.temp.setFont(tf)
        unit = make_label("C", 16, T.TEXT_2, QFont.Weight.Medium)
        unit.setAlignment(Qt.AlignmentFlag.AlignTop)
        temp_box.addWidget(self.temp, 0, Qt.AlignmentFlag.AlignTop)
        temp_box.addWidget(unit, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(temp_box)

        top.addWidget(vline())
        info = QVBoxLayout()
        info.setSpacing(2)
        city_row = QHBoxLayout()
        city_row.setSpacing(5)
        pin = QLabel(Glyph.PIN)
        pin.setFont(icon_font(12))
        pin.setStyleSheet(f"color: {T.CYAN}; background: transparent;")
        city_row.addWidget(pin)
        self.city = ElidedLabel("Detectando local…", 14, T.TEXT, QFont.Weight.DemiBold)
        city_row.addWidget(self.city, 1)
        info.addLayout(city_row)
        self.condition = make_label("", 12, T.TEXT_2)
        self.feels = make_label("", 12, T.TEXT_3)
        info.addWidget(self.condition)
        info.addWidget(self.feels)
        top.addLayout(info, 1)
        root.addLayout(top)
        root.addStretch(1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_humidity = self._chip("💧 --%")
        self.chip_wind = self._chip("💨 -- km/h")
        self.chip_extra = self._chip("☀️ UV --")
        for c in (self.chip_humidity, self.chip_wind, self.chip_extra):
            chips.addWidget(c)
        chips.addStretch(1)
        root.addLayout(chips)

    @staticmethod
    def _chip(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(ui_font(12, families=EMOJI_FAMILIES))
        lbl.setFixedHeight(26)
        lbl.setStyleSheet(
            f"QLabel {{ background: rgba(255,255,255,15); border-radius: 13px; padding: 0 10px; color: {T.SOFT}; }}"
        )
        return lbl

    def set_weather(self, data: "WeatherData") -> None:
        self.icon.set_condition(data.kind, data.is_day)
        self.temp.setText(f"{round(data.temperature)}°")
        self.city.set_full_text(data.location.label)
        self.condition.setText(data.condition)
        self.feels.setText(f"Sensação térmica {round(data.apparent_temperature)}°C")
        self.chip_humidity.setText(f"💧 {round(data.humidity)}%")
        self.chip_wind.setText(f"💨 {round(data.wind_speed)} km/h")
        if data.uv_index is not None:
            self.chip_extra.setText(f"☀️ UV {round(data.uv_index)} · {data.uv_label}")
            self.chip_extra.show()
        elif data.aqi is not None:
            self.chip_extra.setText(f"🍃 Ar {data.aqi} · {data.aqi_label}")
            self.chip_extra.show()
        else:
            self.chip_extra.hide()


# ════════════════════════════════════════════════════════════════════════════
#  Andar 3 — Player de mídia universal
# ════════════════════════════════════════════════════════════════════════════
class MediaFloor(QWidget):
    command = pyqtSignal(str)  # "previous" | "play_pause" | "next"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, T.RAIL_W + 4, 14)
        outer.setSpacing(0)

        # — conteúdo com mídia
        self.content = QWidget()
        root = QVBoxLayout(self.content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.cover = CoverArt(48, 10)
        top.addWidget(self.cover)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addStretch(1)
        self.title = MarqueeLabel(ui_font(14, QFont.Weight.DemiBold), T.TEXT)
        self.artist = ElidedLabel("", 12, T.TEXT_2)
        texts.addWidget(self.title)
        texts.addWidget(self.artist)
        texts.addStretch(1)
        top.addLayout(texts, 1)

        controls = QHBoxLayout()
        controls.setSpacing(4)
        self.btn_prev = icon_button(Glyph.PREV, 32, tooltip="Faixa anterior")
        self.btn_play = icon_button(Glyph.PAUSE, 36, kind="solid", tooltip="Pausar")
        self.btn_next = icon_button(Glyph.NEXT, 32, tooltip="Próxima faixa")
        self.btn_prev.clicked.connect(lambda: self.command.emit("previous"))
        self.btn_play.clicked.connect(lambda: self.command.emit("play_pause"))
        self.btn_next.clicked.connect(lambda: self.command.emit("next"))
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            controls.addWidget(b)
        top.addLayout(controls)
        root.addLayout(top)
        root.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.pos = make_label("0:00", 11, T.TEXT_2)
        self.bar = ThinProgress()
        self.rem = make_label("", 11, T.TEXT_2)
        self.source = QLabel("")
        self.source.setFont(ui_font(11))
        self.source.setFixedHeight(22)
        self.source.setStyleSheet(
            "QLabel { background: rgba(255,255,255,15); border-radius: 11px; padding: 0 8px; color: #D4D4D8; }"
        )
        bottom.addWidget(self.pos)
        bottom.addWidget(self.bar, 1)
        bottom.addWidget(self.rem)
        bottom.addWidget(self.source)
        root.addLayout(bottom)
        outer.addWidget(self.content)

        # — estado vazio
        self.empty = QWidget()
        el = QHBoxLayout(self.empty)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(14)
        ph = QLabel(Glyph.MUSIC)
        ph.setFont(icon_font(20))
        ph.setFixedSize(48, 48)
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(f"background: rgba(255,255,255,15); border-radius: 10px; color: {T.TEXT_3};")
        el.addWidget(ph)
        txt = QVBoxLayout()
        txt.setSpacing(4)
        txt.addStretch(1)
        txt.addWidget(make_label("Nada tocando agora", 14, T.TEXT, QFont.Weight.DemiBold))
        hint = make_label("Abra o Spotify, YouTube ou outro player e ele aparece aqui.", 12, T.TEXT_2)
        hint.setWordWrap(True)
        txt.addWidget(hint)
        txt.addStretch(1)
        el.addLayout(txt, 1)
        outer.addWidget(self.empty)
        self.content.hide()

    def set_media(self, info: Optional["MediaInfo"]) -> None:
        has = info is not None and bool(info.title)
        self.content.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            return
        self.cover.set_image(info.thumbnail)
        self.title.setText(info.title)
        self.artist.set_full_text(info.artist or "")
        self.btn_play.setText(Glyph.PAUSE if info.is_playing else Glyph.PLAY)
        tip = "Pausar" if info.is_playing else "Reproduzir"
        self.btn_play.setToolTip(tip)
        self.btn_play.setAccessibleName(tip)
        self.source.setText(info.source_name)
        self.pos.setText(fmt_time(info.position))
        if info.duration > 0:
            self.bar.set_value(info.position / info.duration)
            self.rem.setText("-" + fmt_time(info.duration - info.position))
            self.rem.show()
        else:                       # transmissões ao vivo não têm duração
            self.bar.set_value(0)
            self.rem.hide()


# ════════════════════════════════════════════════════════════════════════════
#  A Ilha
# ════════════════════════════════════════════════════════════════════════════
ISLAND_QSS = f"""
QLabel {{ color: {T.TEXT}; background: transparent; }}
QLineEdit#chloeAsk {{
    background: rgba(255,255,255,15); border: 1px solid transparent; border-radius: 10px;
    padding: 0 12px; color: {T.TEXT}; selection-background-color: #0E7490;
}}
QLineEdit#chloeAsk:focus {{ border: 1px solid rgba(34,211,238,140); }}
QToolTip {{ color: {T.TEXT}; background: #1C1D22; border: 1px solid rgba(255,255,255,30); padding: 4px 8px; }}
"""


class DynamicIsland(QWidget):
    restore_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    message_submitted = pyqtSignal(str)
    mic_toggled = pyqtSignal(bool)        # pedido do usuário: True = mutar, False = ativar
    media_command = pyqtSignal(str)       # "previous" | "play_pause" | "next"
    floor_changed = pyqtSignal(int)

    FLOOR_ASSISTANT, FLOOR_WEATHER, FLOOR_MEDIA = 0, 1, 2

    def __init__(self, screen: Optional[QScreen] = None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # A ilha nunca mantém o app vivo sozinha: fechar a janela principal encerra a CHLOE.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setWindowTitle("CHLOE — Ilha Dinâmica")
        self.setStyleSheet(ISLAND_QSS)

        self._screen = screen or QGuiApplication.primaryScreen()
        self._win32 = _Win32()
        self._expanded = False
        self._floor = self.FLOOR_ASSISTANT
        self._media: Optional["MediaInfo"] = None
        self._state = "listening"
        self._input_active = False
        self._menu_open = False
        self._wheel_accum = 0
        self._wheel_locked = False
        self._anim: Optional[QParallelAnimationGroup] = None
        self._fade: Optional[QPropertyAnimation] = None

        self._build()

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(T.LEAVE_GRACE_MS)
        self._collapse_timer.timeout.connect(self._maybe_collapse)

        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.setInterval(T.WHEEL_LOCK_MS)
        self._wheel_timer.timeout.connect(self._unlock_wheel)

        self.setGeometry(self._rect_for(T.COMPACT))
        self._screen.availableGeometryChanged.connect(self._reposition)

    # ── construção ──────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.compact = CompactView(self)
        self._compact_fx = QGraphicsOpacityEffect(self.compact)
        self._compact_fx.setOpacity(1.0)
        self.compact.setGraphicsEffect(self._compact_fx)

        self.floors = QWidget(self)
        self.floors.setGeometry(0, 0, T.EXPANDED.width(), T.EXPANDED.height())
        self._floors_fx = QGraphicsOpacityEffect(self.floors)
        self._floors_fx.setOpacity(0.0)
        self.floors.setGraphicsEffect(self._floors_fx)

        self.stack = VerticalSlideStack(self.floors)
        self.stack.setGeometry(0, 0, T.EXPANDED.width(), T.EXPANDED.height())
        self.assistant = AssistantFloor()
        self.weather = WeatherFloor()
        self.media = MediaFloor()
        for page in (self.assistant, self.weather, self.media):
            self.stack.addWidget(page)

        rail = QWidget(self.floors)
        rail.setGeometry(T.EXPANDED.width() - T.RAIL_W, 0, T.RAIL_W, T.EXPANDED.height())
        self.btn_restore = icon_button(Glyph.EXPAND, 28, kind="square", tooltip="Abrir janela completa da CHLOE")
        self.btn_restore.setFont(icon_font(12))
        self.btn_restore.setParent(rail)
        self.btn_restore.move(6, 8)
        self.dots = PageDots(3, rail)
        self.dots.move((T.RAIL_W - self.dots.width()) // 2, (T.EXPANDED.height() - self.dots.height()) // 2 + 6)
        self.dots.set_index(self._floor)
        self.floors.hide()

        # sinais internos
        self.btn_restore.clicked.connect(self._on_restore)
        self.assistant.message_submitted.connect(self.message_submitted)
        self.assistant.mic_clicked.connect(self._toggle_mic)
        self.assistant.input.clicked.connect(self._activate_input)
        self.assistant.input.focus_lost.connect(self._release_input)
        self.assistant.input.escape.connect(self._release_input)
        self.media.command.connect(self.media_command)

    # ── geometria ───────────────────────────────────────────────────────────
    def _rect_for(self, size: QSize) -> QRect:
        geo = self._screen.availableGeometry()
        x = geo.x() + (geo.width() - size.width()) // 2
        return QRect(x, geo.y() + T.TOP_MARGIN, size.width(), size.height())

    def _reposition(self) -> None:
        self.setGeometry(self._rect_for(T.EXPANDED if self._expanded else T.COMPACT))

    def resizeEvent(self, e):  # noqa: N802
        self.compact.setGeometry(self.rect())
        super().resizeEvent(e)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(r.height() / 2, T.MAX_RADIUS)   # 18 na pílula → 28 expandida
        p.setPen(QPen(T.BORDER, 1))
        p.setBrush(T.BG)
        p.drawRoundedRect(r, radius, radius)

    # ── exibir / esconder ───────────────────────────────────────────────────
    def show_island(self) -> None:
        self.setGeometry(self._rect_for(T.EXPANDED if self._expanded else T.COMPACT))
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_window(1.0)

    def hide_island(self) -> None:
        self._release_input()
        self._collapse_timer.stop()
        self._fade_window(0.0, on_done=self.hide)

    def _fade_window(self, to: float, on_done=None) -> None:
        if self._fade is not None:
            self._fade.stop()
            self._fade.deleteLater()   # antes, cada abrir/fechar acumulava uma animação
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(200)
        a.setStartValue(self.windowOpacity())
        a.setEndValue(to)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on_done:
            a.finished.connect(on_done)
        self._fade = a
        a.start()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._win32.set_no_activate(self, True)

    # ── expandir / contrair ─────────────────────────────────────────────────
    def expand(self) -> None:
        self._collapse_timer.stop()
        if self._expanded:
            return
        self._expanded = True
        if self._media is not None and self._media.is_playing:
            self._set_floor(self.FLOOR_MEDIA, animate=False)
        self.floors.show()
        self.floors.raise_()
        self._transition(T.EXPANDED, T.EXPAND_MS, QEasingCurve.Type.OutCubic, compact_to=0.0, floors_to=1.0)

    def collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self._release_input(schedule=False)
        self.compact.show()
        self._transition(T.COMPACT, T.COLLAPSE_MS, QEasingCurve.Type.InOutCubic, compact_to=1.0, floors_to=0.0)

    def _transition(self, size: QSize, ms: int, curve: QEasingCurve.Type,
                    compact_to: float, floors_to: float) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()   # antes, cada hover acumulava um grupo de animação
        group = QParallelAnimationGroup(self)

        geo = QPropertyAnimation(self, b"geometry")
        geo.setDuration(ms)
        geo.setStartValue(self.geometry())
        geo.setEndValue(self._rect_for(size))
        geo.setEasingCurve(curve)
        group.addAnimation(geo)

        for fx, to, dur in ((self._compact_fx, compact_to, int(ms * 0.6) if compact_to == 0 else ms),
                            (self._floors_fx, floors_to, ms if floors_to == 1 else int(ms * 0.55))):
            a = QPropertyAnimation(fx, b"opacity")
            a.setDuration(dur)
            a.setStartValue(fx.opacity())
            a.setEndValue(to)
            a.setEasingCurve(QEasingCurve.Type.OutQuad)
            group.addAnimation(a)

        group.finished.connect(self._on_transition_done)
        self._anim = group
        group.start()

    def _on_transition_done(self) -> None:
        if self._expanded:
            self.compact.hide()
        else:
            self.floors.hide()

    # ── mouse ───────────────────────────────────────────────────────────────
    def enterEvent(self, e):  # noqa: N802
        self._collapse_timer.stop()
        self.expand()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        if not self._input_active:
            self._collapse_timer.start()
        super().leaveEvent(e)

    def _maybe_collapse(self) -> None:
        if self._input_active or self._menu_open or self.geometry().contains(QCursor.pos()):
            return
        self.collapse()

    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: #1C1D22; color: {T.TEXT}; border: 1px solid rgba(255,255,255,30);"
            " border-radius: 8px; padding: 4px; }"
            " QMenu::item { padding: 6px 18px; border-radius: 6px; }"
            " QMenu::item:selected { background: rgba(255,255,255,26); }"
        )
        menu.setFont(ui_font(13))
        menu.addAction("Abrir janela completa", self._on_restore)
        menu.addSeparator()
        menu.addAction("Encerrar CHLOE", self.quit_requested.emit)
        self._menu_open = True
        self._collapse_timer.stop()
        try:
            menu.exec(self.mapToGlobal(pos))
        finally:
            self._menu_open = False
            if not self.geometry().contains(QCursor.pos()):
                self._collapse_timer.start()

    def wheelEvent(self, e):  # noqa: N802
        delta = e.angleDelta().y() or e.pixelDelta().y()
        e.accept()
        if delta == 0:
            return
        if self._wheel_locked:
            self._wheel_accum = 0
            return
        self._wheel_accum += delta
        if abs(self._wheel_accum) < 60:          # touchpads mandam deltas pequenos
            return
        step = 1 if self._wheel_accum < 0 else -1  # rolar para baixo → próximo andar
        self._wheel_accum = 0
        self._wheel_locked = True
        self._wheel_timer.start()
        if not self._expanded:
            self.expand()
        self.go_to_floor(self._floor + step)

    def _unlock_wheel(self) -> None:
        self._wheel_locked = False

    # ── andares ─────────────────────────────────────────────────────────────
    def go_to_floor(self, index: int) -> None:
        self._set_floor(index, animate=True)

    def _set_floor(self, index: int, animate: bool) -> None:
        index = max(0, min(self.stack.count() - 1, index))
        if index == self._floor and self.stack.currentIndex() == index:
            return
        if self._floor == self.FLOOR_ASSISTANT and index != self.FLOOR_ASSISTANT:
            self._release_input(schedule=False)
        self._floor = index
        if animate:
            self.stack.slide_to(index)
        else:
            self.stack.set_index(index)
        self.dots.set_index(index)
        self.floor_changed.emit(index)

    @property
    def current_floor(self) -> int:
        return self._floor

    # ── foco de teclado sob demanda ─────────────────────────────────────────
    def _activate_input(self) -> None:
        if self._input_active:
            return
        self._input_active = True
        self._collapse_timer.stop()
        self._win32.set_no_activate(self, False)
        self.activateWindow()
        self.raise_()
        self.assistant.input.setFocus(Qt.FocusReason.MouseFocusReason)

    def _release_input(self, schedule: bool = True) -> None:
        if not self._input_active:
            return
        self._input_active = False
        self.assistant.input.clearFocus()
        self._win32.set_no_activate(self, True)
        if schedule and self._expanded and not self.geometry().contains(QCursor.pos()):
            self._collapse_timer.start()

    # ── ações ───────────────────────────────────────────────────────────────
    def _on_restore(self) -> None:
        self.collapse()
        self.restore_requested.emit()

    def _toggle_mic(self) -> None:
        # Só pede; quem decide (e confirma via set_assistant_state) é o app.
        self.mic_toggled.emit(self._state != "muted")

    # ── API pública (slots) ─────────────────────────────────────────────────
    @property
    def is_muted(self) -> bool:
        return self._state == "muted"

    def set_response(self, text: str) -> None:
        """Última resposta falada pela Chloe (ou a transcrição)."""
        self.assistant.set_response(text)

    def set_assistant_state(self, state: str) -> None:
        """listening | muted | speaking | thinking | sleeping | offline | connecting | unavailable."""
        if state not in AssistantFloor.MIC:
            state = "listening"
        self._state = state
        self.assistant.set_mic_state(state)
        self.compact.set_state(state)

    # Compatibilidade com a primeira versão da API.
    set_mic_state = set_assistant_state

    def set_voice_level(self, level: float) -> None:
        """Amplitude (0–1) da voz da Chloe ou do usuário, para o orbe reagir."""
        self.assistant.orb.set_level(level)
        self.compact.orb.set_level(level)

    def set_reduced_motion(self, enabled: bool) -> None:
        """Mesma opção "Movimento reduzido" da janela: a esfera para de girar e pulsar."""
        for orb in (self.assistant.orb, self.compact.orb):
            orb.reduced_motion = bool(enabled)
            orb.update()

    def update_weather(self, data: "WeatherData") -> None:
        self.weather.set_weather(data)

    def update_media(self, info: Optional["MediaInfo"]) -> None:
        self._media = info
        self.compact.set_media(info)
        self.media.set_media(info)


# ════════════════════════════════════════════════════════════════════════════
#  Demonstração isolada:  python dynamic_island.py
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    from datetime import datetime
    from types import SimpleNamespace

    app = QApplication(sys.argv)
    island = DynamicIsland()

    loc = SimpleNamespace(label="São Paulo, SP")
    island.update_weather(SimpleNamespace(
        location=loc, temperature=24.2, apparent_temperature=26.0, humidity=68, wind_speed=14,
        kind="partly", is_day=True, condition="Parcialmente nublado", uv_index=5, uv_label="Moderado",
        aqi=None, aqi_label="", updated_at=datetime.now(),
    ))
    demo_media = SimpleNamespace(
        title="Lo-fi para foco — Mix 03 (sessão longa de estudo)", artist="Canal de exemplo",
        source_name="Chrome", is_playing=True, position=84.0, duration=235.0, thumbnail=None,
    )
    island.update_media(demo_media)

    def _tick() -> None:
        if demo_media.is_playing:
            demo_media.position = (demo_media.position + 1) % demo_media.duration
        island.update_media(demo_media)

    def _cmd(cmd: str) -> None:
        if cmd == "play_pause":
            demo_media.is_playing = not demo_media.is_playing
        else:
            demo_media.position = 0
        _tick()

    t = QTimer()
    t.timeout.connect(_tick)
    t.start(1000)
    island.media_command.connect(_cmd)
    island.message_submitted.connect(lambda m: island.set_response(f"“{m}” — deixa comigo, já estou verificando…"))
    island.mic_toggled.connect(lambda mute: island.set_assistant_state("muted" if mute else "listening"))
    island.restore_requested.connect(app.quit)
    island.quit_requested.connect(app.quit)
    island.show_island()
    sys.exit(app.exec())
