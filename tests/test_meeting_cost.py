"""Tests for meeting cost estimation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.meeting_cost import (
    MeetingCost,
    estimate_cost,
    estimate_recording_cost,
    format_cost,
    aggregate_costs,
    DEFAULT_HOURLY_RATE,
)


def _make_rec(
    base: Path,
    name: str,
    duration: float = 1800,
    attendees: list[str] = None,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta: dict = {"duration_seconds": duration}
    if attendees:
        meta["meeting_attendees"] = attendees
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return rec


class TestEstimateCost:
    def test_basic(self):
        cost = estimate_cost(3600, 1, 100.0)
        assert cost.total_cost == 100.0
        assert cost.duration_hours == 1.0
        assert cost.attendee_count == 1

    def test_multiple_attendees(self):
        # 1 hour, 4 people, $100/hr = $400
        cost = estimate_cost(3600, 4, 100.0)
        assert cost.total_cost == 400.0

    def test_half_hour(self):
        # 30 min, 2 people, $100/hr = $100
        cost = estimate_cost(1800, 2, 100.0)
        assert cost.total_cost == 100.0

    def test_zero_attendees_minimum_one(self):
        cost = estimate_cost(3600, 0, 100.0)
        assert cost.attendee_count == 1
        assert cost.total_cost == 100.0

    def test_default_rate(self):
        cost = estimate_cost(3600, 1)
        assert cost.hourly_rate == DEFAULT_HOURLY_RATE
        assert cost.total_cost == DEFAULT_HOURLY_RATE

    def test_cost_per_minute(self):
        # 1 hour, 1 person, $60/hr = $1/min
        cost = estimate_cost(3600, 1, 60.0)
        assert cost.cost_per_minute == 1.0


class TestEstimateRecordingCost:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test", duration=3600)
        cost = estimate_recording_cost(rec)
        assert cost is not None
        assert cost.total_cost == DEFAULT_HOURLY_RATE  # 1hr × 1 person

    def test_with_attendees(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Team",
                        duration=3600, attendees=["Alice", "Bob", "Charlie"])
        cost = estimate_recording_cost(rec)
        assert cost.attendee_count == 3
        assert cost.total_cost == 3 * DEFAULT_HOURLY_RATE

    def test_custom_rate(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test", duration=3600)
        cost = estimate_recording_cost(rec, hourly_rate=150.0)
        assert cost.total_cost == 150.0

    def test_zero_duration(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test", duration=0)
        assert estimate_recording_cost(rec) is None

    def test_no_metadata(self, tmp_path):
        rec = tmp_path / "2026-03-01_09-00-00_Test"
        rec.mkdir()
        assert estimate_recording_cost(rec) is None

    def test_provided_meta(self, tmp_path):
        rec = tmp_path / "2026-03-01_09-00-00_Test"
        rec.mkdir()
        cost = estimate_recording_cost(
            rec, meta={"duration_seconds": 1800, "meeting_attendees": ["A", "B"]})
        assert cost is not None
        assert cost.attendee_count == 2


class TestFormatCost:
    def test_single_person(self):
        cost = estimate_cost(3600, 1, 100.0)
        text = format_cost(cost)
        assert "$100" in text
        assert "1.0h" in text

    def test_multiple_people(self):
        cost = estimate_cost(1800, 5, 80.0)
        text = format_cost(cost)
        assert "5 people" in text
        assert "$80" in text

    def test_large_cost(self):
        cost = estimate_cost(7200, 10, 150.0)
        text = format_cost(cost)
        assert "$3,000" in text


class TestAggregateCosts:
    def test_basic(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", duration=3600)
        _make_rec(tmp_path, "2026-03-02_09-00-00_B", duration=1800)
        result = aggregate_costs(tmp_path, hourly_rate=100.0)
        assert result["meeting_count"] == 2
        assert result["total_cost"] == 150.0  # 100 + 50
        assert result["avg_cost"] == 75.0

    def test_empty_dir(self, tmp_path):
        result = aggregate_costs(tmp_path)
        assert result["meeting_count"] == 0

    def test_nonexistent_dir(self, tmp_path):
        result = aggregate_costs(tmp_path / "noexist")
        assert result["meeting_count"] == 0

    def test_most_expensive(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Short", duration=600)
        _make_rec(tmp_path, "2026-03-02_09-00-00_Long", duration=7200,
                  attendees=["A", "B", "C"])
        result = aggregate_costs(tmp_path, hourly_rate=100.0)
        assert result["most_expensive_cost"] == 600.0  # 2h × 3 × $100
