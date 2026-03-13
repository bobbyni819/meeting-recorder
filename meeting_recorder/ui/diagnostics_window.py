"""Diagnostics window — runs system checks and displays results in a Tkinter UI."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, RED_DOT,
)

logger = logging.getLogger(__name__)

# Status icons and colors
STATUS_ICON = {"ok": "\u2713", "warn": "\u26a0", "fail": "\u2717"}
STATUS_COLOR = {"ok": GREEN, "warn": AMBER, "fail": RED_DOT}


class DiagnosticsWindow:
    """Window that runs and displays system diagnostic checks."""

    def __init__(self):
        self._window: Optional[tk.Toplevel] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the diagnostics window and start running checks."""
        if self._window is not None:
            try:
                self._window.lift()
                return
            except tk.TclError:
                self._window = None

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("System Diagnostics")
        self._window.geometry("620x500")
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        # Header
        header = tk.Frame(self._window, bg=BG_HEADER, height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="System Diagnostics",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=8, side=tk.LEFT)
        self._status_label = tk.Label(
            header, text="Running...",
            font=("Segoe UI", 9), fg=AMBER, bg=BG_HEADER,
        )
        self._status_label.pack(side=tk.RIGHT, padx=16, pady=8)

        # Scrollable results area
        container = tk.Frame(self._window, bg=BG_COLOR)
        container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(container, bg=BG_COLOR, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        self._results_frame = tk.Frame(canvas, bg=BG_COLOR)

        self._results_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._results_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self._canvas = canvas

        # Bottom buttons
        btn_frame = tk.Frame(self._window, bg=BG_HEADER, height=40)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        btn_frame.pack_propagate(False)

        self._rerun_btn = tk.Label(
            btn_frame, text="  Re-run  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg="#0f3460", cursor="hand2", padx=8, pady=4,
        )
        self._rerun_btn.pack(side=tk.RIGHT, padx=12, pady=6)
        self._rerun_btn.bind("<Button-1>", lambda e: self._run_checks())
        self._rerun_btn.bind("<Enter>", lambda e: self._rerun_btn.configure(fg=TEXT_BRIGHT))
        self._rerun_btn.bind("<Leave>", lambda e: self._rerun_btn.configure(fg=TEXT_DIM))

        close_btn = tk.Label(
            btn_frame, text="  Close  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg="#0f3460", cursor="hand2", padx=8, pady=4,
        )
        close_btn.pack(side=tk.RIGHT, padx=4, pady=6)
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=TEXT_BRIGHT))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=TEXT_DIM))

        # Start checks
        self._run_checks()

    def close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    def _run_checks(self) -> None:
        """Run diagnostics in a background thread."""
        # Clear previous results
        for w in self._results_frame.winfo_children():
            w.destroy()

        # Show loading
        self._loading_label = tk.Label(
            self._results_frame,
            text="Running diagnostics... this may take a moment.",
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR,
        )
        self._loading_label.pack(padx=20, pady=20)

        if self._status_label:
            self._status_label.configure(text="Running...", fg=AMBER)

        self._rerun_btn.configure(fg="#555555")

        thread = threading.Thread(target=self._run_checks_bg, daemon=True)
        thread.start()

    def _run_checks_bg(self) -> None:
        """Background thread: run structured diagnostics."""
        try:
            from meeting_recorder.diagnose import run_diagnostics_structured
            categories = run_diagnostics_structured()
        except Exception as e:
            logger.error("Diagnostics failed: %s", e)
            categories = []

        # Update UI on main thread
        if self._window:
            try:
                self._window.after(0, lambda: self._display_results(categories))
            except tk.TclError:
                pass

    def _display_results(self, categories: list) -> None:
        """Render check results in the results frame."""
        if not self._window:
            return

        # Clear loading
        for w in self._results_frame.winfo_children():
            w.destroy()

        total_ok = 0
        total_warn = 0
        total_fail = 0

        for cat in categories:
            # Category header
            cat_frame = tk.Frame(self._results_frame, bg=BG_PANEL)
            cat_frame.pack(fill=tk.X, padx=12, pady=(8, 2))

            status_icon = STATUS_ICON.get(cat.status, "?")
            status_color = STATUS_COLOR.get(cat.status, TEXT_DIM)

            header_row = tk.Frame(cat_frame, bg=BG_PANEL)
            header_row.pack(fill=tk.X, padx=8, pady=(6, 2))

            tk.Label(
                header_row, text=status_icon,
                font=("Segoe UI", 11), fg=status_color, bg=BG_PANEL,
            ).pack(side=tk.LEFT, padx=(0, 6))

            tk.Label(
                header_row, text=cat.name,
                font=("Segoe UI", 10, "bold"), fg=TEXT_BRIGHT, bg=BG_PANEL,
            ).pack(side=tk.LEFT)

            # Individual results
            for result in cat.results:
                r_icon = STATUS_ICON.get(result.status, "?")
                r_color = STATUS_COLOR.get(result.status, TEXT_DIM)

                result_row = tk.Frame(cat_frame, bg=BG_PANEL)
                result_row.pack(fill=tk.X, padx=(28, 8), pady=1)

                tk.Label(
                    result_row, text=r_icon,
                    font=("Segoe UI", 8), fg=r_color, bg=BG_PANEL,
                ).pack(side=tk.LEFT, padx=(0, 6))

                tk.Label(
                    result_row, text=result.message,
                    font=("Segoe UI", 8), fg=TEXT_COLOR, bg=BG_PANEL,
                    anchor=tk.W, wraplength=500, justify=tk.LEFT,
                ).pack(side=tk.LEFT, fill=tk.X, expand=True)

                if result.status == "ok":
                    total_ok += 1
                elif result.status == "warn":
                    total_warn += 1
                else:
                    total_fail += 1

            # Bottom padding
            tk.Frame(cat_frame, bg=BG_PANEL, height=4).pack(fill=tk.X)

        # Summary
        summary_parts = []
        if total_ok:
            summary_parts.append(f"{total_ok} passed")
        if total_warn:
            summary_parts.append(f"{total_warn} warning{'s' if total_warn != 1 else ''}")
        if total_fail:
            summary_parts.append(f"{total_fail} failed")

        summary_text = " | ".join(summary_parts) if summary_parts else "No checks run"

        if total_fail > 0:
            summary_color = RED_DOT
        elif total_warn > 0:
            summary_color = AMBER
        else:
            summary_color = GREEN

        if self._status_label:
            self._status_label.configure(text=summary_text, fg=summary_color)

        self._rerun_btn.configure(fg=TEXT_DIM)
