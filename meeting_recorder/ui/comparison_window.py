"""Visual comparison window for two recordings.

Shows side-by-side metrics with color-coded diffs for duration,
attendees, topics, quality, and speaker counts.
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from typing import Optional

from meeting_recorder.storage.comparison import RecordingComparison, compare_recordings
from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CONTROLS,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, RED_DOT, BLUE_ACCENT,
)

logger = logging.getLogger(__name__)


class ComparisonWindow:
    """Window showing a visual side-by-side comparison of two recordings."""

    def __init__(self, path_a: Path, path_b: Path):
        self._path_a = path_a
        self._path_b = path_b
        self._window: Optional[tk.Toplevel] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the comparison window."""
        if self._window is not None:
            self._window.lift()
            return

        try:
            self._comparison = compare_recordings(self._path_a, self._path_b)
        except Exception:
            logger.exception("Failed to compare recordings")
            return

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Recording Comparison")
        self._window.geometry("560x600")
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui(self._comparison)

    def close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    def _build_ui(self, comp: RecordingComparison) -> None:
        """Build the comparison UI."""
        window = self._window

        # Header
        header = tk.Frame(window, bg=BG_HEADER, height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="Recording Comparison",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=8, side=tk.LEFT)

        # Copy button
        copy_btn = tk.Label(
            header, text="  \U0001f4cb Copy  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        copy_btn.pack(side=tk.RIGHT, padx=8, pady=8)

        def _copy():
            text = comp.format_text()
            if self._window:
                self._window.clipboard_clear()
                self._window.clipboard_append(text)
                copy_btn.configure(text="\u2713 Copied!", fg=GREEN)
                self._window.after(1500, lambda: copy_btn.configure(
                    text="  \U0001f4cb Copy  ", fg=TEXT_DIM))

        copy_btn.bind("<Button-1>", lambda e: _copy())
        copy_btn.bind("<Enter>", lambda e: copy_btn.configure(fg=TEXT_BRIGHT))
        copy_btn.bind("<Leave>", lambda e: copy_btn.configure(fg=TEXT_DIM))

        # Scrollable content
        canvas = tk.Canvas(window, bg=BG_COLOR, highlightthickness=0)
        scrollbar = tk.Scrollbar(window, orient=tk.VERTICAL, command=canvas.yview)
        content = tk.Frame(canvas, bg=BG_COLOR)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Names header
        self._names_row(content, comp)

        # Duration comparison
        self._section(content, "Duration")
        self._duration_bars(content, comp)

        # Attendee diff
        if comp.attendees_both or comp.attendees_only_a or comp.attendees_only_b:
            self._section(content, "Attendees")
            self._attendee_diff(content, comp)

        # Speaker counts
        if comp.speakers_a > 0 or comp.speakers_b > 0:
            self._section(content, "Speakers")
            self._metric_row(
                content, "Detected speakers",
                str(comp.speakers_a), str(comp.speakers_b),
                comp.speakers_b - comp.speakers_a,
            )

        # Topic diff
        if comp.common_topics or comp.new_topics or comp.dropped_topics:
            self._section(content, "Topics")
            self._topic_diff(content, comp)

        # Tag diff
        if comp.tags_both or comp.tags_only_a or comp.tags_only_b:
            self._section(content, "Tags")
            self._tag_diff(content, comp)

        # Quality comparison
        if comp.quality_a is not None or comp.quality_b is not None:
            self._section(content, "Quality")
            qa = comp.quality_a if comp.quality_a is not None else 0
            qb = comp.quality_b if comp.quality_b is not None else 0
            self._quality_bars(content, qa, qb)

        # Bottom padding
        tk.Frame(content, bg=BG_COLOR, height=20).pack()

    def _names_row(self, parent: tk.Frame, comp: RecordingComparison) -> None:
        """Show recording names as column headers."""
        frame = tk.Frame(parent, bg=BG_PANEL)
        frame.pack(fill=tk.X, padx=16, pady=(12, 4))

        # A column
        a_frame = tk.Frame(frame, bg=BG_PANEL)
        a_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=8)
        tk.Label(
            a_frame, text="A", font=("Segoe UI", 10, "bold"),
            fg=BLUE_ACCENT, bg=BG_PANEL,
        ).pack(anchor=tk.W)
        subject_a = comp.subject_a or comp.name_a[20:].replace("_", " ") if len(comp.name_a) > 20 else comp.name_a
        tk.Label(
            a_frame, text=subject_a, font=("Segoe UI", 9),
            fg=TEXT_COLOR, bg=BG_PANEL, wraplength=200,
        ).pack(anchor=tk.W)
        tk.Label(
            a_frame, text=comp.date_a, font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_PANEL,
        ).pack(anchor=tk.W)

        # Separator
        tk.Frame(frame, bg=BG_CONTROLS, width=1).pack(
            side=tk.LEFT, fill=tk.Y, pady=4)

        # B column
        b_frame = tk.Frame(frame, bg=BG_PANEL)
        b_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=8)
        tk.Label(
            b_frame, text="B", font=("Segoe UI", 10, "bold"),
            fg=GREEN, bg=BG_PANEL,
        ).pack(anchor=tk.W)
        subject_b = comp.subject_b or comp.name_b[20:].replace("_", " ") if len(comp.name_b) > 20 else comp.name_b
        tk.Label(
            b_frame, text=subject_b, font=("Segoe UI", 9),
            fg=TEXT_COLOR, bg=BG_PANEL, wraplength=200,
        ).pack(anchor=tk.W)
        tk.Label(
            b_frame, text=comp.date_b, font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_PANEL,
        ).pack(anchor=tk.W)

    def _duration_bars(self, parent: tk.Frame, comp: RecordingComparison) -> None:
        """Show duration comparison with visual bars."""
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.pack(fill=tk.X, padx=20, pady=4)

        max_dur = max(comp.duration_a, comp.duration_b, 1)
        bar_max = 250

        # A bar
        row_a = tk.Frame(frame, bg=BG_COLOR)
        row_a.pack(fill=tk.X, pady=2)
        tk.Label(
            row_a, text="A", font=("Segoe UI", 9, "bold"),
            fg=BLUE_ACCENT, bg=BG_COLOR, width=3,
        ).pack(side=tk.LEFT)
        bar_a = tk.Canvas(row_a, width=bar_max, height=18, bg=BG_PANEL, highlightthickness=0)
        bar_a.pack(side=tk.LEFT, padx=4)
        w_a = int(bar_max * comp.duration_a / max_dur)
        bar_a.create_rectangle(0, 0, w_a, 18, fill="#0f3460", outline="")
        dur_a_str = _fmt_dur(comp.duration_a)
        tk.Label(
            row_a, text=dur_a_str, font=("Segoe UI", 9),
            fg=TEXT_COLOR, bg=BG_COLOR, width=10,
        ).pack(side=tk.LEFT)

        # B bar
        row_b = tk.Frame(frame, bg=BG_COLOR)
        row_b.pack(fill=tk.X, pady=2)
        tk.Label(
            row_b, text="B", font=("Segoe UI", 9, "bold"),
            fg=GREEN, bg=BG_COLOR, width=3,
        ).pack(side=tk.LEFT)
        bar_b = tk.Canvas(row_b, width=bar_max, height=18, bg=BG_PANEL, highlightthickness=0)
        bar_b.pack(side=tk.LEFT, padx=4)
        w_b = int(bar_max * comp.duration_b / max_dur)
        bar_b.create_rectangle(0, 0, w_b, 18, fill="#1a5c3a", outline="")
        dur_b_str = _fmt_dur(comp.duration_b)
        tk.Label(
            row_b, text=dur_b_str, font=("Segoe UI", 9),
            fg=TEXT_COLOR, bg=BG_COLOR, width=10,
        ).pack(side=tk.LEFT)

        # Change label
        if comp.duration_change:
            change_color = RED_DOT if comp.duration_change > 20 else (
                GREEN if comp.duration_change < -10 else TEXT_DIM
            )
            change_str = f"{comp.duration_change:+.0f}%"
            tk.Label(
                frame, text=change_str, font=("Segoe UI", 9, "bold"),
                fg=change_color, bg=BG_COLOR,
            ).pack(anchor=tk.W, padx=24, pady=(2, 0))

    def _attendee_diff(self, parent: tk.Frame, comp: RecordingComparison) -> None:
        """Show attendee changes with color coding."""
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.pack(fill=tk.X, padx=20, pady=4)

        for att in comp.attendees_both:
            tk.Label(
                frame, text=f"  =  {att}", font=("Segoe UI", 9),
                fg=TEXT_COLOR, bg=BG_COLOR, anchor=tk.W,
            ).pack(fill=tk.X)

        for att in comp.attendees_only_a:
            tk.Label(
                frame, text=f"  -  {att}  (not in B)", font=("Segoe UI", 9),
                fg=RED_DOT, bg=BG_COLOR, anchor=tk.W,
            ).pack(fill=tk.X)

        for att in comp.attendees_only_b:
            tk.Label(
                frame, text=f"  +  {att}  (new in B)", font=("Segoe UI", 9),
                fg=GREEN, bg=BG_COLOR, anchor=tk.W,
            ).pack(fill=tk.X)

        total_a = len(comp.attendees_both) + len(comp.attendees_only_a)
        total_b = len(comp.attendees_both) + len(comp.attendees_only_b)
        tk.Label(
            frame, text=f"  {total_a} \u2192 {total_b} attendees", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.W,
        ).pack(fill=tk.X, pady=(4, 0))

    def _topic_diff(self, parent: tk.Frame, comp: RecordingComparison) -> None:
        """Show topic changes."""
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.pack(fill=tk.X, padx=20, pady=4)

        if comp.common_topics:
            topics_str = ", ".join(comp.common_topics[:8])
            tk.Label(
                frame, text=f"  Shared: {topics_str}", font=("Segoe UI", 9),
                fg=TEXT_COLOR, bg=BG_COLOR, anchor=tk.W, wraplength=480,
            ).pack(fill=tk.X)

        if comp.dropped_topics:
            topics_str = ", ".join(comp.dropped_topics[:5])
            tk.Label(
                frame, text=f"  Dropped: {topics_str}", font=("Segoe UI", 9),
                fg=AMBER, bg=BG_COLOR, anchor=tk.W, wraplength=480,
            ).pack(fill=tk.X)

        if comp.new_topics:
            topics_str = ", ".join(comp.new_topics[:5])
            tk.Label(
                frame, text=f"  New: {topics_str}", font=("Segoe UI", 9),
                fg=GREEN, bg=BG_COLOR, anchor=tk.W, wraplength=480,
            ).pack(fill=tk.X)

    def _tag_diff(self, parent: tk.Frame, comp: RecordingComparison) -> None:
        """Show tag changes."""
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.pack(fill=tk.X, padx=20, pady=4)

        row = tk.Frame(frame, bg=BG_COLOR)
        row.pack(fill=tk.X)

        for tag in comp.tags_both:
            tk.Label(
                row, text=f" {tag} ", font=("Segoe UI", 8),
                fg=TEXT_COLOR, bg=BG_CONTROLS,
            ).pack(side=tk.LEFT, padx=2, pady=2)

        for tag in comp.tags_only_a:
            tk.Label(
                row, text=f" -{tag} ", font=("Segoe UI", 8),
                fg=RED_DOT, bg=BG_CONTROLS,
            ).pack(side=tk.LEFT, padx=2, pady=2)

        for tag in comp.tags_only_b:
            tk.Label(
                row, text=f" +{tag} ", font=("Segoe UI", 8),
                fg=GREEN, bg=BG_CONTROLS,
            ).pack(side=tk.LEFT, padx=2, pady=2)

    def _quality_bars(self, parent: tk.Frame, qa: int, qb: int) -> None:
        """Show quality score comparison."""
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.pack(fill=tk.X, padx=20, pady=4)

        for label, score, color in [("A", qa, BLUE_ACCENT), ("B", qb, GREEN)]:
            row = tk.Frame(frame, bg=BG_COLOR)
            row.pack(fill=tk.X, pady=2)
            tk.Label(
                row, text=label, font=("Segoe UI", 9, "bold"),
                fg=color, bg=BG_COLOR, width=3,
            ).pack(side=tk.LEFT)

            bar = tk.Canvas(row, width=200, height=16, bg=BG_PANEL, highlightthickness=0)
            bar.pack(side=tk.LEFT, padx=4)
            w = int(200 * score / 100) if score > 0 else 0
            bar_color = GREEN if score >= 75 else AMBER if score >= 50 else RED_DOT
            bar.create_rectangle(0, 0, w, 16, fill=bar_color, outline="")

            score_str = str(score) if score > 0 else "n/a"
            tk.Label(
                row, text=f"{score_str}/100", font=("Segoe UI", 9),
                fg=TEXT_COLOR, bg=BG_COLOR,
            ).pack(side=tk.LEFT, padx=4)

        if qa > 0 and qb > 0:
            diff = qb - qa
            if abs(diff) >= 5:
                color = GREEN if diff > 0 else RED_DOT
                tk.Label(
                    frame, text=f"  {diff:+d} points", font=("Segoe UI", 8),
                    fg=color, bg=BG_COLOR,
                ).pack(anchor=tk.W, padx=24, pady=(2, 0))

    def _metric_row(
        self, parent: tk.Frame, label: str,
        val_a: str, val_b: str, diff: int | float,
    ) -> None:
        """Show a single metric comparison row."""
        frame = tk.Frame(parent, bg=BG_COLOR)
        frame.pack(fill=tk.X, padx=20, pady=2)

        tk.Label(
            frame, text=label, font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_COLOR, width=20, anchor=tk.W,
        ).pack(side=tk.LEFT)

        tk.Label(
            frame, text=val_a, font=("Segoe UI", 9, "bold"),
            fg=BLUE_ACCENT, bg=BG_COLOR, width=5,
        ).pack(side=tk.LEFT)

        tk.Label(
            frame, text="\u2192", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_COLOR,
        ).pack(side=tk.LEFT, padx=4)

        tk.Label(
            frame, text=val_b, font=("Segoe UI", 9, "bold"),
            fg=GREEN, bg=BG_COLOR, width=5,
        ).pack(side=tk.LEFT)

        if diff != 0:
            color = GREEN if diff > 0 else RED_DOT
            diff_str = f"({diff:+d})" if isinstance(diff, int) else f"({diff:+.1f})"
            tk.Label(
                frame, text=diff_str, font=("Segoe UI", 8),
                fg=color, bg=BG_COLOR,
            ).pack(side=tk.LEFT, padx=4)

    @staticmethod
    def _section(parent: tk.Frame, title: str) -> None:
        """Add a section header."""
        tk.Label(
            parent, text=title, font=("Segoe UI", 10, "bold"),
            fg=TEXT_BRIGHT, bg=BG_COLOR, anchor=tk.W,
        ).pack(fill=tk.X, padx=16, pady=(12, 4))
        tk.Frame(parent, bg=BG_HEADER, height=1).pack(fill=tk.X, padx=16)


def _fmt_dur(seconds: float) -> str:
    """Format seconds as short duration string."""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"
