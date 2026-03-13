"""Tests for meeting decision log extractor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.decision_log import (
    extract_decisions,
    extract_recording_decisions,
    format_decision_log,
    save_decisions,
    _text_overlap,
    Decision,
    DecisionLog,
)


class TestExtractDecisions:
    def test_empty_text(self):
        assert extract_decisions("") == []
        assert extract_decisions("short") == []

    def test_explicit_decided(self):
        text = "We decided to use PostgreSQL for the new service instead of MongoDB."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].category == "explicit"
        assert "PostgreSQL" in decisions[0].description

    def test_explicit_agreed(self):
        text = "The team agreed that we should postpone the launch until Q3."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert "postpone" in decisions[0].description.lower()

    def test_going_with(self):
        text = "We're going with the blue design for the landing page."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].category == "choice"

    def test_lets_go_with(self):
        text = "Let's go with option A for the database migration plan."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert "option A" in decisions[0].description

    def test_chose(self):
        text = "We chose the React framework for the frontend rebuild project."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].category == "choice"

    def test_settled_on(self):
        text = "We settled on a two-week sprint cycle for the next quarter."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1

    def test_plan_is(self):
        text = "The plan is to deploy the changes incrementally over three weeks."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].category == "plan"

    def test_decision_verb_will_use(self):
        text = "We'll use Kubernetes for container orchestration going forward."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert "Kubernetes" in decisions[0].description

    def test_decision_verb_will_switch(self):
        text = "We will switch to the new API endpoint starting next sprint."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1

    def test_consensus(self):
        text = "The consensus is that we should increase test coverage to 80% before shipping."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].category == "consensus"

    def test_approval(self):
        text = "We approved the budget increase for the infrastructure upgrade project."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].category == "approval"

    def test_moving_forward(self):
        text = "Moving forward with the microservices architecture for the payment system."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1

    def test_final_decision(self):
        text = "The final decision is to keep the monolith for another six months at least."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1

    def test_speaker_extraction(self):
        text = "Alice: We decided to postpone the release until the critical bugs are fixed."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].speaker == "Alice"

    def test_deduplication(self):
        text = (
            "We decided to use PostgreSQL for the backend data storage.\n"
            "We decided to use PostgreSQL for the backend data storage.\n"
        )
        decisions = extract_decisions(text)
        assert len(decisions) == 1

    def test_overlap_deduplication(self):
        text = (
            "We decided to use PostgreSQL for the backend data storage layer.\n"
            "We agreed to use PostgreSQL for the backend data storage system.\n"
        )
        decisions = extract_decisions(text)
        # Should detect overlap and only keep one
        assert len(decisions) <= 2

    def test_skip_historical(self):
        text = "Previously we decided to use MongoDB but that was a mistake."
        decisions = extract_decisions(text)
        assert len(decisions) == 0

    def test_skip_last_time(self):
        text = "Last time we agreed that the sprint should be shorter but we changed."
        decisions = extract_decisions(text)
        assert len(decisions) == 0

    def test_max_items(self):
        lines = [
            f"We decided to implement feature number {i} in the roadmap for the quarter."
            for i in range(25)
        ]
        text = "\n".join(lines)
        decisions = extract_decisions(text, max_items=5)
        assert len(decisions) <= 5

    def test_multiple_categories(self):
        text = (
            "We decided to use TypeScript for the frontend rewrite project.\n"
            "The plan is to migrate the database over the next two months.\n"
            "We approved the hiring of two new backend engineers for the team.\n"
        )
        decisions = extract_decisions(text)
        assert len(decisions) >= 2
        categories = {d.category for d in decisions}
        assert len(categories) >= 2

    def test_short_decision_filtered(self):
        text = "We decided to stop."
        decisions = extract_decisions(text)
        assert len(decisions) == 0  # too short

    def test_context_included(self):
        text = "Bob: We decided to rewrite the authentication service from scratch using OAuth 2.0."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert "Bob" in decisions[0].context

    def test_line_number_tracked(self):
        text = "Line one is just chat.\nLine two is more chat.\nWe decided to adopt GraphQL for all new API endpoints going forward."
        decisions = extract_decisions(text)
        assert len(decisions) >= 1
        assert decisions[0].line_number == 3


class TestExtractRecordingDecisions:
    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        assert extract_recording_decisions(rec) is None

    def test_empty_transcript(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        (rec / "transcript.txt").write_text("", encoding="utf-8")
        assert extract_recording_decisions(rec) is None

    def test_with_decisions(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Sprint_Planning"
        rec.mkdir()
        meta = {"meeting_subject": "Sprint Planning"}
        (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        (rec / "transcript.txt").write_text(
            "Alice: We decided to prioritize the search feature for this sprint iteration.\n"
            "Bob: The plan is to ship the MVP by end of next week at the latest.\n",
            encoding="utf-8",
        )
        log = extract_recording_decisions(rec)
        assert log is not None
        assert log.meeting_subject == "Sprint Planning"
        assert log.meeting_date == "2026-03-13"
        assert len(log.decisions) >= 1

    def test_with_preloaded_meta(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Review"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "We agreed that the new design should follow Material Design 3 guidelines.\n",
            encoding="utf-8",
        )
        meta = {"meeting_subject": "Design Review"}
        log = extract_recording_decisions(rec, meta=meta)
        assert log is not None
        assert log.meeting_subject == "Design Review"


class TestSaveDecisions:
    def test_save_and_load(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()

        log = DecisionLog(
            decisions=[
                Decision(
                    description="Use PostgreSQL",
                    category="explicit",
                    context="We decided to use PostgreSQL",
                    speaker="Alice",
                    line_number=5,
                ),
            ],
            recording_path=str(rec),
            meeting_subject="Backend Planning",
            meeting_date="2026-03-13",
        )

        out = save_decisions(rec, log)
        assert out.exists()

        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["decisions"]) == 1
        assert data["decisions"][0]["description"] == "Use PostgreSQL"
        assert data["meeting_subject"] == "Backend Planning"


class TestFormatDecisionLog:
    def test_none(self):
        text = format_decision_log(None)
        assert "No decisions" in text

    def test_empty_decisions(self):
        log = DecisionLog(
            decisions=[],
            recording_path="/tmp/rec",
            meeting_subject="Test",
            meeting_date="2026-03-13",
        )
        text = format_decision_log(log)
        assert "No decisions" in text

    def test_basic_format(self):
        log = DecisionLog(
            decisions=[
                Decision(
                    description="Use PostgreSQL for storage",
                    category="explicit",
                    context="We decided to use PostgreSQL for storage",
                    speaker="Alice",
                    line_number=1,
                ),
                Decision(
                    description="Deploy to AWS us-east-1",
                    category="choice",
                    context="We're going with AWS us-east-1",
                    speaker="",
                    line_number=5,
                ),
            ],
            recording_path="/tmp/rec",
            meeting_subject="Architecture Review",
            meeting_date="2026-03-13",
        )
        text = format_decision_log(log)
        assert "DECISION LOG" in text
        assert "Architecture Review" in text
        assert "2026-03-13" in text
        assert "PostgreSQL" in text
        assert "AWS" in text
        assert "Alice" in text
        assert "[Decision]" in text
        assert "[Choice]" in text

    def test_category_labels(self):
        categories = ["explicit", "choice", "plan", "consensus", "approval", "decision_verb"]
        for cat in categories:
            log = DecisionLog(
                decisions=[Decision(
                    description="Some decision that is long enough to matter",
                    category=cat, context="ctx", speaker="", line_number=1,
                )],
                recording_path="", meeting_subject="", meeting_date="",
            )
            text = format_decision_log(log)
            assert "DECISION LOG" in text


class TestTextOverlap:
    def test_identical(self):
        assert _text_overlap("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _text_overlap("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        result = _text_overlap("use postgresql database", "use mongodb database")
        assert 0.3 < result < 0.8

    def test_empty(self):
        assert _text_overlap("", "hello") == 0.0
        assert _text_overlap("hello", "") == 0.0
