"""Tests for mic-track-based user speaker attribution."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from meeting_recorder.transcription.local_whisper import TranscriptSegment
from meeting_recorder.transcription import mic_attribution


def _write_mic_wav(
    path: Path,
    active_windows: list[tuple[float, float]],
    duration: float,
    sample_rate: int = 16000,
) -> Path:
    """Write a mic WAV that is loud during active_windows, silent elsewhere."""
    n = int(duration * sample_rate)
    samples = np.zeros(n, dtype=np.int16)
    rng = np.arange(n)
    t = rng / sample_rate
    for start, end in active_windows:
        mask = (t >= start) & (t < end)
        # Loud speech-like signal in the active window
        samples[mask] = (8000 * np.sin(2 * np.pi * 180 * t[mask])).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return path


def _seg(start, end, text, speaker):
    return TranscriptSegment(start=start, end=end, text=text, speaker=speaker)


class TestIdentifyUserSpeaker:
    def test_identifies_speaker_matching_mic(self, tmp_path):
        """Speaker 2 talks exactly when the mic is active -> identified."""
        mic = _write_mic_wav(
            tmp_path / "mic.wav",
            active_windows=[(5.0, 10.0), (15.0, 20.0)],
            duration=20.0,
        )
        segments = [
            _seg(0.0, 5.0, "hello team", "Speaker 1"),
            _seg(5.0, 10.0, "hi there, my update is", "Speaker 2"),  # user
            _seg(10.0, 15.0, "thanks for that", "Speaker 1"),
            _seg(15.0, 20.0, "one more thing", "Speaker 2"),  # user
        ]
        match = mic_attribution.identify_user_speaker(segments, mic, 20.0)
        assert match == "Speaker 2"

    def test_silent_mic_returns_none(self, tmp_path):
        """A muted/silent mic track gives no attribution."""
        mic = _write_mic_wav(tmp_path / "mic.wav", active_windows=[], duration=20.0)
        segments = [
            _seg(0.0, 10.0, "a", "Speaker 1"),
            _seg(10.0, 20.0, "b", "Speaker 2"),
        ]
        assert mic_attribution.identify_user_speaker(segments, mic, 20.0) is None

    def test_ambiguous_overlap_returns_none(self, tmp_path):
        """If the mic overlaps both speakers equally, don't guess."""
        mic = _write_mic_wav(
            tmp_path / "mic.wav",
            active_windows=[(0.0, 20.0)],  # active the whole time
            duration=20.0,
        )
        segments = [
            _seg(0.0, 10.0, "a", "Speaker 1"),
            _seg(10.0, 20.0, "b", "Speaker 2"),
        ]
        # Both speakers overlap mic ~100%; margin too small -> None
        assert mic_attribution.identify_user_speaker(segments, mic, 20.0) is None

    def test_missing_file_returns_none(self, tmp_path):
        segments = [_seg(0.0, 5.0, "a", "Speaker 1")]
        result = mic_attribution.identify_user_speaker(
            segments, tmp_path / "nope.wav", 5.0
        )
        assert result is None

    def test_empty_segments_returns_none(self, tmp_path):
        mic = _write_mic_wav(tmp_path / "mic.wav", [(0.0, 5.0)], 5.0)
        assert mic_attribution.identify_user_speaker([], mic, 5.0) is None


class TestAttributeUser:
    def test_relabels_in_place(self, tmp_path):
        mic = _write_mic_wav(
            tmp_path / "mic.wav",
            active_windows=[(5.0, 10.0), (15.0, 20.0)],
            duration=20.0,
        )
        segments = [
            _seg(0.0, 5.0, "hello", "Speaker 1"),
            _seg(5.0, 10.0, "my turn", "Speaker 2"),
            _seg(15.0, 20.0, "again", "Speaker 2"),
        ]
        renamed = mic_attribution.attribute_user(segments, mic, "Bobby", 20.0)
        assert renamed == "Speaker 2"
        assert segments[1].speaker == "Bobby"
        assert segments[2].speaker == "Bobby"
        assert segments[0].speaker == "Speaker 1"  # other speaker untouched

    def test_no_username_is_noop(self, tmp_path):
        mic = _write_mic_wav(tmp_path / "mic.wav", [(0.0, 5.0)], 5.0)
        segments = [_seg(0.0, 5.0, "a", "Speaker 1")]
        assert mic_attribution.attribute_user(segments, mic, "", 5.0) is None
        assert segments[0].speaker == "Speaker 1"

    def test_missing_mic_is_noop(self, tmp_path):
        segments = [_seg(0.0, 5.0, "a", "Speaker 1")]
        result = mic_attribution.attribute_user(
            segments, tmp_path / "nope.wav", "Bobby", 5.0
        )
        assert result is None
        assert segments[0].speaker == "Speaker 1"

    def test_never_raises_on_garbage(self, tmp_path):
        bad = tmp_path / "bad.wav"
        bad.write_bytes(b"not a wav file")
        segments = [_seg(0.0, 5.0, "a", "Speaker 1")]
        # Must degrade gracefully, not raise
        assert mic_attribution.attribute_user(segments, bad, "Bobby", 5.0) is None
