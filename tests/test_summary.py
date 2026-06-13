"""Tests for AI meeting summary module."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meeting_recorder.transcription.local_whisper import TranscriptSegment
from meeting_recorder.summary.prompts import build_user_prompt
from meeting_recorder.summary.summarizer import (
    ActionItem,
    MeetingSummary,
    ParticipantStats,
    OpenAISummaryProvider,
    AnthropicSummaryProvider,
    GeminiSummaryProvider,
    compute_participant_stats,
    create_provider,
    format_participant_stats,
    save_summary,
    _parse_summary_response,
)


@dataclass
class MockSummaryConfig:
    enabled: bool = True
    provider: str = "openai"
    api_key: str = "test-key"
    model: str = ""
    max_transcript_tokens: int = 0


# ---------------------------------------------------------------------------
# compute_participant_stats
# ---------------------------------------------------------------------------

class TestComputeParticipantStats:
    def test_multiple_speakers(self):
        segments = [
            TranscriptSegment(start=0.0, end=10.0, text="Hello", speaker="Alice"),
            TranscriptSegment(start=10.0, end=25.0, text="Hi there", speaker="Bob"),
            TranscriptSegment(start=25.0, end=30.0, text="Hey", speaker="Alice"),
        ]
        stats = compute_participant_stats(segments)
        assert len(stats) == 2
        alice = next(s for s in stats if s.name == "Alice")
        bob = next(s for s in stats if s.name == "Bob")
        assert alice.speaking_time_seconds == pytest.approx(15.0)
        assert alice.segment_count == 2
        assert bob.speaking_time_seconds == pytest.approx(15.0)
        assert bob.segment_count == 1

    def test_single_speaker(self):
        segments = [
            TranscriptSegment(start=0.0, end=5.0, text="Hello", speaker="Alice"),
            TranscriptSegment(start=5.0, end=12.0, text="More", speaker="Alice"),
        ]
        stats = compute_participant_stats(segments)
        assert len(stats) == 1
        assert stats[0].name == "Alice"
        assert stats[0].speaking_time_seconds == pytest.approx(12.0)
        assert stats[0].segment_count == 2

    def test_empty_segments(self):
        stats = compute_participant_stats([])
        assert stats == []

    def test_sorted_by_speaking_time_descending(self):
        segments = [
            TranscriptSegment(start=0.0, end=5.0, text="Short", speaker="Alice"),
            TranscriptSegment(start=5.0, end=25.0, text="Long", speaker="Bob"),
            TranscriptSegment(start=25.0, end=35.0, text="Medium", speaker="Carol"),
        ]
        stats = compute_participant_stats(segments)
        assert stats[0].name == "Bob"
        assert stats[1].name == "Carol"
        assert stats[2].name == "Alice"

    def test_empty_speaker_uses_unknown(self):
        segments = [
            TranscriptSegment(start=0.0, end=5.0, text="Hello", speaker=""),
        ]
        stats = compute_participant_stats(segments)
        assert len(stats) == 1
        assert stats[0].name == "Unknown"
        assert stats[0].speaking_time_seconds == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# format_participant_stats
# ---------------------------------------------------------------------------

class TestFormatParticipantStats:
    def test_formats_correctly(self):
        stats = [
            ParticipantStats(name="Alice", speaking_time_seconds=120.0, segment_count=5),
            ParticipantStats(name="Bob", speaking_time_seconds=60.0, segment_count=3),
        ]
        result = format_participant_stats(stats)
        assert "Alice: 2.0 min speaking time (5 segments)" in result
        assert "Bob: 1.0 min speaking time (3 segments)" in result
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert all(line.startswith("- ") for line in lines)

    def test_empty_list_returns_empty_string(self):
        result = format_participant_stats([])
        assert result == ""


# ---------------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------------

class TestBuildUserPrompt:
    def test_all_fields(self):
        result = build_user_prompt(
            transcript_text="Alice: Hello\nBob: Hi",
            meeting_subject="Sprint Planning",
            attendees=["Alice", "Bob"],
            duration_str="30m 0s",
            participant_stats_text="- Alice: 15.0 min\n- Bob: 15.0 min",
        )
        assert "Meeting Subject: Sprint Planning" in result
        assert "Attendees: Alice, Bob" in result
        assert "Duration: 30m 0s" in result
        assert "Participant Statistics:" in result
        assert "--- TRANSCRIPT ---" in result
        assert "Alice: Hello" in result

    def test_minimal_fields(self):
        result = build_user_prompt(
            transcript_text="Alice: Hello",
            meeting_subject="Standup",
        )
        assert "Meeting Subject: Standup" in result
        assert "--- TRANSCRIPT ---" in result
        assert "Alice: Hello" in result
        assert "Attendees:" not in result
        assert "Duration:" not in result

    def test_no_optional_fields(self):
        result = build_user_prompt(transcript_text="Some text here")
        assert "--- TRANSCRIPT ---" in result
        assert "Some text here" in result
        assert "Meeting Subject:" not in result
        assert "Attendees:" not in result
        assert "Duration:" not in result
        assert "Participant Statistics:" not in result


# ---------------------------------------------------------------------------
# _parse_summary_response
# ---------------------------------------------------------------------------

class TestParseSummaryResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "summary": "A good meeting.",
            "action_items": [
                {"description": "Fix bug", "assignee": "Alice"},
            ],
            "key_decisions": ["Use Python"],
            "open_questions": ["When to deploy?"],
        })
        result = _parse_summary_response(raw)
        assert result.summary == "A good meeting."
        assert len(result.action_items) == 1
        assert result.action_items[0].description == "Fix bug"
        assert result.action_items[0].assignee == "Alice"
        assert result.key_decisions == ["Use Python"]
        assert result.open_questions == ["When to deploy?"]

    def test_action_items_as_dicts(self):
        raw = json.dumps({
            "summary": "Test",
            "action_items": [
                {"description": "Task 1", "assignee": "Bob"},
                {"description": "Task 2", "assignee": ""},
            ],
        })
        result = _parse_summary_response(raw)
        assert len(result.action_items) == 2
        assert result.action_items[0].description == "Task 1"
        assert result.action_items[0].assignee == "Bob"
        assert result.action_items[1].description == "Task 2"
        assert result.action_items[1].assignee == ""

    def test_action_items_as_strings(self):
        raw = json.dumps({
            "summary": "Test",
            "action_items": ["Do thing one", "Do thing two"],
        })
        result = _parse_summary_response(raw)
        assert len(result.action_items) == 2
        assert result.action_items[0].description == "Do thing one"
        assert result.action_items[0].assignee == ""
        assert result.action_items[1].description == "Do thing two"

    def test_invalid_json_falls_back(self):
        raw = "This is not JSON at all, just plain text."
        result = _parse_summary_response(raw)
        assert result.summary == raw
        assert result.action_items == []
        assert result.key_decisions == []
        assert result.open_questions == []

    def test_empty_json_object(self):
        raw = json.dumps({})
        result = _parse_summary_response(raw)
        assert result.summary == ""
        assert result.action_items == []
        assert result.key_decisions == []
        assert result.open_questions == []


# ---------------------------------------------------------------------------
# save_summary
# ---------------------------------------------------------------------------

class TestSaveSummary:
    def test_creates_both_files(self, tmp_path: Path):
        summary = MeetingSummary(summary="Test summary.")
        save_summary(summary, tmp_path)
        assert (tmp_path / "summary.json").exists()
        assert (tmp_path / "summary.md").exists()

    def test_json_is_valid_and_contains_all_fields(self, tmp_path: Path):
        summary = MeetingSummary(
            summary="Great meeting.",
            action_items=[ActionItem(description="Ship it", assignee="Alice")],
            key_decisions=["Use Rust"],
            open_questions=["Budget?"],
            participants=[ParticipantStats(name="Alice", speaking_time_seconds=60.0, segment_count=3)],
            model_used="gpt-4o",
            provider_used="openai",
        )
        save_summary(summary, tmp_path)
        with open(tmp_path / "summary.json", encoding="utf-8") as f:
            data = json.load(f)
        assert data["summary"] == "Great meeting."
        assert len(data["action_items"]) == 1
        assert data["action_items"][0]["description"] == "Ship it"
        assert data["action_items"][0]["assignee"] == "Alice"
        assert data["key_decisions"] == ["Use Rust"]
        assert data["open_questions"] == ["Budget?"]
        assert len(data["participants"]) == 1
        assert data["participants"][0]["name"] == "Alice"
        assert data["model_used"] == "gpt-4o"
        assert data["provider_used"] == "openai"

    def test_markdown_has_correct_headers(self, tmp_path: Path):
        summary = MeetingSummary(
            summary="Overview text.",
            action_items=[ActionItem(description="Do X", assignee="Bob")],
            key_decisions=["Decision A"],
            open_questions=["Question 1"],
            participants=[ParticipantStats(name="Bob", speaking_time_seconds=300.0, segment_count=10)],
        )
        save_summary(summary, tmp_path)
        md_text = (tmp_path / "summary.md").read_text(encoding="utf-8")
        assert "# Meeting Summary" in md_text
        assert "## Action Items" in md_text
        assert "- [ ] Do X (Bob)" in md_text
        assert "## Key Decisions" in md_text
        assert "- Decision A" in md_text
        assert "## Open Questions" in md_text
        assert "- Question 1" in md_text
        assert "## Participant Statistics" in md_text
        assert "**Bob**" in md_text

    def test_empty_summary(self, tmp_path: Path):
        summary = MeetingSummary()
        save_summary(summary, tmp_path)
        assert (tmp_path / "summary.json").exists()
        assert (tmp_path / "summary.md").exists()
        with open(tmp_path / "summary.json", encoding="utf-8") as f:
            data = json.load(f)
        assert data["summary"] == ""
        assert data["action_items"] == []
        assert data["key_decisions"] == []
        assert data["open_questions"] == []
        md_text = (tmp_path / "summary.md").read_text(encoding="utf-8")
        assert "# Meeting Summary" in md_text
        # No section headers for empty lists
        assert "## Action Items" not in md_text
        assert "## Key Decisions" not in md_text
        assert "## Open Questions" not in md_text


# ---------------------------------------------------------------------------
# create_provider
# ---------------------------------------------------------------------------

class TestCreateProvider:
    def test_openai_provider(self):
        config = MockSummaryConfig(provider="openai", api_key="sk-test", model="")
        provider = create_provider(config)
        assert isinstance(provider, OpenAISummaryProvider)
        assert provider.model == "gpt-4o"
        assert provider.api_key == "sk-test"

    def test_anthropic_provider(self):
        config = MockSummaryConfig(provider="anthropic", api_key="sk-ant-test", model="")
        provider = create_provider(config)
        assert isinstance(provider, AnthropicSummaryProvider)
        assert provider.model == "claude-sonnet-4-20250514"
        assert provider.api_key == "sk-ant-test"

    def test_unknown_provider_raises(self):
        config = MockSummaryConfig(provider="notareal_provider", api_key="test-key")
        with pytest.raises(ValueError, match="Unknown summary provider"):
            create_provider(config)

    def test_gemini_provider_created(self):
        config = MockSummaryConfig(provider="gemini", api_key="test-key", model="gemini-2.0-flash")
        provider = create_provider(config)
        from meeting_recorder.summary.summarizer import GeminiSummaryProvider
        assert isinstance(provider, GeminiSummaryProvider)
        assert provider.model == "gemini-2.0-flash"

    def test_empty_api_key_raises(self):
        config = MockSummaryConfig(provider="openai", api_key="")
        with pytest.raises(ValueError, match="API key is required"):
            create_provider(config)


# ---------------------------------------------------------------------------
# provider client lifetime
# ---------------------------------------------------------------------------

class TestSummaryProviderClientLifetime:
    def test_openai_provider_closes_client_on_success(self):
        provider = OpenAISummaryProvider(api_key="sk-test", model="gpt-test")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"summary":"ok"}'))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value.__enter__.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = provider.generate("system", "user")

        assert result == '{"summary":"ok"}'
        mock_openai.OpenAI.return_value.__exit__.assert_called_once()

    def test_openai_provider_closes_client_on_exception(self):
        provider = OpenAISummaryProvider(api_key="sk-test", model="gpt-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("openai failed")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value.__enter__.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            with pytest.raises(RuntimeError, match="openai failed"):
                provider.generate("system", "user")

        mock_openai.OpenAI.return_value.__exit__.assert_called_once()

    def test_anthropic_provider_closes_client_on_success(self):
        provider = AnthropicSummaryProvider(api_key="sk-ant-test", model="claude-test")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"summary":"ok"}')]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value.__enter__.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = provider.generate("system", "user")

        assert result == '{"summary":"ok"}'
        mock_anthropic.Anthropic.return_value.__exit__.assert_called_once()

    def test_anthropic_provider_closes_client_on_exception(self):
        provider = AnthropicSummaryProvider(api_key="sk-ant-test", model="claude-test")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("anthropic failed")
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value.__enter__.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            with pytest.raises(RuntimeError, match="anthropic failed"):
                provider.generate("system", "user")

        mock_anthropic.Anthropic.return_value.__exit__.assert_called_once()

    def test_gemini_provider_closes_client_on_success(self):
        provider = GeminiSummaryProvider(api_key="test-key", model="gemini-test")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(text='{"summary":"ok"}')
        mock_genai = MagicMock()
        mock_genai.Client.return_value.__enter__.return_value = mock_client

        with patch.dict("sys.modules", {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }):
            result = provider.generate("system", "user")

        assert result == '{"summary":"ok"}'
        mock_genai.Client.return_value.__exit__.assert_called_once()

    def test_gemini_provider_closes_client_on_exception(self):
        provider = GeminiSummaryProvider(api_key="test-key", model="gemini-test")
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("gemini failed")
        mock_genai = MagicMock()
        mock_genai.Client.return_value.__enter__.return_value = mock_client

        with patch.dict("sys.modules", {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }):
            with pytest.raises(RuntimeError, match="gemini failed"):
                provider.generate("system", "user")

        mock_genai.Client.return_value.__exit__.assert_called_once()


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------

class TestDataclassDefaults:
    def test_meeting_summary_defaults(self):
        summary = MeetingSummary()
        assert summary.summary == ""
        assert summary.action_items == []
        assert summary.key_decisions == []
        assert summary.open_questions == []
        assert summary.participants == []
        assert summary.model_used == ""
        assert summary.provider_used == ""

    def test_action_item_defaults(self):
        item = ActionItem(description="Test task")
        assert item.description == "Test task"
        assert item.assignee == ""
