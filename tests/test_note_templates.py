"""Tests for meeting note templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.note_templates import (
    list_templates,
    render_template,
    _build_context,
    _render_standard,
    _render_standup,
    _render_decision,
    _render_oneonone,
    _render_executive,
    TEMPLATES,
)


def _make_rec(
    base: Path,
    name: str = "2026-03-10_09-00-00_StandUp",
    meta: dict | None = None,
    summary: str = "",
    transcript: str = "",
    action_items: list | None = None,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    if meta is None:
        meta = {
            "duration_seconds": 1800,
            "meeting_subject": "Team StandUp",
            "app_name": "Zoom",
            "meeting_organizer": "Alice",
            "meeting_attendees": ["Alice", "Bob", "Charlie"],
            "speaker_count": 3,
            "tags": ["standup", "daily"],
        }
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    if action_items:
        with open(rec / "action_items.json", "w", encoding="utf-8") as f:
            json.dump(action_items, f)
    return rec


class TestListTemplates:
    def test_returns_list(self):
        templates = list_templates()
        assert len(templates) == 5

    def test_template_names(self):
        names = [t.name for t in list_templates()]
        assert "standard" in names
        assert "standup" in names
        assert "decision" in names
        assert "oneonone" in names
        assert "executive" in names


class TestBuildContext:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path)
        meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
        ctx = _build_context(rec, meta)
        assert ctx["subject"] == "Team StandUp"
        assert ctx["date"] == "2026-03-10"
        assert ctx["time"] == "09:00:00"
        assert ctx["duration"] == "30m"
        assert ctx["organizer"] == "Alice"
        assert len(ctx["attendees"]) == 3

    def test_with_summary(self, tmp_path):
        rec = _make_rec(tmp_path, summary="Key discussion points.")
        meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
        ctx = _build_context(rec, meta)
        assert "Key discussion" in ctx["summary"]

    def test_with_action_items(self, tmp_path):
        items = [{"description": "Send report", "assignee": "Bob"}]
        rec = _make_rec(tmp_path, action_items=items)
        meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
        ctx = _build_context(rec, meta)
        assert len(ctx["action_items"]) == 1

    def test_action_items_dict_shape_ignored(self, tmp_path):
        rec = _make_rec(tmp_path)
        (rec / "action_items.json").write_text(
            json.dumps({"task": "x"}), encoding="utf-8"
        )
        text = render_template("standard", rec)
        assert "# Team StandUp" in text

    def test_action_items_non_dict_list_entries_ignored(self, tmp_path):
        rec = _make_rec(tmp_path)
        (rec / "action_items.json").write_text(
            json.dumps(["text"]), encoding="utf-8"
        )
        text = render_template("standard", rec)
        assert "# Team StandUp" in text

    def test_hour_duration(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 7200})
        meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
        ctx = _build_context(rec, meta)
        assert "2h" in ctx["duration"]

    def test_empty_meta(self, tmp_path):
        rec = _make_rec(tmp_path, meta={})
        ctx = _build_context(rec, {})
        assert ctx["subject"] == rec.name


class TestRenderStandard:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path, summary="Discussed sprint progress.")
        text = render_template("standard", rec)
        assert "# Team StandUp" in text
        assert "2026-03-10" in text
        assert "Discussed sprint" in text
        assert "Alice" in text

    def test_with_action_items(self, tmp_path):
        items = [
            {"description": "Send report", "assignee": "Bob"},
            {"description": "Update docs", "assignee": ""},
        ]
        rec = _make_rec(tmp_path, action_items=items)
        text = render_template("standard", rec)
        assert "## Action Items" in text
        assert "Send report" in text
        assert "@Bob" in text
        assert "Update docs" in text

    def test_with_tags(self, tmp_path):
        rec = _make_rec(tmp_path)
        text = render_template("standard", rec)
        assert "standup" in text
        assert "daily" in text


class TestRenderStandup:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path)
        text = render_template("standup", rec)
        assert "Standup" in text
        assert "## Done" in text
        assert "## Doing" in text
        assert "## Blockers" in text

    def test_with_summary(self, tmp_path):
        rec = _make_rec(tmp_path, summary="Sprint review completed.")
        text = render_template("standup", rec)
        assert "Sprint review" in text


class TestRenderDecision:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path)
        text = render_template("decision", rec)
        assert "Decision Log" in text
        assert "## Context" in text
        assert "## Options Considered" in text
        assert "## Decision" in text
        assert "## Rationale" in text

    def test_with_context(self, tmp_path):
        rec = _make_rec(tmp_path, summary="Need to choose a database.")
        text = render_template("decision", rec)
        assert "choose a database" in text


class TestRenderOneonone:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path)
        text = render_template("oneonone", rec)
        assert "1:1" in text
        assert "## Talking Points" in text
        assert "## Feedback" in text
        assert "## Career / Growth" in text
        assert "## Next Meeting" in text


class TestRenderExecutive:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path, summary="Revenue up 15%.\nNew hire starting Monday.")
        text = render_template("executive", rec)
        assert "Executive Brief" in text
        assert "## Key Points" in text
        assert "Revenue" in text

    def test_limited_actions(self, tmp_path):
        items = [{"description": f"Item {i}", "assignee": ""} for i in range(10)]
        rec = _make_rec(tmp_path, action_items=items)
        text = render_template("executive", rec)
        # Executive template caps at 5 items
        assert "Item 0" in text
        assert "Item 4" in text
        assert "Item 5" not in text

    def test_attendee_count(self, tmp_path):
        rec = _make_rec(tmp_path)
        text = render_template("executive", rec)
        assert "3 attendees" in text


class TestRenderTemplate:
    def test_unknown_template_falls_back_to_standard(self, tmp_path):
        rec = _make_rec(tmp_path)
        text = render_template("nonexistent", rec)
        assert "# Team StandUp" in text

    def test_with_provided_meta(self, tmp_path):
        rec = _make_rec(tmp_path)
        meta = {"meeting_subject": "Override Subject", "duration_seconds": 3600}
        text = render_template("standard", rec, meta=meta)
        assert "Override Subject" in text
