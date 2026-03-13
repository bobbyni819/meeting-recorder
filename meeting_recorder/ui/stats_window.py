"""Cross-recording statistics window."""

from __future__ import annotations

import json
import logging
import tkinter as tk
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CONTROLS,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, RED_DOT,
)

logger = logging.getLogger(__name__)


class StatsWindow:
    """Window showing aggregate statistics across all recordings."""

    def __init__(self, recordings_dir: Path):
        self._recordings_dir = recordings_dir
        self._window: Optional[tk.Toplevel] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the stats window."""
        if self._window is not None:
            self._window.lift()
            return

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Meeting Statistics")
        self._window.geometry("500x600")
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        # Gather data
        stats = self._compute_stats()

        # Build UI
        self._build_ui(stats)

    def close(self) -> None:
        if self._window:
            self._window.destroy()
            self._window = None

    def _compute_stats(self) -> dict:
        """Compute aggregate statistics from all recordings."""
        recordings_dir = self._recordings_dir
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

                # App usage
                app = meta.get("app_name", "Unknown")
                if app:
                    app_counts[app] += 1

                # Quality
                qs = meta.get("quality_scores", {})
                if qs and qs.get("overall_score") is not None:
                    quality_scores.append(qs["overall_score"])

                # Weekly duration tracking
                name = rec_dir.name
                if len(name) >= 10:
                    try:
                        date = datetime.strptime(name[:10], "%Y-%m-%d")
                        week_start = date - timedelta(days=date.weekday())
                        week_key = week_start.strftime("%Y-%m-%d")
                        weekly_duration[week_key] += meta.get("duration_seconds", 0)
                    except ValueError:
                        pass

                # Speaker times from transcript.json
                transcript_path = rec_dir / "transcript.json"
                if transcript_path.exists():
                    try:
                        with open(transcript_path, "r", encoding="utf-8") as f:
                            tdata = json.load(f)
                        # Use speaker_map to resolve names
                        smap = meta.get("speaker_map", {})
                        for seg in tdata.get("segments", []):
                            spk = seg.get("speaker", "Unknown")
                            # Resolve to real name if mapped
                            spk = smap.get(spk, spk)
                            dur = max(0, seg.get("end", 0) - seg.get("start", 0))
                            speaker_times[spk] += dur
                    except Exception:
                        pass

            except Exception:
                continue

        # Compute summaries
        total_recordings = len(all_meta)
        total_duration = sum(m.get("duration_seconds", 0) for m in all_meta)
        avg_duration = total_duration / total_recordings if total_recordings > 0 else 0
        completed = sum(1 for m in all_meta if m.get("status") == "completed")
        errors = sum(1 for m in all_meta if m.get("status") == "error")
        avg_quality = round(sum(quality_scores) / len(quality_scores)) if quality_scores else None

        # This week
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
        }

    def _build_ui(self, stats: dict) -> None:
        """Build the stats UI."""
        window = self._window

        # Header
        header = tk.Frame(window, bg=BG_HEADER, height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="Meeting Statistics",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=8, side=tk.LEFT)

        # Scrollable content
        canvas = tk.Canvas(window, bg=BG_COLOR, highlightthickness=0)
        scrollbar = tk.Scrollbar(window, orient=tk.VERTICAL, command=canvas.yview)
        content = tk.Frame(canvas, bg=BG_COLOR)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        if not stats:
            tk.Label(
                content, text="No recordings found.",
                font=("Segoe UI", 10), fg=TEXT_DIM, bg=BG_COLOR,
            ).pack(pady=20)
            return

        # Overview cards
        self._section(content, "Overview")
        overview = tk.Frame(content, bg=BG_COLOR)
        overview.pack(fill=tk.X, padx=16, pady=4)

        total = stats["total_recordings"]
        total_h = stats["total_duration"] / 3600
        avg_m = stats["avg_duration"] / 60
        week_h = stats["this_week_time"] / 3600

        metrics = [
            ("Total Recordings", str(total)),
            ("Total Time", f"{total_h:.1f}h"),
            ("Avg Duration", f"{avg_m:.0f}m"),
            ("This Week", f"{week_h:.1f}h"),
        ]
        if stats.get("avg_quality") is not None:
            metrics.append(("Avg Quality", f"{stats['avg_quality']}/100"))

        for i, (label, value) in enumerate(metrics):
            card = tk.Frame(overview, bg=BG_PANEL, padx=12, pady=8)
            card.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky=tk.NSEW)
            tk.Label(card, text=value, font=("Segoe UI", 14, "bold"),
                     fg=TEXT_BRIGHT, bg=BG_PANEL).pack()
            tk.Label(card, text=label, font=("Segoe UI", 8),
                     fg=TEXT_DIM, bg=BG_PANEL).pack()
        for c in range(3):
            overview.columnconfigure(c, weight=1)

        # Status breakdown
        completed = stats.get("completed", 0)
        errors = stats.get("errors", 0)
        if completed or errors:
            self._section(content, "Status")
            status_frame = tk.Frame(content, bg=BG_COLOR)
            status_frame.pack(fill=tk.X, padx=20, pady=4)
            if completed:
                tk.Label(status_frame, text=f"\u2705  {completed} completed",
                         font=("Segoe UI", 9), fg=GREEN, bg=BG_COLOR).pack(anchor=tk.W)
            if errors:
                tk.Label(status_frame, text=f"\u26a0  {errors} with errors",
                         font=("Segoe UI", 9), fg=AMBER, bg=BG_COLOR).pack(anchor=tk.W)

        # Top speakers
        speaker_times = stats.get("speaker_times", {})
        if speaker_times:
            self._section(content, "Top Speakers (All Time)")
            top_speakers = sorted(speaker_times.items(), key=lambda x: -x[1])[:10]
            max_time = top_speakers[0][1] if top_speakers else 1

            for spk, secs in top_speakers:
                row = tk.Frame(content, bg=BG_COLOR)
                row.pack(fill=tk.X, padx=20, pady=1)

                mins = int(secs // 60)
                bar_width = int(200 * secs / max_time)

                tk.Label(row, text=spk, font=("Segoe UI", 9), fg=TEXT_COLOR,
                         bg=BG_COLOR, width=16, anchor=tk.W).pack(side=tk.LEFT)

                bar_canvas = tk.Canvas(row, width=200, height=14,
                                       bg=BG_PANEL, highlightthickness=0)
                bar_canvas.pack(side=tk.LEFT, padx=4)
                bar_canvas.create_rectangle(0, 0, bar_width, 14, fill="#0f3460", outline="")

                tk.Label(row, text=f"{mins}m", font=("Segoe UI", 8),
                         fg=TEXT_DIM, bg=BG_COLOR, width=6, anchor=tk.E).pack(side=tk.LEFT)

        # App usage
        app_counts = stats.get("app_counts", {})
        if app_counts:
            self._section(content, "Recording Platforms")
            for app, count in sorted(app_counts.items(), key=lambda x: -x[1]):
                row = tk.Frame(content, bg=BG_COLOR)
                row.pack(fill=tk.X, padx=20, pady=1)
                tk.Label(row, text=f"{app}: {count} recording{'s' if count != 1 else ''}",
                         font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
                         anchor=tk.W).pack(fill=tk.X)

        # Weekly trend
        weekly = stats.get("weekly_duration", {})
        if len(weekly) > 1:
            self._section(content, "Weekly Meeting Time")
            recent_weeks = sorted(weekly.items())[-8:]  # Last 8 weeks
            max_weekly = max(v for _, v in recent_weeks) if recent_weeks else 1

            for week_start, secs in recent_weeks:
                row = tk.Frame(content, bg=BG_COLOR)
                row.pack(fill=tk.X, padx=20, pady=1)

                hours = secs / 3600
                bar_width = int(200 * secs / max_weekly) if max_weekly > 0 else 0

                tk.Label(row, text=f"w/{week_start[5:]}", font=("Segoe UI", 8),
                         fg=TEXT_DIM, bg=BG_COLOR, width=10, anchor=tk.W).pack(side=tk.LEFT)

                bar_canvas = tk.Canvas(row, width=200, height=12,
                                       bg=BG_PANEL, highlightthickness=0)
                bar_canvas.pack(side=tk.LEFT, padx=4)
                bar_canvas.create_rectangle(0, 0, bar_width, 12, fill="#2ecc71", outline="")

                tk.Label(row, text=f"{hours:.1f}h", font=("Segoe UI", 8),
                         fg=TEXT_DIM, bg=BG_COLOR, width=6, anchor=tk.E).pack(side=tk.LEFT)

        # Bottom padding
        tk.Frame(content, bg=BG_COLOR, height=20).pack()

    @staticmethod
    def _section(parent: tk.Frame, title: str) -> None:
        """Add a section header."""
        tk.Label(
            parent, text=title, font=("Segoe UI", 10, "bold"),
            fg=TEXT_BRIGHT, bg=BG_COLOR, anchor=tk.W,
        ).pack(fill=tk.X, padx=16, pady=(12, 4))
        tk.Frame(parent, bg=BG_HEADER, height=1).pack(fill=tk.X, padx=16)
