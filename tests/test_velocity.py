"""Tests for meeting velocity score."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.velocity import (
    analyze_velocity,
    format_velocity,
    MeetingVelocity,
)


def _make_rec(tmp_path: Path, name: str = "2026-03-10_09-00-00_Meeting",
              duration: int = 1800, decisions: int = 0, actions: int = 0,
              segments: list | None = None) -> Path:
    rec = tmp_path / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"duration_seconds": duration, "meeting_subject": "Meeting"}
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    if decisions > 0:
        decs = {"decisions": [{"description": f"Decision {i}"} for i in range(decisions)]}
        (rec / "decisions.json").write_text(json.dumps(decs), encoding="utf-8")

    if actions > 0:
        items = [{"text": f"Action item number {i} needs to be completed", "assignee": "Alice"} for i in range(actions)]
        (rec / "action_items.json").write_text(json.dumps(items), encoding="utf-8")

    if segments:
        (rec / "transcript.json").write_text(
            json.dumps({"segments": segments}), encoding="utf-8"
        )

    return rec


def _seg(start: float, end: float, speaker: str, text: str) -> dict:
    return {"start": start, "end": end, "speaker": speaker, "text": text}


class TestAnalyzeVelocity:
    def test_no_dir(self, tmp_path):
        assert analyze_velocity(tmp_path / "nope") is None

    def test_no_metadata(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_velocity(rec) is None

    def test_too_short(self, tmp_path):
        rec = _make_rec(tmp_path, duration=60)
        assert analyze_velocity(rec) is None

    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path, duration=1800, decisions=3, actions=4)
        v = analyze_velocity(rec)
        assert v is not None
        assert v.duration_min == 30.0
        assert v.decisions_per_hour > 0
        assert v.actions_per_hour > 0

    def test_high_velocity(self, tmp_path):
        # Many decisions and actions in short time
        segs = []
        for i in range(60):
            start = i * 10
            end = start + 8
            speaker = ["Alice", "Bob", "Carol"][i % 3]
            segs.append(_seg(start, end, speaker, "discussing important project details " * 3))
        rec = _make_rec(tmp_path, duration=600, decisions=5, actions=6, segments=segs)
        v = analyze_velocity(rec)
        assert v is not None
        assert v.decisions_per_hour >= 20  # 5 decisions in 10 min = 30/hr
        assert v.overall_velocity >= 40

    def test_low_velocity(self, tmp_path):
        # Long meeting with no outputs
        segs = [_seg(i * 30, (i + 1) * 30, "Alice", "hmm") for i in range(120)]
        rec = _make_rec(tmp_path, duration=3600, decisions=0, actions=0, segments=segs)
        v = analyze_velocity(rec)
        assert v is not None
        assert v.overall_velocity < 30
        assert v.label == "low"

    def test_turns_per_minute(self, tmp_path):
        # Rapid back-and-forth
        segs = []
        for i in range(60):
            start = i * 5
            end = start + 4
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker, "quick exchange of ideas"))
        rec = _make_rec(tmp_path, duration=300, segments=segs)
        v = analyze_velocity(rec)
        assert v is not None
        assert v.turns_per_minute > 5  # 60 turns in 5 min = 12/min

    def test_words_per_minute(self, tmp_path):
        segs = []
        for i in range(30):
            start = i * 10
            end = start + 8
            segs.append(_seg(start, end, "Alice", "word " * 50))  # 50 words per segment
        rec = _make_rec(tmp_path, duration=300, segments=segs)
        v = analyze_velocity(rec)
        assert v is not None
        assert v.words_per_minute > 200  # 1500 words in 5 min

    def test_pre_loaded_meta(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir(parents=True)
        meta = {"duration_seconds": 600}
        (rec / "decisions.json").write_text(json.dumps(
            {"decisions": [{"description": "Use React for the frontend"}]}
        ), encoding="utf-8")
        v = analyze_velocity(rec, meta=meta)
        assert v is not None
        assert v.decisions_per_hour > 0


class TestFormatVelocity:
    def test_none(self):
        text = format_velocity(None)
        assert "Not enough data" in text

    def test_high(self):
        v = MeetingVelocity(
            duration_min=30.0,
            decisions_per_hour=8.0,
            actions_per_hour=10.0,
            turns_per_minute=4.0,
            words_per_minute=140.0,
            topic_changes=5,
            overall_velocity=80,
            label="high",
        )
        text = format_velocity(v)
        assert "MEETING VELOCITY" in text
        assert "80/100" in text
        assert "High" in text
        assert "Fast-paced" in text

    def test_low(self):
        v = MeetingVelocity(
            duration_min=60.0,
            decisions_per_hour=0.0,
            actions_per_hour=0.0,
            turns_per_minute=1.0,
            words_per_minute=50.0,
            topic_changes=0,
            overall_velocity=15,
            label="low",
        )
        text = format_velocity(v)
        assert "Low" in text
        assert "tighter agenda" in text
