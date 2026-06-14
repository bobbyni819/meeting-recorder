"""Tests for natural-language meeting Q&A."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from meeting_recorder.search.ask import AskResult, ask_meetings
from meeting_recorder.search.index import SearchResult


def _config(api_key: str = "test-key", model: str = "test-model"):
    return SimpleNamespace(
        transcription=SimpleNamespace(
            gemini_api_key=api_key,
            gemini_model=model,
        )
    )


def _seed_recording(tmp_path, transcript: str = "Alice discussed the Q3 budget allocation in detail."):
    recording_dir = tmp_path / "rec1"
    recording_dir.mkdir()
    (recording_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    (recording_dir / "metadata.json").write_text(
        json.dumps(
            {
                "start_time": "2026-04-01T10:00:00",
                "meeting_subject": "Budget Planning",
            }
        ),
        encoding="utf-8",
    )
    return recording_dir


def _search_result(recording_dir):
    return SearchResult(
        recording_dir=str(recording_dir),
        date="2026-04-01T10:00:00",
        subject="Budget Planning",
        app_name="Zoom",
        organizer="Alice",
        attendees="Alice, Bob",
        speakers="Alice",
        snippet="Alice discussed the Q3 budget allocation in detail.",
    )


class FakeIndex:
    results = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def search(self, query, limit=50):
        self.query = query
        self.limit = limit
        return self.results


class FakeModels:
    def __init__(self, calls):
        self.calls = calls

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="The Q3 budget was discussed by Alice. [Source 1]")


class FakeClient:
    calls = []

    def __init__(self, api_key):
        self.api_key = api_key
        self.models = FakeModels(self.calls)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _patch_index(monkeypatch, results):
    FakeIndex.results = results
    monkeypatch.setattr("meeting_recorder.search.ask.RecordingIndex", FakeIndex)


def _patch_genai(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(
        "meeting_recorder.search.ask.genai",
        SimpleNamespace(Client=FakeClient),
    )
    return FakeClient.calls


def test_ask_returns_answer(tmp_path, monkeypatch):
    recording_dir = _seed_recording(tmp_path)
    _patch_index(monkeypatch, [_search_result(recording_dir)])
    _patch_genai(monkeypatch)

    result = ask_meetings("Who discussed the Q3 budget?", config=_config())

    assert isinstance(result, AskResult)
    assert result.answer == "The Q3 budget was discussed by Alice. [Source 1]"
    assert len(result.sources) == 1
    assert result.sources[0].path == str(recording_dir)
    assert result.used_recordings == 1


def test_ask_zero_results(monkeypatch):
    _patch_index(monkeypatch, [])
    calls = _patch_genai(monkeypatch)

    result = ask_meetings("What happened with the roadmap?", config=_config())

    assert result.answer == "No meetings matched that question."
    assert result.sources == []
    assert result.used_recordings == 0
    assert calls == []


def test_ask_missing_key():
    with pytest.raises(ValueError, match="gemini_api_key"):
        ask_meetings("What happened with the budget?", config=_config(api_key=""))


def test_transcript_truncation(tmp_path, monkeypatch):
    recording_dir = _seed_recording(tmp_path, transcript="x" * 60)
    _patch_index(monkeypatch, [_search_result(recording_dir)])
    calls = _patch_genai(monkeypatch)

    result = ask_meetings(
        "What was discussed?",
        config=_config(),
        max_chars_per_source=10,
    )

    assert result.answer == "The Q3 budget was discussed by Alice. [Source 1]"
    assert len(calls) == 1
    context = calls[0]["contents"][1]
    assert "x" * 10 in context
    assert "x" * 11 not in context


def test_derive_fts_query_uses_or_not_and():
    from meeting_recorder.search.ask import _derive_fts_query
    q = _derive_fts_query("What meetings discussed influenza or modeling?")
    assert " OR " in q                      # recall, not AND-over-restriction
    assert '"influenza"' in q and '"modeling"' in q  # content terms, quoted
