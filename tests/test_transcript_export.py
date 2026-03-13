"""Tests for transcript export formats."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.transcript_export import export_transcript


def _make_transcript(base: Path, segments: list[dict]) -> Path:
    d = base / "2026-03-10_09-00-00_Test"
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8",
    )
    return d


SEGMENTS = [
    {"speaker": "Alice", "start": 0.0, "end": 5.5, "text": "Hello everyone"},
    {"speaker": "Bob", "start": 6.0, "end": 10.2, "text": "Hi there"},
]


class TestExportTxt:
    def test_basic(self, tmp_path):
        rec = _make_transcript(tmp_path, SEGMENTS)
        txt = export_transcript(rec, "txt")
        assert "[00:00:00 - 00:00:05]" in txt
        assert "Alice:" in txt
        assert "Hello everyone" in txt
        assert "Bob:" in txt

    def test_no_speaker(self, tmp_path):
        rec = _make_transcript(tmp_path, [
            {"start": 0, "end": 5, "text": "No speaker"}
        ])
        txt = export_transcript(rec, "txt")
        assert "No speaker" in txt
        assert ":" not in txt.split("]")[1].split("No")[0].strip()


class TestExportSrt:
    def test_basic(self, tmp_path):
        rec = _make_transcript(tmp_path, SEGMENTS)
        srt = export_transcript(rec, "srt")
        assert "1\n" in srt
        assert "2\n" in srt
        assert "-->" in srt
        assert "[Alice]" in srt
        assert "00:00:00,000" in srt
        assert "00:00:05,500" in srt

    def test_milliseconds(self, tmp_path):
        rec = _make_transcript(tmp_path, [
            {"speaker": "A", "start": 1.0, "end": 2.5, "text": "hi"},
        ])
        srt = export_transcript(rec, "srt")
        assert "00:00:01,000" in srt
        assert "00:00:02,500" in srt


class TestExportVtt:
    def test_basic(self, tmp_path):
        rec = _make_transcript(tmp_path, SEGMENTS)
        vtt = export_transcript(rec, "vtt")
        assert "WEBVTT" in vtt
        assert "-->" in vtt
        assert "<v Alice>" in vtt
        assert "00:00:00.000" in vtt

    def test_no_speaker(self, tmp_path):
        rec = _make_transcript(tmp_path, [
            {"start": 0, "end": 1, "text": "test"},
        ])
        vtt = export_transcript(rec, "vtt")
        assert "<v " not in vtt
        assert "test" in vtt


class TestFallbacks:
    def test_no_transcript_json(self, tmp_path):
        d = tmp_path / "2026-03-10_09-00-00_Test"
        d.mkdir()
        (d / "transcript.txt").write_text("Plain text transcript")
        result = export_transcript(d, "txt")
        assert "Plain text transcript" in result

    def test_no_files(self, tmp_path):
        d = tmp_path / "2026-03-10_09-00-00_Test"
        d.mkdir()
        assert export_transcript(d) == ""

    def test_empty_segments(self, tmp_path):
        rec = _make_transcript(tmp_path, [])
        assert export_transcript(rec) == ""
