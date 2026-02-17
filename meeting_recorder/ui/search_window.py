"""Tkinter search dialog for finding recordings."""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

from meeting_recorder.search.index import RecordingIndex, SearchResult

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

        # Search bar
        search_frame = ttk.Frame(self._window, padding=10)
        search_frame.pack(fill=tk.X)

        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        self._query_var = tk.StringVar()
        query_entry = ttk.Entry(search_frame, textvariable=self._query_var, width=40)
        query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        query_entry.bind("<Return>", lambda e: self._do_search())
        ttk.Button(search_frame, text="Search", command=self._do_search).pack(side=tk.LEFT)

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
        ttk.Entry(filter_frame, textvariable=self._attendee_var, width=15).pack(side=tk.LEFT)

        # Results treeview
        tree_frame = ttk.Frame(self._window, padding=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("date", "subject", "app", "speakers", "preview")
        self._tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")

        self._tree.heading("date", text="Date")
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
        self._status_var = tk.StringVar(value="Enter a search query to begin.")
        ttk.Label(self._window, textvariable=self._status_var, padding=5).pack(fill=tk.X)

        self._window.protocol("WM_DELETE_WINDOW", self._close)
        query_entry.focus_set()
        self._window.mainloop()

    def _do_search(self) -> None:
        """Execute the search."""
        query = self._query_var.get().strip()
        speaker = self._speaker_var.get().strip()
        subject = self._subject_var.get().strip()
        attendee = self._attendee_var.get().strip()

        if not query and not speaker and not subject and not attendee:
            self._status_var.set("Enter at least one search criterion.")
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
                )
                self._window.after(0, self._display_results)
            except Exception as e:
                logger.exception("Search failed")
                self._window.after(0, lambda: self._status_var.set(f"Search error: {e}"))

        threading.Thread(target=_search, daemon=True).start()

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

    def _on_double_click(self, event) -> None:
        """Open the recording folder on double-click."""
        selection = self._tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        if 0 <= idx < len(self._results):
            recording_dir = self._results[idx].recording_dir
            try:
                os.startfile(recording_dir)
            except Exception:
                logger.exception("Failed to open folder: %s", recording_dir)

    def _close(self) -> None:
        """Close the search window."""
        self._index.close()
        if self._window is not None:
            self._window.destroy()
            self._window = None
