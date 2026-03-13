"""Tests for action item extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.action_items import (
    ActionItem,
    extract_action_items,
    extract_action_items_for_recording,
    save_action_items,
    load_action_items,
    format_action_items,
    _clean_description,
    _normalize,
)


class TestExtractActionItems:
    def test_empty_text(self):
        """Empty text should return no items."""
        assert extract_action_items("") == []

    def test_short_text(self):
        """Very short text should return no items."""
        assert extract_action_items("Hello everyone") == []

    def test_commitment_i_will(self):
        """Should detect 'I will' commitments."""
        text = (
            "Thanks for the update. I will send the report to the team "
            "by end of day. Let me know if you have questions."
        )
        items = extract_action_items(text)
        assert len(items) >= 1
        found = any("send the report" in i.description.lower() for i in items)
        assert found

    def test_commitment_ill(self):
        """Should detect I'll contractions."""
        text = (
            "That sounds good. I'll schedule a follow-up meeting with "
            "the design team next week. We can review the mockups then."
        )
        items = extract_action_items(text)
        assert len(items) >= 1
        found = any("schedule" in i.description.lower() for i in items)
        assert found

    def test_assignment(self):
        """Should detect assignments to named people."""
        text = (
            "Sarah will prepare the presentation slides for the board. "
            "We need them by Thursday."
        )
        items = extract_action_items(text)
        assert len(items) >= 1
        found = any(
            "sarah" in i.assignee.lower() and "presentation" in i.description.lower()
            for i in items
        )
        assert found

    def test_team_action_we_need_to(self):
        """Should detect 'we need to' team actions."""
        text = (
            "Looking at the numbers, we need to revise the timeline "
            "for the product launch. The current estimate is too aggressive."
        )
        items = extract_action_items(text)
        assert len(items) >= 1
        found = any("revise the timeline" in i.description.lower() for i in items)
        assert found

    def test_team_action_lets(self):
        """Should detect 'let's' directives."""
        text = (
            "Good discussion. Let's set up a working group to tackle "
            "the migration issues. We can meet next Tuesday."
        )
        items = extract_action_items(text)
        assert len(items) >= 1
        found = any("set up a working group" in i.description.lower() for i in items)
        assert found

    def test_request(self):
        """Should detect requests (can you, could you)."""
        text = (
            "Can you send me the latest analytics dashboard? "
            "I need to review the conversion metrics before Monday."
        )
        items = extract_action_items(text)
        assert len(items) >= 1
        found = any("send" in i.description.lower() for i in items)
        assert found

    def test_directive_verbs(self):
        """Should detect action verbs at sentence start."""
        text = (
            "Follow up with the vendor about the contract terms. "
            "Schedule a review meeting for next week. "
            "Send the updated requirements to engineering."
        )
        items = extract_action_items(text)
        assert len(items) >= 2

    def test_explicit_markers(self):
        """Should detect explicit action item markers."""
        text = (
            "Action item: review the security audit findings before release. "
            "Follow-up: coordinate with legal on the partnership agreement."
        )
        items = extract_action_items(text)
        assert len(items) >= 1

    def test_filters_past_tense(self):
        """Should filter out past-tense items (already done)."""
        text = (
            "I already completed the report last week. "
            "We finished the deployment yesterday. "
            "I will start the new feature today."
        )
        items = extract_action_items(text)
        # Should only get the forward-looking "I will start"
        for item in items:
            assert "already" not in item.description.lower()
            assert "yesterday" not in item.description.lower()

    def test_deduplication(self):
        """Should not return duplicate items."""
        text = (
            "I will send the report to the team. "
            "I'll send the report to the team. "
            "I will prepare the budget analysis for Q3."
        )
        items = extract_action_items(text)
        descriptions = [_normalize(i.description) for i in items]
        assert len(descriptions) == len(set(descriptions))

    def test_max_items(self):
        """Should respect max_items limit."""
        text = "\n".join(
            f"Follow up with vendor {i} about the contract details and pricing."
            for i in range(30)
        )
        items = extract_action_items(text, max_items=5)
        assert len(items) <= 5

    def test_multiple_categories(self):
        """Should detect items across multiple categories."""
        text = (
            "I'll prepare the demo for the client meeting. "
            "Sarah should review the code before we merge. "
            "Let's schedule a retrospective for next Friday. "
            "Action item: update the documentation for the API changes."
        )
        items = extract_action_items(text)
        categories = {i.category for i in items}
        assert len(categories) >= 2

    def test_real_meeting_transcript(self):
        """Should extract meaningful items from realistic transcript."""
        text = (
            "John: Good morning everyone. Let's start with the sprint review.\n"
            "Sarah: I finished the login page redesign. Next, I will work on "
            "the password reset flow.\n"
            "John: Great work. Mike, can you review Sarah's pull request today?\n"
            "Mike: Sure. I'll also need to fix the CI pipeline issue.\n"
            "John: Let's make sure we address the performance regression before "
            "the release. Action item: run the load tests on staging.\n"
            "Sarah: I'll coordinate with QA on the test plan.\n"
            "John: Perfect. We need to finalize the release notes by Thursday."
        )
        items = extract_action_items(text)
        assert len(items) >= 3


class TestExtractForRecording:
    def test_from_transcript(self, tmp_path: Path):
        """Should read transcript from disk."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "I will send the quarterly report to all stakeholders. "
            "Follow up with the finance team about budget allocation.",
            encoding="utf-8",
        )
        items = extract_action_items_for_recording(rec)
        assert len(items) >= 1

    def test_from_summary(self, tmp_path: Path):
        """Should also scan summary.md."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        (rec / "summary.md").write_text(
            "## Action Items\n"
            "Action item: review the deployment checklist before Friday.\n"
            "Follow-up: schedule a design review with the UX team.",
            encoding="utf-8",
        )
        items = extract_action_items_for_recording(rec)
        assert len(items) >= 1

    def test_no_files(self, tmp_path: Path):
        """Should return empty list without files."""
        rec = tmp_path / "2026-03-10_09-00-00_Test"
        rec.mkdir()
        assert extract_action_items_for_recording(rec) == []


class TestSaveLoadActionItems:
    def test_round_trip(self, tmp_path: Path):
        """Should save and load action items correctly."""
        items = [
            ActionItem(
                description="Send the report",
                category="commitment",
                assignee="me",
                context="I will send the report to the team.",
                line_number=5,
            ),
            ActionItem(
                description="Review the code",
                category="assignment",
                assignee="Sarah",
                context="Sarah should review the code before merge.",
                line_number=12,
            ),
        ]
        save_action_items(tmp_path, items)
        loaded = load_action_items(tmp_path)
        assert len(loaded) == 2
        assert loaded[0].description == "Send the report"
        assert loaded[0].assignee == "me"
        assert loaded[1].description == "Review the code"
        assert loaded[1].assignee == "Sarah"

    def test_load_missing(self, tmp_path: Path):
        """Should return empty list if file doesn't exist."""
        assert load_action_items(tmp_path) == []

    def test_load_corrupted(self, tmp_path: Path):
        """Should return empty list for corrupted JSON."""
        (tmp_path / "action_items.json").write_text("bad json", encoding="utf-8")
        assert load_action_items(tmp_path) == []


class TestFormatActionItems:
    def test_empty(self):
        """Empty list should return empty string."""
        assert format_action_items([]) == ""

    def test_groups_by_category(self):
        """Should group items by category."""
        items = [
            ActionItem("Do the thing", "commitment", "me", "", 1),
            ActionItem("Fix the bug", "directive", "", "", 5),
            ActionItem("Action: review", "explicit", "", "", 10),
        ]
        text = format_action_items(items)
        assert "ACTION ITEMS" in text
        assert "Commitments" in text
        assert "Directives" in text
        assert "Explicit Action Items" in text

    def test_includes_assignee(self):
        """Should show assignee in brackets."""
        items = [
            ActionItem("Prepare slides", "assignment", "Sarah", "", 1),
        ]
        text = format_action_items(items)
        assert "[Sarah]" in text
        assert "Prepare slides" in text


class TestCleanDescription:
    def test_strips_whitespace(self):
        assert _clean_description("  hello world  ") == "Hello world"

    def test_removes_trailing_punctuation(self):
        assert _clean_description("do the thing,") == "Do the thing"
        assert _clean_description("do the thing;") == "Do the thing"

    def test_capitalizes(self):
        assert _clean_description("send the report") == "Send the report"

    def test_collapses_spaces(self):
        assert _clean_description("send   the   report") == "Send the report"


class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Hello WORLD") == "hello world"

    def test_strips(self):
        assert _normalize("  hello  ") == "hello"

    def test_collapses_spaces(self):
        assert _normalize("hello   world") == "hello world"
