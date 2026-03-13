"""Collaboration analysis.

Analyzes attendee co-occurrence across meetings to identify
collaboration patterns, frequent pairs, and communication clusters.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CollaboratorPair:
    """A pair of people who meet together."""
    person_a: str
    person_b: str
    meeting_count: int
    total_hours: float
    subjects: list[str]  # recent meeting subjects


@dataclass
class CollaborationReport:
    """Full collaboration analysis."""
    top_pairs: list[CollaboratorPair]
    total_people: int
    total_meetings_analyzed: int
    most_connected: str  # person who meets with most unique people
    most_connected_count: int  # how many unique people they meet
    solo_meetings: int  # meetings with only 1 attendee
    avg_attendees: float


def analyze_collaboration(
    recordings_dir: Path,
    top_n: int = 15,
) -> CollaborationReport | None:
    """Analyze collaboration patterns across recordings.

    Args:
        recordings_dir: Base recordings directory.
        top_n: Number of top pairs to return.

    Returns:
        CollaborationReport or None if no data.
    """
    if not recordings_dir.exists():
        return None

    pair_counts: Counter[tuple[str, str]] = Counter()
    pair_hours: defaultdict[tuple[str, str], float] = defaultdict(float)
    pair_subjects: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    person_connections: defaultdict[str, set[str]] = defaultdict(set)
    all_people: set[str] = set()
    total_meetings = 0
    solo_count = 0
    total_attendees = 0

    for rec_dir in sorted(recordings_dir.iterdir(), reverse=True):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue

        meta = _load_meta(rec_dir)
        attendees = meta.get("meeting_attendees", [])
        organizer = meta.get("meeting_organizer", "")

        # Build full participant list
        participants = list(attendees)
        if organizer and organizer not in participants:
            participants.append(organizer)

        if not participants:
            continue

        total_meetings += 1
        total_attendees += len(participants)
        all_people.update(participants)

        if len(participants) <= 1:
            solo_count += 1
            continue

        dur_hours = meta.get("duration_seconds", 0) / 3600
        subject = meta.get("meeting_subject", rec_dir.name[:20])

        # Count all pairs
        for a, b in combinations(sorted(participants), 2):
            pair = (a, b)
            pair_counts[pair] += 1
            pair_hours[pair] += dur_hours
            person_connections[a].add(b)
            person_connections[b].add(a)
            if len(pair_subjects[pair]) < 5:
                pair_subjects[pair].append(subject)

    if total_meetings == 0:
        return None

    # Build top pairs
    top_pairs = []
    for (a, b), count in pair_counts.most_common(top_n):
        top_pairs.append(CollaboratorPair(
            person_a=a,
            person_b=b,
            meeting_count=count,
            total_hours=round(pair_hours[(a, b)], 1),
            subjects=pair_subjects[(a, b)],
        ))

    # Most connected person
    most_connected = ""
    most_connected_count = 0
    for person, connections in person_connections.items():
        if len(connections) > most_connected_count:
            most_connected = person
            most_connected_count = len(connections)

    return CollaborationReport(
        top_pairs=top_pairs,
        total_people=len(all_people),
        total_meetings_analyzed=total_meetings,
        most_connected=most_connected,
        most_connected_count=most_connected_count,
        solo_meetings=solo_count,
        avg_attendees=round(total_attendees / total_meetings, 1) if total_meetings > 0 else 0,
    )


def format_collaboration(report: CollaborationReport | None) -> str:
    """Format collaboration analysis as readable text."""
    if report is None:
        return "No meeting data available for collaboration analysis."

    lines = ["COLLABORATION ANALYSIS", "=" * 50, ""]

    # Overview
    lines.append(f"  People:           {report.total_people}")
    lines.append(f"  Meetings:         {report.total_meetings_analyzed}")
    lines.append(f"  Avg attendees:    {report.avg_attendees}")
    if report.solo_meetings > 0:
        lines.append(f"  Solo meetings:    {report.solo_meetings}")
    if report.most_connected:
        lines.append(f"  Most connected:   {report.most_connected} "
                     f"({report.most_connected_count} unique contacts)")
    lines.append("")

    # Top pairs
    if report.top_pairs:
        lines.append("FREQUENT PAIRS")
        lines.append("-" * 40)
        for pair in report.top_pairs:
            hours_str = f"{pair.total_hours:.1f}h" if pair.total_hours > 0 else ""
            lines.append(
                f"  {pair.person_a} \u2194 {pair.person_b}  "
                f"({pair.meeting_count} meeting{'s' if pair.meeting_count != 1 else ''}"
                f"{', ' + hours_str if hours_str else ''})"
            )
            if pair.subjects:
                lines.append(f"    Topics: {', '.join(pair.subjects[:3])}")
        lines.append("")

    return "\n".join(lines)


def _load_meta(rec_dir: Path) -> dict:
    """Load metadata from recording."""
    try:
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}
