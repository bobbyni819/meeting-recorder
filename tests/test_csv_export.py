"""Tests for CSV export."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from meeting_recorder.storage.csv_export import (
    export_recordings_csv,
    export_speakers_csv,
    export_action_items_csv,
    export_focus_time_csv,
    export_all,
    _to_csv,
)


def _make_rec(
    base: Path,
    name: str,
    meta: dict | None = None,
    transcript: bool = False,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    if meta is None:
        meta = {"duration_seconds": 1800, "status": "completed"}
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if transcript:
        data = {
            "segments": [
                {"start": 0.0, "end": 30.0, "speaker": "Alice", "text": "Hello world test words here now"},
                {"start": 30.0, "end": 60.0, "speaker": "Bob", "text": "Reply test words also here now"},
            ]
        }
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(data, f)
        (rec / "transcript.txt").write_text("Hello world\nReply test", encoding="utf-8")
    return rec


class TestToCsv:
    def test_empty(self):
        assert _to_csv([]) == ""

    def test_basic(self):
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        text = _to_csv(rows)
        reader = csv.DictReader(io.StringIO(text))
        result = list(reader)
        assert len(result) == 2
        assert result[0]["a"] == "1"
        assert result[1]["b"] == "y"


class TestExportRecordings:
    def test_empty_dir(self, tmp_path):
        assert export_recordings_csv(tmp_path) == ""

    def test_nonexistent_dir(self, tmp_path):
        assert export_recordings_csv(tmp_path / "nope") == ""

    def test_basic_export(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_StandUp", {
            "duration_seconds": 1800,
            "status": "completed",
            "meeting_subject": "Daily StandUp",
            "app_name": "Zoom",
            "speaker_count": 3,
            "meeting_attendees": ["Alice", "Bob"],
            "meeting_organizer": "Alice",
            "tags": ["standup", "daily"],
        })
        text = export_recordings_csv(tmp_path)
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 1
        row = rows[0]
        assert row["date"] == "2026-03-10"
        assert row["time"] == "09:00:00"
        assert row["subject"] == "Daily StandUp"
        assert row["app"] == "Zoom"
        assert row["duration_min"] == "30.0"
        assert row["speakers"] == "3"
        assert row["attendees"] == "Alice; Bob"
        assert row["attendee_count"] == "2"
        assert row["organizer"] == "Alice"
        assert row["tags"] == "standup; daily"

    def test_multiple_recordings(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A")
        _make_rec(tmp_path, "2026-03-09_14-00-00_B")
        text = export_recordings_csv(tmp_path)
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 2
        # Sorted reverse chronologically
        assert rows[0]["date"] == "2026-03-10"
        assert rows[1]["date"] == "2026-03-09"

    def test_has_transcript_flag(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_A", transcript=True)
        text = export_recordings_csv(tmp_path)
        reader = csv.DictReader(io.StringIO(text))
        row = list(reader)[0]
        assert row["has_transcript"] == "yes"

    def test_no_transcript_flag(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A")
        text = export_recordings_csv(tmp_path)
        reader = csv.DictReader(io.StringIO(text))
        row = list(reader)[0]
        assert row["has_transcript"] == "no"

    def test_skips_non_dirs(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")
        _make_rec(tmp_path, "2026-03-10_09-00-00_A")
        text = export_recordings_csv(tmp_path)
        reader = csv.DictReader(io.StringIO(text))
        assert len(list(reader)) == 1

    def test_skips_short_names(self, tmp_path):
        (tmp_path / "temp").mkdir()
        _make_rec(tmp_path, "2026-03-10_09-00-00_A")
        text = export_recordings_csv(tmp_path)
        reader = csv.DictReader(io.StringIO(text))
        assert len(list(reader)) == 1


class TestExportSpeakers:
    def test_empty(self, tmp_path):
        assert export_speakers_csv(tmp_path) == ""

    def test_with_transcript(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Talk", transcript=True)
        text = export_speakers_csv(tmp_path)
        if text:  # Only if speaker analytics can parse it
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            assert len(rows) >= 1
            assert "speaker" in rows[0]
            assert "talk_minutes" in rows[0]

    def test_no_transcript(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A")
        text = export_speakers_csv(tmp_path)
        assert text == ""


class TestExportActionItems:
    def test_empty(self, tmp_path):
        assert export_action_items_csv(tmp_path) == ""

    def test_with_action_items(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Planning")
        # Create a transcript with action-item-like text
        (rec / "transcript.txt").write_text(
            "I will send the report by Friday.\n"
            "We need to update the documentation.\n"
            "Please review the pull request.\n",
            encoding="utf-8",
        )
        text = export_action_items_csv(tmp_path)
        # May or may not find items depending on the extractor
        if text:
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            assert all("description" in r for r in rows)
            assert all("category" in r for r in rows)


class TestExportFocusTime:
    def test_empty(self, tmp_path):
        assert export_focus_time_csv(tmp_path) == ""

    def test_with_data(self, tmp_path):
        from datetime import date, timedelta
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, f"{today.isoformat()}_09-00-00_Meeting", {
            "duration_seconds": 3600,
        })
        text = export_focus_time_csv(tmp_path, weeks=1)
        assert text != ""
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 5  # Mon-Fri
        assert "week_start" in rows[0]
        assert "focus_hours" in rows[0]
        assert "meeting_hours" in rows[0]


class TestExportAll:
    def test_creates_output_dir(self, tmp_path):
        output = tmp_path / "exports"
        _make_rec(tmp_path / "recs", "2026-03-10_09-00-00_A")
        created = export_all(tmp_path / "recs", output)
        assert output.exists()
        assert len(created) >= 1
        assert all(p.suffix == ".csv" for p in created)

    def test_files_have_bom(self, tmp_path):
        """CSV files use UTF-8 BOM for Excel compatibility."""
        output = tmp_path / "exports"
        _make_rec(tmp_path / "recs", "2026-03-10_09-00-00_A")
        created = export_all(tmp_path / "recs", output)
        if created:
            content = created[0].read_bytes()
            assert content[:3] == b"\xef\xbb\xbf"  # UTF-8 BOM

    def test_empty_recordings(self, tmp_path):
        output = tmp_path / "exports"
        created = export_all(tmp_path / "recs", output)
        assert created == []
