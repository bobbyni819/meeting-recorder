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
                    for seg in (tdata.get("segments") or []):
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
        for tag in (m.get("tags") or []):
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
    parser.add_argument(
        "--costs", action="store_true",
        help="Show meeting cost budget tracker",
    )
    parser.add_argument(
        "--effectiveness", action="store_true",
        help="Show meeting effectiveness analysis",
    )
    parser.add_argument(
        "--optimizer", action="store_true",
        help="Show meeting duration optimizer suggestions",
    )
    parser.add_argument(
        "--sentiment", action="store_true",
        help="Show sentiment analysis across recent recordings",
    )
    parser.add_argument(
        "--search", type=str, default="",
        help="Search all transcripts for a keyword",
    )
    parser.add_argument(
        "--balance", action="store_true",
        help="Show talk-time balance analysis",
    )
    parser.add_argument(
        "--alerts", action="store_true",
        help="Show keyword alert matches",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Show comprehensive report (stats + weekly + health + streaks + costs)",
    )
    args = parser.parse_args(argv)

    config = Config.load()

    if args.search:
        from meeting_recorder.storage.transcript_search import search_transcripts, format_search_results
        hits = search_transcripts(config.output_dir, args.search, max_results=20)
        print(format_search_results(hits, args.search))
        return 0

    if args.alerts:
        from meeting_recorder.storage.keyword_alerts import scan_all_recordings, format_keyword_alerts
        report = scan_all_recordings(config.output_dir)
        print(format_keyword_alerts(report))
        return 0

    if args.balance:
        from meeting_recorder.storage.talk_balance import analyze_talk_balance_report, format_talk_balance
        report = analyze_talk_balance_report(config.output_dir, weeks=8)
        print(format_talk_balance(report))
        return 0

    if getattr(args, "all", False):
        sections = []
        # General stats
        stats = compute_stats(config.output_dir)
        if stats and stats.get("total_recordings", 0) > 0:
            sections.append(format_stats(stats))

        # Weekly report
        try:
            from meeting_recorder.storage.weekly_report import generate_weekly_report, format_weekly_report
            report = generate_weekly_report(config.output_dir, week_offset=args.week_offset)
            if report:
                sections.append(format_weekly_report(report))
        except Exception:
            pass

        # Health summary
        try:
            from meeting_recorder.storage.health_summary import analyze_health, format_health
            hs = analyze_health(config.output_dir)
            if hs.total_recordings > 0:
                sections.append(format_health(hs))
        except Exception:
            pass

        # Streaks
        try:
            from meeting_recorder.storage.streaks import analyze_streaks, format_streaks
            info = analyze_streaks(config.output_dir)
            if info:
                sections.append(format_streaks(info))
        except Exception:
            pass

        # Costs
        try:
            from meeting_recorder.storage.cost_budget import analyze_cost_budget, format_cost_budget
            cb = analyze_cost_budget(config.output_dir, weeks=8)
            if cb:
                sections.append(format_cost_budget(cb))
        except Exception:
            pass

        if sections:
            print("\n\n".join(sections))
        else:
            print("No recordings found.")
        return 0

    if args.streaks:
        from meeting_recorder.storage.streaks import analyze_streaks, format_streaks
        info = analyze_streaks(config.output_dir)
        print(format_streaks(info))
        return 0

    if args.effectiveness:
        from meeting_recorder.storage.effectiveness import analyze_effectiveness, format_effectiveness
        report = analyze_effectiveness(config.output_dir, weeks=8)
        print(format_effectiveness(report))
        return 0

    if args.costs:
        from meeting_recorder.storage.cost_budget import analyze_cost_budget, format_cost_budget
        cb = analyze_cost_budget(config.output_dir, weeks=8)
        if cb is None:
            print("No meeting cost data available.")
        else:
            print(format_cost_budget(cb))
        return 0

    if args.optimizer:
        from meeting_recorder.storage.duration_optimizer import analyze_duration_optimization, format_duration_optimizer
        report = analyze_duration_optimization(config.output_dir, weeks=12)
        print(format_duration_optimizer(report))
        return 0

    if args.sentiment:
        from meeting_recorder.storage.sentiment import analyze_recording_sentiment, format_sentiment
        if not config.output_dir.exists():
            print("No recordings found.")
            return 0
        all_results = []
        for rec_dir in sorted(config.output_dir.iterdir(), reverse=True):
            if not rec_dir.is_dir():
                continue
            s = analyze_recording_sentiment(rec_dir)
            if s:
                all_results.append((rec_dir.name, s))
            if len(all_results) >= 20:
                break
        if not all_results:
            print("No transcripts found for sentiment analysis.")
        else:
            lines = ["SENTIMENT ACROSS RECORDINGS", "=" * 55, ""]
            for name, s in all_results:
                from meeting_recorder.storage.sentiment import sentiment_emoji
                emoji = sentiment_emoji(s.score)
                lines.append(
                    f"  {name[:35]:<35}  {s.label:>8} {emoji}  ({s.score:+.2f})"
                )
            print("\n".join(lines))
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
