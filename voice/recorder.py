from __future__ import annotations

from pathlib import Path

import sounddevice as sd
from scipy.io.wavfile import write


class VoiceRecorder:
    """Record microphone audio to a WAV file."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

    def record(
        self,
        duration: float,
        output_path: str | Path,
    ) -> Path:
        """Record audio for a fixed duration and save it as WAV."""

        if duration <= 0:
            raise ValueError("duration must be greater than 0")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        audio = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=self.device,
        )

        sd.wait()
        write(str(output), self.sample_rate, audio)

        return output
