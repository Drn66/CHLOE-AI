"""Readable conversation display retaining the legacy string-log contract."""
from html import escape

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import QTextEdit, QPlainTextEdit

from frontend.theme import C


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
        self.setFont(QFont("Segoe UI", 11))
        self.setStyleSheet(f"""
            QTextEdit {{ background: {C.DARK}; color: {C.TEXT}; border: none; padding: 4px; }}
            QScrollBar:vertical {{ background: {C.DARK}; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 32px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self.document().setMaximumBlockCount(600)
        self._ai_name_lc = "chloe ai"
        self._empty = True
        self.setHtml(f'<p style="color:{C.TEXT_DIM};">Suas mensagens aparecem aqui.</p>')
        self._sig.connect(self._append)

    def append_log(self, text):
        self._sig.emit(str(text))

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
        if self._empty:
            self.clear()
            self._empty = False
        html = (f'<p style="margin-top:20px;margin-bottom:6px;color:{colour};font-size:9pt;">'
                f'{label}</p><p style="margin-top:0px;margin-bottom:18px;color:{C.TEXT};line-height:150%;">'
                f'{escape(content).replace(chr(10), "<br>")}</p>')
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        cursor.insertHtml(html)
        if at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(pos)
