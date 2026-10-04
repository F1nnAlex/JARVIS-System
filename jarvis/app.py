"""The JARVIS main window: wires the ears, brain, voice and HUD together."""

from __future__ import annotations

import random
import re
import sys
import time
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme
from .apps import AppCatalog
from .brain import Brain
from .config import Settings, data_dir
from .ears import Ears
from .hud import Backdrop, Panel, Reactor
from .memory import Memory
from .panels import ClockPanel, SystemPanel, WeatherPanel
from .settings_dialog import SettingsDialog
from .voice import Voice

WAKE_WORD = re.compile(r"\b(?:hey|hi|okay|ok|yo)?[\s,]*\b(jarvis|jarvas|jervis|jarvus|javis)\b[\s,.!?]*", re.I)
FOLLOW_UP_S = 8.0
ACKNOWLEDGEMENTS = ["Yes?", "At your service.", "Listening.", "Go ahead.", "How can I help?"]
STATUS_TEXT = {
    "boot": "BOOTING", "standby": "STANDBY", "listening": "LISTENING", "transcribing": "PROCESSING SPEECH",
    "thinking": "THINKING", "speaking": "RESPONDING", "error": "FAULT DETECTED",
}


class Bridge(QObject):
    """Carries events from worker threads onto the GUI thread."""
    brain = Signal(int, dict)
    ears = Signal(str, dict)
    voice = Signal(str, dict)


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 0, 8, 0)
        mark = QLabel("◇")
        mark.setStyleSheet(f"color: {theme.CYAN}; font-size: 16px;")
        name = QLabel("J.A.R.V.I.S.")
        name.setObjectName("brandName")
        sub = QLabel("JUST A RATHER VERY INTELLIGENT SYSTEM")
        sub.setObjectName("brandSub")
        row.addWidget(mark)
        row.addSpacing(8)
        row.addWidget(name)
        row.addSpacing(12)
        row.addWidget(sub)
        row.addStretch(1)
        self.buttons = {}
        for key, glyph, tip in (("settings", "⚙", "Settings (Ctrl+,)"), ("fullscreen", "⛶", "Full screen (F11)"),
                                ("minimize", "–", "Minimise"), ("close", "✕", "Close")):
            btn = QPushButton(glyph)
            btn.setObjectName("closeButton" if key == "close" else "winButton")
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            row.addWidget(btn)
            self.buttons[key] = btn
        self._drag = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def mouseDoubleClickEvent(self, _e):
        w = self.window()
        w.showNormal() if w.isMaximized() else w.showMaximized()


class CommsLog(Panel):
    def __init__(self):
        super().__init__("Comms log")
        self.scroll = QScrollArea()
        self.scroll.setObjectName("logArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(12)
        self.list.addStretch(1)
        self.scroll.setWidget(inner)
        self.layout_.addWidget(self.scroll, 1)
        self.reset_btn = QPushButton("RESET")
        self.reset_btn.setObjectName("ghost")
        self.reset_btn.setToolTip("Start a fresh conversation (memory is kept)")
        self.header.setText(self.header.text())
        head = QHBoxLayout()
        self.layout_.removeWidget(self.header)
        head.addWidget(self.header)
        head.addStretch(1)
        head.addWidget(self.reset_btn)
        self.layout_.insertLayout(0, head)

    COLORS = {"user": theme.AMBER, "jarvis": theme.CYAN, "system": theme.MUTED, "error": theme.RED}

    def add(self, kind: str, who: str, text: str) -> QLabel:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        head = QLabel(f"{who.upper()}    {datetime.now():%H:%M}")
        head.setObjectName("logWho")
        head.setStyleSheet(f"color: {self.COLORS.get(kind, theme.MUTED)};")
        body = QLabel(text)
        body.setObjectName("logText")
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        if kind == "user":
            body.setStyleSheet("color: rgba(255, 220, 170, 0.9);")
        elif kind == "system":
            body.setStyleSheet(f"color: {theme.MUTED}; font-family: '{theme.MONO_FAMILY}'; font-size: 12px;")
        elif kind == "error":
            body.setStyleSheet(f"color: {theme.RED};")
        col.addWidget(head)
        col.addWidget(body)
        theme.apply_spacing(box)
        self.list.insertWidget(self.list.count() - 1, box)
        while self.list.count() > 201:
            old = self.list.takeAt(0).widget()
            if old:
                old.deleteLater()
        QTimer.singleShot(30, self.scroll_to_end)
        return body

    def scroll_to_end(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self):
        while self.list.count() > 1:
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()


class Subsystems(Panel):
    def __init__(self):
        super().__init__("Subsystems")
        self.tags = {}
        for key, label in (("brain", "Neural uplink"), ("ears", "Speech recognition"),
                           ("voice", "Voice synthesis"), ("mic", "Microphone"), ("apps", "App control"),
                           ("memory", "Long-term memory")):
            row = QHBoxLayout()
            name = QLabel(label.upper())
            name.setObjectName("key")
            tag = QLabel("--")
            tag.setObjectName("tag")
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(tag)
            self.layout_.addLayout(row)
            self.tags[key] = tag

    def set(self, key: str, text: str, kind: str = "") -> None:
        tag = self.tags[key]
        tag.setText(text)
        tag.setProperty("kind", kind)
        tag.style().unpolish(tag)
        tag.style().polish(tag)


class BootOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: qradialgradient(cx:0.5, cy:0.45, radius:0.8, fx:0.5, fy:0.45, "
                           "stop:0 rgba(8,40,60,235), stop:0.6 rgba(2,6,12,250), stop:1 rgba(2,6,12,255));")
        col = QVBoxLayout(self)
        col.addStretch(1)
        title = QLabel("J.A.R.V.I.S.")
        title.setObjectName("bootTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("background: transparent;")
        col.addWidget(title)
        col.addSpacing(26)
        self.lines = QVBoxLayout()
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setFixedWidth(560)
        holder.setLayout(self.lines)
        col.addWidget(holder, 0, Qt.AlignHCenter)
        col.addStretch(2)
        theme.apply_spacing(self)

    def add_line(self, label: str) -> QLabel:
        row = QHBoxLayout()
        left = QLabel(label)
        left.setObjectName("bootLine")
        right = QLabel("…")
        right.setObjectName("bootLine")
        for w in (left, right):
            w.setStyleSheet("background: transparent;")
        row.addWidget(left)
        row.addStretch(1)
        row.addWidget(right)
        self.lines.addLayout(row)
        return right

    def fade_out(self):
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(900)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(self.hide)
        anim.start()


class JarvisWindow(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("J.A.R.V.I.S.")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.resize(1440, 900)
        self.setMinimumSize(1024, 640)

        self.bridge = Bridge()
        self.memory = Memory(data_dir() / "memory.db")
        self.memory.start_conversation()
        self.apps = AppCatalog()
        self.apps.refresh_async()
        self.brain = Brain(settings, self.memory, self.apps)
        self.ears = Ears(lambda name, payload: self.bridge.ears.emit(name, payload))
        self.voice = Voice(lambda name, payload: self.bridge.voice.emit(name, payload))
        self.bridge.brain.connect(self.on_brain)
        self.bridge.ears.connect(self.on_ears)
        self.bridge.voice.connect(self.on_voice)

        self.state = "boot"
        self.muted = False
        self.follow_up_until = 0.0
        self.turn_id = 0
        self.turn = None  # dict(id, label, text, brain_done, follow_up)
        self.model_message = ""
        self.ears_error = ""

        self.backdrop = Backdrop()
        self._build_ui()
        self.apply_settings(first=True)

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.reactor.tick)
        self.frame_timer.start(16)
        self.backdrop_timer = QTimer(self)
        self.backdrop_timer.timeout.connect(self.update)
        self.backdrop_timer.start(50)
        QTimer.singleShot(200, self.boot)

    # ---- layout -----------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.title = TitleBar(self)
        root.addWidget(self.title)
        tb = self.title.buttons
        tb["settings"].clicked.connect(self.open_settings)
        tb["fullscreen"].clicked.connect(self.toggle_fullscreen)
        tb["minimize"].clicked.connect(self.showMinimized)
        tb["close"].clicked.connect(self.close)

        body = QHBoxLayout()
        body.setContentsMargins(22, 18, 22, 0)
        body.setSpacing(18)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(14)
        self.clock_panel = ClockPanel()
        self.weather_panel = WeatherPanel()
        self.system_panel = SystemPanel()
        for p in (self.clock_panel, self.weather_panel, self.system_panel):
            left.addWidget(p)
        left.addStretch(1)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(310)
        body.addWidget(left_w)

        center = QVBoxLayout()
        center.setSpacing(10)
        self.reactor = Reactor()
        self.reactor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.reactor.clicked.connect(self.toggle_talk)
        self.reactor.level_source = self._levels
        center.addWidget(self.reactor, 1)
        self.hint = QLabel("CLICK THE REACTOR OR PRESS SPACE TO TALK")
        self.hint.setObjectName("hint")
        self.hint.setAlignment(Qt.AlignCenter)
        center.addWidget(self.hint)
        self.status = QLabel("BOOTING")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        center.addWidget(self.status)
        self.caption = QLabel("")
        self.caption.setObjectName("caption")
        self.caption.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.caption.setWordWrap(True)
        self.caption.setTextFormat(Qt.PlainText)
        self.caption.setMinimumHeight(64)
        center.addWidget(self.caption)
        body.addLayout(center, 1)

        right = QVBoxLayout()
        right.setSpacing(14)
        self.log = CommsLog()
        self.log.reset_btn.clicked.connect(self.reset_conversation)
        self.subsystems = Subsystems()
        right.addWidget(self.log, 1)
        right.addWidget(self.subsystems)
        right_w = QWidget()
        right_w.setLayout(right)
        right_w.setFixedWidth(360)
        body.addWidget(right_w)

        bar = QHBoxLayout()
        bar.setContentsMargins(22, 14, 22, 16)
        bar.setSpacing(14)
        bar.addStretch(1)
        self.wake_btn = QPushButton("WAKE WORD ON")
        self.wake_btn.setObjectName("modeButton")
        self.wake_btn.setCursor(Qt.PointingHandCursor)
        self.wake_btn.clicked.connect(self.toggle_wake_word)
        self.input = QLineEdit()
        self.input.setObjectName("commandInput")
        self.input.setPlaceholderText("Type a command, or say “Jarvis…”")
        self.input.setMaximumWidth(640)
        self.input.setMinimumWidth(360)
        self.input.returnPressed.connect(self.submit_typed)
        self.mic_btn = QPushButton("MIC LIVE")
        self.mic_btn.setObjectName("modeButton")
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.clicked.connect(lambda: self.set_muted(not self.muted))
        bar.addWidget(self.wake_btn)
        bar.addWidget(self.input, 3)
        bar.addWidget(self.mic_btn)
        bar.addStretch(1)
        root.addLayout(bar)

        self.boot_overlay = BootOverlay(self)
        theme.apply_spacing(self)

        QShortcut(QKeySequence("Ctrl+,"), self, self.open_settings)
        QShortcut(QKeySequence("F11"), self, self.toggle_fullscreen)

    def resizeEvent(self, e):
        self.boot_overlay.setGeometry(0, self.title.height(), self.width(), self.height() - self.title.height())
        super().resizeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.backdrop.paint(p, self.width(), self.height())
        p.end()

    def _levels(self):
        if self.voice.speaking:
            return self.voice.level(), None
        if self.muted or not self.ears.mic_ready:
            return 0.0, None
        return min(1.0, self.ears.level * 9), self.ears.spectrum

    # ---- state ----------------------------------------------------------------------

    def in_follow_up(self) -> bool:
        return time.monotonic() < self.follow_up_until

    def set_state(self, state: str, status: str | None = None) -> None:
        self.state = state
        self.reactor.set_state("muted" if state == "standby" and self.muted else state)
        text = status or STATUS_TEXT.get(state, state.upper())
        if state == "standby" and not status:
            if self.muted:
                text = "MICROPHONE MUTED"
            elif self.in_follow_up():
                text = "LISTENING"
            elif self.settings.wake_word:
                text = "STANDBY · SAY “JARVIS”"
        color = theme.STATE_ACCENT.get(state, theme.CYAN)
        self.status.setText(f"●  {text}")
        self.status.setStyleSheet(f"color: {color};")
        self.hint.setVisible(state == "standby")

    def set_caption(self, text: str, who: str = "jarvis") -> None:
        if who == "user" and text:
            self.caption.setStyleSheet(f"color: {theme.CYAN}; font-style: italic;")
            self.caption.setText(f"“{text}”")
        else:
            self.caption.setStyleSheet("")
            self.caption.setText(text)

    def update_subsystems(self) -> None:
        s = self.subsystems
        s.set("brain", "Online" if self.settings.api_key() else "No API key", "ok" if self.settings.api_key() else "bad")
        if self.ears_error:
            s.set("ears", "Offline", "bad")
        elif self.ears.model_ready:
            s.set("ears", "Online", "ok")
        else:
            s.set("ears", "Loading", "warn")
        if self.voice.available:
            name = re.sub(r"^Microsoft\s+", "", self.voice.voice or self.voice.backend.name).split(" - ")[0]
            s.set("voice", name[:18] or "Online", "ok")
        else:
            s.set("voice", "No voices", "warn")
        if not self.ears.mic_ready:
            s.set("mic", "Unavailable", "bad")
        else:
            s.set("mic", "Muted" if self.muted else "Live", "warn" if self.muted else "ok")
        s.set("apps", "Enabled" if self.settings.allow_open_apps else "Disabled", "ok" if self.settings.allow_open_apps else "warn")
        conversations, _messages, notes = self.memory.stats()
        if self.settings.remember_conversations:
            s.set("memory", f"{conversations} chats · {notes} facts", "ok")
        else:
            s.set("memory", "Off", "warn")
        self.wake_btn.setText("WAKE WORD ON" if self.settings.wake_word else "ALWAYS LISTENING")
        self.wake_btn.setProperty("on", "true" if self.settings.wake_word else "")
        mic_on = self.ears.mic_ready and not self.muted
        self.mic_btn.setText("MIC LIVE" if mic_on else "MIC MUTED" if self.muted else "NO MIC")
        self.mic_btn.setProperty("on", "true" if mic_on else "false")
        for b in (self.wake_btn, self.mic_btn):
            b.style().unpolish(b)
            b.style().polish(b)

    # ---- conversation ---------------------------------------------------------------

    def context(self, input_mode: str) -> dict:
        return {
            "local_time": datetime.now().strftime("%A %d %B %Y, %H:%M"),
            "weather": self.weather_panel.summary or None,
            "input_mode": input_mode,
        }

    def ask(self, text: str, input_mode: str) -> None:
        self.interrupt(silent=True)
        self.turn_id += 1
        tid = self.turn_id
        self.follow_up_until = 0
        self.log.add("user", self.settings.user_name or "You", text)
        self.set_caption(text, "user")
        self.turn = {"id": tid, "label": self.log.add("jarvis", "J.A.R.V.I.S.", "…"), "text": "",
                     "brain_done": False, "follow_up": True}
        self.set_state("thinking")
        self.ears.set_paused(True)
        self.voice.begin_stream()
        self.brain.ask(text, self.context(input_mode), lambda e: self.bridge.brain.emit(tid, e))

    def speak_line(self, text: str, log: bool = True, follow_up: bool = False) -> None:
        self.turn_id += 1
        label = self.log.add("jarvis", "J.A.R.V.I.S.", text) if log else None
        self.turn = {"id": self.turn_id, "label": label, "text": text, "brain_done": True, "follow_up": follow_up}
        self.voice.say(text)

    ERROR_LINES = {
        "no_api_key": "I'm afraid my neural uplink is offline. Please add your Anthropic API key in settings.",
        "auth": "My credentials were rejected. Please check the API key in settings.",
        "network": "I can't reach my servers at the moment. Please check the internet connection.",
        "rate_limit": "I'm being rate limited. Give me a moment and try again.",
        "not_found": "That model doesn't appear to exist. Please check the model name in settings.",
    }

    def on_brain(self, tid: int, e: dict) -> None:
        if not self.turn or tid != self.turn["id"]:
            return
        kind = e.get("type")
        if kind == "status":
            if e["status"] != "thinking":
                self.set_state("thinking", e["status"].upper())
        elif kind == "text":
            self.turn["text"] += e["text"]
            self.turn["label"].setText(self.turn["text"].strip() or "…")
            self.log.scroll_to_end()
            self.voice.push_text(e["text"])
        elif kind == "tool":
            self.log.add("system", "System", f"{e['name']} → {str(e['result'])[:160]}")
            if e["name"] in ("remember", "forget"):
                self.update_subsystems()
        elif kind == "error":
            line = self.ERROR_LINES.get(e.get("code"), "I encountered a problem reaching my servers. Details are in the log.")
            self.log.add("error", "Fault", e.get("message") or e.get("code", "error"))
            self.turn["text"] = line
            self.turn["label"].setText(line)
            self.voice.push_text(line)
            self.set_state("error")
            if e.get("code") in ("no_api_key", "auth"):
                QTimer.singleShot(1500, self.open_settings)
        elif kind == "done":
            self.turn["brain_done"] = True
            if not self.turn["text"].strip():
                self.turn["label"].parentWidget().deleteLater()
            self.voice.end_stream()
            self.update_subsystems()

    def on_voice(self, name: str, e: dict) -> None:
        if name == "start":
            self.ears.set_paused(True)
            if self.state != "error":
                self.set_state("speaking")
        elif name == "sentence":
            self.set_caption(e["text"])
        elif name == "idle":
            if e.get("cancelled"):
                return
            if self.turn and not self.turn["brain_done"]:
                return
            self.finish_turn(follow_up=bool(self.turn and self.turn.get("follow_up")))
        elif name == "voices":
            self.voice.set_preferences(self.settings.voice_name, self.settings.voice_rate)
            self.update_subsystems()

    def finish_turn(self, follow_up: bool = False) -> None:
        self.turn = None
        if follow_up and not self.muted:
            self.follow_up_until = time.monotonic() + FOLLOW_UP_S
        self.ears.set_paused(self.muted)
        self.set_state("standby")
        QTimer.singleShot(int(FOLLOW_UP_S * 1000) + 50, self._after_follow_up)

    def _after_follow_up(self):
        if self.state == "standby" and not self.in_follow_up():
            self.set_state("standby")
            if not self.voice.speaking:
                self.set_caption("")

    def interrupt(self, silent: bool = False) -> bool:
        busy = self.turn is not None or self.voice.speaking or self.state == "thinking"
        if not busy:
            return False
        self.turn_id += 1
        self.brain.cancel()
        self.voice.cancel()
        if self.turn and self.turn.get("label") is not None and not self.turn["text"].strip():
            self.turn["label"].parentWidget().deleteLater()
        if silent:
            self.turn = None
        else:
            self.set_caption("")
            self.finish_turn(follow_up=False)
        return True

    def listen_now(self) -> None:
        if not self.ears.mic_ready:
            self.set_caption("No microphone is available. You can still type commands below.")
            return
        if not self.ears.model_ready:
            self.set_caption("Speech recognition is offline." if self.ears_error else "Speech recognition is still loading.")
            return
        if self.muted:
            self.set_muted(False)
        self.ears.listen_now()
        self.set_state("listening")

    def toggle_talk(self) -> None:
        if self.state == "boot":
            return
        if self.interrupt():
            self.listen_now()
            return
        if self.state == "listening" and self.ears.manual:
            self.ears.cancel_manual()
            self.set_state("standby")
            return
        self.listen_now()

    def on_ears(self, name: str, e: dict) -> None:
        if name == "speech-start":
            if self.state in ("standby", "listening") and (e["manual"] or not self.settings.wake_word or self.in_follow_up()):
                self.set_state("listening")
        elif name == "utterance":
            expecting = e["manual"] or not self.settings.wake_word or self.in_follow_up()
            if expecting and self.state in ("standby", "listening"):
                self.set_state("transcribing")
        elif name in ("speech-discarded", "manual-timeout"):
            if self.state == "listening":
                self.set_state("standby")
        elif name == "transcript":
            self.handle_transcript(e["text"], e["manual"])
        elif name == "transcribe-error":
            self.log.add("error", "Fault", f"Speech recognition failed: {e['message']}")
            if self.state == "transcribing":
                self.set_state("standby")
        elif name == "model-progress":
            self.model_message = e.get("message", "")
            self.update_subsystems()
        elif name == "model-ready":
            self.ears_error = ""
            self.update_subsystems()
            if self.state != "boot":
                self.log.add("system", "System", "Speech recognition online.")
        elif name == "model-error":
            self.ears_error = e["message"]
            self.log.add("error", "Fault", f"Speech recognition failed to load: {self.ears_error}")
            self.update_subsystems()

    def handle_transcript(self, text: str, manual: bool) -> None:
        if self.state in ("thinking", "speaking"):
            return
        expecting = manual or not self.settings.wake_word or self.in_follow_up() or self.state == "transcribing"
        has_wake = bool(WAKE_WORD.search(text))
        if not text or (not expecting and not has_wake):
            if self.state in ("transcribing", "listening"):
                self.set_state("standby")
            return
        command = WAKE_WORD.sub(" ", text).strip(" ,.!?") if has_wake else text
        if len(re.sub(r"[^a-z0-9]", "", command, flags=re.I)) < 2:
            self.set_caption(text, "user")
            self.speak_line(random.choice(ACKNOWLEDGEMENTS), log=False, follow_up=True)
            return
        self.ask(command, "voice")

    # ---- controls -------------------------------------------------------------------

    def submit_typed(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.ask(text, "text")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.ears.set_muted(muted)
        if muted:
            self.follow_up_until = 0
        self.update_subsystems()
        if self.state == "standby":
            self.set_state("standby")

    def toggle_wake_word(self) -> None:
        self.settings.wake_word = not self.settings.wake_word
        self.settings.save()
        self.update_subsystems()
        if self.state == "standby":
            self.set_state("standby")

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def reset_conversation(self) -> None:
        self.interrupt(silent=True)
        self.brain.new_session()
        self.log.clear()
        self.log.add("system", "System", "New conversation started. Long-term memory is kept.")
        self.finish_turn(follow_up=False)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.memory, self.voice.voices(), self)
        dialog.setStyleSheet(theme.STYLE)

        def test_voice(name, rate):
            self.voice.set_preferences(name, rate)
            self.speak_line("Good day. All systems are functioning within normal parameters.", log=False)

        dialog.test_voice_requested = test_voice
        had_key = bool(self.settings.api_key())
        prev_model = self.settings.whisper_model
        accepted = dialog.exec()
        if dialog.memory_wiped:
            self.brain.new_session()
            self.log.add("system", "System", "Long-term memory wiped.")
        if accepted:
            self.apply_settings(prev_whisper=prev_model)
            self.log.add("system", "System", "Configuration saved.")
            if not had_key and self.settings.api_key():
                self.speak_line("Neural uplink established. I am fully operational.")
        else:
            self.voice.set_preferences(self.settings.voice_name, self.settings.voice_rate)
        self.update_subsystems()

    def apply_settings(self, first: bool = False, prev_whisper: str | None = None) -> None:
        self.voice.set_preferences(self.settings.voice_name, self.settings.voice_rate)
        self.ears.sensitivity = self.settings.mic_sensitivity
        if not first and prev_whisper and prev_whisper != self.settings.whisper_model:
            self.ears_error = ""
            self.ears.load_model(self.settings.whisper_model)
        self.weather_panel.set_location(self.settings.location)
        if self.state == "standby":
            self.set_state("standby")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if not self.interrupt() and self.state == "listening":
                self.ears.cancel_manual()
                self.set_state("standby")
            return
        if self.state == "boot" or self.input.hasFocus():
            return super().keyPressEvent(e)
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.toggle_talk()
        elif e.key() == Qt.Key_M and not e.modifiers():
            self.set_muted(not self.muted)
        elif e.text() and e.text().isprintable() and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.input.setFocus()
            self.input.setText(self.input.text() + e.text())
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.brain.cancel()
        self.voice.cancel()
        self.ears.stop()
        super().closeEvent(e)

    # ---- boot -------------------------------------------------------------------------

    def boot(self) -> None:
        self._boot_steps = [
            ("Initializing neural interface", lambda: ("OK", False)),
            ("Microphone array", self._boot_mic),
            ("Speech recognition core", self._boot_ears),
            ("Voice synthesis", lambda: ("OK", False) if self.voice.available else ("NO VOICES", True)),
            ("Long-term memory", self._boot_memory),
            ("Application control", lambda: ("OK", False) if self.settings.allow_open_apps else ("DISABLED", True)),
            ("Neural uplink (Claude)", lambda: ("OK", False) if self.settings.api_key() else ("NO API KEY", True)),
            ("All systems", lambda: ("ONLINE", False)),
        ]
        self._boot_next()

    def _boot_mic(self):
        try:
            self.ears.start()
            return "OK", False
        except Exception as exc:  # noqa: BLE001
            print(f"[mic] unavailable: {exc}")
            return "NOT FOUND", True

    def _boot_ears(self):
        self.ears.load_model(self.settings.whisper_model)
        return "LOADING", True

    def _boot_memory(self):
        conversations, _messages, notes = self.memory.stats()
        return f"{conversations} CHATS · {notes} FACTS", False

    def _boot_next(self):
        if not self._boot_steps:
            self._boot_done()
            return
        label, step = self._boot_steps.pop(0)
        value = self.boot_overlay.add_line(label)

        def run():
            text, warn = step()
            value.setText(text)
            value.setStyleSheet(f"background: transparent; color: {theme.AMBER if warn else theme.GREEN};")
            QTimer.singleShot(260, self._boot_next)

        QTimer.singleShot(320, run)

    def _boot_done(self):
        self.boot_overlay.fade_out()
        self.reactor.power = 0.0
        self.set_state("standby")
        self.update_subsystems()
        self.log.add("system", "System", "JARVIS online.")
        QTimer.singleShot(400, self._greet)
        if not self.settings.api_key():
            QTimer.singleShot(1800, self.open_settings)

    def _greet(self):
        if self.turn is None:  # don't talk over a command typed during boot
            self.speak_line(self.greeting())

    def greeting(self) -> str:
        hour = datetime.now().hour
        part = "evening" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        name = self.settings.address_as or self.settings.user_name
        hello = f"Good {part}{', ' + name if name else ''}."
        if not self.settings.api_key():
            return f"{hello} JARVIS online. However, my neural uplink is not configured. Please add your Anthropic API key in settings."
        if not self.ears.mic_ready:
            return f"{hello} JARVIS online. I can't find a microphone, so you'll have to type to me for now."
        back = " Welcome back." if self.memory.stats()[0] > 0 else ""
        tail = " Just say my name when you need me." if self.settings.wake_word else ""
        return f"{hello}{back} JARVIS online and at your service.{tail}"


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    font = QFont(theme.FONT_FAMILY)
    app.setFont(font)
    app.setStyleSheet(theme.STYLE)
    window = JarvisWindow(Settings.load())
    window.show()
    return app.exec()
