"""Configuração local da saudação, sem acesso a credenciais."""
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)
from frontend.theme import C
from memory.ui_preferences import get_startup_preferences, save_startup_preferences


class StartupPreferencesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CHLOE AI · Inicialização")
        self.setMinimumWidth(380)
        self.resize(440, 410)
        self.setFont(QFont("Segoe UI", 11))
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background: #000000; color: {C.TEXT}; }}
            QCheckBox {{ spacing: 10px; padding: 5px 0; }}
            QCheckBox:disabled, QLabel:disabled {{ color: {C.TEXT_DIM}; }}
            QComboBox, QPushButton {{ padding: 8px 12px; border: 1px solid {C.BORDER_B}; border-radius: 6px; }}
            QPushButton:hover {{ border-color: {C.PRI}; }}
            QPushButton:focus, QComboBox:focus {{ border-color: {C.PRI}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        settings = get_startup_preferences()
        self.enabled = QCheckBox("Saudar ao abrir a CHLOE")
        self.enabled.setChecked(settings["enabled"])
        layout.addWidget(self.enabled)
        self.options = QWidget()
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.setAccessibleName("Modo da saudação")
        self.mode.addItem("Silenciosa · mostrar na tela", False)
        self.mode.addItem("Falada · usar a voz da CHLOE", True)
        self.mode.setCurrentIndex(1 if settings["spoken"] else 0)
        options.addWidget(self.mode)
        options.addWidget(QLabel("Incluir na saudação:"))
        self.fields = {}
        for key, title in (("include_time", "Horário local"), ("include_cpu", "Uso da CPU"),
                           ("include_ram", "Uso da RAM"), ("include_gpu", "Uso da GPU")):
            field = QCheckBox(title)
            field.setChecked(settings[key])
            self.fields[key] = field
            options.addWidget(field)
        self.options.setEnabled(settings["enabled"])
        self.enabled.toggled.connect(self.options.setEnabled)
        layout.addWidget(self.options)
        note = QLabel("Vale a partir da próxima abertura. A saudação acontece uma vez, sem notícias automáticas. Leituras não disponíveis são identificadas.")
        note.setWordWrap(True)
        note.setFont(QFont("Segoe UI", 10))
        note.setStyleSheet(f"color: {C.TEXT_DIM};")
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Salvar")
        save.setDefault(True)
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def save(self):
        values = {key: field.isChecked() for key, field in self.fields.items()}
        values.update(enabled=self.enabled.isChecked(), spoken=self.mode.currentData())
        if save_startup_preferences(values):
            self.accept()
        else:
            QMessageBox.warning(self, "Preferências não salvas",
                                "Não foi possível salvar as preferências locais. Verifique o acesso à pasta do projeto.")
