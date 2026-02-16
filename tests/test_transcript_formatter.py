"""Tests for transcript formatting (JSON, TXT, SRT)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.transcription.local_whisper import TranscriptSegment
from meeting_recorder.storage.transcript_formatter import (
    save_transcript_json,
    save_transcript_txt,
    save_transcript_srt,
    save_all_formats,
    _format_timestamp_txt,
    _format_timestamp_srt,
)


# ---------------------------------------------------------------------------
# Timestamp formatting helpers
# ---------------------------------------------------------------------------

class TestTimestampFormatting:
    """Test the internal timestamp formatting functions."""

    def test_txt_format_zero(self):
        assert _format_timestamp_txt(0.0) == "00:00:00"

    def test_txt_format_seconds(self):
        assert _format_timestamp_txt(45.0) == "00:00:45"

    def test_txt_format_minutes(self):
        assert _format_timestamp_txt(125.0) == "00:02:05"

    def test_txt_format_hours(self):
        assert _format_timestamp_txt(3661.0) == "01:01:01"

    def test_srt_format_zero(self):
        assert _format_timestamp_srt(0.0) == "00:00:00,000"

    def test_srt_format_with_milliseconds(self):
        assert _format_timestamp_srt(2.5) == "00:00:02,500"

    def test_srt_format_hours(self):
        assert _format_timestamp_srt(3723.456) == "01:02:03,456"

    def test_srt_format_precise_millis(self):
        assert _format_timestamp_srt(1.123) == "00:00:01,123"


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

class TestJsonOutput:
    """Test save_transcript_json()."""

    def test_json_structure(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        path = tmp_path / "transcript.json"
        save_transcript_json(sample_segments, path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "segments" in data
        assert len(data["segments"]) == 4

    def test_json_segment_fields(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        path = tmp_path / "transcript.json"
        save_transcript_json(sample_segments, path)

        data = json.loads(path.read_text(encoding="utf-8"))
        seg = data["segments"][0]
        assert seg["start"] == 0.0
        assert seg["end"] == 2.5
        assert seg["text"] == "Hello everyone."
        assert seg["speaker"] == "User"

    def test_json_empty_segments(self, tmp_path: Path):
        path = tmp_path / "transcript.json"
        save_transcript_json([], path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["segments"] == []

    def test_json_unicode(self, tmp_path: Path):
        segments = [
            TranscriptSegment(start=0.0, end=1.0, text="Bonjour le monde", speaker="Pierre"),
        ]
        path = tmp_path / "transcript.json"
        save_transcript_json(segments, path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["segments"][0]["text"] == "Bonjour le monde"


# ---------------------------------------------------------------------------
# TXT output
# ---------------------------------------------------------------------------

class TestTxtOutput:
    """Test save_transcript_txt()."""

    def test_txt_format(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        path = tmp_path / "transcript.txt"
        save_transcript_txt(sample_segments, path)

        content = path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 4
        assert lines[0] == "[00:00:00 - 00:00:02] User: Hello everyone."
        assert lines[1] == "[00:00:03 - 00:00:05] Participant 1: Hi there!"

    def test_txt_unknown_speaker(self, tmp_path: Path):
        segments = [
            TranscriptSegment(start=0.0, end=1.0, text="Mystery voice", speaker=""),
        ]
        path = tmp_path / "transcript.txt"
        save_transcript_txt(segments, path)

        content = path.read_text(encoding="utf-8")
        assert "Unknown: Mystery voice" in content

    def test_txt_empty_segments(self, tmp_path: Path):
        path = tmp_path / "transcript.txt"
        save_transcript_txt([], path)
        content = path.read_text(encoding="utf-8")
        assert content.strip() == ""


# ---------------------------------------------------------------------------
# SRT output
# ---------------------------------------------------------------------------

class TestSrtOutput:
    """Test save_transcript_srt()."""

    def test_srt_structure(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        path = tmp_path / "transcript.srt"
        save_transcript_srt(sample_segments, path)

        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # First subtitle block
        assert lines[0] == "1"
        assert lines[1] == "00:00:00,000 --> 00:00:02,500"
        assert lines[2] == "[User] Hello everyone."
        assert lines[3] == ""  # blank separator

        # Second subtitle block
        assert lines[4] == "2"
        assert lines[5] == "00:00:03,000 --> 00:00:05,200"
        assert lines[6] == "[Participant 1] Hi there!"

    def test_srt_numbering(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        path = tmp_path / "transcript.srt"
        save_transcript_srt(sample_segments, path)

        content = path.read_text(encoding="utf-8")
        # Check all subtitle indices are present
        for i in range(1, len(sample_segments) + 1):
            assert f"\n{i}\n" in content or content.startswith(f"{i}\n")

    def test_srt_empty_segments(self, tmp_path: Path):
        path = tmp_path / "transcript.srt"
        save_transcript_srt([], path)
        content = path.read_text(encoding="utf-8")
        assert content.strip() == ""


# ---------------------------------------------------------------------------
# save_all_formats
# ---------------------------------------------------------------------------

class TestSaveAllFormats:
    """Test save_all_formats() convenience function."""

    def test_all_formats(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        save_all_formats(sample_segments, tmp_path)
        assert (tmp_path / "transcript.json").exists()
        assert (tmp_path / "transcript.txt").exists()
        assert (tmp_path / "transcript.srt").exists()

    def test_selected_formats(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        save_all_formats(sample_segments, tmp_path, formats=["json", "srt"])
        assert (tmp_path / "transcript.json").exists()
        assert not (tmp_path / "transcript.txt").exists()
        assert (tmp_path / "transcript.srt").exists()

    def test_unknown_format_ignored(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        # Should not raise
        save_all_formats(sample_segments, tmp_path, formats=["json", "xml"])
        assert (tmp_path / "transcript.json").exists()

    def test_empty_formats_list(self, sample_segments: list[TranscriptSegment], tmp_path: Path):
        save_all_formats(sample_segments, tmp_path, formats=[])
        assert not (tmp_path / "transcript.json").exists()
        assert not (tmp_path / "transcript.txt").exists()
        assert not (tmp_path / "transcript.srt").exists()
