"""Tests for meeting efficiency trend analysis."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.efficiency_trend import (
    analyze_efficiency_trend,
    format_efficiency_trend,
    _trend_direction,
    _duration_trend,
    _sparkline,
    WeekEfficiency,
    EfficiencyTrend,
)


def _make_rec(
    base: Path,
    date_str: str,
    duration: float = 3600,
    subject: str = "Meeting",
    attendees: list[str] | None = None,
    summary: str = "",
    transcript: str = "",
) -> Path:
    name = f"{date_str}_09-00-00_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "meeting_subject": subject,
        "meeting_attendees": attendees or ["Alice", "Bob"],
        "speaker_count": max(len(attendees or ["Alice", "Bob"]), 1),
        "status": "completed",
    }
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    return rec


class TestTrendDirection:
    def test_improving(self):
        assert _trend_direction([10, 20, 30, 40, 50, 60]) == "improving"

    def test_declining(self):
        assert _trend_direction([60, 50, 40, 30, 20, 10]) == "declining"

    def test_stable(self):
        assert _trend_direction([50, 50, 50, 50]) == "stable"

    def test_single_value(self):
        assert _trend_direction([42]) == "stable"

    def test_empty(self):
        assert _trend_direction([]) == "stable"


class TestDurationTrend:
    def test_getting_shorter(self):
        assert _duration_trend([60, 50, 40, 30]) == "getting shorter"

    def test_getting_longer(self):
        assert _duration_trend([30, 40, 50, 60]) == "getting longer"

    def test_stable(self):
        assert _duration_trend([30, 30, 30, 30]) == "stable"


class TestSparkline:
    def test_empty(self):
        assert _sparkline([]) == ""

    def test_increasing(self):
        spark = _sparkline([0, 25, 50, 75, 100])
        assert len(spark) == 5
        # First should be lowest, last highest
        assert spark[0] <= spark[-1]

    def test_constant(self):
        spark = _sparkline([50, 50, 50])
        assert len(spark) == 3

    def test_single(self):
        spark = _sparkline([42])
        assert len(spark) == 1


class TestAnalyzeEfficiencyTrend:
    def test_empty_dir(self, tmp_path):
        assert analyze_efficiency_trend(tmp_path) is None

    def test_nonexistent_dir(self, tmp_path):
        assert analyze_efficiency_trend(tmp_path / "nope") is None

    def test_single_week(self, tmp_path):
        now = datetime.now()
        date = now.strftime("%Y-%m-%d")
        _make_rec(tmp_path, date, duration=1800, summary="Decided to proceed.")
        trend = analyze_efficiency_trend(tmp_path, weeks=4)
        assert trend is not None
        assert len(trend.weeks) >= 1
        assert trend.overall_direction == "stable"

    def test_multiple_weeks(self, tmp_path):
        now = datetime.now()
        for i in range(4):
            date = (now - timedelta(weeks=i)).strftime("%Y-%m-%d")
            _make_rec(
                tmp_path, date,
                duration=3600,
                subject=f"Meeting{i}",
                summary="We decided to extend the deadline.",
            )
        trend = analyze_efficiency_trend(tmp_path, weeks=8)
        assert trend is not None
        assert len(trend.weeks) >= 1
        assert trend.sparkline != ""

    def test_old_data_excluded(self, tmp_path):
        # Create a recording far in the past
        old_date = (datetime.now() - timedelta(weeks=20)).strftime("%Y-%m-%d")
        _make_rec(tmp_path, old_date, duration=3600)
        trend = analyze_efficiency_trend(tmp_path, weeks=4)
        assert trend is None  # No data in the window


class TestFormatEfficiencyTrend:
    def test_basic_format(self):
        trend = EfficiencyTrend(
            weeks=[
                WeekEfficiency(
                    week_start="2026-03-03",
                    meeting_count=5,
                    avg_duration_min=45.0,
                    avg_roi_score=65.0,
                    avg_participation_equity=78.0,
                    avg_sentiment=0.3,
                    total_action_items=12,
                    total_person_hours=15.0,
                ),
                WeekEfficiency(
                    week_start="2026-03-10",
                    meeting_count=3,
                    avg_duration_min=35.0,
                    avg_roi_score=72.0,
                    avg_participation_equity=82.0,
                    avg_sentiment=0.4,
                    total_action_items=8,
                    total_person_hours=9.0,
                ),
            ],
            overall_direction="improving",
            roi_trend="improving",
            participation_trend="improving",
            duration_trend="getting shorter",
            sparkline="\u2582\u2586",
        )
        text = format_efficiency_trend(trend)
        assert "MEETING EFFICIENCY TREND" in text
        assert "Improving" in text
        assert "w/03-03" in text
        assert "w/03-10" in text
        assert "5 mtgs" in text
        assert "12 actions" in text

    def test_empty_trend(self):
        trend = EfficiencyTrend(
            weeks=[],
            overall_direction="stable",
            roi_trend="stable",
            participation_trend="stable",
            duration_trend="stable",
            sparkline="",
        )
        text = format_efficiency_trend(trend)
        assert "No efficiency data" in text
