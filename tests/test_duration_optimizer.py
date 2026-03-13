"""Tests for meeting duration optimizer."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.duration_optimizer import (
    analyze_duration_optimization,
    format_duration_optimizer,
    _normalize_subject,
    _round_to_slot,
    _guess_scheduled,
    DurationSuggestion,
    DurationOptimizer,
)


def _make_rec(base: Path, d: date, subject: str, duration: int) -> Path:
    name = f"{d.isoformat()}_09-00-00_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "status": "completed",
        "duration_seconds": duration,
        "meeting_subject": subject,
    }
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rec


def _this_week(offset: int = 0) -> date:
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=offset)


class TestNormalizeSubject:
    def test_basic(self):
        assert _normalize_subject("Sprint Planning") == "Sprint Planning"

    def test_date_suffix(self):
        assert _normalize_subject("Standup 2026-03-13") == "Standup"

    def test_number_suffix(self):
        assert _normalize_subject("Sprint #42") == "Sprint"

    def test_extra_spaces(self):
        assert _normalize_subject("  Hello   World  ") == "Hello World"


class TestRoundToSlot:
    def test_short(self):
        assert _round_to_slot(10) == 15

    def test_half_hour(self):
        assert _round_to_slot(28) == 30

    def test_hour(self):
        assert _round_to_slot(55) == 60

    def test_ninety(self):
        assert _round_to_slot(85) == 90


class TestGuessScheduled:
    def test_short(self):
        assert _guess_scheduled(12) == 15

    def test_half_hour(self):
        assert _guess_scheduled(25) == 30

    def test_hour(self):
        assert _guess_scheduled(55) == 60

    def test_long(self):
        assert _guess_scheduled(110) == 120


class TestAnalyzeDurationOptimization:
    def test_nonexistent_dir(self, tmp_path):
        assert analyze_duration_optimization(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert analyze_duration_optimization(tmp_path) is None

    def test_single_meeting_type(self, tmp_path):
        # Need at least 2 recordings of same subject
        for i in range(3):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Standup", 1800)  # 30 min
        report = analyze_duration_optimization(tmp_path)
        assert report is not None
        assert report.total_meetings == 3
        assert len(report.suggestions) == 1
        assert report.suggestions[0].subject == "Standup"
        assert report.suggestions[0].avg_duration_min == 30.0

    def test_multiple_meeting_types(self, tmp_path):
        for i in range(3):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Standup", 1800)
        for i in range(3):
            d = _this_week(-i) + timedelta(days=1)
            _make_rec(tmp_path, d, "Sprint Planning", 5400)  # 90 min
        report = analyze_duration_optimization(tmp_path)
        assert report is not None
        assert len(report.suggestions) == 2

    def test_overrun_detection(self, tmp_path):
        # Meetings that consistently run 70 min, with explicit 60 min schedule
        for i in range(5):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Review", 4200)  # 70 min
        report = analyze_duration_optimization(
            tmp_path, scheduled_minutes={"Review": 60}
        )
        assert report is not None
        assert len(report.top_overrunners) >= 1

    def test_underrun_detection(self, tmp_path):
        # Meetings that consistently end at 15 min, with explicit 30 min schedule
        for i in range(5):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Quick Sync", 900)  # 15 min
        report = analyze_duration_optimization(
            tmp_path, scheduled_minutes={"Quick Sync": 30}
        )
        assert report is not None
        assert len(report.top_underrunners) >= 1

    def test_wasted_time_calculation(self, tmp_path):
        for i in range(5):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Quick Sync", 900)  # 15 min
        report = analyze_duration_optimization(
            tmp_path, scheduled_minutes={"Quick Sync": 30}
        )
        assert report is not None
        assert report.total_wasted_minutes > 0

    def test_confidence_levels(self, tmp_path):
        # 2 meetings = low confidence
        for i in range(2):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Rare", 1800)
        report = analyze_duration_optimization(tmp_path)
        assert report is not None
        assert report.suggestions[0].confidence == "low"

    def test_high_confidence(self, tmp_path):
        for i in range(12):
            d = _this_week(0) - timedelta(days=i)
            _make_rec(tmp_path, d, "Daily", 1800)
        report = analyze_duration_optimization(tmp_path)
        assert report is not None
        assert report.suggestions[0].confidence == "high"

    def test_old_recordings_excluded(self, tmp_path):
        old = _this_week(0) - timedelta(weeks=20)
        for i in range(3):
            _make_rec(tmp_path, old + timedelta(days=i), "Old Meeting", 1800)
        assert analyze_duration_optimization(tmp_path, weeks=12) is None

    def test_suggested_slot_rounding(self, tmp_path):
        # 22-minute meetings should suggest 15 or 30 min slot
        for i in range(5):
            d = _this_week(-i)
            _make_rec(tmp_path, d, "Brief", 1320)  # 22 min
        report = analyze_duration_optimization(tmp_path)
        assert report is not None
        assert report.suggestions[0].suggested_slot_min in (15, 30)


class TestFormatDurationOptimizer:
    def test_none(self):
        text = format_duration_optimizer(None)
        assert "Not enough" in text

    def test_basic_format(self):
        report = DurationOptimizer(
            suggestions=[
                DurationSuggestion(
                    subject="Standup",
                    avg_duration_min=25.0,
                    median_duration_min=24.0,
                    min_duration_min=18.0,
                    max_duration_min=35.0,
                    count=15,
                    suggested_slot_min=30,
                    confidence="high",
                    note="Duration matches scheduled time well",
                ),
            ],
            total_meetings=15,
            total_wasted_minutes=0,
            avg_overrun_minutes=0,
            top_overrunners=[],
            top_underrunners=[],
        )
        text = format_duration_optimizer(report)
        assert "MEETING DURATION OPTIMIZER" in text
        assert "Standup" in text
        assert "suggest: 30m" in text
        assert "***" in text  # high confidence

    def test_overrunners_shown(self):
        report = DurationOptimizer(
            suggestions=[],
            total_meetings=10,
            total_wasted_minutes=0,
            avg_overrun_minutes=15.0,
            top_overrunners=[("Sprint Review", 20.0)],
            top_underrunners=[],
        )
        text = format_duration_optimizer(report)
        assert "Sprint Review" in text
        assert "+20" in text

    def test_wasted_time_shown(self):
        report = DurationOptimizer(
            suggestions=[],
            total_meetings=10,
            total_wasted_minutes=150,
            avg_overrun_minutes=0,
            top_overrunners=[],
            top_underrunners=[("Quick Sync", 15.0)],
        )
        text = format_duration_optimizer(report)
        assert "150 min" in text
        assert "Quick Sync" in text
