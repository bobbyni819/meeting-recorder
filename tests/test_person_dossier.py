"""Tests for person dossier aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.person_dossier import (
    Dossier,
    MeetingRef,
    build_dossier,
    format_dossier,
)


def _make_rec(
    base: Path,
    name: str,
    *,
    meta: dict,
    action_items: list[dict] | None = None,
    segments: list[dict] | None = None,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if action_items is not None:
        (rec / "action_items.json").write_text(
            json.dumps(action_items), encoding="utf-8"
        )
    if segments is not None:
        (rec / "transcript.json").write_text(
            json.dumps({"segments": segments}), encoding="utf-8"
        )
    return rec


def test_build_dossier_aggregates_person_data(tmp_path: Path):
    _make_rec(
        tmp_path,
        "2026-06-10_09-00-00_Product_Sync",
        meta={
            "meeting_attendees": ["Rachel Wu", "Sam Patel", "Dana Lee"],
            "meeting_subject": "Product Sync",
            "start_time": "2026-06-10T09:00:00",
            "duration_seconds": 1800,
            "tags": ["roadmap", "launch"],
            "speaker_map": {"SPEAKER_00": "Rachel Wu", "SPEAKER_01": "Sam Patel"},
        },
        action_items=[
            {
                "description": "Draft the launch checklist",
                "assignee": "Rachel Wu",
            }
        ],
        segments=[
            {
                "speaker": "SPEAKER_00",
                "start": 0,
                "end": 300,
                "text": "I will draft the launch checklist",
            },
            {
                "speaker": "SPEAKER_01",
                "start": 300,
                "end": 600,
                "text": "I can review the milestones",
            },
        ],
    )
    _make_rec(
        tmp_path,
        "2026-06-11_10-00-00_Planning",
        meta={
            "meeting_attendees": ["Rachel Wu", "Sam Patel"],
            "meeting_subject": "Planning",
            "start_time": "2026-06-11T10:00:00",
            "duration_seconds": 1200,
            "tags": ["roadmap"],
        },
        action_items=[
            {
                "text": "Rachel should send the roadmap update",
                "owner": "",
            }
        ],
        segments=[
            {
                "speaker": "Rachel Wu",
                "start": 0,
                "end": 120,
                "text": "Here is the roadmap update",
            }
        ],
    )
    _make_rec(
        tmp_path,
        "2026-06-12_11-00-00_Null_Fields",
        meta={
            "meeting_attendees": None,
            "meeting_subject": "Null Fields",
            "duration_seconds": None,
            "tags": None,
            "speaker_map": None,
        },
        action_items=[{"description": None, "assignee": None}],
        segments=None,
    )

    dossier = build_dossier("rachel", recordings_dir=tmp_path)

    assert dossier.name == "rachel"
    assert dossier.matched_name == "Rachel Wu"
    assert dossier.meeting_count == 2
    assert dossier.total_minutes == pytest.approx(50.0)
    assert [m.subject for m in dossier.meetings] == ["Planning", "Product Sync"]
    assert any("launch checklist" in item for item in dossier.action_items)
    assert any("roadmap update" in item for item in dossier.action_items)
    assert dossier.talk_time_minutes == pytest.approx(7.0)
    assert ("Sam Patel", 2) in dossier.top_collaborators
    assert dossier.recent_topics[0] == "roadmap"


def test_build_dossier_matches_person_as_speaker_only(tmp_path: Path):
    _make_rec(
        tmp_path,
        "2026-06-10_09-00-00_Speaker_Only",
        meta={
            "meeting_attendees": ["Sam Patel"],
            "meeting_subject": "Speaker Only",
            "duration_seconds": 600,
            "tags": ["design"],
            "speaker_map": {"SPEAKER_00": "Rachel Wu"},
        },
        segments=[
            {
                "speaker": "SPEAKER_00",
                "start": 0,
                "end": 60,
                "text": "Design review notes",
            }
        ],
    )

    dossier = build_dossier("rachel", recordings_dir=tmp_path)

    assert dossier.meeting_count == 1
    assert dossier.matched_name == "Rachel Wu"
    assert dossier.meetings[0].subject == "Speaker Only"


def test_build_dossier_nonexistent_person_is_empty(tmp_path: Path):
    _make_rec(
        tmp_path,
        "2026-06-10_09-00-00_Product_Sync",
        meta={
            "meeting_attendees": ["Rachel Wu", "Sam Patel"],
            "meeting_subject": "Product Sync",
            "duration_seconds": 1800,
            "tags": ["roadmap"],
        },
    )

    dossier = build_dossier("No Such Person", recordings_dir=tmp_path)

    assert dossier.meeting_count == 0
    assert dossier.meetings == []
    assert dossier.action_items == []
    assert dossier.talk_time_minutes is None


def test_format_dossier_includes_sections():
    dossier = Dossier(
        name="rachel",
        matched_name="Rachel Wu",
        meeting_count=1,
        total_minutes=30.0,
        meetings=[
            MeetingRef(
                dir_name="2026-06-10_09-00-00_Product_Sync",
                date="2026-06-10",
                subject="Product Sync",
                duration_min=30.0,
            )
        ],
        action_items=["Draft the launch checklist"],
        talk_time_minutes=5.0,
        top_collaborators=[("Sam Patel", 1)],
        recent_topics=["roadmap"],
    )

    text = format_dossier(dossier)

    assert "PERSON DOSSIER: Rachel Wu" in text
    assert "RECENT MEETINGS" in text
    assert "ACTION ITEMS" in text
    assert "TOP COLLABORATORS" in text
    assert "TOPICS" in text
