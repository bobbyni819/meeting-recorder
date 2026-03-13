"""Meeting recap generator.

Produces a concise, shareable meeting recap combining key info from multiple
analysis modules into a clipboard-ready format suitable for email or chat.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MeetingRecap:
    """Structured meeting recap."""
    subject: str
    date: str
    duration_min: float
    speakers: list[str]
    summary_lines: list[str]
    action_items: list[str]
    decisions: list[str]
    unanswered_questions: list[str]
    key_topics: list[str]


def generate_recap(
    rec_path: Path,
    meta: dict | None = None,
) -> MeetingRecap | None:
    """Generate a meeting recap from a recording directory.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata.

    Returns:
        MeetingRecap or None if insufficient data.
    """
    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                return None
        else:
            return None

    dur = meta.get("duration_seconds", 0)
    if dur < 60:
        return None

    # Subject
    subject = meta.get("meeting_subject", "")
    if not subject and len(rec_path.name) > 20:
        subject = rec_path.name[20:].replace("_", " ").strip()
    subject = subject or "Meeting"

    # Date
    date_str = rec_path.name[:10] if len(rec_path.name) >= 10 else ""

    # Speakers
    speakers = []
    speaker_map = meta.get("speaker_map", {})
    if speaker_map:
        speakers = list(speaker_map.values())
    elif meta.get("speaker_count"):
        speakers = [f"Speaker {i+1}" for i in range(meta["speaker_count"])]

    # Summary
    summary_lines = []
    summary_path = rec_path / "summary.md"
    if summary_path.exists():
        try:
            text = summary_path.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("- ") or line.startswith("* "):
                    summary_lines.append(line[2:].strip())
                elif line.startswith("## "):
                    continue  # skip headers
                elif line and not line.startswith("#") and len(line) > 20:
                    summary_lines.append(line)
            summary_lines = summary_lines[:5]  # cap at 5 key points
        except Exception:
            pass

    # Action items
    action_items = []
    ai_path = rec_path / "action_items.json"
    if ai_path.exists():
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            for item in items:
                if isinstance(item, dict):
                    text = item.get("text", "")
                    assignee = item.get("assignee", "")
                    if text and len(text) > 10:
                        prefix = f"[{assignee}] " if assignee else ""
                        action_items.append(f"{prefix}{text}")
                elif isinstance(item, str) and len(item) > 10:
                    action_items.append(item)
        except Exception:
            pass

    # Decisions
    decisions = []
    dec_path = rec_path / "decisions.json"
    if dec_path.exists():
        try:
            with open(dec_path, "r", encoding="utf-8") as f:
                dec_data = json.load(f)
            dec_list = dec_data.get("decisions", []) if isinstance(dec_data, dict) else dec_data
            for d in dec_list:
                desc = d.get("description", "") if isinstance(d, dict) else str(d)
                if desc and len(desc) > 10:
                    decisions.append(desc)
        except Exception:
            pass

    # Unanswered questions
    unanswered = []
    try:
        from meeting_recorder.storage.question_tracker import analyze_questions
        qr = analyze_questions(rec_path)
        if qr and qr.unanswered_questions:
            for q in qr.unanswered_questions[:5]:
                unanswered.append(q.text)
    except Exception:
        pass

    # Key topics
    key_topics = []
    txt_path = rec_path / "transcript.txt"
    if txt_path.exists():
        try:
            from meeting_recorder.storage.comparison import _extract_topics
            text = txt_path.read_text(encoding="utf-8")
            key_topics = list(sorted(_extract_topics(text, min_freq=2, top_n=8)))
        except Exception:
            pass

    if not summary_lines and not action_items and not decisions:
        return None

    return MeetingRecap(
        subject=subject,
        date=date_str,
        duration_min=round(dur / 60.0, 1),
        speakers=speakers,
        summary_lines=summary_lines,
        action_items=action_items,
        decisions=decisions,
        unanswered_questions=unanswered,
        key_topics=key_topics,
    )


def format_recap(recap: MeetingRecap | None) -> str:
    """Format recap as shareable text."""
    if recap is None:
        return "Not enough data to generate recap."

    lines = [
        f"MEETING RECAP: {recap.subject}",
        "=" * 50,
        "",
    ]

    # Header
    header_parts = []
    if recap.date:
        header_parts.append(f"Date: {recap.date}")
    header_parts.append(f"Duration: {recap.duration_min:.0f} min")
    if recap.speakers:
        header_parts.append(f"Attendees: {', '.join(recap.speakers[:6])}")
    lines.append("  ".join(header_parts))
    lines.append("")

    # Key points
    if recap.summary_lines:
        lines.append("KEY POINTS")
        lines.append("-" * 40)
        for point in recap.summary_lines:
            lines.append(f"  - {point}")
        lines.append("")

    # Decisions
    if recap.decisions:
        lines.append("DECISIONS")
        lines.append("-" * 40)
        for dec in recap.decisions:
            lines.append(f"  - {dec}")
        lines.append("")

    # Action items
    if recap.action_items:
        lines.append("ACTION ITEMS")
        lines.append("-" * 40)
        for item in recap.action_items:
            lines.append(f"  [ ] {item}")
        lines.append("")

    # Unanswered questions
    if recap.unanswered_questions:
        lines.append("OPEN QUESTIONS")
        lines.append("-" * 40)
        for q in recap.unanswered_questions:
            lines.append(f"  ? {q}")
        lines.append("")

    # Topics
    if recap.key_topics:
        lines.append(f"Topics: {', '.join(recap.key_topics)}")

    return "\n".join(lines)
