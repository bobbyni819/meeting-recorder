"""Generate daily and weekly meeting digests.

Combines information from multiple recordings into a single, shareable
summary that captures what happened across all meetings in a time period.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def daily_digest(
    recordings_dir: Path,
    date: datetime | None = None,
) -> str:
    """Generate a daily digest of all meetings from a specific date.

    Args:
        recordings_dir: Base recordings directory.
        date: The date to generate digest for (defaults to today).

    Returns:
        Formatted markdown digest text.
    """
    if date is None:
        date = datetime.now()
    date_str = date.strftime("%Y-%m-%d")
    recordings = _get_recordings_for_dates(recordings_dir, date_str, date_str)
    if not recordings:
        return f"# Daily Digest — {date_str}\n\nNo meetings recorded today."
    return _build_digest(f"Daily Digest — {date_str}", recordings)


def weekly_digest(
    recordings_dir: Path,
    end_date: datetime | None = None,
) -> str:
    """Generate a weekly digest of all meetings from the past 7 days.

    Args:
        recordings_dir: Base recordings directory.
        end_date: The end date (defaults to today).

    Returns:
        Formatted markdown digest text.
    """
    if end_date is None:
        end_date = datetime.now()
    start_date = end_date - timedelta(days=6)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    recordings = _get_recordings_for_dates(recordings_dir, start_str, end_str)
    if not recordings:
        return f"# Weekly Digest — {start_str} to {end_str}\n\nNo meetings recorded this week."
    return _build_digest(f"Weekly Digest — {start_str} to {end_str}", recordings)


def _get_recordings_for_dates(
    recordings_dir: Path,
    start_date: str,
    end_date: str,
) -> list[tuple[Path, dict]]:
    """Get recordings that fall within a date range.

    Args:
        recordings_dir: Base recordings directory.
        start_date: Start date string YYYY-MM-DD (inclusive).
        end_date: End date string YYYY-MM-DD (inclusive).

    Returns:
        List of (recording_path, metadata) tuples sorted chronologically.
    """
    if not recordings_dir.exists():
        return []

    results: list[tuple[Path, dict]] = []
    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir():
            continue
        name = rec_dir.name
        if len(name) < 10:
            continue
        folder_date = name[:10]
        if folder_date < start_date or folder_date > end_date:
            continue

        meta = {}
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass

        results.append((rec_dir, meta))

    # Sort chronologically
    results.sort(key=lambda x: x[0].name)
    return results


def _build_digest(title: str, recordings: list[tuple[Path, dict]]) -> str:
    """Build a formatted digest from a list of recordings."""
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")

    total_duration = 0
    all_attendees: set[str] = set()
    all_action_items: list[tuple[str, str]] = []  # (description, meeting_subject)

    for rec_path, meta in recordings:
        name = rec_path.name
        date_str = name[:10]
        time_str = name[11:16].replace("-", ":") if len(name) >= 16 else ""
        subject = meta.get("meeting_subject", "")
        if not subject:
            subject = name[20:].replace("_", " ").strip() if len(name) > 20 else "Recording"
        duration = meta.get("duration_seconds", 0)
        total_duration += duration
        attendees = meta.get("meeting_attendees", [])
        all_attendees.update(a.strip() for a in attendees)

        # Meeting heading
        dur_min = int(duration // 60)
        lines.append(f"## {subject}")
        info_parts = [f"{date_str} {time_str}"]
        if dur_min > 0:
            lines_hours = dur_min // 60
            lines_mins = dur_min % 60
            if lines_hours:
                info_parts.append(f"{lines_hours}h {lines_mins}m")
            else:
                info_parts.append(f"{dur_min}min")
        app = meta.get("app_name", "")
        if app:
            info_parts.append(app)
        if attendees:
            info_parts.append(f"{len(attendees)} attendees")
        lines.append(" | ".join(info_parts))
        lines.append("")

        # Summary snippet
        summary_path = rec_path / "summary.md"
        if summary_path.exists():
            try:
                summary = summary_path.read_text(encoding="utf-8").strip()
                # Take first 3 non-empty lines
                summary_lines = [l for l in summary.split("\n") if l.strip()][:3]
                for sl in summary_lines:
                    # Downgrade heading levels
                    if sl.startswith("# "):
                        sl = "**" + sl.lstrip("# ").strip() + "**"
                    lines.append(f"> {sl}")
                lines.append("")
            except Exception:
                pass

        # Action items for this meeting
        ai_path = rec_path / "action_items.json"
        if ai_path.exists():
            try:
                with open(ai_path, "r", encoding="utf-8") as f:
                    items = json.load(f)
                for item in items[:5]:
                    desc = item.get("description", "")
                    if desc:
                        assignee = item.get("assignee", "")
                        suffix = f" @{assignee}" if assignee and assignee != "me" else ""
                        lines.append(f"- [ ] {desc}{suffix}")
                        all_action_items.append((desc, subject))
            except Exception:
                pass
            if lines[-1].startswith("- "):
                lines.append("")

        lines.append("---")
        lines.append("")

    # Summary section
    lines.append("## Overview")
    lines.append("")
    total_hours = total_duration / 3600
    total_mins = int((total_duration % 3600) / 60)
    if total_hours >= 1:
        lines.append(f"- **{len(recordings)} meeting(s)** totaling {int(total_hours)}h {total_mins}m")
    else:
        lines.append(f"- **{len(recordings)} meeting(s)** totaling {int(total_duration / 60)}min")
    if all_attendees:
        lines.append(f"- **{len(all_attendees)} unique attendees**: {', '.join(sorted(all_attendees)[:10])}")
    if all_action_items:
        lines.append(f"- **{len(all_action_items)} action item(s)** across all meetings")
    lines.append("")

    return "\n".join(lines)
