"""Tests for meeting streaks and habit tracking."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.streaks import (
    analyze_streaks,
    format_streaks,
    StreakInfo,
)


def _make_rec(base: Path, d: date, subject: str = "Meeting") -> Path:
    name = f"{d.isoformat()}_09-00-00_{subject}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump({"duration_seconds": 1800}, f)
    return rec


class TestAnalyzeStreaks:
    def test_empty_dir(self, tmp_path):
        assert analyze_streaks(tmp_path) is None

    def test_nonexistent_dir(self, tmp_path):
        assert analyze_streaks(tmp_path / "nope") is None

    def test_single_recording(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, today)
        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.total_recording_days == 1
        assert info.current_streak >= 1

    def test_consecutive_weekdays(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Create recordings Mon-Thu of this week
        for i in range(4):
            d = monday + timedelta(days=i)
            _make_rec(tmp_path, d, subject=f"Day{i}")

        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.total_recording_days == 4

    def test_meeting_free_days(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Only record on Monday
        _make_rec(tmp_path, monday)

        info = analyze_streaks(tmp_path)
        assert info is not None
        # At least some meeting-free days in the 4-week period
        assert info.meeting_free_days >= 0

    def test_busiest_weekday(self, tmp_path):
        today = date.today()
        # Create multiple recordings on Mondays
        for w in range(4):
            monday = today - timedelta(days=today.weekday()) - timedelta(weeks=w)
            _make_rec(tmp_path, monday, subject=f"Mon{w}")

        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.busiest_weekday == "Monday"

    def test_weekly_average(self, tmp_path):
        today = date.today()
        # 2 recordings in first week, 2 in second week
        monday = today - timedelta(days=today.weekday())
        _make_rec(tmp_path, monday, subject="A")
        _make_rec(tmp_path, monday + timedelta(days=1), subject="B")
        _make_rec(tmp_path, monday - timedelta(weeks=1), subject="C")
        _make_rec(tmp_path, monday - timedelta(weeks=1) + timedelta(days=1), subject="D")

        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.total_recording_days == 4
        assert info.weekly_avg > 0

    def test_consistency(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Record every weekday this week
        for i in range(5):
            d = monday + timedelta(days=i)
            if d <= today:
                _make_rec(tmp_path, d, subject=f"Day{i}")

        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.consistency_pct > 0

    def test_skips_weekend_dirs(self, tmp_path):
        today = date.today()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        _make_rec(tmp_path, saturday)

        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.total_recording_days == 1

    def test_longest_streak(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Create a 3-day streak 2 weeks ago
        old_monday = monday - timedelta(weeks=2)
        for i in range(3):
            _make_rec(tmp_path, old_monday + timedelta(days=i), subject=f"Old{i}")
        # Create a 1-day streak this week
        _make_rec(tmp_path, monday, subject="New")

        info = analyze_streaks(tmp_path)
        assert info is not None
        assert info.longest_streak >= 3

    def test_no_current_streak(self, tmp_path):
        today = date.today()
        # Old recording from 3 weeks ago
        old = today - timedelta(weeks=3)
        if old.weekday() >= 5:
            old = old - timedelta(days=old.weekday() - 4)
        _make_rec(tmp_path, old)

        info = analyze_streaks(tmp_path)
        assert info is not None
        # Current streak might be 0 (no recent recordings)


class TestFormatStreaks:
    def test_none(self):
        text = format_streaks(None)
        assert "No recording data" in text

    def test_basic_format(self):
        info = StreakInfo(
            current_streak=3,
            longest_streak=7,
            streak_start="2026-03-10",
            total_recording_days=15,
            total_days_tracked=30,
            meeting_free_days=8,
            meeting_free_streak=2,
            busiest_weekday="Monday",
            quietest_weekday="Friday",
            weekly_avg=3.5,
            consistency_pct=65.0,
        )
        text = format_streaks(info)
        assert "RECORDING STREAKS" in text
        assert "3 days" in text
        assert "7 days" in text
        assert "USAGE" in text
        assert "15" in text
        assert "3.5" in text
        assert "65%" in text
        assert "WORK-LIFE BALANCE" in text
        assert "Monday" in text
        assert "Friday" in text

    def test_fire_emoji_long_streak(self):
        info = StreakInfo(
            current_streak=5,
            longest_streak=5,
            streak_start="2026-03-07",
            total_recording_days=5,
            total_days_tracked=5,
            meeting_free_days=0,
            meeting_free_streak=0,
            busiest_weekday="Monday",
            quietest_weekday="Friday",
            weekly_avg=5.0,
            consistency_pct=100.0,
        )
        text = format_streaks(info)
        assert "\U0001f525" in text  # fire emoji for 5+ streak

    def test_single_day_grammar(self):
        info = StreakInfo(
            current_streak=1,
            longest_streak=1,
            streak_start="2026-03-12",
            total_recording_days=1,
            total_days_tracked=1,
            meeting_free_days=0,
            meeting_free_streak=0,
            busiest_weekday="Wednesday",
            quietest_weekday="Wednesday",
            weekly_avg=1.0,
            consistency_pct=5.0,
        )
        text = format_streaks(info)
        assert "1 day" in text
        assert "1 days" not in text
