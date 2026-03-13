"""Command-line interface for viewing meeting statistics."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from meeting_recorder.config import Config

logger = logging.getLogger(__name__)


def compute_stats(recordings_dir: Path) -> dict:
    """Compute aggregate statistics from all recordings.

    Mirrors StatsWindow._compute_stats() without Tkinter dependencies.
    """
    if not recordings_dir.exists():
        return {}

    all_meta: list[dict] = []
    speaker_times: dict[str, float] = defaultdict(float)
    weekly_duration: dict[str, float] = defaultdict(float)
    app_counts: dict[str, int] = defaultdict(int)
    quality_scores: list[int] = []

    for rec_dir in sorted(recordings_dir.iterdir(), reverse=True):
        if not rec_dir.is_dir():
            continue
        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            all_meta.append(meta)

            app = meta.get("app_name", "Unknown")
            if app:
                app_counts[app] += 1

            qs = meta.get("quality_scores", {})
            if qs and qs.get("overall_score") is not None:
                quality_scores.append(qs["overall_score"])

            name = rec_dir.name
            if len(name) >= 10:
                try:
                    date = datetime.strptime(name[:10], "%Y-%m-%d")
                    week_start = date - timedelta(days=date.weekday())
                    week_key = week_start.strftime("%Y-%m-%d")
                    weekly_duration[week_key] += meta.get("duration_seconds", 0)
                except ValueError:
                    pass

            transcript_path = rec_dir / "transcript.json"
            if transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    smap = meta.get("speaker_map", {})
                    for seg in tdata.get("segments", []):
                        spk = seg.get("speaker", "Unknown")
                        spk = smap.get(spk, spk)
                        dur = max(0, seg.get("end", 0) - seg.get("start", 0))
                        speaker_times[spk] += dur
                except Exception:
                    pass

        except Exception:
            continue

    tag_counts: dict[str, int] = defaultdict(int)
    for m in all_meta:
        for tag in m.get("tags", []):
            tag_counts[tag] += 1

    total_recordings = len(all_meta)
    total_duration = sum(m.get("duration_seconds", 0) for m in all_meta)
    avg_duration = total_duration / total_recordings if total_recordings > 0 else 0
    completed = sum(1 for m in all_meta if m.get("status") == "completed")
    errors = sum(1 for m in all_meta if m.get("status") == "error")
    avg_quality = round(sum(quality_scores) / len(quality_scores)) if quality_scores else None

    now = datetime.now()
    this_week_start = now - timedelta(days=now.weekday())
    this_week_key = this_week_start.strftime("%Y-%m-%d")
    this_week_time = weekly_duration.get(this_week_key, 0)

    return {
        "total_recordings": total_recordings,
        "total_duration": total_duration,
        "avg_duration": avg_duration,
        "completed": completed,
        "errors": errors,
        "avg_quality": avg_quality,
        "speaker_times": dict(speaker_times),
        "app_counts": dict(app_counts),
        "weekly_duration": dict(weekly_duration),
        "this_week_time": this_week_time,
        "tag_counts": dict(tag_counts),
    }


def _fmt_duration(seconds: float) -> str:
    """Format seconds as Xh YYm."""
    h, remainder = divmod(int(seconds), 3600)
    m, _ = divmod(remainder, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def format_stats(stats: dict) -> str:
    """Format stats dict as human-readable text for the terminal."""
    if not stats or stats.get("total_recordings", 0) == 0:
        return "No recordings found."

    lines = [
        "MEETING STATISTICS",
        "=" * 50,
        "",
        f"  Total recordings:   {stats['total_recordings']}",
        f"  Completed:          {stats['completed']}",
    ]
    if stats["errors"]:
        lines.append(f"  Errors:             {stats['errors']}")
    lines.extend([
        f"  Total time:         {_fmt_duration(stats['total_duration'])}",
        f"  Avg duration:       {_fmt_duration(stats['avg_duration'])}",
        f"  This week:          {_fmt_duration(stats['this_week_time'])}",
    ])
    if stats["avg_quality"] is not None:
        lines.append(f"  Avg quality:        {stats['avg_quality']}/100")
    lines.append("")

    # App usage
    if stats.get("app_counts"):
        lines.append("  Platform Usage")
        lines.append("  " + "-" * 30)
        for app, count in sorted(stats["app_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"    {app:<20} {count}")
        lines.append("")

    # Top speakers
    if stats.get("speaker_times"):
        lines.append("  Top Speakers")
        lines.append("  " + "-" * 30)
        top = sorted(stats["speaker_times"].items(), key=lambda x: -x[1])[:10]
        for spk, secs in top:
            lines.append(f"    {spk:<20} {_fmt_duration(secs)}")
        lines.append("")

    # Weekly trend (last 8 weeks)
    if stats.get("weekly_duration"):
        lines.append("  Weekly Trend")
        lines.append("  " + "-" * 30)
        weeks = sorted(stats["weekly_duration"].items())[-8:]
        for week, secs in weeks:
            bar_len = int(secs / 3600)  # 1 char per hour
            bar = "#" * min(bar_len, 30)
            lines.append(f"    w/{week[5:]}  {_fmt_duration(secs):>8}  {bar}")
        lines.append("")

    # Tags
    if stats.get("tag_counts"):
        lines.append("  Common Tags")
        lines.append("  " + "-" * 30)
        top_tags = sorted(stats["tag_counts"].items(), key=lambda x: -x[1])[:10]
        for tag, count in top_tags:
            lines.append(f"    {tag:<20} {count}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the stats CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="meeting-recorder-stats",
        description="Show aggregate meeting recording statistics",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw stats as JSON",
    )
    parser.add_argument(
        "--health", action="store_true",
        help="Show recording health summary",
    )
    parser.add_argument(
        "--weekly", action="store_true",
        help="Show weekly meeting report",
    )
    parser.add_argument(
        "--week-offset", type=int, default=0,
        help="Week offset (0=current, 1=last week, etc.)",
    )
    parser.add_argument(
        "--streaks", action="store_true",
        help="Show recording streaks and habit tracking",
    )
    args = parser.parse_args(argv)

    config = Config.load()

    if args.streaks:
        from meeting_recorder.storage.streaks import analyze_streaks, format_streaks
        info = analyze_streaks(config.output_dir)
        print(format_streaks(info))
        return 0

    if args.health:
        from meeting_recorder.storage.health_summary import analyze_health, format_health
        hs = analyze_health(config.output_dir)
        print(format_health(hs))
        return 0

    if args.weekly:
        from meeting_recorder.storage.weekly_report import generate_weekly_report, format_weekly_report
        report = generate_weekly_report(config.output_dir, week_offset=args.week_offset)
        if report is None:
            print("No recordings found for the selected week.")
            return 0
        if args.json:
            import json as json_mod
            from dataclasses import asdict
            print(json_mod.dumps(asdict(report), indent=2, default=str))
        else:
            print(format_weekly_report(report))
        return 0

    stats = compute_stats(config.output_dir)

    if args.json:
        import json as json_mod
        print(json_mod.dumps(stats, indent=2, default=str))
    else:
        print(format_stats(stats))

    return 0
