"""Tests for keyword alert system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.keyword_alerts import (
    scan_recording,
    scan_all_recordings,
    format_keyword_alerts,
    save_watched_keywords,
    load_watched_keywords,
    KeywordAlert,
    KeywordAlertReport,
    _KEYWORDS_FILE,
)


def _make_rec(base: Path, name: str, subject: str = "",
              transcript: str = "") -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"meeting_subject": subject}
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    return rec


class TestScanRecording:
    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert scan_recording(rec, ["hello"]) == []

    def test_empty_keywords(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", "Meeting", "Hello world")
        assert scan_recording(rec, []) == []

    def test_no_match(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Meeting", "Alice talked about the project status.")
        assert scan_recording(rec, ["zebra"]) == []

    def test_single_match(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Sprint Planning",
                        "We discussed the database migration timeline.")
        alerts = scan_recording(rec, ["database"])
        assert len(alerts) == 1
        assert alerts[0].keyword == "database"
        assert alerts[0].count == 1
        assert "database" in alerts[0].first_context.lower()

    def test_multiple_matches(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Meeting",
                        "Deploy the API. Test the API. Monitor the API daily.")
        alerts = scan_recording(rec, ["API"])
        assert len(alerts) == 1
        assert alerts[0].count == 3

    def test_multiple_keywords(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Meeting",
                        "We discussed the budget and the competitor analysis.")
        alerts = scan_recording(rec, ["budget", "competitor"])
        assert len(alerts) == 2

    def test_case_insensitive(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Meeting", "The BUDGET was discussed at length.")
        alerts = scan_recording(rec, ["budget"])
        assert len(alerts) == 1

    def test_word_boundary(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Meeting", "We discussed testing the API endpoint.")
        alerts = scan_recording(rec, ["test"])
        # "testing" contains "test" as a word, but with word boundary it shouldn't match
        assert len(alerts) == 0

    def test_date_extracted(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Meeting", "Keyword mentioned here for testing purposes.")
        alerts = scan_recording(rec, ["Keyword"])
        assert alerts[0].date == "2026-03-13"

    def test_subject_from_meta(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                        "Sprint Review",
                        "The sprint review was productive and insightful.")
        alerts = scan_recording(rec, ["sprint"])
        assert alerts[0].subject == "Sprint Review"


class TestScanAllRecordings:
    def test_empty_dir(self, tmp_path):
        report = scan_all_recordings(tmp_path, ["hello"])
        assert report.total_alerts == 0

    def test_no_keywords(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting",
                  "Meeting", "Hello world")
        report = scan_all_recordings(tmp_path, [])
        assert report.total_alerts == 0

    def test_basic_scan(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_Meeting_A",
                  "Meeting A", "Discussed the budget for next quarter in detail.")
        _make_rec(tmp_path, "2026-03-12_09-00-00_Meeting_B",
                  "Meeting B", "No relevant topics were discussed today.")
        report = scan_all_recordings(tmp_path, ["budget"])
        assert report.total_alerts == 1
        assert report.recordings_scanned == 2
        assert "budget" in report.keywords_matched

    def test_max_recordings(self, tmp_path):
        for i in range(10):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Mtg_{i}",
                      f"Meeting {i}", f"Mentioned keyword {i} here.")
        report = scan_all_recordings(tmp_path, ["keyword"], max_recordings=3)
        assert report.recordings_scanned == 3

    def test_keyword_totals(self, tmp_path):
        _make_rec(tmp_path, "2026-03-13_09-00-00_A",
                  "A", "Budget discussion. Budget update.")
        _make_rec(tmp_path, "2026-03-12_09-00-00_B",
                  "B", "Budget review meeting.")
        report = scan_all_recordings(tmp_path, ["budget"])
        assert report.keywords_matched["budget"] == 3  # 2+1


class TestSaveLoadKeywords:
    def test_save_and_load(self, tmp_path, monkeypatch):
        import meeting_recorder.storage.keyword_alerts as mod
        test_file = tmp_path / "keywords.json"
        monkeypatch.setattr(mod, "_KEYWORDS_FILE", test_file)

        save_watched_keywords(["budget", "competitor", "deadline"])
        loaded = load_watched_keywords()
        assert loaded == ["budget", "competitor", "deadline"]

    def test_load_empty(self, tmp_path, monkeypatch):
        import meeting_recorder.storage.keyword_alerts as mod
        monkeypatch.setattr(mod, "_KEYWORDS_FILE", tmp_path / "nope.json")
        assert load_watched_keywords() == []


class TestFormatKeywordAlerts:
    def test_no_alerts(self):
        report = KeywordAlertReport(
            total_alerts=0, keywords_matched={},
            alerts=[], recordings_scanned=10,
        )
        text = format_keyword_alerts(report)
        assert "No keyword matches" in text

    def test_no_keywords_configured(self):
        report = KeywordAlertReport(
            total_alerts=0, keywords_matched={},
            alerts=[], recordings_scanned=0,
        )
        text = format_keyword_alerts(report)
        assert "No watched keywords" in text

    def test_basic_format(self):
        report = KeywordAlertReport(
            total_alerts=2,
            keywords_matched={"budget": 3, "deadline": 1},
            alerts=[
                KeywordAlert(
                    keyword="budget", recording_name="rec1",
                    subject="Sprint Planning", date="2026-03-13",
                    count=2, first_context="We discussed the budget allocation",
                ),
                KeywordAlert(
                    keyword="deadline", recording_name="rec1",
                    subject="Sprint Planning", date="2026-03-13",
                    count=1, first_context="The deadline is next Friday",
                ),
            ],
            recordings_scanned=5,
        )
        text = format_keyword_alerts(report)
        assert "KEYWORD ALERTS" in text
        assert "budget" in text
        assert "deadline" in text
        assert "Sprint Planning" in text
