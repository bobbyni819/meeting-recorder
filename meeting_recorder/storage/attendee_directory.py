"""Attendee directory — who you meet with and how often.

Builds a profile for each person you've met with based on recording metadata.
Useful for quickly finding meetings involving specific people.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AttendeeProfile:
    """Profile of a person you've met with."""
    name: str
    meeting_count: int
    total_minutes: float
    first_seen: str  # YYYY-MM-DD
    last_seen: str  # YYYY-MM-DD
    common_subjects: list[str]
    common_apps: list[str]
    recordings: list[Path]


def build_directory(recordings_dir: Path) -> list[AttendeeProfile]:
    """Build an attendee directory from all recordings.

    Args:
        recordings_dir: Base recordings directory.

    Returns:
        List of AttendeeProfile sorted by meeting_count descending.
    """
    if not recordings_dir.exists():
        return []

    # Collect per-attendee data
    attendee_data: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "total_minutes": 0.0,
        "dates": [],
        "subjects": [],
        "apps": [],
        "recordings": [],
    })

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir():
            continue
        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        attendees = meta.get("meeting_attendees", [])
        if not attendees:
            continue

        date_str = rec_dir.name[:10] if len(rec_dir.name) >= 10 else ""
        duration = meta.get("duration_seconds", 0) / 60
        subject = meta.get("meeting_subject", "")
        app = meta.get("app_name", "")

        for att in attendees:
            key = att.strip().lower()
            if not key:
                continue
            data = attendee_data[key]
            data["display_name"] = att.strip()
            data["count"] += 1
            data["total_minutes"] += duration
            if date_str:
                data["dates"].append(date_str)
            if subject:
                data["subjects"].append(subject)
            if app:
                data["apps"].append(app)
            data["recordings"].append(rec_dir)

    # Build profiles
    profiles: list[AttendeeProfile] = []
    for key, data in attendee_data.items():
        dates = sorted(data["dates"])
        subject_counter = Counter(data["subjects"])
        app_counter = Counter(data["apps"])

        profiles.append(AttendeeProfile(
            name=data.get("display_name", key),
            meeting_count=data["count"],
            total_minutes=data["total_minutes"],
            first_seen=dates[0] if dates else "",
            last_seen=dates[-1] if dates else "",
            common_subjects=[s for s, _ in subject_counter.most_common(3)],
            common_apps=[a for a, _ in app_counter.most_common(2)],
            recordings=data["recordings"],
        ))

    profiles.sort(key=lambda p: p.meeting_count, reverse=True)
    return profiles


def find_meetings_with(
    recordings_dir: Path,
    name: str,
) -> list[tuple[Path, dict]]:
    """Find all recordings involving a specific person.

    Args:
        recordings_dir: Base recordings directory.
        name: Person's name (case-insensitive partial match).

    Returns:
        List of (recording_path, metadata) sorted by date descending.
    """
    if not recordings_dir.exists():
        return []

    name_lower = name.lower().strip()
    results: list[tuple[Path, dict]] = []

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir():
            continue
        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        attendees = meta.get("meeting_attendees", [])
        organizer = meta.get("meeting_organizer", "")
        speaker_map = meta.get("speaker_map", {})

        # Check attendees, organizer, and speakers
        all_names = [a.lower() for a in attendees]
        if organizer:
            all_names.append(organizer.lower())
        all_names.extend(v.lower() for v in speaker_map.values())

        if any(name_lower in n for n in all_names):
            results.append((rec_dir, meta))

    results.sort(key=lambda x: x[0].name, reverse=True)
    return results


def format_directory(profiles: list[AttendeeProfile], max_entries: int = 20) -> str:
    """Format the directory as readable text."""
    if not profiles:
        return "No attendees found."

    lines: list[str] = ["ATTENDEE DIRECTORY", "=" * 50, ""]

    for p in profiles[:max_entries]:
        hours = p.total_minutes / 60
        lines.append(f"{p.name}")
        lines.append(
            f"  {p.meeting_count} meetings  \u2022  "
            f"{hours:.1f}h total  \u2022  "
            f"{p.first_seen} to {p.last_seen}"
        )
        if p.common_subjects:
            lines.append(f"  Topics: {', '.join(p.common_subjects)}")
        lines.append("")

    return "\n".join(lines)
