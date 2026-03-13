"""Tests for meeting question tracker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.question_tracker import (
    extract_questions,
    analyze_questions,
    format_question_report,
    _is_question,
    QuestionReport,
    Question,
)


class TestIsQuestion:
    def test_explicit_question_mark(self):
        assert _is_question("What time is the meeting?")

    def test_question_word_no_mark(self):
        assert _is_question("How should we approach this problem")

    def test_not_a_question(self):
        assert not _is_question("This is a statement about the project.")

    def test_too_short(self):
        assert not _is_question("Why?")

    def test_filler_question(self):
        assert not _is_question("You know?")
        assert not _is_question("Right?")
        assert not _is_question("Okay?")

    def test_can_should_would(self):
        assert _is_question("Can we deploy this on Friday?")
        assert _is_question("Should we refactor the module first?")
        assert _is_question("Would that work for everyone?")

    def test_is_are_was(self):
        assert _is_question("Is that the final version?")
        assert _is_question("Are we done with this feature?")

    def test_empty_string(self):
        assert not _is_question("")
        assert not _is_question("   ")


class TestExtractQuestions:
    def test_empty_text(self):
        assert extract_questions("") == []
        assert extract_questions("short") == []

    def test_basic_extraction(self):
        text = """Alice: Welcome everyone.
Alice: Let me present the project update.
Bob: What is the timeline for the new feature?
Alice: We're aiming for next Friday.
Carol: Should we include the legacy migration in this sprint?
Alice: I think so, yes."""
        qs = extract_questions(text)
        assert len(qs) >= 2
        assert any("timeline" in q.text.lower() for q in qs)
        assert any("migration" in q.text.lower() for q in qs)

    def test_speaker_detection(self):
        text = """Bob: What is the timeline for the new feature?
Alice: We're aiming for next Friday."""
        qs = extract_questions(text)
        assert len(qs) >= 1
        assert qs[0].speaker == "Bob"

    def test_answer_detection(self):
        text = """Bob: What is the timeline for the new feature?
Alice: We're aiming for next Friday. I think two weeks is realistic.
Carol: Should we include testing in the estimate?
Bob: That's a good point. Let me think about it."""
        qs = extract_questions(text)
        assert len(qs) >= 1
        # First question should be answered
        timeline_q = next((q for q in qs if "timeline" in q.text.lower()), None)
        assert timeline_q is not None
        assert timeline_q.likely_answered is True

    def test_unanswered_question(self):
        text = """Bob: What about the security audit for the new endpoints?
Alice: Moving on to the next topic.
Carol: Let me share the design document now.
Dave: Here are the mockups I prepared.
Eve: The stakeholder meeting is tomorrow."""
        qs = extract_questions(text)
        security_q = next((q for q in qs if "security" in q.text.lower()), None)
        assert security_q is not None
        assert security_q.likely_answered is False

    def test_max_questions(self):
        lines = [f"Speaker: What is question number {i}?" for i in range(50)]
        text = "\n".join(lines)
        qs = extract_questions(text, max_questions=10)
        assert len(qs) == 10

    def test_line_numbers(self):
        text = """Line one statement.
Bob: What is this about?
Another statement here."""
        qs = extract_questions(text)
        assert len(qs) >= 1
        assert qs[0].line_number == 2

    def test_multiple_questions_per_line(self):
        text = "Bob: Is this ready? Should we deploy it now?"
        qs = extract_questions(text)
        assert len(qs) >= 1  # At least one question detected

    def test_no_speaker_prefix(self):
        text = """What about the deadline for the release?
I think we need more time for testing."""
        qs = extract_questions(text)
        assert len(qs) >= 1
        assert qs[0].speaker == ""


class TestAnalyzeQuestions:
    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_questions(rec) is None

    def test_short_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.txt").write_text("Hi.", encoding="utf-8")
        assert analyze_questions(rec) is None

    def test_no_questions(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "Alice: This is a statement about the project.\n"
            "Bob: I agree with the approach completely.\n"
            "Carol: Let me share my thoughts on this.\n",
            encoding="utf-8",
        )
        assert analyze_questions(rec) is None

    def test_basic_report(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "Alice: What should we focus on this sprint?\n"
            "Bob: I think we should focus on the backend refactor.\n"
            "Carol: How long will the refactor take to complete?\n"
            "Bob: About two weeks, I believe.\n"
            "Alice: Should we involve the frontend team in this?\n"
            "Dave: Let me check the schedule first.\n",
            encoding="utf-8",
        )
        report = analyze_questions(rec)
        assert report is not None
        assert report.total_questions >= 2
        assert report.answered_count >= 1
        assert "Alice" in report.per_speaker

    def test_per_speaker_counts(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "Alice: What is the plan for the deployment?\n"
            "Bob: We deploy on Friday morning.\n"
            "Alice: How do we handle the rollback scenario?\n"
            "Bob: We have a rollback script ready.\n"
            "Carol: Who is responsible for monitoring?\n"
            "Bob: The ops team handles monitoring.\n",
            encoding="utf-8",
        )
        report = analyze_questions(rec)
        assert report is not None
        assert report.per_speaker.get("Alice", 0) >= 2
        assert report.per_speaker.get("Carol", 0) >= 1

    def test_top_questioner(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.txt").write_text(
            "Alice: What should we do about the bug?\n"
            "Bob: Fix it immediately.\n"
            "Alice: How should we test the fix?\n"
            "Bob: Unit tests and integration tests.\n"
            "Alice: When should we deploy the fix?\n"
            "Bob: As soon as possible.\n",
            encoding="utf-8",
        )
        report = analyze_questions(rec)
        assert report is not None
        assert report.top_questioner == "Alice"


class TestFormatQuestionReport:
    def test_none(self):
        text = format_question_report(None)
        assert "No questions" in text

    def test_basic(self):
        report = QuestionReport(
            total_questions=5,
            answered_count=3,
            unanswered_count=2,
            unanswered_questions=[
                Question(
                    text="What about the security audit?",
                    speaker="Bob",
                    line_number=10,
                    likely_answered=False,
                    answer_context="",
                ),
                Question(
                    text="When is the deadline for this feature?",
                    speaker="Carol",
                    line_number=25,
                    likely_answered=False,
                    answer_context="",
                ),
            ],
            per_speaker={"Alice": 2, "Bob": 2, "Carol": 1},
            top_questioner="Alice",
        )
        text = format_question_report(report)
        assert "QUESTION TRACKER" in text
        assert "5" in text  # total
        assert "3" in text  # answered
        assert "security audit" in text
        assert "Alice" in text
        assert "Bob" in text
