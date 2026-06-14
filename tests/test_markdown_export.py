"""Tests for Markdown/Obsidian recording exports."""

from __future__ import annotations

import json
from pathlib import Path

from meeting_recorder.storage.markdown_export import export_markdown, save_markdown


def _make_recording(base: Path, name: str = "2026-03-10_09-00-00_Test") -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    return rec


def test_full_recording_exports_frontmatter_and_sections(tmp_path):
    rec = _make_recording(tmp_path)
    metadata = {
        "meeting_subject": "Weekly Sync",
        "app_name": "Microsoft Teams",
        "start_time": "2026-03-10T09:00:00",
        "duration_seconds": 3900,
        "meeting_attendees": ["Alice", "Bob"],
        "tags": ["planning", "launch"],
        "speaker_count": 2,
    }
    (rec / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (rec / "summary.md").write_text("Discussed launch readiness.", encoding="utf-8")
    (rec / "transcript.txt").write_text(
        "\n".join([
            "Alice: We decided to launch the beta next Tuesday with the current onboarding flow.",
            "Bob: Action item: send launch notes to the customer success team.",
        ]),
        encoding="utf-8",
    )
    (rec / "action_items.json").write_text(
        json.dumps([
            {
                "description": "Send launch notes to the customer success team",
                "category": "explicit",
                "assignee": "Bob",
                "context": "",
                "line_number": 2,
            }
        ]),
        encoding="utf-8",
    )

    text = export_markdown(rec)

    assert text.startswith("---\n")
    assert 'title: "Weekly Sync"' in text
    assert 'platform: "Microsoft Teams"' in text
    assert 'duration: "1:05"' in text
    assert '  - "Alice"' in text
    assert '  - "planning"' in text
    assert "speakers: 2" in text
    assert f'source: "{rec.name}"' in text
    assert "# Weekly Sync" in text
    assert "## Summary" in text
    assert "Discussed launch readiness." in text
    assert "## Decisions" in text
    assert "launch the beta next Tuesday" in text
    assert "## Action Items" in text
    assert "- [ ] Send launch notes to the customer success team (Bob)" in text
    assert "## Transcript" in text
    assert "```text" in text


def test_missing_files_returns_frontmatter(tmp_path):
    rec = _make_recording(tmp_path, "2026-03-11_10-30-00_Empty")

    text = export_markdown(rec)

    assert text.startswith("---\n")
    assert f'title: "{rec.name}"' in text
    assert f"# {rec.name}" in text
    assert "## Summary" not in text
    assert "## Transcript" not in text


def test_null_metadata_fields_do_not_crash(tmp_path):
    rec = _make_recording(tmp_path)
    metadata = {
        "meeting_subject": "Nullable Fields",
        "meeting_attendees": None,
        "tags": None,
        "speaker_count": None,
    }

    text = export_markdown(rec, metadata)

    assert 'title: "Nullable Fields"' in text
    assert "attendees: []" in text
    assert "tags: []" in text
    assert "speakers: 0" in text


def test_save_markdown_writes_default_path_and_round_trips(tmp_path):
    rec = _make_recording(tmp_path)
    metadata = {"meeting_subject": "Round Trip", "tags": ["export"]}
    (rec / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    out_path = save_markdown(rec)

    assert out_path == rec / f"{rec.name}.md"
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == export_markdown(rec)
