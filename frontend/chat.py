"""Conversa em balões, mantendo o contrato antigo de log em texto (append_log)."""
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit

from frontend.theme import C, FONT_FAMILIES

# Papel de cada bloco (QTextBlock.userState): o que o balão desenha atrás dele.
_LABEL, _USER, _ASSISTANT, _SYSTEM = 1, 2, 3, 4
_BUBBLE_INSET = 56          # espaço do lado oposto ao balão
_PAD_X, _PAD_Y = 12, 8


def _font(point_size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(point_size)
    f.setWeight(weight)
    return f


class CommandInput(QPlainTextEdit):
    returnPressed = pyqtSignal()

    def text(self):
        return self.toPlainText()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.returnPressed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class ChatView(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName("Conversa com CHLOE AI")
        self.setFont(_font(10.5))
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet(f"""
            QTextEdit {{ background: transparent; color: {C.TEXT}; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,40); border-radius: 4px; min-height: 32px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.document().setDocumentMargin(10)
        self.document().setMaximumBlockCount(1200)
        self._ai_name_lc = "chloe ai"
        self._empty = True
        self._accent = QColor(C.PRI)
        self.setHtml(f'<p style="color:{C.TEXT_DIM};">Suas mensagens aparecem aqui.</p>')
        self._sig.connect(self._append)

    def append_log(self, text):
        self._sig.emit(str(text))

    # ── inserção ────────────────────────────────────────────────────────────
    def _append(self, text):
        from frontend.locale import localize_event
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 16
        pos = bar.value()
        prefix, sep, body = text.partition(":")
        key = prefix.strip().lower()
        user = key in ("you", "você")
        assistant = key in ("jarvis", "chloe ai", self._ai_name_lc)
        label = "VOCÊ" if user else "CHLOE AI" if assistant else "SISTEMA"
        colour = C.PRI if user else C.ACC if assistant else C.TEXT_DIM
        if key in ("err", "erro"):
            label, colour = "ATENÇÃO", C.RED
        if key == "file":
            label = "ARQUIVO"
        content = body.strip() if sep else text
        if not user and not assistant:
            content = localize_event(content)
        role = _USER if user else _ASSISTANT if assistant else _SYSTEM
        align = Qt.AlignmentFlag.AlignRight if user else Qt.AlignmentFlag.AlignLeft
        if self._empty:
            self.clear()
            self._empty = False

        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        first = self.document().isEmpty()

        caption = QTextBlockFormat()
        caption.setAlignment(align)
        caption.setTopMargin(0 if first else 14)
        caption.setBottomMargin(_PAD_Y + 4)
        caption.setLeftMargin(_PAD_X + 2 if not user else 0)
        caption.setRightMargin(_PAD_X + 2 if user else 0)
        cap_chars = QTextCharFormat()
        cap_chars.setFont(_font(7.5, QFont.Weight.Bold))
        cap_chars.setFontLetterSpacingType(QFont.SpacingType.PercentageSpacing)
        cap_chars.setFontLetterSpacing(108)
        cap_chars.setForeground(QColor(colour))
        if first:
            cursor.setBlockFormat(caption)
            cursor.setBlockCharFormat(cap_chars)
        else:
            cursor.insertBlock(caption, cap_chars)
        cursor.insertText(label, cap_chars)
        cursor.block().setUserState(_LABEL)

        bubble = QTextBlockFormat()
        bubble.setAlignment(align)
        bubble.setLineHeight(135, 1)                 # ProportionalHeight
        bubble.setBottomMargin(_PAD_Y)
        if user:
            bubble.setLeftMargin(_BUBBLE_INSET)
            bubble.setRightMargin(_PAD_X)
        else:
            bubble.setLeftMargin(_PAD_X)
            bubble.setRightMargin(_BUBBLE_INSET)
        body_chars = QTextCharFormat()
        body_chars.setFont(_font(10.5 if role != _SYSTEM else 9.5))
        body_chars.setForeground(QColor(C.TEXT if role != _SYSTEM else C.TEXT_MED))
        cursor.insertBlock(bubble, body_chars)
        cursor.insertText(content.replace("\n", " "), body_chars)
        cursor.block().setUserState(role)

        if at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(pos)
        self.viewport().update()

    # ── balões ──────────────────────────────────────────────────────────────
    def _bubble_rect(self, block) -> QRectF | None:
        layout = block.layout()
        if layout is None or layout.lineCount() == 0:
            return None
        rect = QRectF()
        for i in range(layout.lineCount()):
            rect = rect.united(layout.lineAt(i).naturalTextRect())
        rect.translate(layout.position())
        rect.translate(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())
        return rect.adjusted(-_PAD_X, -_PAD_Y, _PAD_X, _PAD_Y)

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        top = self.cursorForPosition(self.viewport().rect().topLeft()).block()
        block = top.previous() if top.previous().isValid() else top
        bottom = self.viewport().height() + 40
        accent = QColor(C.PRI)
        while block.isValid():
            state = block.userState()
            if state in (_USER, _ASSISTANT, _SYSTEM):
                rect = self._bubble_rect(block)
                if rect is not None:
                    if rect.top() > bottom:
                        break
                    if state == _USER:
                        fill = QColor(accent)
                        fill.setAlpha(46)
                        radius = 18
                    elif state == _ASSISTANT:
                        fill, radius = QColor(255, 255, 255, 18), 18
                    else:
                        fill, radius = QColor(255, 255, 255, 10), rect.height() / 2
                    p.setBrush(fill)
                    p.drawRoundedRect(rect, radius, radius)
            block = block.next()
        p.end()
        super().paintEvent(event)

    def changeEvent(self, e):  # noqa: N802
        if e.type() == e.Type.PaletteChange:
            pal = self.viewport().palette()
            pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
            self.viewport().setPalette(pal)
        super().changeEvent(e)
