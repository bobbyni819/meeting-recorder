"""Tests for speaker interruption analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.interruptions import (
    analyze_interruptions,
    format_interruption_report,
    InterruptionReport,
    Interruption,
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


class TestAnalyzeInterruptions:
    def test_no_dir(self, tmp_path):
        assert analyze_interruptions(tmp_path / "nope") is None

    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_interruptions(rec) is None

    def test_too_few_segments(self, tmp_path):
        rec = _make_transcript(tmp_path, [
            _seg(0, 5, "A", "Hello"),
        ])
        assert analyze_interruptions(rec) is None

    def test_single_speaker(self, tmp_path):
        segs = [_seg(i * 10, (i + 1) * 10, "Alice", "words") for i in range(10)]
        rec = _make_transcript(tmp_path, segs)
        assert analyze_interruptions(rec) is None

    def test_no_interruptions(self, tmp_path):
        # Clean turn-taking: each person finishes before the next starts
        segs = [
            _seg(0, 10, "Alice", "Hello everyone"),
            _seg(11, 20, "Bob", "Hi Alice"),
            _seg(21, 30, "Alice", "Let's begin"),
            _seg(31, 40, "Carol", "Sounds good"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.total_interruptions == 0
        assert report.flow_score == 100

    def test_basic_interruption(self, tmp_path):
        # Bob starts speaking before Alice finishes
        segs = [
            _seg(0, 10, "Alice", "I was saying something important"),
            _seg(9, 18, "Bob", "Let me jump in here"),  # 1 sec overlap
            _seg(19, 30, "Alice", "As I was saying"),
            _seg(29, 40, "Bob", "Sorry, go ahead"),  # 1 sec overlap
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.total_interruptions == 2
        assert report.interrupter_counts.get("Bob", 0) == 2
        assert report.interrupted_counts.get("Alice", 0) == 2
        assert report.top_interrupter == "Bob"
        assert report.most_interrupted == "Alice"

    def test_overlap_threshold(self, tmp_path):
        # Tiny overlap (0.2s) should not count with default threshold (0.5s)
        segs = [
            _seg(0, 10, "Alice", "Finishing up"),
            _seg(9.8, 18, "Bob", "My turn"),  # 0.2s overlap
            _seg(19, 30, "Alice", "More talk"),
            _seg(31, 40, "Bob", "Response"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.total_interruptions == 0

    def test_custom_threshold(self, tmp_path):
        # Same overlap but with lower threshold
        segs = [
            _seg(0, 10, "Alice", "Finishing up"),
            _seg(9.8, 18, "Bob", "My turn"),  # 0.2s overlap
            _seg(19, 30, "Alice", "More talk"),
            _seg(31, 40, "Bob", "Response"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec, overlap_threshold=0.1)
        assert report is not None
        assert report.total_interruptions == 1

    def test_pair_tracking(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "Speaking"),
            _seg(9, 18, "Bob", "Interrupting"),  # Bob -> Alice
            _seg(19, 30, "Bob", "More from Bob"),
            _seg(29, 40, "Alice", "Cutting in"),  # Alice -> Bob
            _seg(41, 50, "Alice", "Continuing"),
            _seg(49, 60, "Bob", "Again"),  # Bob -> Alice
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.pairs.get("Bob -> Alice", 0) == 2
        assert report.pairs.get("Alice -> Bob", 0) == 1

    def test_multiple_speakers(self, tmp_path):
        segs = [
            _seg(0, 10, "Alice", "Start"),
            _seg(9, 20, "Bob", "Jump in"),
            _seg(21, 30, "Carol", "My turn"),
            _seg(29, 40, "Alice", "Cut in"),
            _seg(41, 50, "Bob", "Speaking"),
            _seg(49, 60, "Carol", "Interrupting"),
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.total_interruptions == 3

    def test_flow_score_high_interruptions(self, tmp_path):
        # Many interruptions in short time = low flow score
        segs = []
        t = 0
        for i in range(20):
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(t, t + 5, speaker, "words"))
            t += 4  # 1s overlap each time
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.flow_score < 50

    def test_flow_score_clean(self, tmp_path):
        # No interruptions = perfect flow
        segs = []
        t = 0
        for i in range(20):
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(t, t + 5, speaker, "words"))
            t += 6  # 1s gap
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.flow_score == 100

    def test_interruptions_per_minute(self, tmp_path):
        # 2 interruptions in ~1 minute
        segs = [
            _seg(0, 15, "Alice", "Long speech"),
            _seg(14, 30, "Bob", "Interrupting"),  # 1s overlap
            _seg(31, 45, "Alice", "Continuing"),
            _seg(44, 60, "Bob", "Again"),  # 1s overlap
        ]
        rec = _make_transcript(tmp_path, segs)
        report = analyze_interruptions(rec)
        assert report is not None
        assert report.total_interruptions == 2
        assert report.interruptions_per_minute > 1.5

    def test_invalid_json(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.json").write_text("not json", encoding="utf-8")
        assert analyze_interruptions(rec) is None


class TestFormatInterruptionReport:
    def test_none(self):
        text = format_interruption_report(None)
        assert "Not enough data" in text

    def test_no_interruptions(self):
        report = InterruptionReport(
            total_interruptions=0,
            interruptions_per_minute=0.0,
            interrupter_counts={},
            interrupted_counts={},
            top_interrupter="",
            most_interrupted="",
            flow_score=100,
            pairs={},
            interruptions=[],
        )
        text = format_interruption_report(report)
        assert "INTERRUPTION ANALYSIS" in text
        assert "excellent flow" in text

    def test_with_interruptions(self):
        report = InterruptionReport(
            total_interruptions=5,
            interruptions_per_minute=1.2,
            interrupter_counts={"Bob": 3, "Carol": 2},
            interrupted_counts={"Alice": 4, "Bob": 1},
            top_interrupter="Bob",
            most_interrupted="Alice",
            flow_score=40,
            pairs={"Bob -> Alice": 3, "Carol -> Alice": 1, "Carol -> Bob": 1},
            interruptions=[],
        )
        text = format_interruption_report(report)
        assert "INTERRUPTION ANALYSIS" in text
        assert "5" in text
        assert "Bob" in text
        assert "Alice" in text
        assert "facilitator" in text  # flow score < 50
