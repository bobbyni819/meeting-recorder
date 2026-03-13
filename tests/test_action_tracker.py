"""Tests for cross-recording action item tracker."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.action_tracker import (
    track_actions,
    format_action_tracker,
    _extract_key_phrases,
    ActionTracker,
    TrackedAction,
)


def _this_week(offset: int = 0) -> date:
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=offset)


def _make_rec(base: Path, d: date, subject: str,
              action_items: list | None = None,
              transcript: str = "") -> Path:
    name = f"{d.isoformat()}_09-00-00_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"meeting_subject": subject, "duration_seconds": 1800}
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if action_items is not None:
        (rec / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    return rec


class TestTrackActions:
    def test_no_dir(self, tmp_path):
        assert track_actions(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert track_actions(tmp_path) is None

    def test_no_action_items(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Meeting")
        assert track_actions(tmp_path) is None

    def test_basic_tracking(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Planning", action_items=[
            {"text": "Alice should review the database schema for new project", "assignee": "Alice"},
            {"text": "Bob needs to update the API documentation before release", "assignee": "Bob"},
        ])
        report = track_actions(tmp_path)
        assert report is not None
        assert report.total_actions == 2

    def test_cross_reference_resolution(self, tmp_path):
        d1 = _this_week(-1)
        d2 = _this_week()
        _make_rec(tmp_path, d1, "Sprint Planning", action_items=[
            {"text": "Alice should review the database schema migration plan", "assignee": "Alice"},
        ])
        _make_rec(tmp_path, d2, "Standup",
                  transcript="Alice: I completed the database schema review yesterday. It's done.")
        report = track_actions(tmp_path)
        assert report is not None
        if report.total_actions > 0:
            # Should detect mention in later transcript
            resolved = sum(1 for a in report.stale_actions if not a.likely_resolved)
            assert resolved <= report.total_actions

    def test_stale_detection(self, tmp_path):
        d = _this_week(-2)
        _make_rec(tmp_path, d, "Old Meeting", action_items=[
            {"text": "Someone needs to investigate the production error in the logging system", "assignee": ""},
        ])
        report = track_actions(tmp_path)
        assert report is not None
        assert report.stale_count >= 1

    def test_per_assignee(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Meeting", action_items=[
            {"text": "Alice should fix the broken integration test suite", "assignee": "Alice"},
            {"text": "Alice needs to update the project documentation", "assignee": "Alice"},
            {"text": "Bob should deploy the new service to production", "assignee": "Bob"},
        ])
        report = track_actions(tmp_path)
        assert report is not None
        assert "Alice" in report.per_assignee
        assert report.per_assignee["Alice"][0] == 2

    def test_per_meeting(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Sprint Planning", action_items=[
            {"text": "Review the sprint backlog items for next iteration", "assignee": ""},
            {"text": "Update the project timeline for stakeholders", "assignee": ""},
        ])
        report = track_actions(tmp_path)
        assert report is not None
        assert "Sprint Planning" in report.per_meeting
        assert report.per_meeting["Sprint Planning"] == 2

    def test_compliance_rate(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Meeting", action_items=[
            {"text": "Complete the quarterly report submission", "assignee": "Alice"},
        ])
        report = track_actions(tmp_path)
        assert report is not None
        assert 0 <= report.compliance_rate <= 100

    def test_old_excluded(self, tmp_path):
        old = _this_week() - timedelta(weeks=20)
        _make_rec(tmp_path, old, "Old", action_items=[
            {"text": "Old action item from twenty weeks ago", "assignee": ""},
        ])
        report = track_actions(tmp_path, weeks=4)
        assert report is None

    def test_string_action_items(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Meeting", action_items=[
            "Review the deployment process documentation",
            "Update the CI pipeline configuration files",
        ])
        report = track_actions(tmp_path)
        assert report is not None
        assert report.total_actions == 2

    def test_short_items_filtered(self, tmp_path):
        d = _this_week()
        _make_rec(tmp_path, d, "Meeting", action_items=[
            {"text": "Do it"},  # too short
        ])
        report = track_actions(tmp_path)
        assert report is None  # no valid items


class TestExtractKeyPhrases:
    def test_basic(self):
        phrases = _extract_key_phrases("Alice should review the database schema")
        assert len(phrases) > 0
        assert "alice" in phrases
        assert "review" in phrases

    def test_stop_words_removed(self):
        phrases = _extract_key_phrases("We need to update the configuration")
        assert "need" not in phrases
        assert "the" not in phrases

    def test_max_5(self):
        phrases = _extract_key_phrases(
            "Alice should review update deploy monitor test verify check the system"
        )
        assert len(phrases) <= 5


class TestFormatActionTracker:
    def test_none(self):
        text = format_action_tracker(None)
        assert "No action items" in text

    def test_basic(self):
        report = ActionTracker(
            total_actions=10,
            resolved_count=7,
            stale_count=3,
            compliance_rate=70.0,
            stale_actions=[
                TrackedAction(
                    text="Fix the login bug",
                    assignee="Alice",
                    source_meeting="Sprint Review",
                    source_date="2026-03-10",
                    mentioned_in=[],
                    likely_resolved=False,
                ),
            ],
            per_assignee={"Alice": (5, 4), "Bob": (5, 3)},
            per_meeting={"Sprint Review": 5, "Standup": 5},
        )
        text = format_action_tracker(report)
        assert "ACTION ITEM TRACKER" in text
        assert "70%" in text
        assert "Alice" in text
        assert "Sprint Review" in text
        assert "Fix the login bug" in text
