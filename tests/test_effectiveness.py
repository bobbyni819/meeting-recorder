"""Tests for meeting effectiveness analysis."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.effectiveness import (
    analyze_effectiveness,
    format_effectiveness,
    _hour_to_slot,
    _count_actions,
    _generate_recommendations,
    MeetingEffectiveness,
    EffectivenessReport,
)


def _make_rec(
    base: Path, d: date, subject: str = "Meeting",
    duration: int = 1800, attendees: list | None = None,
    action_items: list | None = None,
    transcript_text: str = "",
    transcript_segments: list | None = None,
) -> Path:
    time_str = "09-00-00"
    name = f"{d.isoformat()}_{time_str}_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)

    meta = {
        "status": "completed",
        "duration_seconds": duration,
        "meeting_subject": subject,
        "meeting_attendees": attendees or [],
    }
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    if action_items:
        (rec / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")

    if transcript_text:
        (rec / "transcript.txt").write_text(transcript_text, encoding="utf-8")

    if transcript_segments:
        (rec / "transcript.json").write_text(
            json.dumps({"segments": transcript_segments}), encoding="utf-8"
        )

    return rec


def _this_week_date(offset_days: int = 0) -> date:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return week_start + timedelta(days=offset_days)


class TestHourToSlot:
    def test_early(self):
        assert _hour_to_slot(7) == "Early (< 9am)"

    def test_morning(self):
        assert _hour_to_slot(10) == "Morning"

    def test_midday(self):
        assert _hour_to_slot(12) == "Midday"

    def test_afternoon(self):
        assert _hour_to_slot(15) == "Afternoon"

    def test_evening(self):
        assert _hour_to_slot(18) == "Evening (5pm+)"


class TestCountActions:
    def test_no_file(self, tmp_path):
        assert _count_actions(tmp_path) == 0

    def test_with_actions(self, tmp_path):
        (tmp_path / "action_items.json").write_text(
            json.dumps([{"text": "foo"}, {"text": "bar"}]), encoding="utf-8"
        )
        assert _count_actions(tmp_path) == 2


class TestAnalyzeEffectiveness:
    def test_nonexistent_dir(self, tmp_path):
        assert analyze_effectiveness(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert analyze_effectiveness(tmp_path) is None

    def test_single_recording(self, tmp_path):
        d = _this_week_date(0)
        words = " ".join(["word"] * 500)
        _make_rec(tmp_path, d, "Standup", duration=1800,
                  attendees=["Alice", "Bob"],
                  action_items=[{"text": "Do thing"}],
                  transcript_text=words,
                  transcript_segments=[
                      {"speaker": "SPEAKER_00", "start": 0, "end": 900, "text": words[:200]},
                      {"speaker": "SPEAKER_01", "start": 900, "end": 1800, "text": words[200:]},
                  ])
        # Need at least 2 meetings
        d2 = _this_week_date(1)
        _make_rec(tmp_path, d2, "Review", duration=3600,
                  attendees=["Alice"],
                  transcript_text=words,
                  transcript_segments=[
                      {"speaker": "SPEAKER_00", "start": 0, "end": 3600, "text": words},
                  ])
        report = analyze_effectiveness(tmp_path)
        assert report is not None
        assert report.total_meetings == 2
        assert 0 <= report.avg_productivity <= 100

    def test_most_least_effective(self, tmp_path):
        words = " ".join(["productive"] * 500)
        for i in range(5):
            d = _this_week_date(0) - timedelta(days=i if i < 5 else 0)
            # Vary quality by adding action items to some
            actions = [{"text": f"action{j}"} for j in range(i)]
            _make_rec(tmp_path, d, f"Meeting{i}", duration=1800 + i * 600,
                      attendees=["Alice", "Bob"],
                      action_items=actions,
                      transcript_text=words,
                      transcript_segments=[
                          {"speaker": "S0", "start": 0, "end": 900, "text": words[:200]},
                          {"speaker": "S1", "start": 900, "end": 1800, "text": words[200:]},
                      ])

        report = analyze_effectiveness(tmp_path)
        assert report is not None
        assert len(report.most_effective) <= 5
        assert len(report.least_effective) <= 5

    def test_by_subject(self, tmp_path):
        words = " ".join(["discussion"] * 200)
        for i in range(4):
            d = _this_week_date(0) - timedelta(days=i)
            subj = "Standup" if i % 2 == 0 else "Review"
            _make_rec(tmp_path, d, subj, duration=1800,
                      transcript_text=words,
                      transcript_segments=[
                          {"speaker": "S0", "start": 0, "end": 1800, "text": words},
                      ])

        report = analyze_effectiveness(tmp_path)
        assert report is not None
        assert len(report.by_subject) >= 1

    def test_trend(self, tmp_path):
        words = " ".join(["meeting"] * 200)
        for w in range(6):
            d = _this_week_date(0) - timedelta(weeks=w)
            actions = [{"text": f"a{j}"} for j in range(w)]  # more actions in older weeks
            _make_rec(tmp_path, d, "Weekly", duration=1800,
                      action_items=actions,
                      transcript_text=words,
                      transcript_segments=[
                          {"speaker": "S0", "start": 0, "end": 1800, "text": words},
                      ])

        report = analyze_effectiveness(tmp_path)
        assert report is not None
        assert report.trend in ("improving", "declining", "stable")

    def test_short_recordings_skipped(self, tmp_path):
        d = _this_week_date(0)
        _make_rec(tmp_path, d, "Quick", duration=30)  # Too short
        _make_rec(tmp_path, d + timedelta(days=1), "Quick2", duration=30)
        assert analyze_effectiveness(tmp_path) is None


class TestGenerateRecommendations:
    def test_declining_trend(self):
        recs = _generate_recommendations([], [], [], [], "declining")
        assert any("declining" in r for r in recs)

    def test_improving_trend(self):
        recs = _generate_recommendations([], [], [], [], "improving")
        assert any("improving" in r for r in recs)

    def test_best_worst_day(self):
        by_weekday = [("Monday", 80.0), ("Friday", 40.0)]
        recs = _generate_recommendations([], by_weekday, [], [], "stable")
        assert any("Monday" in r for r in recs)

    def test_zero_actions_warning(self):
        meetings = [
            MeetingEffectiveness("m1", "2026-03-10", "A", 30, 60, 0, 3, 999),
            MeetingEffectiveness("m2", "2026-03-11", "B", 25, 45, 0, 2, 999),
            MeetingEffectiveness("m3", "2026-03-12", "C", 20, 30, 0, 4, 999),
        ]
        recs = _generate_recommendations(meetings, [], [], [], "stable")
        assert any("zero action" in r.lower() for r in recs)


class TestFormatEffectiveness:
    def test_none(self):
        text = format_effectiveness(None)
        assert "Not enough" in text

    def test_basic_format(self):
        report = EffectivenessReport(
            total_meetings=10,
            avg_productivity=65.0,
            trend="stable",
            trend_pct=2.0,
            most_effective=[
                MeetingEffectiveness("m1", "2026-03-10", "Sprint Planning", 85, 30, 4, 3, 37.5),
            ],
            least_effective=[
                MeetingEffectiveness("m2", "2026-03-11", "Status Sync", 25, 60, 0, 5, 0),
            ],
            by_subject=[("Sprint Planning", 85.0, 5), ("Status Sync", 30.0, 3)],
            by_weekday=[("Monday", 75.0), ("Friday", 50.0)],
            by_time_of_day=[("Morning", 70.0), ("Afternoon", 55.0)],
            recommendations=["Schedule important meetings on Monday mornings"],
        )
        text = format_effectiveness(report)
        assert "MEETING EFFECTIVENESS" in text
        assert "65" in text
        assert "Sprint Planning" in text
        assert "Status Sync" in text
        assert "Monday" in text
        assert "Recommendations" in text
