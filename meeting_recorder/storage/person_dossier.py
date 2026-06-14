"""Person dossiers across meeting recordings.

Builds a compact profile for one person by combining attendee metadata,
speaker analytics, action items, collaborators, and tags.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meeting_recorder.storage.recording_store import RecordingStore

logger = logging.getLogger(__name__)


@dataclass
class MeetingRef:
    dir_name: str
    date: str
    subject: str
    duration_min: float


@dataclass
class Dossier:
    name: str
    matched_name: str
    meeting_count: int
    total_minutes: float
    meetings: list[MeetingRef]
    action_items: list[str]
    talk_time_minutes: float | None
    top_collaborators: list[tuple[str, int]]
    recent_topics: list[str]


def build_dossier(
    name: str,
    *,
    config=None,
    recordings_dir: Path | None = None,
    max_meetings: int = 50,
) -> Dossier:
    """Build a dossier for one person across recordings.

    Matching is case-insensitive and partial against meeting attendees,
    ``metadata.speaker_map`` values, and named speakers in ``transcript.json``.
    Unreadable recordings are skipped.
    """
    query = (name or "").strip()
    empty = Dossier(
        name=query,
        matched_name="",
        meeting_count=0,
        total_minutes=0.0,
        meetings=[],
        action_items=[],
        talk_time_minutes=None,
        top_collaborators=[],
        recent_topics=[],
    )
    if not query:
        return empty

    root = _resolve_recordings_dir(config, recordings_dir)
    try:
        recordings = RecordingStore(root).list_recordings()
    except Exception:
        return empty

    matched: list[tuple[Path, dict[str, Any], list[str], list[str]]] = []
    canonical_counts: Counter[str] = Counter()

    for rec_dir in recordings:
        try:
            meta = _load_dict(rec_dir / "metadata.json")
            attendees = _string_list(meta.get("meeting_attendees") or [])
            speakers = _speaker_names(rec_dir, meta)
            candidates = attendees + speakers
            matched_names = [n for n in candidates if _matches(query, n)]
            if not matched_names:
                continue
            for display_name in matched_names:
                canonical_counts[display_name] += 1
            matched.append((rec_dir, meta, attendees, speakers))
        except Exception:
            logger.debug("Skipping unreadable recording for dossier: %s", rec_dir, exc_info=True)
            continue

    if not matched:
        return empty

    matched_name = _best_display_name(canonical_counts, query)
    selected = matched[:max(0, max_meetings)]

    meetings: list[MeetingRef] = []
    action_items: list[str] = []
    seen_actions: set[str] = set()
    collaborator_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    total_minutes = 0.0
    talk_seconds = 0.0
    saw_named_speaker = False

    for rec_dir, meta, attendees, speakers in selected:
        try:
            duration_min = round(_as_float(meta.get("duration_seconds")) / 60, 1)
            total_minutes += duration_min
            meetings.append(MeetingRef(
                dir_name=rec_dir.name,
                date=_meeting_date(rec_dir, meta),
                subject=_as_str(meta.get("meeting_subject")) or rec_dir.name,
                duration_min=duration_min,
            ))

            for attendee in attendees:
                if not _is_person_name(attendee, query, matched_name):
                    collaborator_counts[attendee] += 1

            for tag in _string_list(meta.get("tags") or []):
                topic_counts[tag] += 1

            for item_text in _person_action_items(rec_dir, meta, query, matched_name):
                key = item_text.lower()
                if key and key not in seen_actions:
                    seen_actions.add(key)
                    action_items.append(item_text)

            rec_talk_seconds, rec_saw_speaker = _talk_time_for_person(
                rec_dir, meta, query, matched_name, speakers
            )
            talk_seconds += rec_talk_seconds
            saw_named_speaker = saw_named_speaker or rec_saw_speaker
        except Exception:
            logger.debug("Skipping partial dossier data for: %s", rec_dir, exc_info=True)
            continue

    meetings.sort(key=lambda m: (m.date, m.dir_name), reverse=True)

    return Dossier(
        name=query,
        matched_name=matched_name,
        meeting_count=len(selected),
        total_minutes=round(total_minutes, 1),
        meetings=meetings,
        action_items=action_items,
        talk_time_minutes=round(talk_seconds / 60, 1) if saw_named_speaker else None,
        top_collaborators=collaborator_counts.most_common(10),
        recent_topics=[topic for topic, _ in topic_counts.most_common(10)],
    )


def format_dossier(d: Dossier) -> str:
    """Format a dossier for CLI output."""
    display = d.matched_name or d.name
    lines: list[str] = [
        f"PERSON DOSSIER: {display}",
        "=" * 50,
        "",
        f"Meetings: {d.meeting_count}",
        f"Total time: {d.total_minutes:.1f} min",
    ]

    if d.talk_time_minutes is None:
        lines.append("Talk time: not available")
    else:
        lines.append(f"Talk time: {d.talk_time_minutes:.1f} min")
    lines.append("")

    if d.meetings:
        lines.append("RECENT MEETINGS")
        lines.append("-" * 40)
        for meeting in d.meetings:
            lines.append(
                f"  {meeting.date} - {meeting.subject} "
                f"({meeting.duration_min:.1f} min) [{meeting.dir_name}]"
            )
        lines.append("")

    if d.action_items:
        lines.append("ACTION ITEMS")
        lines.append("-" * 40)
        for item in d.action_items:
            lines.append(f"  - {item}")
        lines.append("")

    if d.top_collaborators:
        lines.append("TOP COLLABORATORS")
        lines.append("-" * 40)
        for collaborator, count in d.top_collaborators:
            suffix = "meeting" if count == 1 else "meetings"
            lines.append(f"  {collaborator}: {count} {suffix}")
        lines.append("")

    if d.recent_topics:
        lines.append("TOPICS")
        lines.append("-" * 40)
        lines.append("  " + ", ".join(d.recent_topics))
        lines.append("")

    return "\n".join(lines).rstrip()


def _resolve_recordings_dir(config, recordings_dir: Path | None) -> Path:
    if recordings_dir is not None:
        return Path(recordings_dir).expanduser()
    if config is not None:
        try:
            output_dir = getattr(config, "output_dir", None)
            if output_dir is not None:
                return Path(output_dir).expanduser()
        except Exception:
            pass
    return Path("~/MeetingRecordings").expanduser()


def _load_dict(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                result.append(stripped)
    return result


def _speaker_names(rec_dir: Path, meta: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    speaker_map = meta.get("speaker_map") or {}
    if not isinstance(speaker_map, dict):
        speaker_map = {}

    for mapped in speaker_map.values():
        if isinstance(mapped, str):
            _add_name(names, seen, mapped)

    tdata = _load_json(rec_dir / "transcript.json")
    if not isinstance(tdata, dict):
        return names

    segments = tdata.get("segments") or []
    if not isinstance(segments, list):
        return names

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        raw = seg.get("speaker")
        if not isinstance(raw, str):
            continue
        mapped = speaker_map.get(raw, raw)
        if isinstance(mapped, str):
            _add_name(names, seen, mapped)
    return names


def _add_name(names: list[str], seen: set[str], value: str) -> None:
    name = value.strip()
    key = name.lower()
    if name and key not in seen and _is_named_speaker(name):
        seen.add(key)
        names.append(name)


def _is_named_speaker(name: str) -> bool:
    lower = name.strip().lower()
    if not lower or lower == "unknown":
        return False
    generic_prefixes = ("speaker ", "speaker_", "participant ", "participant_")
    return not lower.startswith(generic_prefixes)


def _matches(query: str, candidate: str) -> bool:
    q = query.strip().lower()
    c = candidate.strip().lower()
    return bool(q and c and (q in c or c in q))


def _best_display_name(counts: Counter[str], fallback: str) -> str:
    if not counts:
        return fallback
    return max(counts, key=lambda n: (counts[n], len(n), n.lower()))


def _as_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _meeting_date(rec_dir: Path, meta: dict[str, Any]) -> str:
    start_time = _as_str(meta.get("start_time"))
    if len(start_time) >= 10:
        return start_time[:10]
    if len(rec_dir.name) >= 10:
        return rec_dir.name[:10]
    return ""


def _is_person_name(candidate: str, query: str, matched_name: str) -> bool:
    if _matches(query, candidate):
        return True
    return bool(matched_name and candidate.strip().lower() == matched_name.strip().lower())


def _person_action_items(
    rec_dir: Path,
    meta: dict[str, Any],
    query: str,
    matched_name: str,
) -> list[str]:
    raw_items = _read_action_items(rec_dir)
    if not raw_items:
        try:
            from meeting_recorder.storage.action_items import extract_action_items_for_recording

            raw_items = list(extract_action_items_for_recording(rec_dir, meta) or [])
        except Exception:
            raw_items = []

    results: list[str] = []
    for item in raw_items:
        owner = _item_owner(item)
        text = _item_text(item)
        if not text:
            continue
        if owner:
            if _is_person_name(owner, query, matched_name):
                results.append(text)
        elif _text_mentions_person(text, query, matched_name):
            results.append(text)
    return results


def _read_action_items(rec_dir: Path) -> list[Any]:
    data = _load_json(rec_dir / "action_items.json")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items") or data.get("action_items") or []
        if isinstance(items, list):
            return items
    return []


def _item_owner(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("assignee", "owner"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    else:
        for key in ("assignee", "owner"):
            value = getattr(item, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("description", "text", "title"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    else:
        for key in ("description", "text", "title"):
            value = getattr(item, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _text_mentions_person(text: str, query: str, matched_name: str) -> bool:
    lower = text.lower()
    names = [query, matched_name]
    for value in names:
        candidate = value.strip().lower()
        if candidate and candidate in lower:
            return True
    return False


def _talk_time_for_person(
    rec_dir: Path,
    meta: dict[str, Any],
    query: str,
    matched_name: str,
    speakers: list[str],
) -> tuple[float, bool]:
    saw_named_speaker = any(_is_person_name(speaker, query, matched_name) for speaker in speakers)
    if not saw_named_speaker:
        return 0.0, False

    try:
        from meeting_recorder.storage.speaker_analytics import analyze_speakers

        analytics = analyze_speakers(rec_dir, meta)
    except Exception:
        return 0.0, saw_named_speaker

    if analytics is None:
        return 0.0, saw_named_speaker

    talk_seconds = 0.0
    for stats in analytics.speakers or []:
        stat_name = getattr(stats, "name", "")
        if isinstance(stat_name, str) and _is_person_name(stat_name, query, matched_name):
            talk_seconds += _as_float(getattr(stats, "talk_seconds", 0.0))
    return talk_seconds, saw_named_speaker
