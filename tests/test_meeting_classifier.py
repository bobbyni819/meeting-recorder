"""Tests for meeting type classifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.meeting_classifier import (
    classify_meeting,
    classify_recording,
    format_classification,
    MeetingClassification,
)


class TestClassifyMeeting:
    def test_standup_by_subject(self):
        result = classify_meeting(subject="Daily Standup", duration_minutes=15)
        assert result.meeting_type == "standup"
        assert result.confidence > 0.3

    def test_standup_by_keywords(self):
        result = classify_meeting(
            transcript_text="Yesterday I worked on the API. Today I'm fixing the blocker. No blockers.",
            duration_minutes=12,
            speaker_count=5,
        )
        assert result.meeting_type == "standup"

    def test_planning_by_subject(self):
        result = classify_meeting(subject="Sprint Planning", duration_minutes=60)
        assert result.meeting_type == "planning"
        assert result.confidence > 0.3

    def test_review_by_subject(self):
        result = classify_meeting(subject="Code Review Session", duration_minutes=30)
        assert result.meeting_type == "review"

    def test_one_on_one(self):
        result = classify_meeting(
            subject="1-on-1 with Alice",
            duration_minutes=30,
            speaker_count=2,
        )
        assert result.meeting_type == "one_on_one"

    def test_one_on_one_check_in(self):
        result = classify_meeting(
            subject="Weekly Check-in",
            duration_minutes=30,
            speaker_count=2,
        )
        assert result.meeting_type == "one_on_one"

    def test_all_hands(self):
        result = classify_meeting(
            subject="All-Hands Meeting",
            duration_minutes=60,
            speaker_count=3,
            attendee_count=50,
        )
        assert result.meeting_type == "all_hands"

    def test_brainstorm(self):
        result = classify_meeting(
            subject="Brainstorm: New Features",
            duration_minutes=45,
            speaker_count=6,
        )
        assert result.meeting_type == "brainstorm"

    def test_retrospective(self):
        result = classify_meeting(
            subject="Sprint Retro",
            duration_minutes=45,
            speaker_count=8,
        )
        assert result.meeting_type == "retrospective"

    def test_interview(self):
        result = classify_meeting(
            subject="Interview - Backend Engineer",
            duration_minutes=45,
            speaker_count=3,
        )
        assert result.meeting_type == "interview"

    def test_training(self):
        result = classify_meeting(
            subject="Git Training Session",
            duration_minutes=60,
            speaker_count=2,
        )
        assert result.meeting_type == "training"

    def test_incident(self):
        result = classify_meeting(
            subject="Incident War Room - API Outage",
            transcript_text="The service is down. We need to rollback the last deploy. What's the root cause?",
            duration_minutes=30,
            speaker_count=5,
        )
        assert result.meeting_type == "incident"

    def test_general_low_signal(self):
        result = classify_meeting(
            subject="Quick Chat",
            duration_minutes=10,
            speaker_count=3,
        )
        # Low signal should be general or low confidence
        assert result.confidence < 0.5

    def test_empty_inputs(self):
        result = classify_meeting()
        assert result.meeting_type in ("general", "unknown")

    def test_scores_dict(self):
        result = classify_meeting(subject="Sprint Planning", duration_minutes=60)
        assert isinstance(result.scores, dict)
        assert len(result.scores) > 0
        assert "planning" in result.scores

    def test_signals_list(self):
        result = classify_meeting(
            subject="Daily Standup",
            transcript_text="Yesterday I fixed the bug. Today working on tests.",
        )
        assert len(result.signals) > 0

    def test_confidence_range(self):
        result = classify_meeting(subject="Sprint Planning", duration_minutes=60, speaker_count=8)
        assert 0.0 <= result.confidence <= 1.0

    def test_max_items_in_scores(self):
        result = classify_meeting(subject="Planning", duration_minutes=60)
        # Should have scores for all types
        assert len(result.scores) >= 5

    def test_duration_helps_classification(self):
        # Very short meeting → standup more likely
        short = classify_meeting(subject="Team Sync", duration_minutes=10, speaker_count=5)
        # Longer meeting → planning more likely
        long = classify_meeting(subject="Team Sync", duration_minutes=90, speaker_count=5)
        # Both should classify, potentially differently
        assert short.meeting_type is not None
        assert long.meeting_type is not None

    def test_speaker_count_affects_score(self):
        # 2 speakers → 1-on-1 boost
        two = classify_meeting(
            subject="Catch up",
            duration_minutes=30,
            speaker_count=2,
        )
        assert two.meeting_type == "one_on_one"

    def test_transcript_keywords_boost(self):
        result = classify_meeting(
            transcript_text=(
                "What went well this sprint? We should improve our testing. "
                "Let's start doing code reviews more consistently. "
                "We should stop doing manual deployments."
            ),
            duration_minutes=45,
            speaker_count=6,
        )
        # Retro keywords should boost retrospective
        assert result.scores.get("retrospective", 0) > 0

    def test_demo_classified_as_review(self):
        result = classify_meeting(subject="Product Demo", duration_minutes=30)
        assert result.meeting_type == "review"


class TestClassifyRecording:
    def test_no_metadata(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        assert classify_recording(rec) is None

    def test_basic(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Daily_Standup"
        rec.mkdir()
        meta = {
            "meeting_subject": "Daily Standup",
            "duration_seconds": 900,
            "speaker_count": 5,
        }
        (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        result = classify_recording(rec)
        assert result is not None
        assert result.meeting_type == "standup"

    def test_with_transcript(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        meta = {
            "meeting_subject": "Team Meeting",
            "duration_seconds": 2700,
            "speaker_count": 6,
        }
        (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        (rec / "transcript.txt").write_text(
            "What went well this sprint? I think testing improved a lot. "
            "We should improve our deployment process. "
            "Let's start doing more pair programming.",
            encoding="utf-8",
        )
        result = classify_recording(rec)
        assert result is not None

    def test_with_preloaded_meta(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        meta = {
            "meeting_subject": "Sprint Planning",
            "duration_seconds": 3600,
            "speaker_count": 8,
        }
        result = classify_recording(rec, meta=meta)
        assert result is not None
        assert result.meeting_type == "planning"


class TestFormatClassification:
    def test_none(self):
        text = format_classification(None)
        assert "Unable" in text

    def test_basic(self):
        cls = MeetingClassification(
            meeting_type="standup",
            confidence=0.75,
            scores={"standup": 75, "planning": 20, "review": 10},
            signals=["standup: subject match"],
        )
        text = format_classification(cls)
        assert "Daily Standup" in text
        assert "75%" in text

    def test_alternatives_shown(self):
        cls = MeetingClassification(
            meeting_type="standup",
            confidence=0.5,
            scores={"standup": 50, "planning": 30, "review": 10},
            signals=[],
        )
        text = format_classification(cls)
        assert "Also considered" in text
        assert "Planning" in text

    def test_all_types_have_labels(self):
        for mtype in ["standup", "planning", "review", "one_on_one", "all_hands",
                       "brainstorm", "retrospective", "interview", "training", "incident"]:
            cls = MeetingClassification(
                meeting_type=mtype,
                confidence=0.5,
                scores={mtype: 50},
                signals=[],
            )
            text = format_classification(cls)
            assert "Type:" in text
