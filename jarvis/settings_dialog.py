"""The configuration dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from . import theme
from .config import Settings
from .memory import Memory

WHISPER_MODELS = [
    ("tiny.en", "Tiny (fastest, ~75 MB)"),
    ("base.en", "Base (balanced, ~145 MB)"),
    ("small.en", "Small (most accurate, ~480 MB)"),
]
EFFORTS = [("low", "Low (fastest replies)"), ("medium", "Medium"), ("high", "High (most thorough)")]


def _form_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("formLabel")
    return lab


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, memory: Memory, voices: list[str], parent=None):
        super().__init__(parent)
        self.settings = settings
        self.memory = memory
        self.setWindowTitle("JARVIS configuration")
        self.setMinimumSize(480, 640)
        self.test_voice_requested = None  # callback set by the main window
        self.memory_wiped = False

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("settingsBody")
        scroll.viewport().setObjectName("settingsBody")
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # --- Claude
        box = QGroupBox("NEURAL UPLINK (CLAUDE)")
        form = QFormLayout(box)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("sk-ant-…")
        source = settings.api_key_source()
        status = {
            "keychain": "A key is saved in your system keychain. Leave blank to keep it.",
            "file": "A key is saved. Leave blank to keep it.",
            "environment": "Using ANTHROPIC_API_KEY from the environment.",
            "none": "No key yet: JARVIS needs one to think. Get one at console.anthropic.com.",
        }[source]
        form.addRow(_form_label("Anthropic API key"), self.api_key)
        hint = _form_label(status)
        hint.setWordWrap(True)
        form.addRow(hint)
        self.model = QLineEdit(settings.model)
        form.addRow(_form_label("Model"), self.model)
        self.effort = QComboBox()
        for value, label in EFFORTS:
            self.effort.addItem(label, value)
        self.effort.setCurrentIndex(max(0, self.effort.findData(settings.effort)))
        form.addRow(_form_label("Thinking effort"), self.effort)
        self.web_search = QCheckBox("Allow web search for live information")
        self.web_search.setChecked(settings.web_search)
        form.addRow(self.web_search)
        layout.addWidget(box)

        # --- You
        box = QGroupBox("YOU")
        form = QFormLayout(box)
        self.user_name = QLineEdit(settings.user_name)
        self.user_name.setPlaceholderText("Optional")
        form.addRow(_form_label("Your name"), self.user_name)
        self.address_as = QLineEdit(settings.address_as)
        self.address_as.setPlaceholderText("e.g. sir, ma'am, boss, or your name")
        form.addRow(_form_label("Address me as"), self.address_as)
        self.location = QLineEdit(settings.location)
        self.location.setPlaceholderText("e.g. London")
        form.addRow(_form_label("Location (weather)"), self.location)
        layout.addWidget(box)

        # --- Voice
        box = QGroupBox("VOICE")
        form = QFormLayout(box)
        self.voice = QComboBox()
        self.voice.addItem("Automatic (best British voice)", "")
        for v in voices:
            self.voice.addItem(v, v)
        idx = self.voice.findData(settings.voice_name)
        self.voice.setCurrentIndex(max(0, idx))
        form.addRow(_form_label("JARVIS voice"), self.voice)
        if not voices:
            none = _form_label("No system voices found. On Linux install espeak-ng; on Windows install pywin32.")
            none.setWordWrap(True)
            form.addRow(none)
        self.rate = QSlider(Qt.Horizontal)
        self.rate.setRange(60, 160)
        self.rate.setValue(int(settings.voice_rate * 100))
        form.addRow(_form_label("Speed"), self.rate)
        test = QPushButton("TEST VOICE")
        test.setObjectName("ghost")
        test.clicked.connect(self._test_voice)
        form.addRow(test)
        layout.addWidget(box)

        # --- Listening
        box = QGroupBox("LISTENING")
        form = QFormLayout(box)
        self.wake_word = QCheckBox("Only respond when I say “Jarvis”")
        self.wake_word.setChecked(settings.wake_word)
        form.addRow(self.wake_word)
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(0, 100)
        self.sensitivity.setValue(int(settings.mic_sensitivity * 100))
        form.addRow(_form_label("Mic sensitivity"), self.sensitivity)
        self.whisper = QComboBox()
        for value, label in WHISPER_MODELS:
            self.whisper.addItem(label, value)
        self.whisper.setCurrentIndex(max(0, self.whisper.findData(settings.whisper_model)))
        form.addRow(_form_label("Speech model"), self.whisper)
        note = _form_label("Speech is transcribed on this computer. The model downloads once.")
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(box)

        # --- Abilities & memory
        box = QGroupBox("ABILITIES & MEMORY")
        form = QFormLayout(box)
        self.allow_apps = QCheckBox("Let JARVIS open apps and websites")
        self.allow_apps.setChecked(settings.allow_open_apps)
        form.addRow(self.allow_apps)
        self.remember = QCheckBox("Remember our conversations")
        self.remember.setChecked(settings.remember_conversations)
        form.addRow(self.remember)
        conversations, messages, notes = memory.stats()
        self.stats = _form_label(f"{conversations} conversations, {messages} messages and {notes} saved facts on file.")
        self.stats.setWordWrap(True)
        form.addRow(self.stats)
        self.notes = QListWidget()
        self.notes.setMinimumHeight(110)
        self.notes.setStyleSheet("QListWidget { background: rgba(63,216,255,0.04); border: 1px solid rgba(63,216,255,0.2); }")
        self._fill_notes()
        form.addRow(_form_label("Saved facts"), self.notes)
        row = QHBoxLayout()
        forget = QPushButton("FORGET SELECTED")
        forget.setObjectName("ghost")
        forget.clicked.connect(self._forget_selected)
        wipe = QPushButton("WIPE ALL MEMORY")
        wipe.setObjectName("danger")
        wipe.clicked.connect(self._wipe)
        row.addWidget(forget)
        row.addWidget(wipe)
        form.addRow(row)
        layout.addWidget(box)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("CANCEL")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("SAVE")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addLayout(buttons)
        theme.apply_spacing(self)

    def _fill_notes(self):
        self.notes.clear()
        for note in self.memory.notes():
            item = QListWidgetItem(f"{note.id}. {note.text}")
            item.setData(Qt.UserRole, note.id)
            self.notes.addItem(item)

    def _forget_selected(self):
        for item in self.notes.selectedItems():
            self.memory.delete_note(item.data(Qt.UserRole))
        self._fill_notes()

    def _wipe(self):
        answer = QMessageBox.question(
            self, "Wipe memory", "Delete every saved conversation and fact? This cannot be undone.",
        )
        if answer == QMessageBox.Yes:
            self.memory.wipe()
            self.memory_wiped = True
            self._fill_notes()
            self.stats.setText("Memory wiped.")

    def _test_voice(self):
        if self.test_voice_requested:
            self.test_voice_requested(self.voice.currentData(), self.rate.value() / 100)

    def _save(self):
        s = self.settings
        if self.api_key.text().strip():
            s.set_api_key(self.api_key.text())
        s.model = self.model.text().strip() or "claude-opus-5-5"
        s.effort = self.effort.currentData()
        s.web_search = self.web_search.isChecked()
        s.user_name = self.user_name.text().strip()
        s.address_as = self.address_as.text().strip()
        s.location = self.location.text().strip()
        s.voice_name = self.voice.currentData()
        s.voice_rate = self.rate.value() / 100
        s.wake_word = self.wake_word.isChecked()
        s.mic_sensitivity = self.sensitivity.value() / 100
        s.whisper_model = self.whisper.currentData()
        s.allow_open_apps = self.allow_apps.isChecked()
        s.remember_conversations = self.remember.isChecked()
        s.save()
        self.accept()
