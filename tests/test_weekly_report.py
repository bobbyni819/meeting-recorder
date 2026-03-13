"""Tests for weekly meeting report generator."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.weekly_report import (
    generate_weekly_report,
    format_weekly_report,
    WeeklyReport,
)


def _make_rec(base: Path, name: str, meta: dict, transcript: dict | None = None, action_items: list | None = None) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if transcript is not None:
        (d / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    if action_items is not None:
        (d / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")
    return d


def _this_week_date(offset_days: int = 0) -> str:
    """Return a date string within the current week."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    d = week_start + timedelta(days=offset_days)
    return d.isoformat()


def _last_week_date(offset_days: int = 0) -> str:
    """Return a date string within last week."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday()) - timedelta(weeks=1)
    d = week_start + timedelta(days=offset_days)
    return d.isoformat()


class TestGenerateWeeklyReport:
    def test_nonexistent_dir(self, tmp_path):
        result = generate_weekly_report(tmp_path / "nope")
        assert result is None

    def test_empty_dir(self, tmp_path):
        result = generate_weekly_report(tmp_path)
        assert result is None

    def test_no_recordings_this_week(self, tmp_path):
        old = (date.today() - timedelta(days=30)).isoformat()
        _make_rec(tmp_path, f"{old}_09-00-00_Old", {
            "status": "completed", "duration_seconds": 1800,
        })
        result = generate_weekly_report(tmp_path)
        assert result is None

    def test_single_recording(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Standup", {
            "status": "completed",
            "duration_seconds": 1800,
            "app_name": "Zoom",
            "meeting_subject": "Daily Standup",
            "meeting_attendees": ["Alice", "Bob"],
        })
        report = generate_weekly_report(tmp_path)
        assert report is not None
        assert report.recording_count == 1
        assert report.total_meeting_hours == 0.5
        assert report.apps_used == {"Zoom": 1}
        assert ("Daily Standup", 1) in report.top_subjects

    def test_multiple_recordings(self, tmp_path):
        d1 = _this_week_date(0)
        d2 = _this_week_date(1)
        _make_rec(tmp_path, f"{d1}_09-00-00_Meeting1", {
            "status": "completed", "duration_seconds": 3600,
            "app_name": "Zoom", "meeting_subject": "Planning",
            "meeting_attendees": ["Alice", "Bob", "Carol"],
        })
        _make_rec(tmp_path, f"{d2}_14-00-00_Meeting2", {
            "status": "completed", "duration_seconds": 1800,
            "app_name": "Teams", "meeting_subject": "Review",
            "meeting_attendees": ["Alice"],
        })
        report = generate_weekly_report(tmp_path)
        assert report.recording_count == 2
        assert report.total_meeting_hours == 1.5
        assert report.apps_used == {"Zoom": 1, "Teams": 1}
        assert report.avg_attendees == 2.0

    def test_focus_hours(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Long", {
            "status": "completed", "duration_seconds": 36000,  # 10 hours
        })
        report = generate_weekly_report(tmp_path, work_hours=40.0)
        assert report.total_meeting_hours == 10.0
        assert report.total_focus_hours == 30.0
        assert report.focus_pct == 75.0

    def test_cost_estimate(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Cost", {
            "status": "completed", "duration_seconds": 3600,
            "meeting_attendees": ["Alice", "Bob"],
        })
        report = generate_weekly_report(tmp_path, hourly_rate=100.0)
        # 1 hour * 2 attendees * $100/h = $200
        assert report.estimated_cost == 200.0

    def test_cost_no_attendees(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Solo", {
            "status": "completed", "duration_seconds": 3600,
        })
        report = generate_weekly_report(tmp_path, hourly_rate=75.0)
        # 1 hour * max(0, 1) attendee * $75/h = $75
        assert report.estimated_cost == 75.0

    def test_comparison_more(self, tmp_path):
        # Last week: 1h, this week: 3h -> "more"
        lw = _last_week_date(0)
        tw = _this_week_date(0)
        _make_rec(tmp_path, f"{lw}_09-00-00_Last", {
            "status": "completed", "duration_seconds": 3600,
        })
        _make_rec(tmp_path, f"{tw}_09-00-00_This", {
            "status": "completed", "duration_seconds": 10800,
        })
        report = generate_weekly_report(tmp_path)
        assert report.comparison == "more"
        assert report.comparison_delta == 2.0

    def test_comparison_less(self, tmp_path):
        lw = _last_week_date(0)
        tw = _this_week_date(0)
        _make_rec(tmp_path, f"{lw}_09-00-00_Last", {
            "status": "completed", "duration_seconds": 10800,
        })
        _make_rec(tmp_path, f"{tw}_09-00-00_This", {
            "status": "completed", "duration_seconds": 3600,
        })
        report = generate_weekly_report(tmp_path)
        assert report.comparison == "less"

    def test_comparison_same(self, tmp_path):
        lw = _last_week_date(0)
        tw = _this_week_date(0)
        _make_rec(tmp_path, f"{lw}_09-00-00_Last", {
            "status": "completed", "duration_seconds": 3600,
        })
        _make_rec(tmp_path, f"{tw}_09-00-00_This", {
            "status": "completed", "duration_seconds": 3600,
        })
        report = generate_weekly_report(tmp_path)
        assert report.comparison == "same"

    def test_quality_scores(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Q1", {
            "status": "completed", "duration_seconds": 1800,
            "quality_scores": {"overall_score": 80},
        })
        _make_rec(tmp_path, f"{d}_10-00-00_Q2", {
            "status": "completed", "duration_seconds": 1800,
            "quality_scores": {"overall_score": 60},
        })
        report = generate_weekly_report(tmp_path)
        assert report.avg_quality == 70

    def test_error_count(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Err", {
            "status": "error", "duration_seconds": 100,
        })
        _make_rec(tmp_path, f"{d}_10-00-00_Ok", {
            "status": "completed", "duration_seconds": 1800,
        })
        report = generate_weekly_report(tmp_path)
        assert report.error_count == 1

    def test_action_items(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Actions", {
            "status": "completed", "duration_seconds": 1800,
        }, action_items=[
            {"text": "Follow up with client", "assignee": "Alice"},
            {"text": "Send report", "assignee": "Bob"},
        ])
        report = generate_weekly_report(tmp_path)
        assert report.total_action_items == 2

    def test_speakers_from_transcript(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, f"{d}_09-00-00_Spk", {
            "status": "completed", "duration_seconds": 1800,
            "speaker_map": {"SPEAKER_00": "Alice"},
        }, transcript={
            "segments": [
                {"speaker": "SPEAKER_00", "start": 0, "end": 600},
                {"speaker": "SPEAKER_01", "start": 600, "end": 900},
            ]
        })
        report = generate_weekly_report(tmp_path)
        assert len(report.top_speakers) == 2
        # Alice spoke 600s = 0.2h (rounded to 0.2)
        alice = [s for s in report.top_speakers if s[0] == "Alice"]
        assert len(alice) == 1
        assert alice[0][1] == 0.2

    def test_week_offset(self, tmp_path):
        lw = _last_week_date(2)
        _make_rec(tmp_path, f"{lw}_09-00-00_LastWeek", {
            "status": "completed", "duration_seconds": 3600,
        })
        # Current week should find nothing
        assert generate_weekly_report(tmp_path, week_offset=0) is None
        # Last week should find the recording
        report = generate_weekly_report(tmp_path, week_offset=1)
        assert report is not None
        assert report.recording_count == 1


class TestFormatWeeklyReport:
    def _sample_report(self, **overrides) -> WeeklyReport:
        defaults = dict(
            week_start="2026-03-09",
            week_end="2026-03-15",
            recording_count=5,
            total_meeting_hours=8.5,
            total_focus_hours=31.5,
            focus_pct=78.8,
            avg_duration_min=102,
            avg_attendees=3.2,
            total_action_items=12,
            top_subjects=[("Sprint Planning", 2), ("1:1", 1)],
            top_speakers=[("Alice", 2.5), ("Bob", 1.8)],
            apps_used={"Zoom": 3, "Teams": 2},
            error_count=0,
            avg_quality=75,
            estimated_cost=2040.0,
            comparison="more",
            comparison_delta=1.5,
        )
        defaults.update(overrides)
        return WeeklyReport(**defaults)

    def test_header(self):
        text = format_weekly_report(self._sample_report())
        assert "WEEKLY MEETING REPORT" in text
        assert "2026-03-09" in text
        assert "2026-03-15" in text

    def test_metrics(self):
        text = format_weekly_report(self._sample_report())
        assert "5" in text  # recording count
        assert "8.5h" in text
        assert "31.5h" in text
        assert "79%" in text  # focus_pct rounded
        assert "$2,040" in text

    def test_comparison_more(self):
        text = format_weekly_report(self._sample_report(comparison="more", comparison_delta=1.5))
        assert "+1.5h more" in text

    def test_comparison_less(self):
        text = format_weekly_report(self._sample_report(comparison="less", comparison_delta=2.0))
        assert "-2.0h fewer" in text

    def test_comparison_same(self):
        text = format_weekly_report(self._sample_report(comparison="same"))
        assert "about the same" in text

    def test_subjects(self):
        text = format_weekly_report(self._sample_report())
        assert "Sprint Planning" in text
        assert "1:1" in text

    def test_speakers(self):
        text = format_weekly_report(self._sample_report())
        assert "Alice" in text
        assert "2.5h" in text

    def test_platforms(self):
        text = format_weekly_report(self._sample_report())
        assert "Zoom" in text
        assert "Teams" in text

    def test_quality(self):
        text = format_weekly_report(self._sample_report())
        assert "75/100" in text

    def test_no_quality(self):
        text = format_weekly_report(self._sample_report(avg_quality=None))
        assert "/100" not in text

    def test_errors_shown(self):
        text = format_weekly_report(self._sample_report(error_count=3))
        assert "3" in text
        assert "Errors" in text

    def test_no_errors_hidden(self):
        text = format_weekly_report(self._sample_report(error_count=0))
        assert "Errors" not in text
