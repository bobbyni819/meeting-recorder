"""Keyword alert system for meeting transcripts.

Users define watched keywords; the system scans transcripts and flags
recordings containing them. Useful for tracking mentions of projects,
competitors, key topics, or risk signals.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default watched keywords (user can override via config)
_DEFAULT_KEYWORDS: list[str] = []

# Config file path
_KEYWORDS_FILE = Path.home() / ".meeting_recorder" / "watched_keywords.json"


@dataclass
class KeywordAlert:
    """A keyword match in a recording."""
    keyword: str
    recording_name: str
    subject: str
    date: str
    count: int  # occurrences in transcript
    first_context: str  # line where first match appears


@dataclass
class KeywordAlertReport:
    """Summary of keyword alerts across recordings."""
    total_alerts: int
    keywords_matched: dict[str, int]  # keyword → total hits across all recordings
    alerts: list[KeywordAlert]  # individual alerts
    recordings_scanned: int


def load_watched_keywords() -> list[str]:
    """Load watched keywords from config file.

    Returns:
        List of keyword strings.
    """
    if _KEYWORDS_FILE.exists():
        try:
            with open(_KEYWORDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(k).strip() for k in data if k]
            if isinstance(data, dict):
                return [str(k).strip() for k in data.get("keywords", []) if k]
        except Exception:
            pass
    return list(_DEFAULT_KEYWORDS)


def save_watched_keywords(keywords: list[str]) -> None:
    """Save watched keywords to config file."""
    _KEYWORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_KEYWORDS_FILE, "w", encoding="utf-8") as f:
        json.dump({"keywords": keywords}, f, indent=2)


def scan_recording(
    rec_path: Path,
    keywords: list[str],
) -> list[KeywordAlert]:
    """Scan a single recording for keyword matches.

    Args:
        rec_path: Recording directory.
        keywords: List of keywords to search for.

    Returns:
        List of KeywordAlert objects for matches found.
    """
    if not keywords:
        return []

    txt_path = rec_path / "transcript.txt"
    if not txt_path.exists():
        return []

    try:
        text = txt_path.read_text(encoding="utf-8")
    except Exception:
        return []

    if not text.strip():
        return []

    # Load metadata for subject
    subject = ""
    meta_path = rec_path / "metadata.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            subject = meta.get("meeting_subject", "")
        except Exception:
            pass

    if not subject and len(rec_path.name) > 20:
        subject = rec_path.name[20:].replace("_", " ").strip()

    date_str = rec_path.name[:10] if len(rec_path.name) >= 10 else ""

    alerts: list[KeywordAlert] = []
    text_lower = text.lower()
    lines = text.split("\n")

    for kw in keywords:
        kw_lower = kw.lower()
        # Count occurrences
        try:
            pattern = re.compile(r"\b" + re.escape(kw_lower) + r"\b", re.IGNORECASE)
            matches = pattern.findall(text)
            count = len(matches)
        except re.error:
            count = text_lower.count(kw_lower)

        if count == 0:
            continue

        # Find first context line
        first_context = ""
        for line in lines:
            if kw_lower in line.lower():
                first_context = line.strip()[:200]
                break

        alerts.append(KeywordAlert(
            keyword=kw,
            recording_name=rec_path.name,
            subject=subject or "Meeting",
            date=date_str,
            count=count,
            first_context=first_context,
        ))

    return alerts


def scan_all_recordings(
    recordings_dir: Path,
    keywords: list[str] | None = None,
    max_recordings: int = 50,
) -> KeywordAlertReport:
    """Scan all recordings for keyword matches.

    Args:
        recordings_dir: Base recordings directory.
        keywords: Keywords to search (loads from config if None).
        max_recordings: Maximum recordings to scan.

    Returns:
        KeywordAlertReport with all matches.
    """
    if keywords is None:
        keywords = load_watched_keywords()

    if not keywords or not recordings_dir.exists():
        return KeywordAlertReport(
            total_alerts=0,
            keywords_matched={},
            alerts=[],
            recordings_scanned=0,
        )

    all_alerts: list[KeywordAlert] = []
    keyword_totals: dict[str, int] = {}
    scanned = 0

    rec_dirs = sorted(
        (d for d in recordings_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )

    for rec_dir in rec_dirs[:max_recordings]:
        alerts = scan_recording(rec_dir, keywords)
        if alerts:
            all_alerts.extend(alerts)
            for a in alerts:
                keyword_totals[a.keyword] = keyword_totals.get(a.keyword, 0) + a.count
        scanned += 1

    return KeywordAlertReport(
        total_alerts=len(all_alerts),
        keywords_matched=dict(sorted(keyword_totals.items(), key=lambda x: -x[1])),
        alerts=all_alerts,
        recordings_scanned=scanned,
    )


def format_keyword_alerts(report: KeywordAlertReport) -> str:
    """Format keyword alert report as readable text."""
    if report.total_alerts == 0:
        if report.recordings_scanned == 0:
            return "No watched keywords configured. Add keywords to ~/.meeting_recorder/watched_keywords.json"
        return "No keyword matches found across recordings."

    lines = [
        "KEYWORD ALERTS",
        "=" * 55,
        f"  Scanned: {report.recordings_scanned} recordings",
        f"  Alerts:  {report.total_alerts}",
        "",
    ]

    # Keyword summary
    if report.keywords_matched:
        lines.append("  Keyword Frequency")
        lines.append("  " + "-" * 40)
        for kw, count in report.keywords_matched.items():
            lines.append(f"    {kw:<25}  {count} mention(s)")
        lines.append("")

    # Detailed alerts (latest first)
    lines.append("  Recent Alerts")
    lines.append("  " + "-" * 40)
    shown = 0
    for a in report.alerts[:15]:
        lines.append(f"    [{a.keyword}] {a.subject} ({a.date})")
        lines.append(f"      {a.count}x — \"{a.first_context[:80]}\"")
        shown += 1

    if len(report.alerts) > shown:
        lines.append(f"  ... and {len(report.alerts) - shown} more")
    lines.append("")

    return "\n".join(lines)
