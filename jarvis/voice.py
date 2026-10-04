"""JARVIS's voice: text-to-speech with the operating system's own voices.

Windows uses SAPI (through pywin32), macOS uses `say`, Linux uses
`espeak-ng`/`espeak`. Text streams in from Claude and each finished sentence
is spoken as soon as it arrives.
"""

from __future__ import annotations

import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

PREFERRED = [r"George", r"Daniel", r"Ryan", r"Arthur", r"Oliver", r"Hazel", r"en[-_]gb", r"English \(United Kingdom\)", r"David"]


def clean_for_speech(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"[*_#`>~|]", "", text)
    return re.sub(r"\s+", " ", text).strip()


_BOUNDARY = re.compile(r"([.!?…]+[\"')\]]?)(\s+)|(\n+)")
_ABBREV = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|vs|etc|e\.g|i\.e|approx|No)\.$", re.I)


def take_sentences(buffer: str, final: bool = False) -> tuple[list[str], str]:
    sentences: list[str] = []
    last = 0
    for m in _BOUNDARY.finditer(buffer):
        end = m.start() + (len(m.group(1)) if m.group(1) else 0)
        candidate = buffer[last:end].strip()
        if m.group(1) and _ABBREV.search(candidate):
            continue
        if len(candidate) >= 2:
            sentences.append(candidate)
        last = m.end()
    rest = buffer[last:]
    if final and rest.strip():
        sentences.append(rest.strip())
        rest = ""
    return sentences, rest


class _Backend:
    name = "none"

    def voices(self) -> list[str]:
        return []

    def speak(self, text: str, voice: str, rate: float, cancelled: threading.Event, on_word: Callable[[], None]) -> None:
        # No speech engine: pace the captions by reading speed.
        end = time.monotonic() + 0.4 + len(text) * 0.055 / max(rate, 0.5)
        while time.monotonic() < end and not cancelled.is_set():
            time.sleep(0.05)


class _SapiBackend(_Backend):
    name = "Windows SAPI"

    def __init__(self):
        import pythoncom  # noqa: F401  (pywin32)
        import win32com.client  # noqa: F401
        self._voice = None
        self._tokens: dict[str, object] = {}

    def _ensure(self):
        if self._voice is None:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            tokens = self._voice.GetVoices()
            self._tokens = {tokens.Item(i).GetDescription(): tokens.Item(i) for i in range(tokens.Count)}
        return self._voice

    def voices(self) -> list[str]:
        self._ensure()
        return list(self._tokens)

    def speak(self, text, voice, rate, cancelled, on_word):
        sp = self._ensure()
        if voice in self._tokens:
            sp.Voice = self._tokens[voice]
        sp.Rate = max(-10, min(10, round((rate - 1.0) * 10)))
        sp.Speak(text, 1)  # SVSFlagsAsync
        last_word = -1
        while not sp.WaitUntilDone(30):
            if cancelled.is_set():
                sp.Speak("", 3)  # SVSFlagsAsync | SVSFPurgeBeforeSpeak: stop now
                return
            pos = sp.Status.InputWordPosition
            if pos != last_word:
                last_word = pos
                on_word()


class _ProcessBackend(_Backend):
    """macOS `say` or Linux espeak: one process per sentence, killed to interrupt."""

    def __init__(self, exe: str):
        self.exe = exe
        self.name = exe
        self._voice_cache: list[str] | None = None

    def voices(self) -> list[str]:
        if self._voice_cache is None:
            try:
                if self.exe == "say":
                    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
                    self._voice_cache = [line.split("  ")[0].strip() for line in out.splitlines() if line.strip()]
                else:
                    out = subprocess.run([self.exe, "--voices=en"], capture_output=True, text=True, timeout=10).stdout
                    self._voice_cache = [line.split()[4] for line in out.splitlines()[1:] if len(line.split()) > 4]
            except (OSError, subprocess.SubprocessError, IndexError):
                self._voice_cache = []
        return self._voice_cache

    def speak(self, text, voice, rate, cancelled, on_word):
        if self.exe == "say":
            cmd = ["say", "-r", str(int(185 * rate))]
            if voice:
                cmd += ["-v", voice]
        else:
            cmd = [self.exe, "-s", str(int(165 * rate))]
            if voice:
                cmd += ["-v", voice]
        proc = subprocess.Popen(cmd + [text.lstrip("-")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while proc.poll() is None:
            if cancelled.is_set():
                proc.terminate()
                return
            on_word()  # no word timing available: keep the visualiser lively
            time.sleep(0.12)


def _make_backend() -> _Backend:
    if sys.platform == "win32":
        try:
            return _SapiBackend()
        except ImportError:
            pass
    elif sys.platform == "darwin" and shutil.which("say"):
        return _ProcessBackend("say")
    for exe in ("espeak-ng", "espeak"):
        if shutil.which(exe):
            return _ProcessBackend(exe)
    return _Backend()


class Voice:
    """Calls `on_event(name, payload)` from its thread: start, sentence, idle."""

    def __init__(self, on_event: Callable[[str, dict], None]):
        self.on_event = on_event
        self.backend = _make_backend()
        self.preferred = ""
        self.voice = ""
        self.rate = 1.0
        self.speaking = False
        self._queue: queue.Queue = queue.Queue()
        self._buffer = ""
        self._stream_open = False
        self._generation = 0
        self._cancelled = threading.Event()
        self._pulse_at = 0.0
        self._lock = threading.Lock()
        self._voices: list[str] = []
        threading.Thread(target=self._loop, daemon=True, name="voice").start()
        self._queue.put(("init", None, 0))

    @property
    def available(self) -> bool:
        return self.backend.name != "none"

    def voices(self) -> list[str]:
        return list(self._voices)

    def set_preferences(self, voice_name: str, rate: float) -> None:
        self.preferred = voice_name
        self.rate = rate or 1.0
        self.voice = self._pick(voice_name)

    def _pick(self, preferred: str) -> str:
        if preferred and preferred in self._voices:
            return preferred
        for pattern in PREFERRED:
            for v in self._voices:
                if re.search(pattern, v, re.I):
                    return v
        return ""

    # ---- streaming text -----------------------------------------------------------

    def begin_stream(self) -> None:
        self.cancel()
        with self._lock:
            self._stream_open = True
            self._buffer = ""

    def push_text(self, text: str) -> None:
        with self._lock:
            self._buffer += text
            sentences, self._buffer = take_sentences(self._buffer)
            gen = self._generation
        for s in sentences:
            self._enqueue(s, gen)

    def end_stream(self) -> None:
        with self._lock:
            sentences, _ = take_sentences(self._buffer, final=True)
            self._buffer = ""
            self._stream_open = False
            gen = self._generation
        for s in sentences:
            self._enqueue(s, gen)
        self._queue.put(("check", None, gen))

    def say(self, text: str) -> None:
        self.begin_stream()
        self.push_text(text)
        self.end_stream()

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            self._buffer = ""
            self._stream_open = False
            self._cancelled.set()
            was = self.speaking
            self.speaking = False
        if was:
            self.on_event("idle", {"cancelled": True})

    def level(self) -> float:
        """0..1 loudness estimate for the HUD (speech engines expose no audio)."""
        if not self.speaking:
            return 0.0
        since = time.monotonic() - self._pulse_at
        decay = max(0.0, 1 - since * 3.5)
        flutter = 0.25 + 0.15 * abs(((time.monotonic() * 14) % 2) - 1)
        return min(1.0, flutter + decay * 0.6)

    def _enqueue(self, sentence: str, gen: int) -> None:
        text = clean_for_speech(sentence)
        if text:
            self._queue.put(("say", text, gen))

    def _pulse(self) -> None:
        self._pulse_at = time.monotonic()

    def _loop(self) -> None:
        while True:
            kind, text, gen = self._queue.get()
            if kind == "init":
                try:
                    self._voices = self.backend.voices()
                except Exception as exc:  # noqa: BLE001
                    print(f"[voice] could not list voices: {exc}")
                    self._voices = []
                self.voice = self._pick(self.preferred)
                self.on_event("voices", {"voices": self._voices})
                continue
            if gen != self._generation:
                continue  # stale (cancelled) item
            if kind == "say":
                if not self.speaking:
                    self.speaking = True
                    self.on_event("start", {})
                self.on_event("sentence", {"text": text})
                self._cancelled.clear()
                try:
                    self.backend.speak(text, self.voice, self.rate, self._cancelled, self._pulse)
                except Exception as exc:  # noqa: BLE001
                    print(f"[voice] speech failed: {exc}")
                    _Backend().speak(text, "", self.rate, self._cancelled, self._pulse)
            # After each item: are we done with this reply?
            with self._lock:
                done = gen == self._generation and not self._stream_open and self._queue.empty()
            if done:
                was = self.speaking
                self.speaking = False
                self.on_event("idle", {"cancelled": False, "silent": not was})
