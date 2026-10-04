"""JARVIS's ears: microphone capture, voice-activity detection, and local
speech-to-text with Whisper (faster-whisper). Audio never leaves the computer.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
FRAME = 512  # 32 ms
FRAME_MS = FRAME * 1000 / SAMPLE_RATE
PRE_ROLL_FRAMES = 10
MIN_SPEECH_MS = 350
MAX_SPEECH_MS = 20000

# Whisper sometimes "hears" these in silence or background noise.
HALLUCINATIONS = {
    "", "you", "thank you", "thanks", "thank you very much", "thanks for watching", "bye",
    "okay", "oh", "uh", "um", "hmm", "so", "the", "i", "a",
}


def clean_transcript(text: str) -> str:
    stripped = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*", " ", text or "")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    bare = re.sub(r"[^a-z' ]", "", stripped.lower()).strip()
    return "" if bare in HALLUCINATIONS else stripped


class Ears:
    """Calls `on_event(name, payload)` from background threads:
    model-progress, model-ready, model-error, speech-start, utterance,
    speech-discarded, manual-timeout, transcript, transcribe-error."""

    def __init__(self, on_event: Callable[[str, dict], None]):
        self.on_event = on_event
        self.sensitivity = 0.5
        self.model_name = "base.en"
        self.model = None
        self.model_ready = False
        self.mic_ready = False
        self.mic_error = ""
        self.paused = False
        self.muted = False
        self.manual = False
        self._manual_deadline = 0.0
        self._noise = 0.003
        self._pre_roll: list[np.ndarray] = []
        self._segment: list[np.ndarray] = []
        self._in_speech = False
        self._loud = 0
        self._silent = 0
        self.level = 0.0
        self.spectrum = np.zeros(48, dtype=np.float32)
        self._frames: queue.Queue = queue.Queue()
        self._jobs: queue.Queue = queue.Queue()
        self._stream = None
        self._model_lock = threading.Lock()
        threading.Thread(target=self._vad_loop, daemon=True, name="vad").start()
        threading.Thread(target=self._stt_loop, daemon=True, name="stt").start()

    # ---- model ------------------------------------------------------------------

    def load_model(self, name: str | None = None) -> None:
        if name:
            self.model_name = name
        self.model_ready = False
        threading.Thread(target=self._load_model, args=(self.model_name,), daemon=True, name="whisper-load").start()

    def _load_model(self, name: str) -> None:
        try:
            from faster_whisper import WhisperModel

            self.on_event("model-progress", {"message": f"Loading Whisper {name}"})
            model = WhisperModel(name, device="cpu", compute_type="int8")
            with self._model_lock:
                if name != self.model_name:
                    return  # a different model was chosen meanwhile
                self.model = model
                self.model_ready = True
            self.on_event("model-ready", {"model": name})
        except Exception as exc:  # noqa: BLE001
            self.on_event("model-error", {"message": str(exc)})

    def transcribe(self, audio: np.ndarray) -> str:
        with self._model_lock:
            model = self.model
        if model is None:
            raise RuntimeError("speech model not loaded")
        segments, _info = model.transcribe(
            audio, language="en" if self.model_name.endswith(".en") else None,
            beam_size=1, condition_on_previous_text=False, vad_filter=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    # ---- microphone -------------------------------------------------------------

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME,
                callback=lambda data, frames, t, status: self._frames.put(data[:, 0].copy()),
            )
            self._stream.start()
            self.mic_ready = True
        except Exception as exc:  # noqa: BLE001
            self.mic_error = str(exc)
            self.mic_ready = False
            raise

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def feed(self, audio: np.ndarray) -> None:
        """Push raw 16 kHz audio as if it came from the microphone (tests)."""
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            self._frames.put(audio[i:i + FRAME].astype(np.float32))

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self._reset()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused and not self.manual:
            self._reset()

    def listen_now(self) -> None:
        """Push-to-talk: record the next utterance whatever the wake word."""
        self.manual = True
        self.paused = False
        self._reset()
        self._manual_deadline = time.monotonic() + 8

    def cancel_manual(self) -> None:
        self.manual = False

    # ---- voice activity detection ------------------------------------------------

    def _reset(self) -> None:
        self._in_speech = False
        self._segment = []
        self._loud = 0
        self._silent = 0

    def _threshold(self) -> float:
        s = min(1.0, max(0.0, self.sensitivity))
        ratio = 4.2 - s * 2.6
        minimum = 0.018 - s * 0.014
        return max(minimum, self._noise * ratio)

    def _vad_loop(self) -> None:
        window = np.hanning(FRAME).astype(np.float32)
        while True:
            frame = self._frames.get()
            rms = float(np.sqrt(np.mean(frame * frame)))
            self.level = rms
            mags = np.abs(np.fft.rfft(frame * window))[:192]
            bands = mags.reshape(48, 4).mean(axis=1)
            self.spectrum = np.clip(np.log1p(bands * 4) / 3.0, 0, 1).astype(np.float32)
            if self.muted:
                continue
            if self.manual and not self._in_speech and time.monotonic() > self._manual_deadline:
                self.manual = False
                self.on_event("manual-timeout", {})
            if self.paused and not self.manual:
                continue
            loud = rms > self._threshold()

            if not self._in_speech:
                self._noise = self._noise * 0.97 + min(rms, 0.05) * 0.03
                self._pre_roll.append(frame)
                if len(self._pre_roll) > PRE_ROLL_FRAMES:
                    self._pre_roll.pop(0)
                self._loud = self._loud + 1 if loud else 0
                if self._loud >= 3:
                    self._in_speech = True
                    self._segment = list(self._pre_roll)
                    self._pre_roll = []
                    self._silent = 0
                    self.on_event("speech-start", {"manual": self.manual})
                continue

            self._segment.append(frame)
            self._silent = 0 if loud else self._silent + 1
            silence_limit = (1100 if self.manual else 750) / FRAME_MS
            duration = len(self._segment) * FRAME_MS
            if self._silent >= silence_limit or duration >= MAX_SPEECH_MS:
                frames, manual = self._segment, self.manual
                speech_ms = duration - self._silent * FRAME_MS
                self._reset()
                self.manual = False
                if speech_ms < MIN_SPEECH_MS:
                    self.on_event("speech-discarded", {"manual": manual})
                    continue
                self._jobs.put((np.concatenate(frames), manual))
                self.on_event("utterance", {"manual": manual})

    def _stt_loop(self) -> None:
        while True:
            audio, manual = self._jobs.get()
            try:
                started = time.monotonic()
                text = clean_transcript(self.transcribe(audio))
                self.on_event("transcript", {"text": text, "manual": manual, "seconds": time.monotonic() - started})
            except Exception as exc:  # noqa: BLE001
                self.on_event("transcribe-error", {"message": str(exc), "manual": manual})
