"""Meeting benchmarks — compare recordings against averages.

Computes per-meeting-type benchmarks (avg duration, speakers, actions,
decisions, quality) and reports how a specific recording compares.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Benchmark:
    """Aggregate benchmark for a meeting type."""
    meeting_type: str
    count: int
    avg_duration_min: float
    avg_speakers: float
    avg_actions: float
    avg_quality: float | None


@dataclass
class BenchmarkComparison:
    """How a recording compares to its type's benchmark."""
    recording_name: str
    subject: str
    meeting_type: str
    benchmark: Benchmark
    duration_min: float
    duration_delta: str  # "+15 min longer" or "-10 min shorter"
    speakers: int
    speaker_delta: str
    action_count: int
    action_delta: str
    quality: int | None
    quality_delta: str
    overall_verdict: str  # "above average", "typical", "below average"


def compute_benchmarks(
    recordings_dir: Path,
    weeks: int = 12,
) -> dict[str, Benchmark]:
    """Compute per-meeting-type benchmarks.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.

    Returns:
        Dict of meeting_type → Benchmark.
    """
    if not recordings_dir.exists():
        return {}

    cutoff = date.today() - timedelta(weeks=weeks)
    type_data: dict[str, dict] = defaultdict(lambda: {
        "durations": [], "speakers": [], "actions": [], "qualities": [],
    })

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        try:
            rec_date = date.fromisoformat(rec_dir.name[:10])
        except ValueError:
            continue
        if rec_date < cutoff:
            continue

        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        dur = meta.get("duration_seconds", 0)
        if dur < 60:
            continue

        # Classify
        try:
            from meeting_recorder.storage.meeting_classifier import classify_recording
            cls = classify_recording(rec_dir, meta=meta)
            mtype = cls.meeting_type if cls and cls.confidence > 0.2 else "general"
        except Exception:
            mtype = "general"

        data = type_data[mtype]
        data["durations"].append(dur / 60.0)
        data["speakers"].append(meta.get("speaker_count", 0))

        # Count action items
        ai_path = rec_dir / "action_items.json"
        if ai_path.exists():
            try:
                with open(ai_path, "r", encoding="utf-8") as f:
                    items = json.load(f)
                data["actions"].append(len(items))
            except Exception:
                data["actions"].append(0)
        else:
            data["actions"].append(0)

        # Quality
        qs = meta.get("quality_scores", {})
        if qs and qs.get("overall_score") is not None:
            data["qualities"].append(qs["overall_score"])

    benchmarks: dict[str, Benchmark] = {}
    for mtype, data in type_data.items():
        if not data["durations"]:
            continue
        n = len(data["durations"])
        avg_dur = sum(data["durations"]) / n
        avg_spk = sum(data["speakers"]) / n if data["speakers"] else 0
        avg_act = sum(data["actions"]) / n if data["actions"] else 0
        avg_q = (sum(data["qualities"]) / len(data["qualities"])
                 if data["qualities"] else None)

        benchmarks[mtype] = Benchmark(
            meeting_type=mtype,
            count=n,
            avg_duration_min=round(avg_dur, 1),
            avg_speakers=round(avg_spk, 1),
            avg_actions=round(avg_act, 1),
            avg_quality=round(avg_q, 1) if avg_q is not None else None,
        )

    return benchmarks


def compare_to_benchmark(
    rec_path: Path,
    benchmarks: dict[str, Benchmark],
    meta: dict | None = None,
) -> BenchmarkComparison | None:
    """Compare a recording against its type's benchmark.

    Args:
        rec_path: Recording directory.
        benchmarks: Pre-computed benchmarks.
        meta: Pre-loaded metadata.

    Returns:
        BenchmarkComparison or None.
    """
    if not benchmarks:
        return None

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

    # Classify this recording
    try:
        from meeting_recorder.storage.meeting_classifier import classify_recording
        cls = classify_recording(rec_path, meta=meta)
        mtype = cls.meeting_type if cls and cls.confidence > 0.2 else "general"
    except Exception:
        mtype = "general"

    bm = benchmarks.get(mtype)
    if bm is None:
        bm = benchmarks.get("general")
    if bm is None:
        return None

    dur_min = dur / 60.0
    speakers = meta.get("speaker_count", 0)

    # Count action items
    action_count = 0
    ai_path = rec_path / "action_items.json"
    if ai_path.exists():
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            action_count = len(items)
        except Exception:
            pass

    quality = None
    qs = meta.get("quality_scores", {})
    if qs and qs.get("overall_score") is not None:
        quality = qs["overall_score"]

    subject = meta.get("meeting_subject", "")
    if not subject and len(rec_path.name) > 20:
        subject = rec_path.name[20:].replace("_", " ").strip()

    # Compute deltas
    dur_diff = dur_min - bm.avg_duration_min
    dur_delta = (f"+{dur_diff:.0f} min longer" if dur_diff > 2
                 else f"{dur_diff:.0f} min shorter" if dur_diff < -2
                 else "typical")

    spk_diff = speakers - bm.avg_speakers
    speaker_delta = (f"+{spk_diff:.0f} more" if spk_diff > 1
                     else f"{spk_diff:.0f} fewer" if spk_diff < -1
                     else "typical")

    act_diff = action_count - bm.avg_actions
    action_delta = (f"+{act_diff:.0f} more" if act_diff > 1
                    else f"{act_diff:.0f} fewer" if act_diff < -1
                    else "typical")

    quality_delta = ""
    if quality is not None and bm.avg_quality is not None:
        q_diff = quality - bm.avg_quality
        quality_delta = (f"+{q_diff:.0f}" if q_diff > 5
                         else f"{q_diff:.0f}" if q_diff < -5
                         else "typical")

    # Overall verdict
    score = 0
    if dur_diff < -5:
        score += 1  # shorter is better
    elif dur_diff > 10:
        score -= 1
    if action_count > bm.avg_actions:
        score += 1
    if quality is not None and bm.avg_quality is not None and quality > bm.avg_quality:
        score += 1

    verdict = "above average" if score >= 2 else "below average" if score <= -1 else "typical"

    return BenchmarkComparison(
        recording_name=rec_path.name,
        subject=subject or "Meeting",
        meeting_type=mtype,
        benchmark=bm,
        duration_min=round(dur_min, 1),
        duration_delta=dur_delta,
        speakers=speakers,
        speaker_delta=speaker_delta,
        action_count=action_count,
        action_delta=action_delta,
        quality=quality,
        quality_delta=quality_delta,
        overall_verdict=verdict,
    )


def format_benchmark_comparison(comp: BenchmarkComparison | None) -> str:
    """Format benchmark comparison as readable text."""
    if comp is None:
        return "No benchmark data available."

    type_labels = {
        "standup": "Standup", "planning": "Planning", "review": "Review",
        "one_on_one": "1-on-1", "all_hands": "All-Hands", "brainstorm": "Brainstorm",
        "retrospective": "Retro", "interview": "Interview", "training": "Training",
        "incident": "Incident", "general": "General",
    }

    label = type_labels.get(comp.meeting_type, comp.meeting_type.title())
    bm = comp.benchmark

    lines = [
        "BENCHMARK COMPARISON",
        "-" * 40,
        f"  Type: {label} (n={bm.count})",
        f"  Verdict: {comp.overall_verdict.title()}",
        "",
        f"  Duration:  {comp.duration_min:.0f} min  vs avg {bm.avg_duration_min:.0f} min  ({comp.duration_delta})",
        f"  Speakers:  {comp.speakers}  vs avg {bm.avg_speakers:.1f}  ({comp.speaker_delta})",
        f"  Actions:   {comp.action_count}  vs avg {bm.avg_actions:.1f}  ({comp.action_delta})",
    ]

    if comp.quality is not None and bm.avg_quality is not None:
        lines.append(
            f"  Quality:   {comp.quality}  vs avg {bm.avg_quality:.0f}  ({comp.quality_delta})"
        )

    return "\n".join(lines)
