"""Shared fixtures for meeting_recorder tests."""

from __future__ import annotations

import struct
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from meeting_recorder.transcription.local_whisper import TranscriptSegment


# ---------------------------------------------------------------------------
# Temporary directory helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def recording_dir(tmp_path: Path) -> Path:
    """Create and return a temporary recording directory."""
    d = tmp_path / "recording_001"
    d.mkdir()
    return d


@pytest.fixture
def base_recordings_dir(tmp_path: Path) -> Path:
    """Create and return a temporary base recordings directory."""
    d = tmp_path / "MeetingRecordings"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Audio generation helpers
# ---------------------------------------------------------------------------

def generate_sine_wav(
    path: Path,
    frequency: float = 440.0,
    duration: float = 1.0,
    sample_rate: int = 16000,
    channels: int = 1,
    amplitude: float = 0.5,
) -> Path:
    """Generate a WAV file containing a sine wave.

    Args:
        path: Output file path.
        frequency: Sine wave frequency in Hz.
        duration: Duration in seconds.
        sample_rate: Sample rate in Hz.
        channels: Number of audio channels.
        amplitude: Amplitude as a fraction of int16 max (0.0 - 1.0).

    Returns:
        The path to the created WAV file.
    """
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    samples = (amplitude * 32767 * np.sin(2 * np.pi * frequency * t)).astype(np.int16)

    # If multi-channel, duplicate the mono signal
    if channels > 1:
        samples = np.column_stack([samples] * channels).flatten()

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    return path


def generate_silence_wav(
    path: Path,
    duration: float = 1.0,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """Generate a WAV file containing silence."""
    return generate_sine_wav(
        path, frequency=0.0, duration=duration,
        sample_rate=sample_rate, channels=channels, amplitude=0.0,
    )


@pytest.fixture
def sine_wav_factory(tmp_path: Path):
    """Factory fixture that creates sine wave WAV files on demand."""
    counter = 0

    def _make(
        frequency: float = 440.0,
        duration: float = 1.0,
        sample_rate: int = 16000,
        channels: int = 1,
        amplitude: float = 0.5,
        filename: str | None = None,
    ) -> Path:
        nonlocal counter
        if filename is None:
            counter += 1
            filename = f"sine_{counter}.wav"
        path = tmp_path / filename
        return generate_sine_wav(
            path, frequency=frequency, duration=duration,
            sample_rate=sample_rate, channels=channels, amplitude=amplitude,
        )

    return _make


# ---------------------------------------------------------------------------
# Transcript segment helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_segments() -> list[TranscriptSegment]:
    """Return a list of sample transcript segments for testing."""
    return [
        TranscriptSegment(start=0.0, end=2.5, text="Hello everyone.", speaker="User"),
        TranscriptSegment(start=3.0, end=5.2, text="Hi there!", speaker="Participant 1"),
        TranscriptSegment(start=5.5, end=8.0, text="Let's get started.", speaker="User"),
        TranscriptSegment(
            start=8.5, end=12.0,
            text="Sure, I have the slides ready.",
            speaker="Participant 2",
        ),
    ]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_config_toml(tmp_path: Path) -> Path:
    """Write a minimal TOML config file and return its path."""
    content = """\
[recording]
output_dir = "~/TestRecordings"
language = "en"
user_name = "TestUser"

[audio]
sample_rate = 16000
channels = 1
chunk_duration_ms = 30
mic_device = ""

[vad]
threshold = 0.5
min_speech_duration_ms = 250
min_silence_duration_ms = 300

[transcription]
backend = "local"
model_size = "tiny"
device = "cpu"
compute_type = "int8"
openai_api_key = ""

[diarization]
enabled = false
huggingface_token = ""
min_speakers = 2
max_speakers = 6

[output]
formats = ["json", "txt", "srt"]

[hotkey]
toggle_recording = "ctrl+shift+r"
toggle_mute = "ctrl+shift+u"

[screen_recording]
enabled = true
fps = 5.0
"""
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path
