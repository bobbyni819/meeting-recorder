"""Recurring meeting analysis.

Groups recordings by subject/attendee pattern, detects recurring meetings,
and calculates trends (duration, attendee consistency, topic evolution).
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MeetingInstance:
    """A single occurrence of a recurring meeting."""
    path: Path
    date: datetime
    duration: float  # seconds
    attendees: list[str]
    subject: str
    speaker_count: int
    quality: Optional[int]
    tags: list[str]


@dataclass
class RecurringSeries:
    """A series of recurring meetings with trend analysis."""
    subject: str
    instances: list[MeetingInstance]

    @property
    def count(self) -> int:
        return len(self.instances)

    @property
    def avg_duration(self) -> float:
        if not self.instances:
            return 0
        return sum(i.duration for i in self.instances) / len(self.instances)

    @property
    def duration_trend(self) -> float:
        """Duration trend: positive means meetings getting longer."""
        if len(self.instances) < 2:
            return 0.0
        durations = [i.duration for i in self.instances]
        first_half = sum(durations[:len(durations)//2]) / max(len(durations)//2, 1)
        second_half = sum(durations[len(durations)//2:]) / max(len(durations) - len(durations)//2, 1)
        if first_half <= 0:
            return 0.0
        return (second_half - first_half) / first_half * 100

    @property
    def core_attendees(self) -> list[str]:
        """Attendees present in >= 60% of meetings."""
        if not self.instances:
            return []
        counts: Counter[str] = Counter()
        for inst in self.instances:
            for att in inst.attendees:
                counts[att.lower()] += 1
        threshold = len(self.instances) * 0.6
        return sorted(
            name for name, cnt in counts.items() if cnt >= threshold
        )

    @property
    def all_attendees(self) -> list[str]:
        """All unique attendees across instances."""
        seen: set[str] = set()
        result: list[str] = []
        for inst in self.instances:
            for att in inst.attendees:
                low = att.lower()
                if low not in seen:
                    seen.add(low)
                    result.append(att)
        return result

    @property
    def avg_attendee_count(self) -> float:
        if not self.instances:
            return 0
        attended = [len(i.attendees) for i in self.instances if i.attendees]
        return sum(attended) / max(len(attended), 1)

    @property
    def date_range(self) -> tuple[datetime, datetime]:
        """First and last meeting dates."""
        dates = [i.date for i in self.instances]
        return min(dates), max(dates)

    @property
    def avg_interval_days(self) -> float:
        """Average days between meetings."""
        if len(self.instances) < 2:
            return 0
        dates = sorted(i.date for i in self.instances)
        intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        return sum(intervals) / len(intervals)

    @property
    def frequency_label(self) -> str:
        """Human-readable meeting frequency."""
        avg = self.avg_interval_days
        if avg <= 0:
            return "one-time"
        if avg <= 1.5:
            return "daily"
        if avg <= 3:
            return "every other day"
        if avg <= 8:
            return "weekly"
        if avg <= 16:
            return "biweekly"
        if avg <= 35:
            return "monthly"
        return "occasional"

    def format_summary(self) -> str:
        """Format the series as a readable summary."""
        lines: list[str] = []

        lines.append(f"RECURRING MEETING: {self.subject}")
        lines.append("=" * 50)
        lines.append("")

        # Overview
        first, last = self.date_range
        lines.append(f"Occurrences: {self.count}")
        lines.append(f"Frequency: {self.frequency_label} (avg {self.avg_interval_days:.0f} days)")
        lines.append(f"Date range: {first.strftime('%Y-%m-%d')} to {last.strftime('%Y-%m-%d')}")
        lines.append("")

        # Duration
        avg_min = self.avg_duration / 60
        trend = self.duration_trend
        trend_str = f" ({trend:+.0f}%)" if abs(trend) > 5 else " (stable)"
        lines.append(f"Avg duration: {avg_min:.0f} min{trend_str}")
        lines.append("")

        # Attendees
        core = self.core_attendees
        if core:
            lines.append(f"Core attendees ({len(core)}): {', '.join(core)}")
        all_att = self.all_attendees
        if len(all_att) > len(core):
            occasional = [a for a in all_att if a.lower() not in set(core)]
            lines.append(f"Occasional: {', '.join(occasional[:10])}")
        lines.append(f"Avg attendees: {self.avg_attendee_count:.1f}")
        lines.append("")

        # Instance list
        lines.append("History:")
        for inst in self.instances:
            dur_min = inst.duration / 60
            att_count = len(inst.attendees)
            lines.append(
                f"  {inst.date.strftime('%Y-%m-%d')}  "
                f"{dur_min:.0f}min  "
                f"{att_count} attendees"
            )

        return "\n".join(lines)


def find_recurring_meetings(
    recordings_dir: Path,
    min_occurrences: int = 2,
) -> list[RecurringSeries]:
    """Find recurring meetings by grouping recordings with similar subjects.

    Args:
        recordings_dir: Base recordings directory.
        min_occurrences: Minimum number of occurrences to count as recurring.

    Returns:
        List of RecurringSeries sorted by count descending.
    """
    if not recordings_dir.exists():
        return []

    # Load all recordings with subjects
    instances: list[MeetingInstance] = []
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

        subject = meta.get("meeting_subject", "").strip()
        if not subject:
            continue

        # Parse date from folder name (YYYY-MM-DD_HH-MM-SS_...)
        date = _parse_folder_date(rec_dir.name)
        if date is None:
            continue

        quality = meta.get("quality_scores", {}).get("overall_score")
        instances.append(MeetingInstance(
            path=rec_dir,
            date=date,
            duration=meta.get("duration_seconds", 0),
            attendees=meta.get("meeting_attendees") or [],
            subject=subject,
            speaker_count=meta.get("speaker_count", 0),
            quality=quality,
            tags=meta.get("tags") or [],
        ))

    if not instances:
        return []

    # Group by normalized subject
    groups: dict[str, list[MeetingInstance]] = defaultdict(list)
    for inst in instances:
        key = _normalize_subject(inst.subject)
        groups[key].append(inst)

    # Merge groups with high subject similarity
    merged = _merge_similar_groups(groups)

    # Build series, filter by min occurrences
    result: list[RecurringSeries] = []
    for key, group_instances in merged.items():
        if len(group_instances) < min_occurrences:
            continue
        # Sort chronologically
        group_instances.sort(key=lambda i: i.date)
        # Use most common subject as display name
        subjects = Counter(i.subject for i in group_instances)
        display_subject = subjects.most_common(1)[0][0]
        result.append(RecurringSeries(
            subject=display_subject,
            instances=group_instances,
        ))

    result.sort(key=lambda s: s.count, reverse=True)
    return result


def _parse_folder_date(name: str) -> Optional[datetime]:
    """Parse datetime from folder name like '2026-03-06_14-30-00_...'."""
    match = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", name)
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group(1)}_{match.group(2)}", "%Y-%m-%d_%H-%M-%S"
        )
    except ValueError:
        return None


def _normalize_subject(subject: str) -> str:
    """Normalize a meeting subject for grouping."""
    s = subject.lower().strip()
    # Remove common prefixes like "RE:", "FW:", date suffixes
    s = re.sub(r"^(re|fw|fwd)\s*:\s*", "", s)
    # Remove trailing dates like "(2026-03-01)" or "- March 1"
    s = re.sub(r"\s*[\(\[]\s*\d{4}[-/]\d{2}[-/]\d{2}\s*[\)\]]$", "", s)
    s = re.sub(r"\s*-\s*(january|february|march|april|may|june|july|august|"
               r"september|october|november|december)\s+\d{1,2}.*$", "", s)
    # Remove trailing numbers (like "Sprint Planning 23" → "Sprint Planning")
    s = re.sub(r"\s+#?\d+\s*$", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _merge_similar_groups(
    groups: dict[str, list[MeetingInstance]],
) -> dict[str, list[MeetingInstance]]:
    """Merge groups whose normalized subjects are very similar."""
    keys = list(groups.keys())
    merged: dict[str, list[MeetingInstance]] = {}
    used: set[str] = set()

    for i, k1 in enumerate(keys):
        if k1 in used:
            continue
        merged_instances = list(groups[k1])
        used.add(k1)

        for k2 in keys[i+1:]:
            if k2 in used:
                continue
            if _subjects_match(k1, k2):
                merged_instances.extend(groups[k2])
                used.add(k2)

        merged[k1] = merged_instances

    return merged


def _subjects_match(a: str, b: str) -> bool:
    """Check if two normalized subjects are similar enough to merge."""
    if not a or not b:
        return False
    if a == b:
        return True
    # One contains the other (only meaningful substrings)
    if len(a) >= 3 and a in b:
        return True
    if len(b) >= 3 and b in a:
        return True
    # Word overlap >= 50%
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    total = len(words_a | words_b)
    return overlap / total >= 0.5
