"""Tests for meeting duration heatmap."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.heatmap import (
    build_heatmap,
    format_heatmap,
    _hour_to_slot,
    _intensity_block,
    TIME_SLOTS,
    DAY_NAMES,
    MeetingHeatmap,
)


def _make_rec(
    base: Path,
    d: date,
    hour: int = 9,
    minute: int = 0,
    duration: float = 1800,
    subject: str = "Meeting",
) -> Path:
    name = f"{d.isoformat()}_{hour:02d}-{minute:02d}-00_{subject}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump({"duration_seconds": duration}, f)
    return rec


class TestHourToSlot:
    def test_morning(self):
        assert _hour_to_slot(7) == 0
        assert _hour_to_slot(8) == 0

    def test_midday(self):
        assert _hour_to_slot(11) == 2
        assert _hour_to_slot(12) == 2

    def test_afternoon(self):
        assert _hour_to_slot(15) == 4
        assert _hour_to_slot(16) == 4

    def test_evening(self):
        assert _hour_to_slot(17) == 5
        assert _hour_to_slot(18) == 5

    def test_out_of_range(self):
        assert _hour_to_slot(6) == -1
        assert _hour_to_slot(19) == -1
        assert _hour_to_slot(0) == -1
        assert _hour_to_slot(23) == -1


class TestIntensityBlock:
    def test_zero(self):
        assert _intensity_block(0.0) == " "

    def test_max(self):
        assert _intensity_block(1.0) == "\u2588"

    def test_mid(self):
        result = _intensity_block(0.5)
        assert result in "\u2591\u2592\u2593"


class TestBuildHeatmap:
    def test_empty_dir(self, tmp_path):
        assert build_heatmap(tmp_path) is None

    def test_nonexistent_dir(self, tmp_path):
        assert build_heatmap(tmp_path / "nope") is None

    def test_basic(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, today, hour=10, duration=3600)

        hm = build_heatmap(tmp_path, weeks=1)
        assert hm is not None
        assert hm.total_meetings == 1
        assert hm.peak_minutes == 60.0  # 1h

    def test_multiple_slots(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        _make_rec(tmp_path, monday, hour=9, duration=1800, subject="Morning")
        _make_rec(tmp_path, monday, hour=14, duration=3600, subject="Afternoon")

        hm = build_heatmap(tmp_path, weeks=1)
        assert hm is not None
        assert hm.total_meetings == 2

    def test_skips_weekend(self, tmp_path):
        today = date.today()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        _make_rec(tmp_path, saturday, hour=10)

        hm = build_heatmap(tmp_path, weeks=2)
        assert hm is None

    def test_peak_detection(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Heavy Tuesday morning
        _make_rec(tmp_path, monday + timedelta(days=1), hour=9,
                  duration=7200, subject="Long")
        _make_rec(tmp_path, monday, hour=14, duration=1800, subject="Short")

        hm = build_heatmap(tmp_path, weeks=1)
        assert hm is not None
        assert hm.peak_day == "Tue"

    def test_weeks_covered(self, tmp_path):
        today = date.today()
        for w in range(3):
            d = today - timedelta(weeks=w)
            if d.weekday() >= 5:
                d = d - timedelta(days=d.weekday() - 4)
            _make_rec(tmp_path, d, hour=10, subject=f"W{w}")

        hm = build_heatmap(tmp_path, weeks=4)
        assert hm is not None
        assert hm.weeks_covered == 3

    def test_short_name_skipped(self, tmp_path):
        (tmp_path / "short").mkdir()
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, today, hour=10)

        hm = build_heatmap(tmp_path, weeks=1)
        assert hm is not None
        assert hm.total_meetings == 1

    def test_old_data_excluded(self, tmp_path):
        old = date.today() - timedelta(weeks=10)
        if old.weekday() >= 5:
            old = old - timedelta(days=old.weekday() - 4)
        _make_rec(tmp_path, old, hour=10)

        hm = build_heatmap(tmp_path, weeks=4)
        assert hm is None


class TestFormatHeatmap:
    def test_none(self):
        text = format_heatmap(None)
        assert "No meeting data" in text

    def test_basic_format(self):
        grid = [[0.0] * 6 for _ in range(5)]
        counts = [[0] * 6 for _ in range(5)]
        grid[0][1] = 60.0  # Monday 9-11am
        counts[0][1] = 2
        hm = MeetingHeatmap(
            grid=grid, counts=counts,
            peak_day="Mon", peak_slot="9am-11am",
            peak_minutes=60.0, total_meetings=2, weeks_covered=1,
        )
        text = format_heatmap(hm)
        assert "MEETING HEATMAP" in text
        assert "Mon" in text
        assert "Peak" in text
        assert "9am-11am" in text

    def test_all_days_present(self):
        grid = [[10.0] * 6 for _ in range(5)]
        counts = [[1] * 6 for _ in range(5)]
        hm = MeetingHeatmap(
            grid=grid, counts=counts,
            peak_day="Mon", peak_slot="7am-9am",
            peak_minutes=10.0, total_meetings=30, weeks_covered=1,
        )
        text = format_heatmap(hm)
        for day in DAY_NAMES:
            assert day in text
