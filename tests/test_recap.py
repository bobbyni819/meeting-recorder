"""Tests for meeting recap generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.recap import (
    generate_recap,
    format_recap,
    MeetingRecap,
)


def _make_rec(tmp_path: Path, name: str = "2026-03-10_09-00-00_Sprint_Planning",
              **kwargs) -> Path:
    rec = tmp_path / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": kwargs.get("duration", 1800),
        "meeting_subject": kwargs.get("subject", "Sprint Planning"),
        "speaker_count": kwargs.get("speakers", 3),
        "speaker_map": kwargs.get("speaker_map", {}),
    }
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rec


class TestGenerateRecap:
    def test_no_dir(self, tmp_path):
        assert generate_recap(tmp_path / "nope") is None

    def test_no_metadata(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert generate_recap(rec) is None

    def test_too_short(self, tmp_path):
        rec = _make_rec(tmp_path, duration=30)
        assert generate_recap(rec) is None

    def test_no_content(self, tmp_path):
        rec = _make_rec(tmp_path)
        # No summary, action items, or decisions
        assert generate_recap(rec) is None

    def test_with_summary(self, tmp_path):
        rec = _make_rec(tmp_path)
        (rec / "summary.md").write_text(
            "## Summary\n"
            "- First key point about the sprint\n"
            "- Second key point about planning\n",
            encoding="utf-8",
        )
        recap = generate_recap(rec)
        assert recap is not None
        assert recap.subject == "Sprint Planning"
        assert len(recap.summary_lines) == 2

    def test_with_action_items(self, tmp_path):
        rec = _make_rec(tmp_path)
        (rec / "action_items.json").write_text(json.dumps([
            {"text": "Alice needs to update the deployment script", "assignee": "Alice"},
            {"text": "Bob should review the pull request", "assignee": "Bob"},
        ]), encoding="utf-8")
        (rec / "summary.md").write_text("- Key point about the work\n", encoding="utf-8")
        recap = generate_recap(rec)
        assert recap is not None
        assert len(recap.action_items) == 2
        assert "[Alice]" in recap.action_items[0]

    def test_with_decisions(self, tmp_path):
        rec = _make_rec(tmp_path)
        (rec / "decisions.json").write_text(json.dumps({
            "decisions": [
                {"description": "We will use PostgreSQL for the new service"},
                {"description": "Deploy to staging by end of week"},
            ]
        }), encoding="utf-8")
        (rec / "summary.md").write_text("- Key point\n", encoding="utf-8")
        recap = generate_recap(rec)
        assert recap is not None
        assert len(recap.decisions) == 2

    def test_speaker_map(self, tmp_path):
        rec = _make_rec(tmp_path, speaker_map={"S0": "Alice", "S1": "Bob"})
        (rec / "summary.md").write_text("- Key point about the discussion\n", encoding="utf-8")
        recap = generate_recap(rec)
        assert recap is not None
        assert "Alice" in recap.speakers
        assert "Bob" in recap.speakers

    def test_subject_from_folder(self, tmp_path):
        rec = _make_rec(tmp_path, name="2026-03-10_09-00-00_Team_Standup",
                        subject="")
        (rec / "summary.md").write_text("- Key point\n", encoding="utf-8")
        recap = generate_recap(rec)
        assert recap is not None
        assert "Team Standup" in recap.subject

    def test_string_action_items(self, tmp_path):
        rec = _make_rec(tmp_path)
        (rec / "action_items.json").write_text(json.dumps([
            "Update the documentation before release",
            "Review the test coverage report",
        ]), encoding="utf-8")
        (rec / "summary.md").write_text("- Key point\n", encoding="utf-8")
        recap = generate_recap(rec)
        assert recap is not None
        assert len(recap.action_items) == 2

    def test_date_extraction(self, tmp_path):
        rec = _make_rec(tmp_path, name="2026-03-10_09-00-00_Meeting")
        (rec / "summary.md").write_text("- Key point\n", encoding="utf-8")
        recap = generate_recap(rec)
        assert recap is not None
        assert recap.date == "2026-03-10"

    def test_pre_loaded_meta(self, tmp_path):
        rec = tmp_path / "2026-03-10_09-00-00_Meeting"
        rec.mkdir(parents=True)
        meta = {"duration_seconds": 1800, "meeting_subject": "Test"}
        (rec / "summary.md").write_text("- Important discussion point\n", encoding="utf-8")
        recap = generate_recap(rec, meta=meta)
        assert recap is not None
        assert recap.subject == "Test"


class TestFormatRecap:
    def test_none(self):
        text = format_recap(None)
        assert "Not enough data" in text

    def test_basic(self):
        recap = MeetingRecap(
            subject="Sprint Planning",
            date="2026-03-10",
            duration_min=30.0,
            speakers=["Alice", "Bob", "Carol"],
            summary_lines=["Reviewed sprint backlog", "Assigned tasks for the week"],
            action_items=["[Alice] Update deployment script", "[Bob] Review PR #42"],
            decisions=["Use PostgreSQL for new service"],
            unanswered_questions=["What about the security audit timeline?"],
            key_topics=["deployment", "database", "sprint"],
        )
        text = format_recap(recap)
        assert "MEETING RECAP: Sprint Planning" in text
        assert "2026-03-10" in text
        assert "30 min" in text
        assert "KEY POINTS" in text
        assert "DECISIONS" in text
        assert "ACTION ITEMS" in text
        assert "OPEN QUESTIONS" in text
        assert "security audit" in text
        assert "Alice" in text

    def test_minimal(self):
        recap = MeetingRecap(
            subject="Quick Sync",
            date="2026-03-10",
            duration_min=15.0,
            speakers=[],
            summary_lines=["Discussed the current status of the project"],
            action_items=[],
            decisions=[],
            unanswered_questions=[],
            key_topics=[],
        )
        text = format_recap(recap)
        assert "MEETING RECAP: Quick Sync" in text
        assert "KEY POINTS" in text
        assert "ACTION ITEMS" not in text  # no action items to show
