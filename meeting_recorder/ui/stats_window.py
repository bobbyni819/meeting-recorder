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

        # Attendee frequency
        attendee_counts: dict[str, int] = defaultdict(int)
        attendee_time: dict[str, float] = defaultdict(float)
        for m in all_meta:
            dur = m.get("duration_seconds", 0)
            for att in m.get("meeting_attendees", []):
                name = att.strip()
                if name:
                    attendee_counts[name] += 1
                    attendee_time[name] += dur

        # Time-of-day distribution (hour buckets)
        hour_counts: dict[int, int] = defaultdict(int)
        for rec_dir in sorted(recordings_dir.iterdir()):
            if not rec_dir.is_dir():
                continue
            name = rec_dir.name
            if len(name) >= 16:
                try:
                    hour = int(name[11:13])
                    hour_counts[hour] += 1
                except ValueError:
                    pass

        # Day-of-week distribution
        day_counts: dict[int, int] = defaultdict(int)  # 0=Mon ... 6=Sun
        for rec_dir in sorted(recordings_dir.iterdir()):
            if not rec_dir.is_dir():
                continue
            name = rec_dir.name
            if len(name) >= 10:
                try:
                    date = datetime.strptime(name[:10], "%Y-%m-%d")
                    day_counts[date.weekday()] += 1
                except ValueError:
                    pass

        # Tag frequency
        tag_counts: dict[str, int] = defaultdict(int)
        for m in all_meta:
            for tag in m.get("tags", []):
                tag_counts[tag] += 1

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
            "attendee_counts": dict(attendee_counts),
            "attendee_time": dict(attendee_time),
            "hour_counts": dict(hour_counts),
            "day_counts": dict(day_counts),
            "tag_counts": dict(tag_counts),
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

        # Frequent collaborators
        attendee_counts = stats.get("attendee_counts", {})
        attendee_time_map = stats.get("attendee_time", {})
        if attendee_counts:
            self._section(content, "Frequent Collaborators")
            top_attendees = sorted(attendee_counts.items(), key=lambda x: -x[1])[:10]
            max_count = top_attendees[0][1] if top_attendees else 1

            for name, count in top_attendees:
                row = tk.Frame(content, bg=BG_COLOR)
                row.pack(fill=tk.X, padx=20, pady=1)

                bar_width = int(200 * count / max_count)
                time_h = attendee_time_map.get(name, 0) / 3600

                tk.Label(row, text=name, font=("Segoe UI", 9), fg=TEXT_COLOR,
                         bg=BG_COLOR, width=16, anchor=tk.W).pack(side=tk.LEFT)

                bar_canvas = tk.Canvas(row, width=200, height=14,
                                       bg=BG_PANEL, highlightthickness=0)
                bar_canvas.pack(side=tk.LEFT, padx=4)
                bar_canvas.create_rectangle(0, 0, bar_width, 14, fill="#9b59b6", outline="")

                tk.Label(row, text=f"{count}x \u2022 {time_h:.1f}h",
                         font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR,
                         width=10, anchor=tk.E).pack(side=tk.LEFT)

        # Time of day distribution
        hour_counts = stats.get("hour_counts", {})
        if hour_counts:
            self._section(content, "Meeting Time of Day")
            tod_frame = tk.Frame(content, bg=BG_COLOR)
            tod_frame.pack(fill=tk.X, padx=20, pady=4)

            max_hour_count = max(hour_counts.values()) if hour_counts else 1
            bar_total_width = 400
            bar_h = 60

            tod_canvas = tk.Canvas(tod_frame, width=bar_total_width, height=bar_h + 20,
                                   bg=BG_PANEL, highlightthickness=0)
            tod_canvas.pack()

            for hour in range(24):
                count = hour_counts.get(hour, 0)
                x = int(hour * bar_total_width / 24)
                w = max(int(bar_total_width / 24) - 2, 4)
                h = int(bar_h * count / max_hour_count) if max_hour_count > 0 else 0

                color = "#2ecc71" if 9 <= hour <= 17 else "#0f3460"
                tod_canvas.create_rectangle(x, bar_h - h, x + w, bar_h,
                                           fill=color, outline="")

                if hour % 3 == 0:
                    label = f"{hour:02d}"
                    tod_canvas.create_text(x + w // 2, bar_h + 10,
                                          text=label, fill=TEXT_DIM,
                                          font=("Segoe UI", 7))

        # Day of week distribution
        day_counts = stats.get("day_counts", {})
        if day_counts:
            self._section(content, "Meetings by Day of Week")
            dow_frame = tk.Frame(content, bg=BG_COLOR)
            dow_frame.pack(fill=tk.X, padx=20, pady=4)

            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            max_day_count = max(day_counts.values()) if day_counts else 1

            for day_idx in range(7):
                row = tk.Frame(dow_frame, bg=BG_COLOR)
                row.pack(fill=tk.X, pady=1)

                count = day_counts.get(day_idx, 0)
                bar_width = int(200 * count / max_day_count) if max_day_count > 0 else 0

                tk.Label(row, text=day_names[day_idx], font=("Segoe UI", 9),
                         fg=TEXT_COLOR, bg=BG_COLOR, width=5, anchor=tk.W).pack(side=tk.LEFT)

                bar_canvas = tk.Canvas(row, width=200, height=14,
                                       bg=BG_PANEL, highlightthickness=0)
                bar_canvas.pack(side=tk.LEFT, padx=4)
                color = "#e74c3c" if day_idx >= 5 else "#3498db"
                bar_canvas.create_rectangle(0, 0, bar_width, 14, fill=color, outline="")

                tk.Label(row, text=str(count) if count else "",
                         font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR,
                         width=4, anchor=tk.E).pack(side=tk.LEFT)

        # Top tags
        tag_counts = stats.get("tag_counts", {})
        if tag_counts:
            self._section(content, "Common Tags")
            tag_frame = tk.Frame(content, bg=BG_COLOR)
            tag_frame.pack(fill=tk.X, padx=20, pady=4)

            top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:15]
            for tag, count in top_tags:
                pill = tk.Label(
                    tag_frame, text=f" {tag} ({count}) ",
                    font=("Segoe UI", 8), fg=TEXT_COLOR,
                    bg=BG_CONTROLS, padx=4, pady=2,
                )
                pill.pack(side=tk.LEFT, padx=2, pady=2)

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

        # Focus time
        try:
            from meeting_recorder.storage.focus_time import analyze_focus_time
            focus_weeks = analyze_focus_time(self._recordings_dir, weeks=4)
            if focus_weeks and any(w.meeting_count > 0 for w in focus_weeks):
                self._section(content, "Focus Time (Last 4 Weeks)")

                for week in focus_weeks:
                    if week.meeting_count == 0:
                        continue
                    row = tk.Frame(content, bg=BG_COLOR)
                    row.pack(fill=tk.X, padx=20, pady=1)

                    focus_width = int(200 * week.focus_pct / 100)
                    mtg_width = 200 - focus_width

                    tk.Label(row, text=f"w/{week.week_start[5:]}",
                             font=("Segoe UI", 8),
                             fg=TEXT_DIM, bg=BG_COLOR, width=10,
                             anchor=tk.W).pack(side=tk.LEFT)

                    bar_canvas = tk.Canvas(row, width=200, height=14,
                                           bg=BG_PANEL, highlightthickness=0)
                    bar_canvas.pack(side=tk.LEFT, padx=4)
                    # Focus = green, meetings = amber
                    bar_canvas.create_rectangle(
                        0, 0, focus_width, 14, fill="#2ecc71", outline="")
                    bar_canvas.create_rectangle(
                        focus_width, 0, 200, 14, fill=AMBER, outline="")

                    tk.Label(row, text=f"{week.focus_pct:.0f}% focus",
                             font=("Segoe UI", 8),
                             fg=TEXT_DIM, bg=BG_COLOR, width=10,
                             anchor=tk.E).pack(side=tk.LEFT)
        except Exception:
            pass

        # Meeting cost
        try:
            from meeting_recorder.storage.meeting_cost import aggregate_costs
            cost_data = aggregate_costs(self._recordings_dir)
            if cost_data.get("meeting_count", 0) > 0:
                self._section(content, "Estimated Meeting Cost")
                cost_frame = tk.Frame(content, bg=BG_COLOR)
                cost_frame.pack(fill=tk.X, padx=20, pady=4)

                total = cost_data["total_cost"]
                avg = cost_data["avg_cost"]
                count = cost_data["meeting_count"]

                tk.Label(cost_frame,
                         text=f"Total: ${total:,.0f}  |  "
                              f"Average: ${avg:,.0f}/meeting  |  "
                              f"{count} meetings",
                         font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
                         anchor=tk.W).pack(fill=tk.X)
        except Exception:
            pass

        # Meeting Heatmap
        try:
            from meeting_recorder.storage.heatmap import build_heatmap, format_heatmap
            heatmap = build_heatmap(self._recordings_dir, weeks=8)
            if heatmap is not None:
                self._section(content, "Meeting Heatmap (Last 8 Weeks)")
                hm_frame = tk.Frame(content, bg=BG_COLOR)
                hm_frame.pack(fill=tk.X, padx=16, pady=4)

                hm_text = format_heatmap(heatmap)
                # Show just the summary line in stats (full heatmap is too wide)
                tk.Label(hm_frame,
                         text=f"Peak: {heatmap.peak_day} {heatmap.peak_slot} "
                              f"({heatmap.peak_minutes:.0f} min)  |  "
                              f"{heatmap.total_meetings} meetings across "
                              f"{heatmap.weeks_covered} weeks",
                         font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
                         anchor=tk.W).pack(fill=tk.X)

                # Compact grid: day vs slot with color indicators
                grid_frame = tk.Frame(hm_frame, bg=BG_COLOR)
                grid_frame.pack(fill=tk.X, pady=(4, 0))
                slot_labels = [s[1] for s in [("", "7-9"), ("", "9-11"), ("", "11-1"),
                                                ("", "1-3"), ("", "3-5"), ("", "5-7")]]
                # Header
                tk.Label(grid_frame, text="     ", font=("Consolas", 8),
                         fg=TEXT_DIM, bg=BG_COLOR, width=5).grid(row=0, column=0)
                for s, label in enumerate(slot_labels):
                    tk.Label(grid_frame, text=label, font=("Consolas", 8),
                             fg=TEXT_DIM, bg=BG_COLOR, width=5).grid(row=0, column=s+1)
                # Grid cells
                from meeting_recorder.storage.heatmap import DAY_NAMES as _DAYS
                max_val = max(max(row) for row in heatmap.grid) if heatmap.grid else 1
                if max_val == 0:
                    max_val = 1
                for d in range(5):
                    tk.Label(grid_frame, text=_DAYS[d], font=("Consolas", 8),
                             fg=TEXT_DIM, bg=BG_COLOR, width=5).grid(row=d+1, column=0)
                    for s in range(6):
                        val = heatmap.grid[d][s]
                        intensity = val / max_val if max_val > 0 else 0
                        if heatmap.counts[d][s] == 0:
                            color = BG_COLOR
                            fg = TEXT_DIM
                            label = "\u00b7"
                        else:
                            # Green -> amber -> red gradient
                            if intensity < 0.33:
                                color = "#1a3a1a"
                                fg = GREEN
                            elif intensity < 0.66:
                                color = "#3a3a1a"
                                fg = AMBER
                            else:
                                color = "#3a1a1a"
                                fg = RED_DOT
                            label = str(heatmap.counts[d][s])
                        tk.Label(grid_frame, text=label, font=("Consolas", 8),
                                 fg=fg, bg=color, width=5, relief=tk.FLAT,
                                 padx=1, pady=1).grid(row=d+1, column=s+1, padx=1, pady=1)
        except Exception:
            pass

        # Recording Streaks
        try:
            from meeting_recorder.storage.streaks import analyze_streaks, format_streaks
            streak_info = analyze_streaks(self._recordings_dir)
            if streak_info is not None:
                self._section(content, "Recording Streaks")
                streak_frame = tk.Frame(content, bg=BG_COLOR)
                streak_frame.pack(fill=tk.X, padx=20, pady=4)

                flame = "\U0001f525 " if streak_info.current_streak >= 5 else ""
                tk.Label(streak_frame,
                         text=f"{flame}Current: {streak_info.current_streak} days  |  "
                              f"Longest: {streak_info.longest_streak} days  |  "
                              f"Consistency: {streak_info.consistency_pct:.0f}%",
                         font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
                         anchor=tk.W).pack(fill=tk.X)
                tk.Label(streak_frame,
                         text=f"Meeting-free days (4 wk): {streak_info.meeting_free_days}  |  "
                              f"Busiest: {streak_info.busiest_weekday}  |  "
                              f"Quietest: {streak_info.quietest_weekday}",
                         font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR,
                         anchor=tk.W).pack(fill=tk.X, pady=(2, 0))
        except Exception:
            pass

        # Collaboration
        try:
            from meeting_recorder.storage.collaboration import analyze_collaboration
            collab = analyze_collaboration(self._recordings_dir, top_n=5)
            if collab is not None and collab.top_pairs:
                self._section(content, "Top Collaborators")
                collab_frame = tk.Frame(content, bg=BG_COLOR)
                collab_frame.pack(fill=tk.X, padx=20, pady=4)

                for pair in collab.top_pairs[:5]:
                    tk.Label(collab_frame,
                             text=f"{pair.person_a} \u2194 {pair.person_b}  "
                                  f"({pair.meeting_count} meetings, {pair.total_hours:.1f}h)",
                             font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
                             anchor=tk.W).pack(fill=tk.X, pady=1)

                if collab.most_connected:
                    tk.Label(collab_frame,
                             text=f"Most connected: {collab.most_connected} "
                                  f"({collab.most_connected_count} contacts)",
                             font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR,
                             anchor=tk.W).pack(fill=tk.X, pady=(4, 0))
        except Exception:
            pass

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
