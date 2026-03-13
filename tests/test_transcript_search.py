"""Tests for full-text transcript search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.transcript_search import (
    search_transcripts,
    format_search_results,
    SearchHit,
)


def _make_rec(base: Path, name: str, subject: str = "",
              transcript: str = "", summary: str = "") -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"meeting_subject": subject}
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    return rec


class TestSearchTranscripts:
    def test_empty_dir(self, tmp_path):
        assert search_transcripts(tmp_path, "hello") == []

    def test_empty_query(self, tmp_path):
        assert search_transcripts(tmp_path, "") == []

    def test_no_match(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="Alice said hello to Bob.")
        assert search_transcripts(tmp_path, "zebra") == []

    def test_basic_match(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Sprint Planning", transcript="Alice discussed the database migration plan.\nBob agreed.")
        hits = search_transcripts(tmp_path, "database")
        assert len(hits) == 1
        assert hits[0].subject == "Sprint Planning"
        assert hits[0].match_count == 1
        assert "database" in hits[0].context.lower()

    def test_multiple_matches_in_recording(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="Deploy the API.\nTest the API.\nMonitor the API.")
        hits = search_transcripts(tmp_path, "API")
        assert len(hits) == 1
        assert hits[0].match_count == 3

    def test_multiple_recordings(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting_A",
                  "Meeting A", transcript="Alice talked about the deadline for the project.")
        _make_rec(tmp_path, "2026-03-12_09-00-00_Meeting_B",
                  "Meeting B", transcript="Bob mentioned the deadline was moved to Friday.")
        hits = search_transcripts(tmp_path, "deadline")
        assert len(hits) == 2
        # Newest first
        assert hits[0].date == "2026-03-13"

    def test_case_insensitive_default(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="The API endpoint is ready.")
        hits = search_transcripts(tmp_path, "api")
        assert len(hits) == 1

    def test_case_sensitive(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="The API endpoint is ready.")
        hits = search_transcripts(tmp_path, "api", case_sensitive=True)
        assert len(hits) == 0

    def test_search_summaries(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", summary="Discussed the migration strategy for Q3.")
        hits = search_transcripts(tmp_path, "migration", search_summaries=True)
        assert len(hits) == 1
        assert hits[0].file_name == "summary.md"

    def test_no_search_summaries(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", summary="Discussed the migration strategy.")
        hits = search_transcripts(tmp_path, "migration", search_summaries=False)
        assert len(hits) == 0

    def test_max_results(self, tmp_path):
        for i in range(10):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Meeting_{i}",
                      f"Meeting {i}", transcript=f"Discussion about topic {i} and more details.")
        hits = search_transcripts(tmp_path, "topic", max_results=3)
        assert len(hits) == 3

    def test_regex_search(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="The meeting is on 2026-03-15 at 10am sharp.")
        hits = search_transcripts(tmp_path, r"\d{4}-\d{2}-\d{2}")
        assert len(hits) == 1

    def test_invalid_regex_fallback(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="Check the log [error] details carefully.")
        hits = search_transcripts(tmp_path, "[error]")
        assert len(hits) == 1

    def test_context_includes_surrounding(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="Line one.\nThe important keyword here.\nLine three.")
        hits = search_transcripts(tmp_path, "keyword")
        assert len(hits) == 1
        assert "Line one" in hits[0].context
        assert "keyword" in hits[0].context

    def test_nonexistent_dir(self, tmp_path):
        assert search_transcripts(tmp_path / "nope", "hello") == []

    def test_date_extracted(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="Hello world from the transcript content.")
        hits = search_transcripts(tmp_path, "Hello")
        assert hits[0].date == "2026-03-13"

    def test_line_number(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", transcript="Line 1\nLine 2\nTarget keyword here\nLine 4")
        hits = search_transcripts(tmp_path, "Target")
        assert hits[0].line_number == 3


class TestFormatSearchResults:
    def test_empty(self):
        text = format_search_results([], "hello")
        assert "No results" in text

    def test_basic(self):
        hits = [
            SearchHit(
                recording_path="/tmp/rec",
                recording_name="2026-03-13_09-00-00_Meeting",
                subject="Sprint Planning",
                date="2026-03-13",
                file_name="transcript.txt",
                line_number=5,
                context="> Alice talked about the database",
                match_count=2,
            ),
        ]
        text = format_search_results(hits, "database")
        assert "TRANSCRIPT SEARCH" in text
        assert "database" in text
        assert "Sprint Planning" in text
        assert "2 match" in text
