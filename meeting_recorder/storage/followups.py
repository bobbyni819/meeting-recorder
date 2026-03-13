"""Cross-recording follow-up tracker.

Aggregates action items from all recordings, groups by meeting subject,
and provides a unified view of pending follow-ups.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FollowUp:
    """A follow-up item from a specific recording."""
    description: str
    assignee: str
    category: str
    meeting_subject: str
    meeting_date: str
    recording_dir: str
    completed: bool = False


def gather_followups(
    recordings_dir: Path,
    include_completed: bool = False,
) -> list[FollowUp]:
    """Gather all follow-up items from all recordings.

    Args:
        recordings_dir: Base recordings directory.
        include_completed: Whether to include completed items.

    Returns:
        List of FollowUp items sorted by date descending.
    """
    if not recordings_dir.exists():
        return []

    followups: list[FollowUp] = []

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir():
            continue

        # Load action items
        ai_path = rec_dir / "action_items.json"
        if not ai_path.exists():
            continue

        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            continue

        if not items:
            continue

        # Load metadata for context
        meta = {}
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass

        subject = meta.get("meeting_subject", "")
        if not subject:
            # Derive from folder name
            name = rec_dir.name
            subject = name[20:].replace("_", " ").strip() if len(name) > 20 else "Unknown"

        # Parse date from folder name
        date_str = rec_dir.name[:10] if len(rec_dir.name) >= 10 else ""

        # Load completion status
        status = _load_completion_status(rec_dir)

        for item in items:
            desc = item.get("description", "")
            if not desc:
                continue
            is_completed = status.get(desc, False)
            if not include_completed and is_completed:
                continue
            followups.append(FollowUp(
                description=desc,
                assignee=item.get("assignee", ""),
                category=item.get("category", ""),
                meeting_subject=subject,
                meeting_date=date_str,
                recording_dir=str(rec_dir),
                completed=is_completed,
            ))

    # Sort by date descending (newest meetings first)
    followups.sort(key=lambda f: f.meeting_date, reverse=True)
    return followups


def mark_completed(rec_path: Path, description: str, completed: bool = True) -> None:
    """Mark a follow-up item as completed or not.

    Stores completion state in followup_status.json in the recording dir.
    """
    status = _load_completion_status(rec_path)
    if completed:
        status[description] = True
    else:
        status.pop(description, None)
    _save_completion_status(rec_path, status)


def _load_completion_status(rec_path: Path) -> dict[str, bool]:
    """Load completion status from recording directory."""
    status_path = rec_path / "followup_status.json"
    if status_path.exists():
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_completion_status(rec_path: Path, status: dict[str, bool]) -> None:
    """Save completion status to recording directory."""
    status_path = rec_path / "followup_status.json"
    try:
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to save followup status to %s", status_path)


def format_followups(followups: list[FollowUp]) -> str:
    """Format follow-ups as readable text grouped by meeting."""
    if not followups:
        return "No pending follow-ups."

    lines: list[str] = ["PENDING FOLLOW-UPS", "=" * 50, ""]

    # Group by meeting subject + date
    groups: dict[str, list[FollowUp]] = {}
    for fu in followups:
        key = f"{fu.meeting_date} — {fu.meeting_subject}"
        groups.setdefault(key, []).append(fu)

    for key, items in groups.items():
        lines.append(key)
        lines.append("-" * len(key))
        for item in items:
            check = "\u2713" if item.completed else " "
            assignee = f" @{item.assignee}" if item.assignee and item.assignee != "me" else ""
            lines.append(f"  [{check}] {item.description}{assignee}")
        lines.append("")

    total = len(followups)
    completed = sum(1 for f in followups if f.completed)
    lines.append(f"Total: {total} items ({completed} completed)")

    return "\n".join(lines)
