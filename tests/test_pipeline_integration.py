"""Integration test: generate synthetic WAV, run through transcription pipeline.

These tests require faster-whisper (and optionally torch) to be installed.
They are automatically skipped when the dependencies are not available.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import generate_sine_wav, generate_silence_wav

# Skip the entire module if faster-whisper is not available
try:
    from faster_whisper import WhisperModel
    _HAS_FASTER_WHISPER = True
except ImportError:
    _HAS_FASTER_WHISPER = False

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTER_WHISPER,
    reason="faster-whisper not installed",
)

from meeting_recorder.transcription.local_whisper import LocalWhisperTranscriber, TranscriptSegment
from meeting_recorder.storage.transcript_formatter import save_all_formats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_speech_like_wav(path: Path, duration: float = 3.0, sample_rate: int = 16000) -> Path:
    """Generate a WAV file with speech-like characteristics.

    Creates a signal with multiple harmonics and amplitude modulation
    to loosely resemble speech. This is NOT actual speech, but it exercises
    the pipeline code paths.
    """
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # Fundamental + harmonics (roughly vowel-like)
    signal = np.zeros_like(t)
    for freq, amp in [(200, 0.4), (400, 0.3), (800, 0.15), (1200, 0.1)]:
        signal += amp * np.sin(2 * np.pi * freq * t)

    # Amplitude modulation (syllable-like envelope)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t)  # ~3 Hz modulation
    signal *= envelope

    # Normalize and convert to int16
    signal = signal / np.max(np.abs(signal)) * 0.7
    samples = (signal * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    return path


# ---------------------------------------------------------------------------
# LocalWhisperTranscriber tests
# ---------------------------------------------------------------------------

class TestLocalWhisperTranscriber:
    """Test the local whisper transcriber with a tiny model."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.transcriber = LocalWhisperTranscriber(
            model_size="tiny",
            device="cpu",
            compute_type="int8",
            language="en",
        )

    def test_load_model(self):
        """Verify that the model loads without errors."""
        self.transcriber.load()
        assert self.transcriber._model is not None

    def test_transcribe_silence(self):
        """Transcribing silence should produce zero or minimal segments."""
        wav_path = generate_silence_wav(self.tmp_path / "silence.wav", duration=2.0)
        segments = self.transcriber.transcribe(wav_path)
        # Silence should produce very few or no segments
        assert isinstance(segments, list)

    def test_transcribe_returns_segments(self):
        """Transcribing speech-like audio should return TranscriptSegment objects."""
        wav_path = generate_speech_like_wav(self.tmp_path / "speech.wav", duration=3.0)
        segments = self.transcriber.transcribe(wav_path)
        assert isinstance(segments, list)
        for seg in segments:
            assert isinstance(seg, TranscriptSegment)
            assert isinstance(seg.start, float)
            assert isinstance(seg.end, float)
            assert isinstance(seg.text, str)
            assert seg.end >= seg.start

    def test_transcribe_auto_loads_model(self):
        """transcribe() should auto-load the model if not loaded yet."""
        wav_path = generate_silence_wav(self.tmp_path / "auto.wav", duration=1.0)
        assert self.transcriber._model is None
        self.transcriber.transcribe(wav_path)
        assert self.transcriber._model is not None


# ---------------------------------------------------------------------------
# Pipeline integration: transcribe -> format
# ---------------------------------------------------------------------------

class TestTranscriptionPipelineIntegration:
    """End-to-end: generate audio, transcribe, save to all formats."""

    def test_full_pipeline_silence(self, tmp_path: Path):
        """Run the pipeline on silence and verify output files are created."""
        wav_path = generate_silence_wav(tmp_path / "mixed.wav", duration=2.0)

        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu",
            compute_type="int8", language="en",
        )
        segments = transcriber.transcribe(wav_path)

        # Save in all formats
        save_all_formats(segments, tmp_path)

        # All format files should exist (even if content is minimal)
        assert (tmp_path / "transcript.json").exists()
        assert (tmp_path / "transcript.txt").exists()
        assert (tmp_path / "transcript.srt").exists()

        # JSON should be valid
        data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
        assert "segments" in data

    def test_full_pipeline_speech_like(self, tmp_path: Path):
        """Run the pipeline on speech-like audio and verify output structure."""
        wav_path = generate_speech_like_wav(tmp_path / "mixed.wav", duration=3.0)

        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu",
            compute_type="int8", language="en",
        )
        segments = transcriber.transcribe(wav_path)

        # Add fake speaker labels for formatting test
        for i, seg in enumerate(segments):
            seg.speaker = f"Speaker {i % 2}"

        save_all_formats(segments, tmp_path)

        # Verify JSON
        json_data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
        assert isinstance(json_data["segments"], list)

        # Verify TXT
        txt_content = (tmp_path / "transcript.txt").read_text(encoding="utf-8")
        if segments:
            assert "[" in txt_content  # timestamp brackets

        # Verify SRT
        srt_content = (tmp_path / "transcript.srt").read_text(encoding="utf-8")
        if segments:
            assert "-->" in srt_content  # SRT timestamp arrow

    def test_segment_timestamps_monotonic(self, tmp_path: Path):
        """Verify that segment timestamps are non-decreasing."""
        wav_path = generate_speech_like_wav(tmp_path / "test.wav", duration=5.0)

        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu",
            compute_type="int8", language="en",
        )
        segments = transcriber.transcribe(wav_path)

        for i in range(1, len(segments)):
            assert segments[i].start >= segments[i - 1].start, (
                f"Segment {i} starts before segment {i-1}: "
                f"{segments[i].start} < {segments[i-1].start}"
            )
