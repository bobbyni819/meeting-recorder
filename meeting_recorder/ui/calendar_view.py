"""Recording calendar view.

Displays a monthly calendar showing which days have recordings.
Clicking a day filters the history or opens the recordings for that day.
"""

from __future__ import annotations

import calendar
import json
import logging
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CARD, BG_CARD_HOVER,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, BLUE_ACCENT,
)

logger = logging.getLogger(__name__)


def scan_recording_dates(base_dir: Path) -> dict[str, list[dict]]:
    """Scan recording directories and return a map of date -> recording info.

    Returns:
        Dict mapping "YYYY-MM-DD" to list of recording info dicts.
    """
    date_map: dict[str, list[dict]] = {}
    if not base_dir.exists():
        return date_map

    for d in base_dir.iterdir():
        if not d.is_dir() or len(d.name) < 10:
            continue
        # Parse date from folder name: "2026-03-06_14-30-00_Subject_App"
        date_str = d.name[:10]
        try:
            # Validate date
            date.fromisoformat(date_str)
        except ValueError:
            continue

        info: dict = {"path": str(d), "name": d.name}
        try:
            meta_path = d / "metadata.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                info["subject"] = meta.get("meeting_subject", "")
                info["duration"] = meta.get("duration_seconds", 0)
                info["status"] = meta.get("status", "")
                info["app"] = meta.get("app_name", "")
        except Exception:
            pass

        date_map.setdefault(date_str, []).append(info)

    return date_map


class CalendarWindow:
    """Window showing a monthly calendar of recordings."""

    def __init__(self, base_dir: Path, on_date_click: Optional[Callable] = None):
        self._base_dir = base_dir
        self._on_date_click = on_date_click
        self._window: Optional[tk.Toplevel] = None
        self._current_year: int = date.today().year
        self._current_month: int = date.today().month
        self._date_map: dict[str, list[dict]] = {}

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the calendar window."""
        if self._window is not None:
            try:
                self._window.lift()
                return
            except tk.TclError:
                self._window = None

        self._date_map = scan_recording_dates(self._base_dir)

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Recording Calendar")
        self._window.geometry("420x420")
        self._window.configure(bg=BG_COLOR)
        self._window.resizable(False, False)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui()

    def close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    def _build_ui(self) -> None:
        """Build the calendar UI."""
        # Navigation header
        nav = tk.Frame(self._window, bg=BG_HEADER, height=44)
        nav.pack(fill=tk.X)
        nav.pack_propagate(False)

        prev_btn = tk.Label(
            nav, text="\u25c0", font=("Segoe UI", 12),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=12,
        )
        prev_btn.pack(side=tk.LEFT, pady=8)
        prev_btn.bind("<Button-1>", lambda e: self._change_month(-1))
        prev_btn.bind("<Enter>", lambda e: prev_btn.configure(fg=TEXT_BRIGHT))
        prev_btn.bind("<Leave>", lambda e: prev_btn.configure(fg=TEXT_DIM))

        self._month_label = tk.Label(
            nav, text="", font=("Segoe UI", 11, "bold"),
            fg=TEXT_BRIGHT, bg=BG_HEADER,
        )
        self._month_label.pack(side=tk.LEFT, expand=True, pady=8)

        next_btn = tk.Label(
            nav, text="\u25b6", font=("Segoe UI", 12),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=12,
        )
        next_btn.pack(side=tk.RIGHT, pady=8)
        next_btn.bind("<Button-1>", lambda e: self._change_month(1))
        next_btn.bind("<Enter>", lambda e: next_btn.configure(fg=TEXT_BRIGHT))
        next_btn.bind("<Leave>", lambda e: next_btn.configure(fg=TEXT_DIM))

        # Today button
        today_btn = tk.Label(
            nav, text="Today", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
        )
        today_btn.pack(side=tk.RIGHT, padx=4, pady=8)
        today_btn.bind("<Button-1>", lambda e: self._go_today())
        today_btn.bind("<Enter>", lambda e: today_btn.configure(fg=TEXT_BRIGHT))
        today_btn.bind("<Leave>", lambda e: today_btn.configure(fg=TEXT_DIM))

        # Calendar grid frame
        self._grid_frame = tk.Frame(self._window, bg=BG_COLOR)
        self._grid_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # Stats at bottom
        self._stats_label = tk.Label(
            self._window, text="", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_COLOR,
        )
        self._stats_label.pack(pady=(0, 8))

        self._draw_month()

    def _change_month(self, delta: int) -> None:
        """Navigate to a different month."""
        m = self._current_month + delta
        y = self._current_year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self._current_month = m
        self._current_year = y
        self._draw_month()

    def _go_today(self) -> None:
        """Jump to current month."""
        today = date.today()
        self._current_year = today.year
        self._current_month = today.month
        self._draw_month()

    def _draw_month(self) -> None:
        """Draw the calendar grid for the current month."""
        # Clear grid
        for w in self._grid_frame.winfo_children():
            w.destroy()

        year = self._current_year
        month = self._current_month
        self._month_label.configure(
            text=f"{calendar.month_name[month]} {year}"
        )

        # Day headers
        for col, day_name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            tk.Label(
                self._grid_frame, text=day_name, font=("Segoe UI", 8, "bold"),
                fg=TEXT_DIM, bg=BG_COLOR, width=5,
            ).grid(row=0, column=col, padx=1, pady=(0, 4))

        # Calendar days
        cal = calendar.Calendar(firstweekday=0)  # Monday first
        today = date.today()
        month_recordings = 0
        month_duration = 0.0

        for row_idx, week in enumerate(cal.monthdayscalendar(year, month)):
            for col_idx, day in enumerate(week):
                if day == 0:
                    # Empty cell for days outside this month
                    tk.Label(
                        self._grid_frame, text="", bg=BG_COLOR, width=5, height=2,
                    ).grid(row=row_idx + 1, column=col_idx, padx=1, pady=1)
                    continue

                d = date(year, month, day)
                date_key = d.isoformat()
                recordings = self._date_map.get(date_key, [])
                count = len(recordings)
                is_today = d == today

                # Background color based on state
                if count > 0:
                    bg = "#1a3a2e" if count < 3 else "#1a4a2e"  # green tint
                elif is_today:
                    bg = "#1a2a4e"  # blue tint
                else:
                    bg = BG_PANEL

                # Day cell
                cell = tk.Frame(
                    self._grid_frame, bg=bg, width=50, height=44,
                    cursor="hand2" if count > 0 else "",
                )
                cell.grid(row=row_idx + 1, column=col_idx, padx=1, pady=1, sticky="nsew")
                cell.grid_propagate(False)

                # Day number
                day_color = TEXT_BRIGHT if is_today else (TEXT_COLOR if count > 0 else TEXT_DIM)
                day_font = ("Segoe UI", 9, "bold") if is_today else ("Segoe UI", 9)
                tk.Label(
                    cell, text=str(day), font=day_font,
                    fg=day_color, bg=bg, anchor=tk.NW,
                ).pack(anchor=tk.NW, padx=3, pady=(2, 0))

                # Recording count indicator
                if count > 0:
                    day_duration = sum(r.get("duration", 0) for r in recordings)
                    month_recordings += count
                    month_duration += day_duration

                    indicator = f"{count} rec" if count > 1 else "1 rec"
                    tk.Label(
                        cell, text=indicator, font=("Segoe UI", 7),
                        fg=GREEN, bg=bg,
                    ).pack(anchor=tk.SW, padx=3, pady=(0, 2))

                    # Click handler
                    if self._on_date_click:
                        cell.bind("<Button-1>", lambda e, dt=date_key: self._on_date_click(dt))
                        for child in cell.winfo_children():
                            child.bind("<Button-1>", lambda e, dt=date_key: self._on_date_click(dt))

                    # Hover
                    hover_bg = "#244a3e" if count >= 3 else "#1e4a36"

                    def _enter(e, c=cell, hb=hover_bg):
                        c.configure(bg=hb)
                        for ch in c.winfo_children():
                            ch.configure(bg=hb)

                    def _leave(e, c=cell, ob=bg):
                        c.configure(bg=ob)
                        for ch in c.winfo_children():
                            ch.configure(bg=ob)

                    cell.bind("<Enter>", _enter)
                    cell.bind("<Leave>", _leave)
                    for child in cell.winfo_children():
                        child.bind("<Enter>", _enter)
                        child.bind("<Leave>", _leave)

        # Configure column weights
        for col in range(7):
            self._grid_frame.columnconfigure(col, weight=1)

        # Month stats
        if month_recordings > 0:
            hours = month_duration / 3600
            if hours >= 1:
                dur_str = f"{hours:.1f}h"
            else:
                dur_str = f"{month_duration / 60:.0f}m"
            self._stats_label.configure(
                text=f"{month_recordings} recording{'s' if month_recordings != 1 else ''}  \u2022  {dur_str} total"
            )
        else:
            self._stats_label.configure(text="No recordings this month")
