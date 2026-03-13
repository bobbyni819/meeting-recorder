"""Cross-recording action item tracker.

Tracks action items across recordings, identifies stale (unresolved) items,
detects when items from one meeting are mentioned in later meetings, and
reports compliance rates per assignee and meeting series.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TrackedAction:
    """An action item tracked across meetings."""
    text: str
    assignee: str
    source_meeting: str
    source_date: str
    mentioned_in: list[str]  # later meetings that reference it
    likely_resolved: bool


@dataclass
class ActionTracker:
    """Action item tracking report."""
    total_actions: int
    resolved_count: int
    stale_count: int
    compliance_rate: float  # 0-100
    stale_actions: list[TrackedAction]  # unresolved items
    per_assignee: dict[str, tuple[int, int]]  # assignee → (total, resolved)
    per_meeting: dict[str, int]  # subject → action count


def track_actions(
    recordings_dir: Path,
    weeks: int = 8,
) -> ActionTracker | None:
    """Track action items across recent recordings.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.

    Returns:
        ActionTracker or None if insufficient data.
    """
    if not recordings_dir.exists():
        return None

    cutoff = date.today() - timedelta(weeks=weeks)

    # Collect all action items with dates
    all_items: list[tuple[str, str, str, str, list]] = []  # (text, assignee, meeting, date, raw_items)
    meeting_transcripts: dict[str, str] = {}  # rec_name → transcript text

    rec_dirs = sorted(
        (d for d in recordings_dir.iterdir() if d.is_dir() and len(d.name) >= 10),
        key=lambda d: d.name,
    )

    for rec_dir in rec_dirs:
        try:
            rec_date = date.fromisoformat(rec_dir.name[:10])
        except ValueError:
            continue
        if rec_date < cutoff:
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

        # Load metadata for subject
        subject = ""
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                subject = meta.get("meeting_subject", "")
            except Exception:
                pass

        if not subject and len(rec_dir.name) > 20:
            subject = rec_dir.name[20:].replace("_", " ").strip()

        # Load transcript for cross-reference
        txt_path = rec_dir / "transcript.txt"
        if txt_path.exists():
            try:
                meeting_transcripts[rec_dir.name] = txt_path.read_text(encoding="utf-8").lower()
            except Exception:
                pass

        date_str = rec_dir.name[:10]
        for item in items:
            if isinstance(item, dict):
                text = item.get("text", item.get("description", ""))
                assignee = item.get("assignee", "")
            else:
                text = str(item)
                assignee = ""

            if text and len(text) > 10:
                all_items.append((text, assignee, subject or "Meeting", date_str, []))

    if not all_items:
        return None

    # Cross-reference: check if action keywords appear in later transcripts
    tracked: list[TrackedAction] = []
    per_assignee: dict[str, list[bool]] = defaultdict(list)
    per_meeting: dict[str, int] = defaultdict(int)

    for text, assignee, meeting, item_date, _ in all_items:
        per_meeting[meeting] += 1

        # Extract key phrases from the action item
        key_words = _extract_key_phrases(text)
        mentioned_in: list[str] = []
        likely_resolved = False

        # Search for mentions in later transcripts
        for rec_name, transcript in meeting_transcripts.items():
            rec_date_str = rec_name[:10]
            if rec_date_str <= item_date:
                continue  # only check later meetings

            # Check if key phrases appear in later transcript
            matches = sum(1 for kw in key_words if kw in transcript)
            if matches >= min(2, len(key_words)):
                mentioned_in.append(rec_name[:10])
                # Check for resolution signals
                if any(sig in transcript for sig in [
                    "done", "completed", "finished", "resolved",
                    "shipped", "deployed", "sent", "submitted",
                ]):
                    likely_resolved = True

        ta = TrackedAction(
            text=text[:150],
            assignee=assignee,
            source_meeting=meeting,
            source_date=item_date,
            mentioned_in=mentioned_in,
            likely_resolved=likely_resolved or len(mentioned_in) > 0,
        )
        tracked.append(ta)

        if assignee:
            per_assignee[assignee].append(ta.likely_resolved)

    total = len(tracked)
    resolved = sum(1 for t in tracked if t.likely_resolved)
    stale = [t for t in tracked if not t.likely_resolved]
    compliance = (resolved / total * 100) if total > 0 else 0

    # Aggregate per-assignee stats
    assignee_stats: dict[str, tuple[int, int]] = {}
    for name, statuses in per_assignee.items():
        assignee_stats[name] = (len(statuses), sum(statuses))

    return ActionTracker(
        total_actions=total,
        resolved_count=resolved,
        stale_count=len(stale),
        compliance_rate=round(compliance, 1),
        stale_actions=stale[:20],  # limit
        per_assignee=assignee_stats,
        per_meeting=dict(per_meeting),
    )


def format_action_tracker(report: ActionTracker | None) -> str:
    """Format action tracker report as readable text."""
    if report is None:
        return "No action items found across recent recordings."

    lines = [
        "ACTION ITEM TRACKER",
        "=" * 55,
        "",
        f"  Total actions:     {report.total_actions}",
        f"  Resolved:          {report.resolved_count}",
        f"  Stale:             {report.stale_count}",
        f"  Compliance rate:   {report.compliance_rate:.0f}%",
        "",
    ]

    # Per-assignee breakdown
    if report.per_assignee:
        lines.append("  Per Assignee")
        lines.append("  " + "-" * 45)
        for name, (total, resolved) in sorted(
            report.per_assignee.items(), key=lambda x: -x[1][0]
        ):
            rate = (resolved / total * 100) if total > 0 else 0
            bar = "\u2588" * int(rate / 10) + "\u2591" * (10 - int(rate / 10))
            lines.append(f"    {name[:20]:<20}  [{bar}] {rate:.0f}%  ({resolved}/{total})")
        lines.append("")

    # Per-meeting breakdown
    if report.per_meeting:
        lines.append("  Actions by Meeting")
        lines.append("  " + "-" * 45)
        for subject, count in sorted(report.per_meeting.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"    {subject[:30]:<30}  {count} action(s)")
        lines.append("")

    # Stale actions
    if report.stale_actions:
        lines.append("  Stale Actions (likely unresolved)")
        lines.append("  " + "-" * 45)
        for a in report.stale_actions[:10]:
            lines.append(f"    [{a.source_date}] {a.text[:60]}")
            if a.assignee:
                lines.append(f"      Assignee: {a.assignee}")
        lines.append("")

    return "\n".join(lines)


def _extract_key_phrases(text: str) -> list[str]:
    """Extract key phrases from action item text for cross-referencing."""
    text_lower = text.lower()
    # Remove common filler words
    stop_words = {"the", "a", "an", "to", "and", "or", "of", "in", "on", "for", "with",
                  "is", "are", "was", "were", "will", "should", "need", "needs",
                  "i", "we", "they", "it", "that", "this"}
    words = re.findall(r"[a-z]+", text_lower)
    key = [w for w in words if w not in stop_words and len(w) > 3]
    return key[:5]  # top 5 keywords
