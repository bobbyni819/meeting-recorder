"""Meeting participation equity analysis.

Measures how balanced participation is across speakers —
identifies dominant speakers, silent attendees, and calculates
an equity score for meeting health assessment.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ParticipationScore:
    """Participation equity analysis for a recording."""
    equity_score: int  # 0-100: how balanced participation is (100 = perfectly equal)
    gini_coefficient: float  # 0-1: inequality measure (0 = equal, 1 = one person dominates)
    speaker_count: int
    dominant_speaker: str
    dominant_pct: float
    quietest_speaker: str
    quietest_pct: float
    speaker_shares: list[tuple[str, float]]  # (name, pct), sorted by pct desc
    label: str  # "balanced", "moderate", "dominated", "monologue"


def analyze_participation(
    rec_path: Path,
    meta: dict | None = None,
) -> ParticipationScore | None:
    """Analyze participation equity in a recording.

    Args:
        rec_path: Recording directory path.
        meta: Optional pre-loaded metadata.

    Returns:
        ParticipationScore or None if insufficient data.
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
        meta = _load_meta(rec_path)

    smap = meta.get("speaker_map", {})

    # Calculate per-speaker speaking time
    speaker_times: dict[str, float] = {}
    for seg in tdata.get("segments", []):
        spk = seg.get("speaker", "Unknown")
        spk = smap.get(spk, spk)
        duration = max(0.0, seg.get("end", 0) - seg.get("start", 0))
        speaker_times[spk] = speaker_times.get(spk, 0.0) + duration

    if len(speaker_times) < 2:
        return None

    total = sum(speaker_times.values())
    if total <= 0:
        return None

    # Speaker shares as percentages
    shares = sorted(
        [(spk, t / total * 100) for spk, t in speaker_times.items()],
        key=lambda x: -x[1],
    )

    # Gini coefficient
    n = len(shares)
    pcts = sorted(s[1] for s in shares)
    gini = _gini_coefficient(pcts)

    # Equity score (inverted Gini, scaled 0-100)
    equity = int(round((1 - gini) * 100))

    # Label
    if gini < 0.15:
        label = "balanced"
    elif gini < 0.35:
        label = "moderate"
    elif gini < 0.55:
        label = "dominated"
    else:
        label = "monologue"

    dominant = shares[0]
    quietest = shares[-1]

    return ParticipationScore(
        equity_score=equity,
        gini_coefficient=round(gini, 3),
        speaker_count=n,
        dominant_speaker=dominant[0],
        dominant_pct=round(dominant[1], 1),
        quietest_speaker=quietest[0],
        quietest_pct=round(quietest[1], 1),
        speaker_shares=[(s, round(p, 1)) for s, p in shares],
        label=label,
    )


def format_participation(ps: ParticipationScore) -> str:
    """Format participation analysis as readable text."""
    label_emoji = {
        "balanced": "\u2705",
        "moderate": "\U0001f7e1",
        "dominated": "\U0001f7e0",
        "monologue": "\U0001f534",
    }
    emoji = label_emoji.get(ps.label, "")

    lines = [
        "PARTICIPATION EQUITY",
        "-" * 40,
        f"  Score:     {ps.equity_score}/100  {ps.label.title()} {emoji}",
        f"  Speakers:  {ps.speaker_count}",
        "",
    ]

    # Speaker breakdown with bars
    max_pct = ps.speaker_shares[0][1] if ps.speaker_shares else 100
    for spk, pct in ps.speaker_shares:
        bar_len = int(20 * pct / max_pct) if max_pct > 0 else 0
        bar = "\u2588" * bar_len
        lines.append(f"  {spk:<16} {pct:5.1f}%  {bar}")

    lines.append("")

    if ps.label in ("dominated", "monologue"):
        lines.append(
            f"  \u26a0 {ps.dominant_speaker} dominated "
            f"({ps.dominant_pct:.0f}% of speaking time)"
        )

    return "\n".join(lines)


def _gini_coefficient(values: list[float]) -> float:
    """Compute Gini coefficient for a list of values.

    Args:
        values: Sorted list of non-negative values.

    Returns:
        Gini coefficient (0 = perfect equality, 1 = perfect inequality).
    """
    n = len(values)
    if n < 2:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0

    # Mean absolute difference formula
    numerator = sum(
        abs(values[i] - values[j])
        for i in range(n)
        for j in range(n)
    )
    return numerator / (2 * n * total)


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
