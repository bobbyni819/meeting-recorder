"""Tests for meeting time-of-day pattern analysis."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.time_patterns import (
    analyze_time_patterns,
    format_time_patterns,
    TimePatterns,
    TimeSlotStats,
)


def _this_week(offset: int = 0) -> date:
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=offset)


def _make_rec(base: Path, d: date, hour: int, duration: int,
              quality: int | None = None) -> Path:
    name = f"{d.isoformat()}_{hour:02d}-00-00_Meeting"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"duration_seconds": duration}
    if quality is not None:
        meta["quality_scores"] = {"overall_score": quality}
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rec


class TestAnalyzeTimePatterns:
    def test_no_dir(self, tmp_path):
        assert analyze_time_patterns(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert analyze_time_patterns(tmp_path) is None

    def test_too_few_recordings(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 9, 1800)
        _make_rec(tmp_path, d, 10, 1800)
        # 2 recordings, need >= 3
        assert analyze_time_patterns(tmp_path) is None

    def test_basic_analysis(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 9, 1800)
        _make_rec(tmp_path, d, 10, 3600)
        _make_rec(tmp_path, d, 14, 1800)
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        assert report.total_meetings == 3
        assert 9 in report.hourly_counts
        assert 10 in report.hourly_counts
        assert 14 in report.hourly_counts

    def test_peak_hour(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 9, 1800)
        _make_rec(tmp_path, d + timedelta(days=1), 9, 1800)
        _make_rec(tmp_path, d + timedelta(days=2), 9, 1800)
        _make_rec(tmp_path, d, 14, 1800)
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        assert report.peak_hour == 9

    def test_morning_afternoon_evening(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 8, 1800)      # morning
        _make_rec(tmp_path, d, 10, 1800)     # morning
        _make_rec(tmp_path, d, 14, 1800)     # afternoon
        _make_rec(tmp_path, d, 18, 1800)     # evening
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        assert report.morning_count == 2
        assert report.afternoon_count == 1
        assert report.evening_count == 1

    def test_day_of_week(self, tmp_path):
        # Monday meetings
        mon = _this_week(0)
        _make_rec(tmp_path, mon, 9, 1800)
        _make_rec(tmp_path, mon, 10, 1800)
        _make_rec(tmp_path, mon, 14, 1800)
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        assert report.busiest_day == "Monday"
        assert report.day_counts["Monday"] == 3

    def test_quality_tracking(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 9, 1800, quality=90)
        _make_rec(tmp_path, d + timedelta(days=1), 9, 1800, quality=85)
        _make_rec(tmp_path, d, 14, 1800, quality=70)
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        assert report.best_quality_hour == 9

    def test_quiet_hours(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 9, 1800)
        _make_rec(tmp_path, d, 10, 1800)
        _make_rec(tmp_path, d + timedelta(days=1), 9, 1800)
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        # Many hours should be quiet
        assert 20 in report.quiet_hours  # 8pm should be quiet

    def test_old_excluded(self, tmp_path):
        old = _this_week() - timedelta(weeks=20)
        _make_rec(tmp_path, old, 9, 1800)
        _make_rec(tmp_path, old, 10, 1800)
        _make_rec(tmp_path, old, 14, 1800)
        assert analyze_time_patterns(tmp_path, weeks=4) is None

    def test_time_slots(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, 9, 1800)
        _make_rec(tmp_path, d, 10, 3600)
        _make_rec(tmp_path, d + timedelta(days=1), 9, 900)
        report = analyze_time_patterns(tmp_path)
        assert report is not None
        assert len(report.time_slots) >= 2
        slot_9 = next(s for s in report.time_slots if s.hour == 9)
        assert slot_9.count == 2


class TestFormatTimePatterns:
    def test_none(self):
        text = format_time_patterns(None)
        assert "Not enough" in text

    def test_basic(self):
        report = TimePatterns(
            total_meetings=15,
            hourly_counts={9: 5, 10: 4, 14: 3, 15: 2, 16: 1},
            hourly_minutes={9: 150, 10: 240, 14: 90, 15: 60, 16: 30},
            peak_hour=9,
            quiet_hours=[6, 7, 8, 11, 12, 13, 17, 18, 19, 20, 21],
            morning_count=9,
            afternoon_count=6,
            evening_count=0,
            busiest_day="Tuesday",
            day_counts={"Monday": 3, "Tuesday": 5, "Wednesday": 4, "Thursday": 2, "Friday": 1},
            time_slots=[
                TimeSlotStats(hour=9, count=5, total_minutes=150,
                              avg_quality=82.0, avg_duration_min=30.0),
            ],
            best_quality_hour=9,
        )
        text = format_time_patterns(report)
        assert "MEETING TIME PATTERNS" in text
        assert "15" in text  # total meetings
        assert "09:00" in text  # peak hour
        assert "Tuesday" in text  # busiest day
        assert "Morning" in text or "morning" in text
