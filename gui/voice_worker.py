"""Background voice capture for the Speak button.

VoiceWorker lives on its own QThread for the whole session. The window emits a
request; the worker records VOICE_DURATION seconds with VoiceRecorder, runs the
shared VoiceTranscriber (the Whisper model loads lazily on first use, on this
thread, so the GUI never freezes) and reports back through signals, which Qt
delivers on the GUI thread. The worker never touches widgets.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from voice.recorder import VoiceRecorder
from voice.transcriber import VoiceTranscriber

VOICE_DURATION = 5  # seconds of audio recorded per Speak click

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "scratch" / "voice"


class VoiceWorker(QObject):
    """record -> transcribe -> emit result. One request at a time."""

    transcribing = Signal()
    transcribed = Signal(str)  # may be "" when no speech was detected
    recording_failed = Signal(str)
    transcription_failed = Signal(str)
    cancelled = Signal()       # Stop was pressed: the recording/transcript is discarded
    finished = Signal()  # always emitted last, success or failure

    def __init__(self) -> None:
        super().__init__()
        self._recorder = VoiceRecorder()
        self._transcriber = VoiceTranscriber()  # reused, so the model loads once
        self._cancel = threading.Event()

    def prepare(self) -> None:
        """Clear a previous cancel request (called by the window before each capture)."""
        self._cancel.clear()

    def cancel(self) -> None:
        """Thread-safe. The fixed-length recording is not cut short, but transcription
        is skipped (or its result discarded) and nothing reaches the command box."""
        self._cancel.set()

    @Slot()
    def capture(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        wav_path = RECORDINGS_DIR / f"voice_{stamp}.wav"
        try:
            try:
                self._recorder.record(VOICE_DURATION, wav_path)
            except Exception as exc:
                self.recording_failed.emit(f"{type(exc).__name__}: {exc}")
                return
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self.transcribing.emit()
            try:
                text = self._transcriber.transcribe(wav_path)
            except Exception as exc:
                self.transcription_failed.emit(f"{type(exc).__name__}: {exc}")
                return
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            self.transcribed.emit(text.strip())
        finally:
            _delete_quietly(wav_path)
            self.finished.emit()


def _delete_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
