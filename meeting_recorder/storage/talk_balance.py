"""Meeting talk-time balance analyzer.

Analyzes speaker participation across recordings to identify meetings
dominated by one person and flag imbalanced discussions. Helps ensure
meetings are collaborative, not one-way broadcasts.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TalkBalance:
    """Talk-time balance analysis for a single recording."""
    recording_name: str
    subject: str
    dominant_speaker: str
    dominant_pct: float  # 0-100
    speaker_count: int
    balance_score: float  # 0-100, where 100 = perfectly equal
    is_imbalanced: bool  # True if one speaker > threshold
    speakers: list[tuple[str, float]]  # (name, pct) sorted by talk time


@dataclass
class TalkBalanceReport:
    """Aggregate talk-balance report across recordings."""
    recordings_analyzed: int
    avg_balance_score: float
    most_balanced: list[TalkBalance]  # top 3 most balanced
    most_imbalanced: list[TalkBalance]  # top 3 most imbalanced
    frequent_dominators: list[tuple[str, int]]  # (speaker, count of meetings dominated)
    meetings_with_one_speaker_over_70: int


def analyze_talk_balance(
    rec_path: Path,
    meta: dict | None = None,
    imbalance_threshold: float = 70.0,
) -> TalkBalance | None:
    """Analyze talk-time balance for a single recording.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata.
        imbalance_threshold: Percentage above which a speaker is "dominant".

    Returns:
        TalkBalance or None if insufficient data.
    """
    transcript_path = rec_path / "transcript.json"
    if not transcript_path.exists():
        return None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
    except Exception:
        return None

    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        else:
            meta = {}

    speaker_map = meta.get("speaker_map", {})
    speaker_times: dict[str, float] = {}

    for seg in (tdata.get("segments") or []):
        spk = seg.get("speaker", "Unknown")
        spk = speaker_map.get(spk, spk)
        dur = max(0, seg.get("end", 0) - seg.get("start", 0))
        speaker_times[spk] = speaker_times.get(spk, 0) + dur

    if len(speaker_times) < 2:
        return None

    total = sum(speaker_times.values())
    if total < 30:
        return None

    # Calculate percentages
    speakers_pct = sorted(
        [(name, (secs / total) * 100) for name, secs in speaker_times.items()],
        key=lambda x: -x[1],
    )

    dominant_name, dominant_pct = speakers_pct[0]

    # Balance score: based on Shannon entropy, normalized to 0-100
    # Perfect balance among N speakers has entropy log2(N)
    n = len(speakers_pct)
    max_entropy = math.log2(n) if n > 1 else 1.0
    entropy = 0.0
    for _, pct in speakers_pct:
        p = pct / 100.0
        if p > 0:
            entropy -= p * math.log2(p)
    balance_score = (entropy / max_entropy) * 100 if max_entropy > 0 else 0

    subject = meta.get("meeting_subject", "")
    if not subject and len(rec_path.name) > 20:
        subject = rec_path.name[20:].replace("_", " ").strip()

    return TalkBalance(
        recording_name=rec_path.name,
        subject=subject or "Meeting",
        dominant_speaker=dominant_name,
        dominant_pct=round(dominant_pct, 1),
        speaker_count=n,
        balance_score=round(balance_score, 1),
        is_imbalanced=dominant_pct >= imbalance_threshold,
        speakers=[(name, round(pct, 1)) for name, pct in speakers_pct],
    )


def analyze_talk_balance_report(
    recordings_dir: Path,
    weeks: int = 8,
    imbalance_threshold: float = 70.0,
) -> TalkBalanceReport | None:
    """Analyze talk-time balance across all recent recordings.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.
        imbalance_threshold: Percentage threshold for "dominant".

    Returns:
        TalkBalanceReport or None if insufficient data.
    """
    if not recordings_dir.exists():
        return None

    cutoff = date.today() - timedelta(weeks=weeks)
    results: list[TalkBalance] = []
    dominator_counts: dict[str, int] = {}

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        try:
            rec_date = date.fromisoformat(rec_dir.name[:10])
        except ValueError:
            continue
        if rec_date < cutoff:
            continue

        tb = analyze_talk_balance(rec_dir, imbalance_threshold=imbalance_threshold)
        if tb is not None:
            results.append(tb)
            if tb.is_imbalanced:
                dominator_counts[tb.dominant_speaker] = dominator_counts.get(
                    tb.dominant_speaker, 0) + 1

    if not results:
        return None

    avg_balance = sum(r.balance_score for r in results) / len(results)
    imbalanced_count = sum(1 for r in results if r.is_imbalanced)

    most_balanced = sorted(results, key=lambda r: -r.balance_score)[:3]
    most_imbalanced = sorted(results, key=lambda r: r.balance_score)[:3]

    frequent_dominators = sorted(
        dominator_counts.items(), key=lambda x: -x[1]
    )[:5]

    return TalkBalanceReport(
        recordings_analyzed=len(results),
        avg_balance_score=round(avg_balance, 1),
        most_balanced=most_balanced,
        most_imbalanced=most_imbalanced,
        frequent_dominators=frequent_dominators,
        meetings_with_one_speaker_over_70=imbalanced_count,
    )


def format_talk_balance(report: TalkBalanceReport | None) -> str:
    """Format talk-balance report as readable text."""
    if report is None:
        return "Not enough data for talk-balance analysis."

    lines = [
        "TALK-TIME BALANCE",
        "=" * 55,
        "",
        f"  Recordings analyzed:  {report.recordings_analyzed}",
        f"  Avg balance score:    {report.avg_balance_score:.0f}/100",
        f"  Imbalanced meetings:  {report.meetings_with_one_speaker_over_70}"
        f" ({report.meetings_with_one_speaker_over_70 / max(report.recordings_analyzed, 1) * 100:.0f}%)",
        "",
    ]

    if report.most_imbalanced:
        lines.append("  Most Imbalanced")
        lines.append("  " + "-" * 50)
        for tb in report.most_imbalanced:
            lines.append(
                f"    {tb.subject[:25]:<25}  "
                f"{tb.dominant_speaker[:15]:<15}  "
                f"{tb.dominant_pct:.0f}%  "
                f"bal:{tb.balance_score:.0f}"
            )
        lines.append("")

    if report.most_balanced:
        lines.append("  Most Balanced")
        lines.append("  " + "-" * 50)
        for tb in report.most_balanced:
            lines.append(
                f"    {tb.subject[:25]:<25}  "
                f"bal:{tb.balance_score:.0f}  "
                f"{tb.speaker_count} speakers"
            )
        lines.append("")

    if report.frequent_dominators:
        lines.append("  Frequent Dominant Speakers")
        lines.append("  " + "-" * 50)
        for name, count in report.frequent_dominators:
            lines.append(f"    {name[:25]:<25}  dominated {count} meeting(s)")
        lines.append("")

    return "\n".join(lines)
