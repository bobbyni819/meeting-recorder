"""Tests for HTML report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.html_export import (
    generate_html_report,
    _markdown_to_html,
    _format_transcript_html,
    _inline_format,
)


@pytest.fixture
def rec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "2026-03-10_09-30-00_Team_Standup_Zoom"
    d.mkdir()
    return d


class TestGenerateHtmlReport:
    def test_basic_report(self, rec_dir: Path):
        """Should generate valid HTML with title and date."""
        meta = {"meeting_subject": "Team Standup", "app_name": "Zoom"}
        html = generate_html_report(rec_dir, meta)
        assert "<!DOCTYPE html>" in html
        assert "Team Standup" in html
        assert "Zoom" in html
        assert "2026-03-10" in html

    def test_report_with_transcript(self, rec_dir: Path):
        """Should include transcript content."""
        (rec_dir / "transcript.txt").write_text(
            "Alice: Hello everyone\nBob: Hi there", encoding="utf-8")
        html = generate_html_report(rec_dir, {})
        assert "Transcript" in html
        assert "Hello everyone" in html
        assert "Hi there" in html

    def test_report_with_summary(self, rec_dir: Path):
        """Should include summary content."""
        (rec_dir / "summary.md").write_text(
            "## Key Points\n- Point 1\n- Point 2", encoding="utf-8")
        html = generate_html_report(rec_dir, {})
        assert "Summary" in html
        assert "Key Points" in html
        assert "Point 1" in html

    def test_report_with_notes(self, rec_dir: Path):
        """Should include notes if present."""
        (rec_dir / "notes.md").write_text("Follow up on budget", encoding="utf-8")
        html = generate_html_report(rec_dir, {})
        assert "Notes" in html
        assert "Follow up on budget" in html

    def test_report_with_attendees(self, rec_dir: Path):
        """Should list attendees."""
        meta = {
            "meeting_organizer": "Alice Smith",
            "meeting_attendees": ["Alice Smith", "Bob Jones"],
        }
        html = generate_html_report(rec_dir, meta)
        assert "Attendees" in html
        assert "Alice Smith" in html
        assert "(organizer)" in html
        assert "Bob Jones" in html

    def test_report_with_duration(self, rec_dir: Path):
        """Should show formatted duration."""
        meta = {"duration_seconds": 3725}  # 1h 2m 5s
        html = generate_html_report(rec_dir, meta)
        assert "1h 02m" in html

    def test_report_with_quality(self, rec_dir: Path):
        """Should show quality bar."""
        meta = {"quality_scores": {"overall_score": 85}}
        html = generate_html_report(rec_dir, meta)
        assert "Quality" in html
        assert "85/100" in html

    def test_report_loads_metadata_from_disk(self, rec_dir: Path):
        """Should load metadata if not provided."""
        meta = {"meeting_subject": "From Disk", "app_name": "Teams"}
        (rec_dir / "metadata.json").write_text(
            json.dumps(meta), encoding="utf-8")
        html = generate_html_report(rec_dir)
        assert "From Disk" in html
        assert "Teams" in html

    def test_report_without_metadata(self, rec_dir: Path):
        """Should work with no metadata at all."""
        html = generate_html_report(rec_dir, {})
        assert "<!DOCTYPE html>" in html
        assert "2026-03-10" in html

    def test_report_speaker_stats(self, rec_dir: Path):
        """Should include speaker stats from transcript.json."""
        data = {
            "segments": [
                {"speaker": "Alice", "start": 0.0, "end": 30.0, "text": "hello"},
                {"speaker": "Bob", "start": 30.0, "end": 45.0, "text": "hi"},
            ]
        }
        (rec_dir / "transcript.json").write_text(
            json.dumps(data), encoding="utf-8")
        html = generate_html_report(rec_dir, {})
        assert "Speaker Stats" in html
        assert "Alice" in html
        assert "Bob" in html

    def test_html_escaping(self, rec_dir: Path):
        """Should escape HTML special characters."""
        meta = {"meeting_subject": "<script>alert('xss')</script>"}
        html = generate_html_report(rec_dir, meta)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_report_with_participation(self, rec_dir: Path):
        """Should include participation section when transcript.json has speakers."""
        data = {
            "segments": [
                {"speaker": "Alice", "start": 0.0, "end": 60.0, "text": "talking"},
                {"speaker": "Bob", "start": 60.0, "end": 90.0, "text": "talking"},
            ]
        }
        (rec_dir / "transcript.json").write_text(json.dumps(data), encoding="utf-8")
        html = generate_html_report(rec_dir, {})
        assert "Participation Equity" in html

    def test_report_with_word_frequency(self, rec_dir: Path):
        """Should include key terms section when transcript exists."""
        (rec_dir / "transcript.txt").write_text(
            "project timeline deadline project meeting project agenda deadline",
            encoding="utf-8",
        )
        html = generate_html_report(rec_dir, {})
        assert "Key Terms" in html
        assert "project" in html

    def test_report_with_roi(self, rec_dir: Path):
        """Should include ROI section when summary has decisions."""
        (rec_dir / "summary.md").write_text(
            "We decided to proceed with plan A. Approved the budget.", encoding="utf-8"
        )
        meta = {
            "duration_seconds": 3600,
            "meeting_attendees": ["Alice", "Bob"],
        }
        html = generate_html_report(rec_dir, meta)
        assert "Meeting ROI" in html

    def test_report_is_self_contained(self, rec_dir: Path):
        """Report should have inline CSS, no external dependencies."""
        html = generate_html_report(rec_dir, {})
        assert "<style>" in html
        assert "link rel=" not in html
        assert "<script" not in html

    def test_report_writable(self, rec_dir: Path):
        """Should write to a file successfully."""
        html = generate_html_report(rec_dir, {"meeting_subject": "Test"})
        dest = rec_dir / "report.html"
        dest.write_text(html, encoding="utf-8")
        assert dest.exists()
        content = dest.read_text(encoding="utf-8")
        assert "Test" in content


class TestMarkdownToHtml:
    def test_heading(self):
        assert "<h3>" in _markdown_to_html("# Hello")

    def test_list(self):
        result = _markdown_to_html("- item 1\n- item 2")
        assert "<ul>" in result
        assert "<li>" in result
        assert "item 1" in result

    def test_horizontal_rule(self):
        assert "<hr>" in _markdown_to_html("---")

    def test_paragraph(self):
        result = _markdown_to_html("Some text here")
        assert "<p>" in result
        assert "Some text here" in result

    def test_bold_italic(self):
        result = _markdown_to_html("This is **bold** and *italic*")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result


class TestInlineFormat:
    def test_bold(self):
        assert "<strong>word</strong>" in _inline_format("**word**")

    def test_italic(self):
        assert "<em>word</em>" in _inline_format("*word*")

    def test_escaping(self):
        assert "&lt;" in _inline_format("<tag>")

    def test_plain_text(self):
        assert "hello" in _inline_format("hello")


class TestFormatTranscriptHtml:
    def test_speaker_label(self):
        result = _format_transcript_html("Alice: Hello")
        assert "speaker" in result
        assert "Alice" in result

    def test_timestamp(self):
        result = _format_transcript_html("[00:01:23] Alice: Hello")
        assert "00:01:23" in result
        assert "timestamp" in result

    def test_plain_line(self):
        result = _format_transcript_html("Just some text")
        assert "Just some text" in result

    def test_empty_lines(self):
        result = _format_transcript_html("Line 1\n\nLine 2")
        assert "<br>" in result
