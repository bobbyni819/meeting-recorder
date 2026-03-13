"""Tests for quick-look meeting summary card."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.quick_summary import (
    generate_quick_card,
    format_quick_card,
    _extract_key_points,
    _extract_top_actions,
    _extract_top_speakers,
    QuickCard,
)


def _make_rec(
    tmp_path: Path, meta: dict,
    summary: str = "", action_items: list | None = None,
    transcript_segments: list | None = None,
) -> Path:
    rec = tmp_path / "2026-03-13_09-00-00_Test_Meeting"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    if action_items is not None:
        (rec / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")
    if transcript_segments is not None:
        (rec / "transcript.json").write_text(
            json.dumps({"segments": transcript_segments}), encoding="utf-8"
        )
    return rec


class TestGenerateQuickCard:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path, {
            "duration_seconds": 1800,
            "meeting_subject": "Sprint Planning",
            "meeting_attendees": ["Alice", "Bob"],
            "app_name": "Zoom",
        })
        card = generate_quick_card(rec)
        assert card is not None
        assert card.subject == "Sprint Planning"
        assert card.duration_min == 30
        assert card.attendee_count == 2
        assert card.app_name == "Zoom"

    def test_no_metadata(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        assert generate_quick_card(rec) is None

    def test_too_short(self, tmp_path):
        rec = _make_rec(tmp_path, {"duration_seconds": 10})
        assert generate_quick_card(rec) is None

    def test_fallback_subject(self, tmp_path):
        rec = _make_rec(tmp_path, {
            "duration_seconds": 1800,
        })
        card = generate_quick_card(rec)
        assert card is not None
        assert card.subject == "Test Meeting"  # from directory name

    def test_with_summary(self, tmp_path):
        rec = _make_rec(tmp_path, {
            "duration_seconds": 1800,
            "meeting_subject": "Review",
        }, summary="# Review\n- Discussed the new feature requirements\n- Agreed on timeline\n- Next steps defined")
        card = generate_quick_card(rec)
        assert card is not None
        assert len(card.key_points) == 3

    def test_with_actions(self, tmp_path):
        rec = _make_rec(tmp_path, {
            "duration_seconds": 1800,
        }, action_items=[
            {"text": "Alice to send the report"},
            {"text": "Bob to review the PR"},
        ])
        card = generate_quick_card(rec)
        assert card is not None
        assert len(card.action_items) == 2

    def test_with_speakers(self, tmp_path):
        rec = _make_rec(tmp_path, {
            "duration_seconds": 1800,
            "speaker_map": {"SPEAKER_00": "Alice"},
        }, transcript_segments=[
            {"speaker": "SPEAKER_00", "start": 0, "end": 600},
            {"speaker": "SPEAKER_01", "start": 600, "end": 1200},
        ])
        card = generate_quick_card(rec)
        assert card is not None
        assert "Alice" in card.speakers

    def test_quality_score(self, tmp_path):
        rec = _make_rec(tmp_path, {
            "duration_seconds": 1800,
            "quality_scores": {"overall_score": 85},
        })
        card = generate_quick_card(rec)
        assert card is not None
        assert card.quality_score == 85


class TestExtractKeyPoints:
    def test_bullet_points(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "summary.md").write_text(
            "# Summary\n- First important point here\n- Second important point here\n- Third point",
            encoding="utf-8"
        )
        points = _extract_key_points(rec, 3)
        assert len(points) == 3

    def test_no_summary(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert _extract_key_points(rec, 3) == []

    def test_max_n(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "summary.md").write_text(
            "- Point one here is long enough\n- Point two here is long enough\n- Point three\n- Point four",
            encoding="utf-8"
        )
        points = _extract_key_points(rec, 2)
        assert len(points) == 2


class TestExtractTopActions:
    def test_dict_items(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "action_items.json").write_text(
            json.dumps([{"text": "Do A"}, {"text": "Do B"}]), encoding="utf-8"
        )
        items = _extract_top_actions(rec, 3)
        assert items == ["Do A", "Do B"]

    def test_no_file(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert _extract_top_actions(rec, 3) == []


class TestFormatQuickCard:
    def test_basic(self):
        card = QuickCard(
            subject="Sprint Planning",
            date="2026-03-13",
            duration_min=30,
            attendee_count=3,
            key_points=["Reviewed sprint goals", "Assigned tasks"],
            action_items=["Alice to write specs"],
            speakers=["Alice", "Bob"],
            quality_score=80,
            app_name="Zoom",
        )
        text = format_quick_card(card)
        assert "Sprint Planning" in text
        assert "30 min" in text
        assert "3 attendees" in text
        assert "Reviewed sprint goals" in text
        assert "Alice to write specs" in text
        assert "Alice, Bob" in text
        assert "80/100" in text

    def test_minimal(self):
        card = QuickCard(
            subject="Quick Chat",
            date="2026-03-13",
            duration_min=5,
            attendee_count=0,
            key_points=[],
            action_items=[],
            speakers=[],
            quality_score=None,
            app_name="",
        )
        text = format_quick_card(card)
        assert "Quick Chat" in text
        assert "5 min" in text
