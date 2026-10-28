from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel


class VoiceTranscriber:
    """Local speech-to-text using faster-whisper."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

        self._model: WhisperModel | None = None

    @property
    def model(self) -> WhisperModel:
        """Load the Whisper model lazily."""

        if self._model is None:
            print(f"Loading Whisper model: {self.model_size}")
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )

        return self._model

    def transcribe(
        self,
        audio_path: str | Path,
    ) -> str:
        """Transcribe an audio file into plain text."""

        path = Path(audio_path)

        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        segments, _info = self.model.transcribe(
            str(path),
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )

        text = " ".join(
            segment.text.strip()
            for segment in segments
            if segment.text.strip()
        )

        return text.strip()
