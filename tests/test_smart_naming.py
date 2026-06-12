"""Tests for smart meeting naming (folder rename + title selection)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from meeting_recorder.storage import smart_naming


class TestSanitizeSubject:
    def test_spaces_to_underscores(self):
        assert smart_naming.sanitize_subject("Weekly Standup") == "Weekly_Standup"

    def test_strips_punctuation(self):
        assert smart_naming.sanitize_subject("Q3 Review: Budget!") == "Q3_Review_Budget"

    def test_collapses_underscores(self):
        assert smart_naming.sanitize_subject("a  -  b") == "a_-_b"

    def test_truncates_long(self):
        out = smart_naming.sanitize_subject("x" * 100)
        assert len(out) <= 60


class TestTimestampPrefix:
    def test_extracts_prefix(self):
        name = "2026-06-11_13-33-22_Some_Meeting_Zoom"
        assert smart_naming.timestamp_prefix(name) == "2026-06-11_13-33-22"

    def test_none_without_prefix(self):
        assert smart_naming.timestamp_prefix("no_timestamp_here") is None


class TestCurrentSubject:
    def test_extracts_subject_between_prefix_and_app(self):
        name = "2026-06-11_13-33-22_Weekly_Standup_Zoom"
        assert smart_naming.current_subject(name, "Zoom") == "Weekly_Standup"

    def test_handles_no_subject(self):
        name = "2026-06-11_13-33-22_Zoom"
        assert smart_naming.current_subject(name, "Zoom") == ""


class TestRenameRecordingDir:
    def _make_recording(self, tmp_path, name):
        d = tmp_path / name
        d.mkdir()
        (d / "transcript.json").write_text("{}", encoding="utf-8")
        (d / "app_audio.wav").write_bytes(b"RIFF")
        return d

    def test_renames_preserving_timestamp_and_files(self, tmp_path):
        d = self._make_recording(tmp_path, "2026-06-11_13-33-22_Wrong_Meeting_Zoom")
        new = smart_naming.rename_recording_dir(d, "Correct Meeting", "Zoom")
        assert new is not None
        assert new.name == "2026-06-11_13-33-22_Correct_Meeting_Zoom"
        # Files moved with the folder, untouched
        assert (new / "transcript.json").exists()
        assert (new / "app_audio.wav").read_bytes() == b"RIFF"
        assert not d.exists()

    def test_no_rename_when_title_matches(self, tmp_path):
        d = self._make_recording(tmp_path, "2026-06-11_13-33-22_Standup_Zoom")
        assert smart_naming.rename_recording_dir(d, "Standup", "Zoom") is None
        assert d.exists()

    def test_no_rename_without_timestamp(self, tmp_path):
        d = self._make_recording(tmp_path, "weird_folder_name")
        assert smart_naming.rename_recording_dir(d, "New Title", "Zoom") is None

    def test_collision_gets_suffix(self, tmp_path):
        d = self._make_recording(tmp_path, "2026-06-11_13-33-22_Old_Zoom")
        # Pre-create the target name
        (tmp_path / "2026-06-11_13-33-22_Taken_Zoom").mkdir()
        new = smart_naming.rename_recording_dir(d, "Taken", "Zoom")
        assert new is not None
        assert new.name == "2026-06-11_13-33-22_Taken_Zoom_2"

    def test_empty_title_skips(self, tmp_path):
        d = self._make_recording(tmp_path, "2026-06-11_13-33-22_Old_Zoom")
        assert smart_naming.rename_recording_dir(d, "!!!", "Zoom") is None


class TestSelectMeetingTitle:
    def test_single_candidate_no_llm(self):
        title, source = smart_naming.select_meeting_title(
            "some transcript", ["Weekly Standup"], summary_config=None,
        )
        assert title == "Weekly Standup"
        assert source == "single"

    def test_zero_candidates_returns_none(self):
        title, source = smart_naming.select_meeting_title(
            "transcript", [], summary_config=None,
        )
        assert title is None
        assert source == "none"

    def test_multiple_candidates_llm_picks_calendar_match(self):
        cfg = mock.MagicMock(api_key="key")
        with mock.patch(
            "meeting_recorder.summary.summarizer.create_provider"
        ) as mk:
            mk.return_value.generate.return_value = "Budget Review"
            title, source = smart_naming.select_meeting_title(
                "we discussed the budget", ["Budget Review", "1:1 with Sam"], cfg,
            )
        assert title == "Budget Review"
        assert source == "calendar"

    def test_multiple_candidates_llm_generates_when_no_fit(self):
        cfg = mock.MagicMock(api_key="key")
        with mock.patch(
            "meeting_recorder.summary.summarizer.create_provider"
        ) as mk:
            mk.return_value.generate.return_value = "Incident Postmortem"
            title, source = smart_naming.select_meeting_title(
                "the outage last night", ["Budget Review", "1:1 with Sam"], cfg,
            )
        assert title == "Incident Postmortem"
        assert source == "generated"

    def test_multiple_candidates_no_key_uses_first(self):
        cfg = mock.MagicMock(api_key="")
        title, source = smart_naming.select_meeting_title(
            "transcript", ["First Meeting", "Second Meeting"], cfg,
        )
        assert title == "First Meeting"

    def test_llm_failure_falls_back_to_content_match(self):
        """On LLM 503, pick the candidate the transcript actually matches."""
        cfg = mock.MagicMock(api_key="key")
        transcript = (
            "I'm more familiar with net logo for agent-based models, ABM. "
            "Regan asked about the implementation."
        )
        with mock.patch(
            "meeting_recorder.summary.summarizer.create_provider",
            side_effect=RuntimeError("503"),
        ):
            title, source = smart_naming.select_meeting_title(
                transcript,
                ["Data+ Program breakfast 10 AM talk", "Regan ABM sync"],
                cfg,
            )
        # The ABM meeting wins on content, not the first (breakfast) candidate
        assert title == "Regan ABM sync"


class TestBestCandidateByContent:
    def test_picks_content_match_over_first(self):
        transcript = "we discussed the quarterly budget and revenue forecasts"
        best = smart_naming._best_candidate_by_content(
            transcript, ["Standup", "Budget planning"],
        )
        assert best == "Budget planning"

    def test_generic_words_ignored(self):
        # "weekly", "meeting", "sync" are stopwords -> no signal -> first
        transcript = "totally unrelated content here"
        best = smart_naming._best_candidate_by_content(
            transcript, ["Weekly sync meeting", "Monthly review call"],
        )
        assert best == "Weekly sync meeting"  # tie -> first

    def test_no_match_falls_back_to_first(self):
        best = smart_naming._best_candidate_by_content(
            "xyz", ["Alpha project", "Beta launch"],
        )
        assert best == "Alpha project"
