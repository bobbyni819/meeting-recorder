"""Tests for cross-recording follow-up tracker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.followups import (
    FollowUp,
    gather_followups,
    mark_completed,
    format_followups,
    _load_completion_status,
)


def _make_rec(base: Path, name: str, meta: dict = None, action_items: list = None) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        with open(rec / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)
    if action_items is not None:
        with open(rec / "action_items.json", "w", encoding="utf-8") as f:
            json.dump(action_items, f)
    return rec


class TestGatherFollowups:
    def test_basic(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint_Teams", meta={
            "meeting_subject": "Sprint Planning",
        }, action_items=[
            {"description": "Review the PR by Friday", "assignee": "Alice", "category": "assignment"},
            {"description": "Deploy to staging", "assignee": "", "category": "directive"},
        ])
        result = gather_followups(tmp_path)
        assert len(result) == 2
        assert result[0].description == "Review the PR by Friday"
        assert result[0].meeting_subject == "Sprint Planning"
        assert result[0].meeting_date == "2026-03-01"

    def test_multiple_recordings(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={
            "meeting_subject": "Sprint",
        }, action_items=[
            {"description": "Task A", "assignee": "", "category": "directive"},
        ])
        _make_rec(tmp_path, "2026-03-08_09-00-00_Budget", meta={
            "meeting_subject": "Budget",
        }, action_items=[
            {"description": "Task B", "assignee": "Bob", "category": "assignment"},
        ])
        result = gather_followups(tmp_path)
        assert len(result) == 2

    def test_sorted_by_date_descending(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Old", meta={}, action_items=[
            {"description": "Old task", "assignee": "", "category": ""},
        ])
        _make_rec(tmp_path, "2026-03-10_09-00-00_New", meta={}, action_items=[
            {"description": "New task", "assignee": "", "category": ""},
        ])
        result = gather_followups(tmp_path)
        assert result[0].meeting_date == "2026-03-10"
        assert result[1].meeting_date == "2026-03-01"

    def test_excludes_completed(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={}, action_items=[
            {"description": "Done task", "assignee": "", "category": ""},
            {"description": "Pending task", "assignee": "", "category": ""},
        ])
        mark_completed(rec, "Done task")
        result = gather_followups(tmp_path, include_completed=False)
        assert len(result) == 1
        assert result[0].description == "Pending task"

    def test_includes_completed(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={}, action_items=[
            {"description": "Done task", "assignee": "", "category": ""},
            {"description": "Pending task", "assignee": "", "category": ""},
        ])
        mark_completed(rec, "Done task")
        result = gather_followups(tmp_path, include_completed=True)
        assert len(result) == 2

    def test_empty_dir(self, tmp_path):
        assert gather_followups(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert gather_followups(tmp_path / "noexist") == []

    def test_no_action_items_file(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={})
        assert gather_followups(tmp_path) == []

    def test_corrupt_action_items(self, tmp_path):
        rec = tmp_path / "2026-03-01_09-00-00_Sprint"
        rec.mkdir()
        (rec / "action_items.json").write_text("bad json", encoding="utf-8")
        assert gather_followups(tmp_path) == []

    def test_subject_from_folder_name(self, tmp_path):
        """When no meeting_subject in meta, derives from folder name."""
        _make_rec(tmp_path, "2026-03-01_09-00-00_Budget_Review_Teams",
                  meta={}, action_items=[
            {"description": "Some task", "assignee": "", "category": ""},
        ])
        result = gather_followups(tmp_path)
        assert "Budget" in result[0].meeting_subject

    def test_empty_description_skipped(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={}, action_items=[
            {"description": "", "assignee": "", "category": ""},
            {"description": "Real task", "assignee": "", "category": ""},
        ])
        result = gather_followups(tmp_path)
        assert len(result) == 1


class TestMarkCompleted:
    def test_mark_and_load(self, tmp_path):
        mark_completed(tmp_path, "Task A", True)
        status = _load_completion_status(tmp_path)
        assert status["Task A"] is True

    def test_unmark(self, tmp_path):
        mark_completed(tmp_path, "Task A", True)
        mark_completed(tmp_path, "Task A", False)
        status = _load_completion_status(tmp_path)
        assert "Task A" not in status

    def test_multiple_items(self, tmp_path):
        mark_completed(tmp_path, "Task A", True)
        mark_completed(tmp_path, "Task B", True)
        status = _load_completion_status(tmp_path)
        assert status["Task A"] is True
        assert status["Task B"] is True


class TestFormatFollowups:
    def test_empty(self):
        assert format_followups([]) == "No pending follow-ups."

    def test_basic_format(self):
        followups = [
            FollowUp(
                description="Review the PR",
                assignee="Alice",
                category="assignment",
                meeting_subject="Sprint Planning",
                meeting_date="2026-03-01",
                recording_dir="/rec/2026-03-01",
            ),
        ]
        text = format_followups(followups)
        assert "PENDING FOLLOW-UPS" in text
        assert "Review the PR" in text
        assert "@Alice" in text
        assert "Sprint Planning" in text

    def test_grouped_by_meeting(self):
        followups = [
            FollowUp("Task A", "", "", "Sprint", "2026-03-01", "/a"),
            FollowUp("Task B", "", "", "Sprint", "2026-03-01", "/a"),
            FollowUp("Task C", "", "", "Budget", "2026-03-02", "/b"),
        ]
        text = format_followups(followups)
        assert "Sprint" in text
        assert "Budget" in text
        assert "Total: 3 items" in text

    def test_completed_count(self):
        followups = [
            FollowUp("Task A", "", "", "Sprint", "2026-03-01", "/a", completed=True),
            FollowUp("Task B", "", "", "Sprint", "2026-03-01", "/a", completed=False),
        ]
        text = format_followups(followups)
        assert "1 completed" in text
