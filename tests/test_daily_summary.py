"""Tests for daily meeting summary."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from meeting_recorder.storage.daily_summary import (
    generate_daily_summary,
    format_daily_summary,
    DailySummary,
    DailyMeetingEntry,
)


def _make_rec(base: Path, d: date, time: str, subject: str, duration: int,
              speaker_count: int = 2, app: str = "Zoom",
              action_items: list | None = None,
              decisions: list | None = None) -> Path:
    name = f"{d.isoformat()}_{time.replace(':', '-')}-00_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "meeting_subject": subject,
        "speaker_count": speaker_count,
        "app_name": app,
    }
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if action_items is not None:
        (rec / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")
    if decisions is not None:
        (rec / "decisions.json").write_text(
            json.dumps({"decisions": decisions}), encoding="utf-8"
        )
    return rec


class TestGenerateDailySummary:
    def test_no_dir(self, tmp_path):
        assert generate_daily_summary(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert generate_daily_summary(tmp_path) is None

    def test_no_meetings_today(self, tmp_path):
        # Create a recording from yesterday
        yesterday = date(2026, 3, 12)
        _make_rec(tmp_path, yesterday, "09-00", "Meeting", 1800)
        today = date(2026, 3, 13)
        assert generate_daily_summary(tmp_path, target_date=today) is None

    def test_single_meeting(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Standup", 900, speaker_count=5)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert len(summary.meetings) == 1
        assert summary.meetings[0].subject == "Standup"
        assert summary.meetings[0].duration_min == 15
        assert summary.total_minutes == 15

    def test_multiple_meetings(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Standup", 900)
        _make_rec(tmp_path, today, "10-00", "Sprint Planning", 3600)
        _make_rec(tmp_path, today, "14-00", "Code Review", 1800)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert len(summary.meetings) == 3
        assert summary.total_minutes == 15 + 60 + 30

    def test_free_time_calculation(self, tmp_path):
        today = date(2026, 3, 13)
        # 4 hours of meetings in an 8-hour day = 50% free
        _make_rec(tmp_path, today, "09-00", "Meeting 1", 7200)
        _make_rec(tmp_path, today, "13-00", "Meeting 2", 7200)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.free_time_pct == 50.0

    def test_action_items_counted(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Planning", 3600,
                  action_items=[{"text": "Do A"}, {"text": "Do B"}])
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.total_action_items == 2
        assert summary.meetings[0].action_count == 2

    def test_decisions_counted(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Review", 3600,
                  decisions=[{"description": "Use Postgres"}, {"description": "Ship v2"}])
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.total_decisions == 2
        assert summary.meetings[0].decision_count == 2

    def test_busiest_hour(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Meeting A", 3600)
        _make_rec(tmp_path, today, "09-30", "Meeting B", 1800)
        _make_rec(tmp_path, today, "14-00", "Meeting C", 900)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.busiest_hour.startswith("09:")

    def test_busiest_hour_wraps_after_23(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "23-00", "Late Meeting", 3600)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.busiest_hour == "23:00-00:00"

    def test_time_extracted(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "14-30", "Afternoon Meeting", 1800)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.meetings[0].time == "14:30"

    def test_short_recording_excluded(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Quick", 10)  # too short
        assert generate_daily_summary(tmp_path, target_date=today) is None

    def test_date_field(self, tmp_path):
        today = date(2026, 3, 13)
        _make_rec(tmp_path, today, "09-00", "Meeting", 1800)
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary.date == "2026-03-13"

    def test_fallback_subject(self, tmp_path):
        today = date(2026, 3, 13)
        rec = tmp_path / f"{today.isoformat()}_09-00-00_Quick_Chat"
        rec.mkdir(parents=True)
        meta = {"duration_seconds": 1800}
        (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        summary = generate_daily_summary(tmp_path, target_date=today)
        assert summary is not None
        assert summary.meetings[0].subject == "Quick Chat"


class TestFormatDailySummary:
    def test_none(self):
        text = format_daily_summary(None)
        assert "No meetings" in text

    def test_basic(self):
        summary = DailySummary(
            date="2026-03-13",
            meetings=[
                DailyMeetingEntry(
                    time="09:00", subject="Standup", duration_min=15,
                    speaker_count=5, app_name="Zoom",
                    action_count=0, decision_count=0,
                    quality_score=80, meeting_type="standup",
                    path="/tmp/rec",
                ),
                DailyMeetingEntry(
                    time="10:00", subject="Sprint Planning", duration_min=60,
                    speaker_count=8, app_name="Teams",
                    action_count=3, decision_count=2,
                    quality_score=75, meeting_type="planning",
                    path="/tmp/rec2",
                ),
            ],
            total_minutes=75,
            total_action_items=3,
            total_decisions=2,
            free_time_pct=84.4,
            busiest_hour="10:00-11:00",
        )
        text = format_daily_summary(summary)
        assert "TODAY'S MEETINGS" in text
        assert "2026-03-13" in text
        assert "Standup" in text
        assert "Sprint Planning" in text
        assert "75 min" in text
        assert "3" in text  # action items
        assert "2" in text  # decisions

    def test_minimal(self):
        summary = DailySummary(
            date="2026-03-13",
            meetings=[
                DailyMeetingEntry(
                    time="09:00", subject="Quick Chat", duration_min=5,
                    speaker_count=2, app_name="",
                    action_count=0, decision_count=0,
                    quality_score=None, meeting_type="",
                    path="/tmp/rec",
                ),
            ],
            total_minutes=5,
            total_action_items=0,
            total_decisions=0,
            free_time_pct=99.0,
            busiest_hour="",
        )
        text = format_daily_summary(summary)
        assert "Quick Chat" in text
        assert "5 min" in text
