"""Tkinter search dialog for finding recordings."""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

from meeting_recorder.search.index import RecordingIndex, SearchResult
from meeting_recorder.utils import open_in_explorer

logger = logging.getLogger(__name__)


class SearchWindow:
    """Tkinter-based recording search dialog."""

    def __init__(self):
        self._window: Optional[tk.Tk] = None
        self._index = RecordingIndex()
        self._results: list[SearchResult] = []

    def show(self) -> None:
        """Show the search window."""
        if self._window is not None:
            try:
                self._window.lift()
                return
            except tk.TclError:
                self._window = None

        self._window = tk.Tk()
        self._window.title("Search Recordings")
        self._window.geometry("700x500")
        self._window.resizable(True, True)

        # Apply dark theme
        self._apply_dark_theme()

        # Search bar
        search_frame = ttk.Frame(self._window, padding=10)
        search_frame.pack(fill=tk.X)

        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        self._query_var = tk.StringVar()
        query_entry = ttk.Entry(search_frame, textvariable=self._query_var, width=40)
        query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        query_entry.bind("<Return>", lambda e: self._do_search())
        ttk.Button(search_frame, text="Search", command=self._do_search).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(search_frame, text="Browse All", command=self._browse_all).pack(side=tk.LEFT)

        # Filters
        filter_frame = ttk.Frame(self._window, padding=(10, 0, 10, 5))
        filter_frame.pack(fill=tk.X)

        ttk.Label(filter_frame, text="Speaker:").pack(side=tk.LEFT, padx=(0, 3))
        self._speaker_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self._speaker_var, width=15).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(filter_frame, text="Subject:").pack(side=tk.LEFT, padx=(0, 3))
        self._subject_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self._subject_var, width=15).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(filter_frame, text="Attendee:").pack(side=tk.LEFT, padx=(0, 3))
        self._attendee_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self._attendee_var, width=15).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(filter_frame, text="From:").pack(side=tk.LEFT, padx=(0, 3))
        self._date_from_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self._date_from_var, width=10).pack(side=tk.LEFT, padx=(0, 5))

        ttk.Label(filter_frame, text="To:").pack(side=tk.LEFT, padx=(0, 3))
        self._date_to_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self._date_to_var, width=10).pack(side=tk.LEFT)

        # Date shortcut buttons
        date_shortcut_frame = ttk.Frame(self._window, padding=(10, 0, 10, 5))
        date_shortcut_frame.pack(fill=tk.X)
        ttk.Label(date_shortcut_frame, text="Quick:").pack(side=tk.LEFT, padx=(0, 5))
        for label, days in [("Today", 0), ("Last 7 days", 7), ("Last 30 days", 30), ("All time", None)]:
            ttk.Button(
                date_shortcut_frame, text=label,
                command=lambda d=days: self._set_date_range(d),
            ).pack(side=tk.LEFT, padx=(0, 4))

        # Results treeview
        tree_frame = ttk.Frame(self._window, padding=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("date", "subject", "app", "speakers", "preview")
        self._tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")

        self._tree.heading("date", text="Date", command=lambda: self._sort_column("date"))
        self._tree.heading("subject", text="Subject")
        self._tree.heading("app", text="App")
        self._tree.heading("speakers", text="Speakers")
        self._tree.heading("preview", text="Preview")

        self._tree.column("date", width=130, minwidth=100)
        self._tree.column("subject", width=150, minwidth=100)
        self._tree.column("app", width=80, minwidth=60)
        self._tree.column("speakers", width=120, minwidth=80)
        self._tree.column("preview", width=200, minwidth=100)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind("<Double-1>", self._on_double_click)

        # Status bar
        self._status_var = tk.StringVar(value="Enter a search query or click Browse All. Date format: YYYY-MM-DD")
        ttk.Label(self._window, textvariable=self._status_var, padding=5).pack(fill=tk.X)

        self._window.protocol("WM_DELETE_WINDOW", self._close)
        query_entry.focus_set()
        self._window.mainloop()

    def _apply_dark_theme(self) -> None:
        """Apply dark theme to ttk widgets."""
        bg = "#1a1a2e"
        fg = "#e0e0e0"
        field_bg = "#0f1a2e"
        select_bg = "#0f3460"
        self._window.configure(bg=bg)

        style = ttk.Style(self._window)
        style.theme_use("clam")
        style.configure(".", background=bg, foreground=fg, fieldbackground=field_bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TButton", background="#0f3460", foreground=fg)
        style.map("TButton", background=[("active", "#1a5276")])
        style.configure("TEntry", fieldbackground=field_bg, foreground=fg)
        style.configure("Treeview", background=field_bg, foreground=fg,
                         fieldbackground=field_bg)
        style.configure("Treeview.Heading", background="#16213e", foreground=fg)
        style.map("Treeview", background=[("selected", select_bg)])

    def _do_search(self) -> None:
        """Execute the search."""
        query = self._query_var.get().strip()
        speaker = self._speaker_var.get().strip()
        subject = self._subject_var.get().strip()
        attendee = self._attendee_var.get().strip()
        date_from = self._date_from_var.get().strip()
        date_to = self._date_to_var.get().strip()

        if not any([query, speaker, subject, attendee, date_from, date_to]):
            self._status_var.set("Enter a search query or filter, or click Browse All.")
            return

        self._status_var.set("Searching...")
        self._tree.delete(*self._tree.get_children())

        # Run search in background thread
        def _search():
            try:
                self._results = self._index.search(
                    query=query,
                    speaker=speaker,
                    subject=subject,
                    attendee=attendee,
                    date_from=date_from,
                    date_to=date_to,
                )
                self._window.after(0, self._display_results)
            except Exception as e:
                logger.exception("Search failed")
                self._window.after(0, lambda: self._status_var.set(f"Search error: {e}"))

        threading.Thread(target=_search, daemon=True).start()

    def _browse_all(self) -> None:
        """Show all recordings without any filter."""
        self._status_var.set("Loading all recordings...")
        self._tree.delete(*self._tree.get_children())

        def _load():
            try:
                self._results = self._index.search(limit=200)
                self._window.after(0, self._display_results)
            except Exception as e:
                logger.exception("Browse all failed")
                self._window.after(0, lambda: self._status_var.set(f"Error: {e}"))

        threading.Thread(target=_load, daemon=True).start()

    def _display_results(self) -> None:
        """Display search results in the treeview."""
        self._tree.delete(*self._tree.get_children())

        for i, r in enumerate(self._results):
            date_short = r.date[:19] if r.date else ""
            self._tree.insert("", tk.END, iid=str(i), values=(
                date_short,
                r.subject,
                r.app_name,
                r.speakers,
                r.snippet[:80] if r.snippet else "",
            ))

        count = len(self._results)
        self._status_var.set(f"{count} result(s) found.")

    def _sort_column(self, col: str) -> None:
        """Sort results by column (toggle ascending/descending)."""
        if not self._results:
            return
        # Simple reverse of current display order
        self._results.reverse()
        self._display_results()

    def _on_double_click(self, event) -> None:
        """Open the recording folder on double-click."""
        selection = self._tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        if 0 <= idx < len(self._results):
            recording_dir = self._results[idx].recording_dir
            try:
                open_in_explorer(recording_dir)
            except Exception:
                logger.exception("Failed to open folder: %s", recording_dir)

    def _set_date_range(self, days: int | None) -> None:
        """Set the From/To date fields and trigger a search.

        Args:
            days: Number of days back from today, or None for all time.
        """
        from datetime import date, timedelta
        self._date_to_var.set("")
        if days is None:
            self._date_from_var.set("")
        elif days == 0:
            self._date_from_var.set(date.today().isoformat())
        else:
            start = date.today() - timedelta(days=days)
            self._date_from_var.set(start.isoformat())
        self._do_search() if any([
            self._query_var.get().strip(),
            self._speaker_var.get().strip(),
            self._subject_var.get().strip(),
            self._attendee_var.get().strip(),
            self._date_from_var.get().strip(),
        ]) else self._browse_all()

    def _close(self) -> None:
        """Close the search window."""
        self._index.close()
        if self._window is not None:
            self._window.destroy()
            self._window = None
