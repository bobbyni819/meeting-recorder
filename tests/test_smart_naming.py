"""Tests for smart meeting naming (folder rename + title selection)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from meeting_recorder.storage import smart_naming
from meeting_recorder.integrations.outlook import CalendarEvent


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
    def test_single_unrelated_candidate_is_arbitrated_and_generated(self):
        event = CalendarEvent(
            subject="Weekly Standup",
            start_time="2026-06-18T10:00:00",
            end_time="2026-06-18T10:30:00",
            attendees=["Sam", "Priya"],
        )
        response = {
            "title": "Protein Assay Planning",
            "source": "generated",
            "matched_candidate_index": None,
        }
        with mock.patch.object(
            smart_naming, "ask_json",
            return_value=(response, SimpleNamespace(ok=True)),
        ) as ask:
            title, source = smart_naming.select_meeting_title(
                "We reviewed protein assay controls and sample preparation.",
                [event],
                summary_config=None,
                recording_start_time="2026-06-18T10:03:00",
                duration_seconds=1420.0,
                llm_backend="luna",
            )

        assert (title, source) == ("Protein Assay Planning", "generated")
        prompt = ask.call_args.args[0]
        assert "2026-06-18T10:03:00" in prompt
        assert "1420.0 seconds" in prompt
        assert "2026-06-18T10:00:00" in prompt
        assert "2026-06-18T10:30:00" in prompt
        assert "Sam, Priya" in prompt

    def test_luna_success_skips_gemini(self):
        response = {
            "title": "Budget Review",
            "source": "calendar",
            "matched_candidate_index": 0,
        }
        cfg = mock.MagicMock(api_key="key")
        with mock.patch.object(
            smart_naming, "ask_json",
            return_value=(response, SimpleNamespace(ok=True)),
        ), mock.patch(
            "meeting_recorder.summary.summarizer.create_provider"
        ) as create_provider:
            title, source = smart_naming.select_meeting_title(
                "We discussed the budget.",
                ["Budget Review", "1:1 with Sam"],
                cfg,
                llm_backend="luna",
            )

        assert (title, source) == ("Budget Review", "calendar")
        create_provider.assert_not_called()

    def test_luna_exception_falls_through_to_gemini(self):
        cfg = SimpleNamespace(
            provider="gemini", api_key="key", model="gemini-2.5-flash",
        )
        gemini_response = (
            '{"title": "Production Incident Postmortem", "source": "generated", '
            '"matched_candidate_index": null}'
        )
        with mock.patch.object(
            smart_naming, "ask_json", side_effect=TimeoutError("luna timeout"),
        ), mock.patch(
            "meeting_recorder.summary.summarizer.GeminiSummaryProvider"
        ) as provider_cls:
            provider_cls.return_value.generate.return_value = gemini_response
            title, source = smart_naming.select_meeting_title(
                "We investigated last night's production outage.",
                ["Budget Review", "1:1 with Sam"],
                cfg,
                llm_backend="luna",
            )

        assert (title, source) == ("Production Incident Postmortem", "generated")
        provider_cls.assert_called_once_with(
            api_key="key", model="gemini-2.5-flash",
        )

    def test_gemini_rung_never_reuses_non_gemini_summary_provider(self):
        cfg = SimpleNamespace(
            provider="openai", api_key="openai-key", model="gpt-4o-mini",
        )
        with mock.patch.object(
            smart_naming, "ask_json", side_effect=RuntimeError("unavailable"),
        ), mock.patch(
            "meeting_recorder.summary.summarizer.GeminiSummaryProvider"
        ) as gemini_provider, mock.patch(
            "meeting_recorder.summary.summarizer.create_provider"
        ) as create_provider:
            title, source = smart_naming.select_meeting_title(
                "We discussed the Apollo launch plan.",
                ["Budget Review", "Apollo Launch"],
                cfg,
                llm_backend="luna",
            )

        assert (title, source) == ("Apollo Launch", "calendar")
        gemini_provider.assert_not_called()
        create_provider.assert_not_called()

    def test_explicit_gemini_credentials_work_with_non_gemini_summary_provider(self):
        cfg = SimpleNamespace(
            provider="openai", api_key="openai-key", model="gpt-4o-mini",
        )
        response = (
            '{"title": "Apollo Launch", "source": "calendar", '
            '"matched_candidate_index": 1}'
        )
        with mock.patch.object(
            smart_naming, "ask_json", side_effect=RuntimeError("unavailable"),
        ), mock.patch(
            "meeting_recorder.summary.summarizer.GeminiSummaryProvider"
        ) as provider_cls:
            provider_cls.return_value.generate.return_value = response
            title, source = smart_naming.select_meeting_title(
                "We discussed the Apollo launch plan.",
                ["Budget Review", "Apollo Launch"],
                cfg,
                llm_backend="luna",
                gemini_api_key="gemini-key",
                gemini_model="gemini-2.5-flash",
            )

        assert (title, source) == ("Apollo Launch", "calendar")
        provider_cls.assert_called_once_with(
            api_key="gemini-key", model="gemini-2.5-flash",
        )

    def test_both_llms_fail_falls_through_to_local_content_match(self):
        cfg = mock.MagicMock(api_key="key")
        transcript = (
            "I'm more familiar with net logo for agent-based models, ABM. "
            "Regan asked about the implementation."
        )
        with mock.patch.object(
            smart_naming, "ask_json", side_effect=RuntimeError("unavailable"),
        ), mock.patch(
            "meeting_recorder.summary.summarizer.create_provider",
            side_effect=RuntimeError("503"),
        ):
            title, source = smart_naming.select_meeting_title(
                transcript,
                ["Data+ Program breakfast 10 AM talk", "Regan ABM sync"],
                cfg,
                llm_backend="luna",
            )

        assert (title, source) == ("Regan ABM sync", "calendar")

    @pytest.mark.parametrize(
        "response",
        [
            {},
            {
                "title": "Budget Review",
                "source": "calendar",
                "matched_candidate_index": 99,
            },
            {
                "title": "Budget Review",
                "source": "calendar",
                "matched_candidate_index": "0",
            },
        ],
    )
    def test_malformed_luna_response_falls_through_without_raising(self, response):
        cfg = mock.MagicMock(api_key="")
        with mock.patch.object(
            smart_naming, "ask_json",
            return_value=(response, SimpleNamespace(ok=True)),
        ):
            title, source = smart_naming.select_meeting_title(
                "We discussed the Apollo launch plan.",
                ["Budget Review", "Apollo Launch"],
                cfg,
                llm_backend="luna",
            )

        assert (title, source) == ("Apollo Launch", "calendar")

    def test_local_mode_skips_both_llms(self):
        cfg = mock.MagicMock(api_key="key")
        with mock.patch.object(smart_naming, "ask_json") as luna, mock.patch(
            "meeting_recorder.summary.summarizer.create_provider"
        ) as create_provider:
            title, source = smart_naming.select_meeting_title(
                "The launch plan for Apollo is ready.",
                ["Budget Review", "Apollo Launch"],
                cfg,
                llm_backend="local",
            )

        assert (title, source) == ("Apollo Launch", "calendar")
        luna.assert_not_called()
        create_provider.assert_not_called()

    def test_string_candidate_seam_remains_supported(self):
        title, source = smart_naming.select_meeting_title(
            "Weekly standup blockers and priorities",
            ["Weekly Standup"],
            summary_config=None,
            llm_backend="local",
        )
        assert title == "Weekly Standup"
        assert source == "calendar"

    def test_zero_candidates_returns_none(self):
        title, source = smart_naming.select_meeting_title(
            "transcript", [], summary_config=None,
        )
        assert title is None
        assert source == "none"

    def test_multiple_candidates_no_key_uses_first(self):
        cfg = mock.MagicMock(api_key="")
        title, source = smart_naming.select_meeting_title(
            "transcript", ["First Meeting", "Second Meeting"], cfg,
            llm_backend="gemini",
        )
        assert title == "First Meeting"


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
