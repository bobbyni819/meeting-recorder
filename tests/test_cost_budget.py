"""Tests for meeting cost budget tracker."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.cost_budget import (
    analyze_cost_budget,
    format_cost_budget,
    CostBudget,
    WeeklyCost,
)


def _make_rec(base: Path, d: date, meta: dict) -> Path:
    name = f"{d.isoformat()}_09-00-00_Meeting"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rec


def _this_week_date(offset_days: int = 0) -> date:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return week_start + timedelta(days=offset_days)


class TestAnalyzeCostBudget:
    def test_nonexistent_dir(self, tmp_path):
        assert analyze_cost_budget(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert analyze_cost_budget(tmp_path) is None

    def test_single_recording(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 3600,
            "meeting_attendees": ["Alice", "Bob"],
        })
        cb = analyze_cost_budget(tmp_path, hourly_rate=100.0)
        assert cb is not None
        assert cb.total_cost == 200.0  # 1h * 2 people * $100
        assert len(cb.weekly_costs) >= 1

    def test_weekly_budget_check(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 7200,  # 2 hours
            "meeting_attendees": ["Alice", "Bob", "Carol"],
        })
        # Cost = 2h * 3 people * $100 = $600
        cb = analyze_cost_budget(tmp_path, hourly_rate=100.0, weekly_budget=500.0)
        assert cb is not None
        assert cb.over_budget_weeks >= 1

    def test_under_budget(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 1800,  # 0.5h
            "meeting_attendees": ["Alice"],
        })
        cb = analyze_cost_budget(tmp_path, hourly_rate=75.0, weekly_budget=5000.0)
        assert cb is not None
        assert cb.over_budget_weeks == 0

    def test_multiple_weeks(self, tmp_path):
        for w in range(3):
            d = _this_week_date(0) - timedelta(weeks=w)
            _make_rec(tmp_path, d, {
                "duration_seconds": 3600,
                "meeting_attendees": ["Alice"],
                "meeting_subject": "Standup",
            })
        cb = analyze_cost_budget(tmp_path, weeks=4, hourly_rate=75.0)
        assert cb is not None
        assert cb.total_cost == 225.0  # 3 * 1h * 1 person * $75
        assert len(cb.weekly_costs) == 4

    def test_trend_increasing(self, tmp_path):
        # Older weeks: small meetings; newer weeks: big meetings
        for w in range(4):
            d = _this_week_date(0) - timedelta(weeks=w)
            attendees = ["Alice"] if w >= 2 else ["Alice", "Bob", "Carol", "Dave"]
            _make_rec(tmp_path, d, {
                "duration_seconds": 3600,
                "meeting_attendees": attendees,
            })
        cb = analyze_cost_budget(tmp_path, weeks=4, hourly_rate=100.0)
        assert cb is not None
        assert cb.cost_trend == "increasing"

    def test_trend_decreasing(self, tmp_path):
        for w in range(4):
            d = _this_week_date(0) - timedelta(weeks=w)
            attendees = ["Alice", "Bob", "Carol", "Dave"] if w >= 2 else ["Alice"]
            _make_rec(tmp_path, d, {
                "duration_seconds": 3600,
                "meeting_attendees": attendees,
            })
        cb = analyze_cost_budget(tmp_path, weeks=4, hourly_rate=100.0)
        assert cb is not None
        assert cb.cost_trend == "decreasing"

    def test_trend_stable(self, tmp_path):
        for w in range(4):
            d = _this_week_date(0) - timedelta(weeks=w)
            _make_rec(tmp_path, d, {
                "duration_seconds": 3600,
                "meeting_attendees": ["Alice", "Bob"],
            })
        cb = analyze_cost_budget(tmp_path, weeks=4, hourly_rate=100.0)
        assert cb is not None
        assert cb.cost_trend == "stable"

    def test_top_subjects(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 7200,
            "meeting_subject": "Sprint Planning",
            "meeting_attendees": ["Alice", "Bob"],
        })
        d2 = _this_week_date(1)
        _make_rec(tmp_path, d2, {
            "duration_seconds": 1800,
            "meeting_subject": "1:1",
            "meeting_attendees": ["Alice"],
        })
        cb = analyze_cost_budget(tmp_path, hourly_rate=100.0)
        assert cb is not None
        assert len(cb.top_costly_subjects) == 2
        # Sprint Planning should be more expensive
        assert cb.top_costly_subjects[0][0] == "Sprint Planning"

    def test_top_attendees(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 3600,
            "meeting_attendees": ["Alice", "Bob"],
        })
        cb = analyze_cost_budget(tmp_path, hourly_rate=100.0)
        assert cb is not None
        assert len(cb.top_costly_attendees) == 2

    def test_no_budget(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 3600,
        })
        cb = analyze_cost_budget(tmp_path)
        assert cb is not None
        assert cb.budget_per_week == 0.0
        assert cb.over_budget_weeks == 0

    def test_zero_duration_skipped(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, {
            "duration_seconds": 0,
        })
        assert analyze_cost_budget(tmp_path) is None

    def test_old_recordings_excluded(self, tmp_path):
        old = _this_week_date(0) - timedelta(weeks=20)
        _make_rec(tmp_path, old, {
            "duration_seconds": 3600,
        })
        cb = analyze_cost_budget(tmp_path, weeks=8)
        assert cb is None


class TestFormatCostBudget:
    def _sample_budget(self, **overrides) -> CostBudget:
        defaults = dict(
            weekly_costs=[
                WeeklyCost("2026-03-02", 3, 4.5, 12.0, 900.0),
                WeeklyCost("2026-03-09", 5, 7.0, 18.0, 1350.0),
            ],
            total_cost=2250.0,
            avg_weekly_cost=1125.0,
            budget_per_week=1500.0,
            over_budget_weeks=0,
            cost_trend="stable",
            trend_pct=2.5,
            top_costly_subjects=[("Sprint Planning", 800.0), ("Retro", 450.0)],
            top_costly_attendees=[("Alice", 1200.0), ("Bob", 900.0)],
        )
        defaults.update(overrides)
        return CostBudget(**defaults)

    def test_header(self):
        text = format_cost_budget(self._sample_budget())
        assert "MEETING COST TRACKER" in text

    def test_totals(self):
        text = format_cost_budget(self._sample_budget())
        assert "$2,250" in text
        assert "$1,125" in text

    def test_budget_shown(self):
        text = format_cost_budget(self._sample_budget())
        assert "$1,500" in text
        assert "none" in text  # over_budget_weeks = 0

    def test_over_budget_shown(self):
        text = format_cost_budget(self._sample_budget(over_budget_weeks=2))
        assert "2" in text

    def test_no_budget(self):
        text = format_cost_budget(self._sample_budget(budget_per_week=0))
        assert "budget" not in text.lower() or "Weekly budget" not in text

    def test_weekly_breakdown(self):
        text = format_cost_budget(self._sample_budget())
        assert "03-02" in text
        assert "03-09" in text
        assert "900" in text

    def test_trend(self):
        text = format_cost_budget(self._sample_budget(cost_trend="increasing", trend_pct=25.0))
        assert "increasing" in text
        assert "+25%" in text

    def test_subjects(self):
        text = format_cost_budget(self._sample_budget())
        assert "Sprint Planning" in text
        assert "Retro" in text

    def test_attendees(self):
        text = format_cost_budget(self._sample_budget())
        assert "Alice" in text
        assert "Bob" in text
