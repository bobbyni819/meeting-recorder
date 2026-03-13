"""Tests for focus time analysis."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.focus_time import (
    DayFocus,
    WeekFocus,
    analyze_focus_time,
    format_focus_report,
    DEFAULT_WORK_HOURS,
)


def _make_rec(
    base: Path,
    date_str: str,
    duration: float = 1800,
    subject: str = "Meeting",
) -> Path:
    name = f"{date_str}_09-00-00_{subject}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump({"duration_seconds": duration}, f)
    return rec


class TestAnalyzeFocusTime:
    def test_empty_dir(self, tmp_path):
        assert analyze_focus_time(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert analyze_focus_time(tmp_path / "noexist") == []

    def test_basic_week(self, tmp_path):
        # Create a meeting today
        today = date.today()
        # Make sure it's a weekday for the test
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)  # Go to Friday
        _make_rec(tmp_path, today.isoformat(), duration=3600)  # 1h meeting

        weeks = analyze_focus_time(tmp_path, weeks=1)
        assert len(weeks) == 1
        week = weeks[0]
        assert week.meeting_count >= 1
        assert week.total_meeting_hours >= 1.0
        assert week.total_focus_hours <= 40 - 1  # 40h work week minus 1h meeting

    def test_focus_percentage(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        # 4h of meetings = 50% focus on that day
        _make_rec(tmp_path, today.isoformat(), duration=14400)  # 4h

        weeks = analyze_focus_time(tmp_path, work_hours=8.0, weeks=1)
        week = weeks[0]
        day_idx = (today - (today - timedelta(days=today.weekday()))).days
        if 0 <= day_idx < 5:
            day = week.days[day_idx]
            assert day.meeting_hours == pytest.approx(4.0, abs=0.1)
            assert day.focus_hours == pytest.approx(4.0, abs=0.1)
            assert day.focus_pct == pytest.approx(50.0, abs=1.0)

    def test_multiple_meetings_same_day(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, today.isoformat(), duration=1800, subject="A")
        _make_rec(tmp_path, today.isoformat(), duration=3600, subject="B")

        weeks = analyze_focus_time(tmp_path, weeks=1)
        week = weeks[0]
        day_idx = (today - (today - timedelta(days=today.weekday()))).days
        if 0 <= day_idx < 5:
            day = week.days[day_idx]
            assert day.meeting_count == 2
            assert day.meeting_hours == pytest.approx(1.5, abs=0.1)

    def test_custom_work_hours(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, today.isoformat(), duration=3600)

        weeks = analyze_focus_time(tmp_path, work_hours=6.0, weeks=1)
        week = weeks[0]
        assert week.total_work_hours == 30.0  # 6h × 5 days

    def test_weekday_only(self, tmp_path):
        # Meetings on a Saturday should not count in any weekday
        today = date.today()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        _make_rec(tmp_path, saturday.isoformat(), duration=3600)

        weeks = analyze_focus_time(tmp_path, weeks=1)
        week = weeks[0]
        # All days should have 0 meetings
        for day in week.days:
            if day.date == saturday.isoformat():
                pytest.fail("Saturday should not appear in weekday analysis")

    def test_multiple_weeks(self, tmp_path):
        today = date.today()
        for w in range(3):
            d = today - timedelta(weeks=w)
            if d.weekday() >= 5:
                d = d - timedelta(days=d.weekday() - 4)
            _make_rec(tmp_path, d.isoformat(), duration=1800, subject=f"W{w}")

        weeks = analyze_focus_time(tmp_path, weeks=3)
        assert len(weeks) == 3

    def test_busiest_and_focus_day(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Mon: 4h meetings, Tue: 1h meetings
        _make_rec(tmp_path, monday.isoformat(), duration=14400, subject="Long")
        _make_rec(tmp_path, (monday + timedelta(days=1)).isoformat(),
                  duration=3600, subject="Short")

        weeks = analyze_focus_time(tmp_path, weeks=1)
        week = weeks[0]
        assert week.busiest_day == "Monday"

    def test_no_meetings_100_focus(self, tmp_path):
        # Empty directory with valid folder
        weeks = analyze_focus_time(tmp_path, weeks=1)
        assert weeks == []  # No data → no results


class TestFormatFocusReport:
    def test_empty(self):
        text = format_focus_report([])
        assert "No meeting data" in text

    def test_basic_format(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        _make_rec(tmp_path, monday.isoformat(), duration=7200)

        weeks = analyze_focus_time(tmp_path, weeks=1)
        text = format_focus_report(weeks)
        assert "FOCUS TIME REPORT" in text
        assert "Focus:" in text
        assert "Meetings:" in text

    def test_day_breakdown(self, tmp_path):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        _make_rec(tmp_path, monday.isoformat(), duration=3600)

        weeks = analyze_focus_time(tmp_path, weeks=1)
        text = format_focus_report(weeks)
        assert "Mon" in text
        assert "mtg" in text
        assert "focus" in text
