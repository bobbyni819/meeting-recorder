"""Tests for meeting ROI calculator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.meeting_roi import (
    calculate_roi,
    format_roi,
    aggregate_roi,
    _count_decisions,
    _generate_recommendations,
    MeetingROI,
)


def _make_rec(
    base: Path,
    name: str,
    duration: float = 3600,
    attendees: list[str] | None = None,
    summary: str = "",
    action_items: list[str] | None = None,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "meeting_attendees": attendees or [],
        "speaker_count": max(len(attendees or []), 1),
        "status": "completed",
    }
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    if action_items:
        # Create transcript with action item patterns
        lines = []
        for item in action_items:
            lines.append(f"We need to {item}.")
        (rec / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")
    return rec


class TestCalculateROI:
    def test_no_duration(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        duration=0)
        assert calculate_roi(rec) is None

    def test_basic_roi(self, tmp_path):
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            duration=3600,
            attendees=["Alice", "Bob", "Charlie"],
        )
        roi = calculate_roi(rec)
        assert roi is not None
        assert roi.duration_minutes == 60.0
        assert roi.attendee_count == 3
        assert roi.person_hours == 3.0
        assert roi.estimated_cost > 0

    def test_with_decisions(self, tmp_path):
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            duration=1800,
            attendees=["Alice", "Bob"],
            summary="We decided to proceed with option A. Also agreed to extend the deadline.",
        )
        roi = calculate_roi(rec)
        assert roi is not None
        assert roi.decision_count >= 2
        assert roi.output_count >= 2

    def test_no_outputs(self, tmp_path):
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            duration=3600,
            attendees=["Alice", "Bob", "Charlie", "Dave"],
        )
        roi = calculate_roi(rec)
        assert roi is not None
        assert roi.label == "no_outputs"
        assert roi.roi_score < 20

    def test_custom_hourly_rate(self, tmp_path):
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            duration=3600,
            attendees=["Alice"],
        )
        roi_default = calculate_roi(rec, hourly_rate=75)
        roi_high = calculate_roi(rec, hourly_rate=150)
        assert roi_high.estimated_cost == roi_default.estimated_cost * 2

    def test_high_value_short(self, tmp_path):
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            duration=1800,  # 30 min
            attendees=["Alice", "Bob"],
            summary="Decided to launch the feature. Agreed to push to prod. Approved budget.",
        )
        roi = calculate_roi(rec)
        assert roi is not None
        assert roi.roi_score >= 60

    def test_with_meta_param(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        duration=3600)
        meta = {
            "duration_seconds": 3600,
            "meeting_attendees": ["Alice", "Bob"],
            "speaker_count": 2,
        }
        roi = calculate_roi(rec, meta=meta)
        assert roi is not None
        assert roi.attendee_count == 2


class TestCountDecisions:
    def test_no_summary(self, tmp_path):
        rec = tmp_path / "test_rec"
        rec.mkdir()
        assert _count_decisions(rec) == 0

    def test_decisions_found(self, tmp_path):
        rec = tmp_path / "test_rec"
        rec.mkdir()
        (rec / "summary.md").write_text(
            "We decided to extend the deadline. "
            "Team agreed to increase test coverage. "
            "Approved the new design.",
            encoding="utf-8",
        )
        count = _count_decisions(rec)
        assert count >= 3

    def test_no_decisions(self, tmp_path):
        rec = tmp_path / "test_rec"
        rec.mkdir()
        (rec / "summary.md").write_text(
            "We discussed the project status and reviewed metrics.",
            encoding="utf-8",
        )
        assert _count_decisions(rec) == 0


class TestRecommendations:
    def test_no_outputs(self):
        recs = _generate_recommendations(30, 3, 0, 1.5)
        assert any("agenda" in r.lower() for r in recs)

    def test_long_few_outputs(self):
        recs = _generate_recommendations(90, 4, 1, 6.0)
        assert any("shorter" in r.lower() for r in recs)

    def test_many_attendees(self):
        recs = _generate_recommendations(60, 10, 3, 10.0)
        assert any("smaller" in r.lower() or "async" in r.lower() for r in recs)

    def test_efficient(self):
        recs = _generate_recommendations(25, 3, 5, 1.25)
        assert any("efficient" in r.lower() for r in recs)

    def test_max_three(self):
        recs = _generate_recommendations(90, 10, 0, 15.0)
        assert len(recs) <= 3


class TestAggregateROI:
    def test_empty_dir(self, tmp_path):
        assert aggregate_roi(tmp_path) == {}

    def test_nonexistent_dir(self, tmp_path):
        assert aggregate_roi(tmp_path / "nope") == {}

    def test_basic_aggregate(self, tmp_path):
        for i in range(3):
            _make_rec(
                tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                duration=3600,
                attendees=["Alice", "Bob"],
                summary="Decided to proceed.",
            )
        result = aggregate_roi(tmp_path)
        assert result["meeting_count"] == 3
        assert result["total_cost"] > 0
        assert result["avg_roi_score"] > 0


class TestFormatROI:
    def test_high_value(self):
        roi = MeetingROI(
            duration_minutes=30.0,
            attendee_count=3,
            person_hours=1.5,
            estimated_cost=112.0,
            action_item_count=4,
            decision_count=2,
            output_count=6,
            cost_per_output=19.0,
            roi_score=85,
            label="high_value",
            recommendations=["Efficient meeting — keep this format"],
        )
        text = format_roi(roi)
        assert "MEETING ROI" in text
        assert "85/100" in text
        assert "High Value" in text
        assert "$112" in text
        assert "4 action items" in text
        assert "$19" in text
        assert "Efficient" in text

    def test_no_outputs(self):
        roi = MeetingROI(
            duration_minutes=60.0,
            attendee_count=5,
            person_hours=5.0,
            estimated_cost=375.0,
            action_item_count=0,
            decision_count=0,
            output_count=0,
            cost_per_output=0,
            roi_score=10,
            label="no_outputs",
            recommendations=["Consider adding an agenda"],
        )
        text = format_roi(roi)
        assert "No Outputs" in text
        assert "agenda" in text
