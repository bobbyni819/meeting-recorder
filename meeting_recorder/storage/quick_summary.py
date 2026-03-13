"""Quick-look meeting summary card.

Generates a compact, shareable summary card for a recording —
like a tweet-sized overview with key metrics, decisions, and action items.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QuickCard:
    """Compact meeting summary card."""
    subject: str
    date: str
    duration_min: int
    attendee_count: int
    key_points: list[str]  # top 3 points from summary
    action_items: list[str]  # top 3 action items
    speakers: list[str]  # top 3 speakers
    quality_score: int | None
    app_name: str


def generate_quick_card(
    rec_path: Path,
    meta: dict | None = None,
    max_points: int = 3,
) -> QuickCard | None:
    """Generate a compact summary card for a recording.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata (loaded from file if None).
        max_points: Maximum number of key points and action items.

    Returns:
        QuickCard or None if insufficient data.
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
    if dur < 30:
        return None

    subject = meta.get("meeting_subject", "")
    if not subject and len(rec_path.name) > 20:
        subject = rec_path.name[20:].replace("_", " ").strip()
    subject = subject or "Meeting"

    date_str = rec_path.name[:10] if len(rec_path.name) >= 10 else ""
    attendees = meta.get("meeting_attendees", [])
    app = meta.get("app_name", "")

    qs = meta.get("quality_scores", {})
    quality = qs.get("overall_score") if qs else None

    # Extract key points from summary
    key_points = _extract_key_points(rec_path, max_points)

    # Extract top action items
    action_items = _extract_top_actions(rec_path, max_points)

    # Top speakers from transcript
    speakers = _extract_top_speakers(rec_path, meta, max_points)

    return QuickCard(
        subject=subject,
        date=date_str,
        duration_min=round(dur / 60),
        attendee_count=len(attendees),
        key_points=key_points,
        action_items=action_items,
        speakers=speakers,
        quality_score=quality,
        app_name=app,
    )


def format_quick_card(card: QuickCard) -> str:
    """Format a quick card as shareable text."""
    lines = [
        f"{card.subject}",
        f"{card.date}  |  {card.duration_min} min"
        + (f"  |  {card.attendee_count} attendees" if card.attendee_count > 0 else "")
        + (f"  |  {card.app_name}" if card.app_name else ""),
    ]

    if card.key_points:
        lines.append("")
        for point in card.key_points:
            lines.append(f"  - {point}")

    if card.action_items:
        lines.append("")
        lines.append("  Action items:")
        for item in card.action_items:
            lines.append(f"  [ ] {item}")

    if card.speakers:
        lines.append("")
        lines.append(f"  Speakers: {', '.join(card.speakers)}")

    if card.quality_score is not None:
        lines.append(f"  Quality: {card.quality_score}/100")

    return "\n".join(lines)


# --- Helpers ---


def _extract_key_points(rec_path: Path, max_n: int) -> list[str]:
    """Extract key points from the summary file."""
    summary_path = rec_path / "summary.md"
    if not summary_path.exists():
        return []

    try:
        text = summary_path.read_text(encoding="utf-8").strip()
    except Exception:
        return []

    # Look for bullet points or numbered lists
    points: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Match bullet points or numbered items
        if line.startswith(("- ", "* ", "• ")):
            point = line.lstrip("-*• ").strip()
            if len(point) > 10:
                points.append(point[:120])
        elif len(line) > 10 and not line.startswith("#"):
            # First few sentences as fallback
            if not points:
                points.append(line[:120])

        if len(points) >= max_n:
            break

    return points


def _extract_top_actions(rec_path: Path, max_n: int) -> list[str]:
    """Extract top action items."""
    ai_path = rec_path / "action_items.json"
    if not ai_path.exists():
        return []

    try:
        with open(ai_path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return []

    result: list[str] = []
    for item in items[:max_n]:
        if isinstance(item, dict):
            text = item.get("text", "")
        else:
            text = str(item)
        if text:
            result.append(text[:100])
    return result


def _extract_top_speakers(rec_path: Path, meta: dict, max_n: int) -> list[str]:
    """Extract top speakers by talk time."""
    transcript_path = rec_path / "transcript.json"
    if not transcript_path.exists():
        return []

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
    except Exception:
        return []

    speaker_map = meta.get("speaker_map", {})
    speaker_times: dict[str, float] = {}

    for seg in tdata.get("segments", []):
        spk = seg.get("speaker", "Unknown")
        spk = speaker_map.get(spk, spk)
        dur = max(0, seg.get("end", 0) - seg.get("start", 0))
        speaker_times[spk] = speaker_times.get(spk, 0) + dur

    sorted_speakers = sorted(speaker_times.items(), key=lambda x: -x[1])
    return [name for name, _ in sorted_speakers[:max_n]]
