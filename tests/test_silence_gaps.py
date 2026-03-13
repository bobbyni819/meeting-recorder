"""Tests for silence gap analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.silence_gaps import (
    analyze_silence_gaps,
    format_silence_report,
    SilenceReport,
    SilenceGap,
)


def _make_transcript(tmp_path: Path, segments: list[dict]) -> Path:
    rec = tmp_path / "2026-03-10_09-00-00_Team_Meeting"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "transcript.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8"
    )
    return rec


def _seg(start: float, end: float, speaker: str, text: str = "words") -> dict:
    return {"start": start, "end": end, "speaker": speaker, "text": text}


class TestAnalyzeSilenceGaps:
    def test_no_dir(self, tmp_path):
        assert analyze_silence_gaps(tmp_path / "nope") is None

    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_silence_gaps(rec) is None

    def test_too_few_segments(self, tmp_path):
        rec = _make_transcript(tmp_path, [_seg(0, 5, "A")])
        assert analyze_silence_gaps(rec) is None

    def test_no_gaps(self, tmp_path):
        # Continuous speech with no significant gaps
        segs = [_seg(i * 5, (i + 1) * 5, "Alice", "talking") for i in range(10)]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.total_gaps == 0

    def test_single_gap(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "First part of the conversation"),
            _seg(20, 30, "Bob", "Response after a long pause"),  # 10s gap
            _seg(30, 40, "Alice", "Continuing the discussion"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.total_gaps == 1
        assert report.gaps[0].duration_seconds == 10.0
        assert report.gaps[0].speaker_before == "Alice"
        assert report.gaps[0].speaker_after == "Bob"

    def test_multiple_gaps(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "Hello everyone"),
            _seg(20, 30, "Bob", "Hi there"),  # 10s gap
            _seg(40, 50, "Carol", "Good morning"),  # 10s gap
            _seg(60, 70, "Alice", "Let's begin"),  # 10s gap
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.total_gaps == 3
        assert report.total_silence_seconds == 30.0

    def test_custom_min_gap(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "Speaking"),
            _seg(13, 20, "Bob", "Quick reply"),  # 3s gap
            _seg(30, 40, "Carol", "After long pause"),  # 10s gap
        ]
        rec = _make_transcript(tmp_path, segs)
        # Default 5s threshold
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.total_gaps == 1

        # Lower threshold
        report2 = analyze_silence_gaps(rec, min_gap=2.0)
        assert report2 is not None
        assert report2.total_gaps == 2

    def test_silence_percentage(self, tmp_path):
        # 30s of speech, 30s of silence out of 60s total
        segs = [
            _seg(0, 10, "Alice", "Talking"),
            _seg(20, 30, "Bob", "More talking"),  # 10s gap
            _seg(40, 50, "Carol", "Even more"),  # 10s gap
            _seg(60, 70, "Alice", "Final bit"),  # 10s gap after
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.silence_percentage > 20

    def test_longest_gap(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "Start"),
            _seg(15, 25, "Bob", "Short pause"),  # 5s
            _seg(50, 60, "Carol", "Long pause"),  # 25s
            _seg(65, 75, "Alice", "Another short"),  # 5s
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.longest_gap is not None
        assert report.longest_gap.duration_seconds == 25.0

    def test_context_extraction(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "This is important context before the gap"),
            _seg(20, 30, "Bob", "This is the response after the silence"),
            _seg(31, 40, "Carol", "Adding a third segment for minimum"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.total_gaps == 1
        assert "context before" in report.gaps[0].context_before
        assert "response after" in report.gaps[0].context_after

    def test_avg_gap(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "Start"),
            _seg(20, 30, "Bob", "After 10s"),
            _seg(50, 60, "Carol", "After 20s"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_silence_gaps(rec)
        assert report is not None
        assert report.avg_gap_seconds == 15.0  # (10 + 20) / 2

    def test_invalid_json(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.json").write_text("not json", encoding="utf-8")
        assert analyze_silence_gaps(rec) is None


class TestFormatSilenceReport:
    def test_none(self):
        text = format_silence_report(None)
        assert "Not enough data" in text

    def test_no_gaps(self):
        report = SilenceReport(
            total_gaps=0,
            total_silence_seconds=0,
            silence_percentage=0,
            longest_gap=None,
            avg_gap_seconds=0,
            gaps=[],
        )
        text = format_silence_report(report)
        assert "SILENCE GAP ANALYSIS" in text
        assert "No significant silence" in text

    def test_with_gaps(self):
        gap = SilenceGap(
            start_seconds=120.0,
            duration_seconds=15.0,
            speaker_before="Alice",
            speaker_after="Bob",
            context_before="...finishing my point",
            context_after="Let me respond to that...",
        )
        report = SilenceReport(
            total_gaps=3,
            total_silence_seconds=35.0,
            silence_percentage=8.5,
            longest_gap=gap,
            avg_gap_seconds=11.7,
            gaps=[gap],
        )
        text = format_silence_report(report)
        assert "SILENCE GAP ANALYSIS" in text
        assert "3" in text  # total gaps
        assert "2:00" in text  # longest gap at 120s
        assert "Alice" in text
        assert "Moderate silence" in text
