"""Tests for recording quality scoring."""

from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest

from meeting_recorder.storage.quality import (
    _analyze_wav,
    _compute_audio_score,
    _score_audio,
    _score_transcript,
    _score_video,
    quality_bar,
    quality_label,
    score_recording,
)


@pytest.fixture
def recording_dir(tmp_path: Path) -> Path:
    """Create a recording directory with sample files."""
    rec = tmp_path / "2026-03-12_10-00-00_TestMeeting"
    rec.mkdir()
    return rec


def _write_wav(path: Path, duration: float = 5.0, rate: int = 16000,
               amplitude: float = 0.3, silence_ratio: float = 0.0) -> None:
    """Write a synthetic WAV file with controlled properties."""
    import math
    n_frames = int(rate * duration)
    silence_frames = int(n_frames * silence_ratio)
    active_frames = n_frames - silence_frames

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)

        # Active portion: 440 Hz sine wave
        data = b""
        for i in range(active_frames):
            sample = int(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / rate))
            data += struct.pack("<h", sample)

        # Silent portion
        data += b"\x00\x00" * silence_frames
        wf.writeframes(data)


def _write_transcript(path: Path, segments: list[dict]) -> None:
    """Write a transcript.json file."""
    with open(path, "w") as f:
        json.dump({"segments": segments}, f)


class TestAnalyzeWav:
    def test_normal_wav(self, tmp_path: Path):
        wav = tmp_path / "test.wav"
        _write_wav(wav, duration=5.0, amplitude=0.3)
        result = _analyze_wav(wav)
        assert result is not None
        assert result["duration"] == 5.0
        assert -20 < result["rms_db"] < -5
        assert result["clip_ratio"] == 0
        assert result["sample_rate"] == 16000

    def test_silent_wav(self, tmp_path: Path):
        wav = tmp_path / "silent.wav"
        _write_wav(wav, duration=3.0, amplitude=0.0001)
        result = _analyze_wav(wav)
        assert result is not None
        assert result["rms_db"] < -60

    def test_loud_wav(self, tmp_path: Path):
        wav = tmp_path / "loud.wav"
        _write_wav(wav, duration=2.0, amplitude=0.95)
        result = _analyze_wav(wav)
        assert result is not None
        assert result["rms_db"] > -10

    def test_nonexistent_wav(self, tmp_path: Path):
        result = _analyze_wav(tmp_path / "nope.wav")
        assert result is None

    def test_empty_wav(self, tmp_path: Path):
        wav = tmp_path / "empty.wav"
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"")
        result = _analyze_wav(wav)
        assert result is None

    def test_mostly_silent(self, tmp_path: Path):
        wav = tmp_path / "mostly_silent.wav"
        _write_wav(wav, duration=10.0, amplitude=0.3, silence_ratio=0.9)
        result = _analyze_wav(wav)
        assert result is not None
        assert result["silence_ratio"] > 0.5


class TestComputeAudioScore:
    def test_good_audio(self):
        metrics = {
            "rms_db": -20.0, "peak_db": -3.0, "clip_ratio": 0.0,
            "silence_ratio": 0.1, "dynamic_range": 17.0,
        }
        assert _compute_audio_score(metrics) >= 90

    def test_very_quiet(self):
        metrics = {
            "rms_db": -55.0, "peak_db": -40.0, "clip_ratio": 0.0,
            "silence_ratio": 0.5, "dynamic_range": 15.0,
        }
        assert _compute_audio_score(metrics) <= 85

    def test_clipped(self):
        metrics = {
            "rms_db": -10.0, "peak_db": 0.0, "clip_ratio": 0.02,
            "silence_ratio": 0.1, "dynamic_range": 10.0,
        }
        assert _compute_audio_score(metrics) <= 70

    def test_mostly_silent(self):
        metrics = {
            "rms_db": -30.0, "peak_db": -10.0, "clip_ratio": 0.0,
            "silence_ratio": 0.95, "dynamic_range": 20.0,
        }
        assert _compute_audio_score(metrics) <= 75

    def test_score_clamped_0_100(self):
        # Even horrible metrics shouldn't go below 0
        metrics = {
            "rms_db": -70.0, "peak_db": -60.0, "clip_ratio": 0.1,
            "silence_ratio": 0.99, "dynamic_range": 50.0,
        }
        assert 0 <= _compute_audio_score(metrics) <= 100


class TestScoreAudio:
    def test_with_app_audio(self, recording_dir: Path):
        _write_wav(recording_dir / "app_audio.wav", amplitude=0.3)
        result = _score_audio(recording_dir)
        assert result["score"] is not None
        assert result["score"] >= 70
        assert "scored_source" in result["details"]

    def test_no_audio_files(self, recording_dir: Path):
        result = _score_audio(recording_dir)
        assert result["score"] is None

    def test_picks_best_source(self, recording_dir: Path):
        # App audio is quiet, mic audio is good
        _write_wav(recording_dir / "app_audio.wav", amplitude=0.001)
        _write_wav(recording_dir / "mic_audio.wav", amplitude=0.3)
        result = _score_audio(recording_dir)
        assert result["score"] >= 70  # should pick the better one


class TestScoreTranscript:
    def test_good_transcript(self, recording_dir: Path):
        segments = []
        for i in range(20):
            segments.append({
                "start": i * 5.0, "end": i * 5.0 + 4.0,
                "text": f"This is segment number {i} with some reasonable text content.",
                "speaker": f"SPEAKER_{i % 3:02d}",
            })
        _write_transcript(recording_dir / "transcript.json", segments)
        result = _score_transcript(recording_dir)
        assert result["score"] is not None
        assert result["score"] >= 80
        assert result["details"]["word_count"] > 100

    def test_empty_segments(self, recording_dir: Path):
        _write_transcript(recording_dir / "transcript.json", [])
        result = _score_transcript(recording_dir)
        assert result["score"] == 0

    def test_very_short_transcript(self, recording_dir: Path):
        _write_transcript(recording_dir / "transcript.json", [
            {"start": 0, "end": 2, "text": "Hello", "speaker": "A"},
        ])
        result = _score_transcript(recording_dir)
        assert result["score"] < 80

    def test_no_transcript_file(self, recording_dir: Path):
        result = _score_transcript(recording_dir)
        assert result["score"] is None

    def test_gappy_transcript(self, recording_dir: Path):
        segments = [
            {"start": 0, "end": 5, "text": "First part with some words here.", "speaker": "A"},
            {"start": 60, "end": 65, "text": "Second part after a long gap.", "speaker": "A"},
            {"start": 120, "end": 125, "text": "Third part after another gap.", "speaker": "A"},
        ]
        _write_transcript(recording_dir / "transcript.json", segments)
        result = _score_transcript(recording_dir)
        assert result["details"]["large_gaps"] == 2


class TestScoreVideo:
    def test_good_video(self, recording_dir: Path):
        # Write 2MB dummy video
        video = recording_dir / "screen.mp4"
        video.write_bytes(b"\x00" * (2 * 1024 * 1024))
        result = _score_video(recording_dir)
        assert result["score"] == 100

    def test_tiny_video(self, recording_dir: Path):
        video = recording_dir / "screen.mp4"
        video.write_bytes(b"\x00" * 50)  # 50 bytes
        result = _score_video(recording_dir)
        assert result["score"] <= 30

    def test_no_video(self, recording_dir: Path):
        result = _score_video(recording_dir)
        assert result["score"] is None


class TestScoreRecording:
    def test_full_recording(self, recording_dir: Path):
        _write_wav(recording_dir / "app_audio.wav", amplitude=0.3)
        segments = [
            {"start": i * 3, "end": i * 3 + 2.5, "text": f"Good segment {i} here.", "speaker": "A"}
            for i in range(30)
        ]
        _write_transcript(recording_dir / "transcript.json", segments)
        video = recording_dir / "screen.mp4"
        video.write_bytes(b"\x00" * (5 * 1024 * 1024))

        scores = score_recording(recording_dir)
        assert scores["overall_score"] >= 70
        assert scores["audio_score"] is not None
        assert scores["transcript_score"] is not None
        assert scores["video_score"] is not None

    def test_audio_only(self, recording_dir: Path):
        _write_wav(recording_dir / "app_audio.wav", amplitude=0.3)
        scores = score_recording(recording_dir)
        assert scores["overall_score"] >= 70
        assert scores["transcript_score"] is None
        assert scores["video_score"] is None

    def test_empty_recording(self, recording_dir: Path):
        scores = score_recording(recording_dir)
        assert scores["overall_score"] == 0


class TestQualityHelpers:
    def test_labels(self):
        assert quality_label(95) == "Excellent"
        assert quality_label(80) == "Good"
        assert quality_label(60) == "Fair"
        assert quality_label(30) == "Poor"
        assert quality_label(10) == "Bad"

    def test_bar(self):
        bar = quality_bar(50, width=10)
        assert len(bar) == 10
        assert "\u2588" in bar
        assert "\u2591" in bar

    def test_bar_full(self):
        bar = quality_bar(100, width=10)
        assert bar == "\u2588" * 10

    def test_bar_empty(self):
        bar = quality_bar(0, width=10)
        assert bar == "\u2591" * 10
