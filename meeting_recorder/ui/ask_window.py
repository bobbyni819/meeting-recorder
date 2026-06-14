"""Tkinter dialog for asking questions across meeting recordings."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

from meeting_recorder.search.ask import ask_meetings, format_ask_result

logger = logging.getLogger(__name__)


class AskWindow:
    """Tkinter-based natural-language Q&A dialog for recordings."""

    def __init__(self):
        self._window: Optional[tk.Toplevel | tk.Tk] = None
        self._question_text: Optional[tk.Text] = None
        self._result_text: Optional[tk.Text] = None
        self._ask_button: Optional[ttk.Button] = None
        self._top_k_var: Optional[tk.IntVar] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the ask window."""
        if self._window is not None:
            try:
                self._window.lift()
                return
            except tk.TclError:
                self._window = None

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Ask Your Meetings")
        self._window.geometry("700x520")
        self._window.resizable(True, True)

        self._apply_dark_theme()
        self._build_ui()

        self._window.protocol("WM_DELETE_WINDOW", self.close)
        if self._question_text is not None:
            self._question_text.focus_set()

    def _apply_dark_theme(self) -> None:
        """Apply dark theme to ttk widgets."""
        if self._window is None:
            return

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
        style.configure("TSpinbox", fieldbackground=field_bg, foreground=fg)
        style.map("TSpinbox", fieldbackground=[("readonly", field_bg)])
        style.map("TSpinbox", foreground=[("readonly", fg)])
        self._select_bg = select_bg

    def _build_ui(self) -> None:
        if self._window is None:
            return

        bg = "#1a1a2e"
        fg = "#e0e0e0"
        field_bg = "#0f1a2e"
        select_bg = getattr(self, "_select_bg", "#0f3460")

        question_frame = ttk.Frame(self._window, padding=10)
        question_frame.pack(fill=tk.X)

        ttk.Label(question_frame, text="Question:").pack(anchor=tk.W, pady=(0, 4))
        question_body = ttk.Frame(question_frame)
        question_body.pack(fill=tk.X)

        question_scrollbar = ttk.Scrollbar(question_body, orient=tk.VERTICAL)
        question_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._question_text = tk.Text(
            question_body,
            height=4,
            wrap=tk.WORD,
            bg=field_bg,
            fg=fg,
            insertbackground=fg,
            selectbackground=select_bg,
            relief=tk.FLAT,
            padx=8,
            pady=6,
            yscrollcommand=question_scrollbar.set,
        )
        self._question_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        question_scrollbar.configure(command=self._question_text.yview)
        self._question_text.bind("<Control-Return>", lambda e: self._do_ask())

        control_frame = ttk.Frame(self._window, padding=(10, 0, 10, 5))
        control_frame.pack(fill=tk.X)

        ttk.Label(control_frame, text="Top matches:").pack(side=tk.LEFT, padx=(0, 5))
        self._top_k_var = tk.IntVar(value=5)
        top_k_spin = ttk.Spinbox(
            control_frame,
            from_=1,
            to=20,
            width=5,
            textvariable=self._top_k_var,
        )
        top_k_spin.pack(side=tk.LEFT, padx=(0, 10))

        self._ask_button = ttk.Button(control_frame, text="Ask", command=self._do_ask)
        self._ask_button.pack(side=tk.LEFT)

        result_frame = ttk.Frame(self._window, padding=10)
        result_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(result_frame, text="Answer:").pack(anchor=tk.W, pady=(0, 4))
        result_body = ttk.Frame(result_frame)
        result_body.pack(fill=tk.BOTH, expand=True)

        result_scrollbar = ttk.Scrollbar(result_body, orient=tk.VERTICAL)
        result_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._result_text = tk.Text(
            result_body,
            wrap=tk.WORD,
            bg=field_bg,
            fg=fg,
            insertbackground=fg,
            selectbackground=select_bg,
            relief=tk.FLAT,
            padx=8,
            pady=6,
            yscrollcommand=result_scrollbar.set,
        )
        self._result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_scrollbar.configure(command=self._result_text.yview)
        self._set_result("Ask a question about your recorded meetings.")

    def _do_ask(self) -> None:
        """Ask a question in a background thread."""
        if self._question_text is None:
            return

        question = self._question_text.get("1.0", tk.END).strip()
        if not question:
            self._set_result("Enter a question first.")
            return

        top_k = 5
        if self._top_k_var is not None:
            try:
                top_k = max(1, int(self._top_k_var.get()))
            except (tk.TclError, ValueError):
                top_k = 5

        if self._ask_button is not None:
            self._ask_button.configure(state=tk.DISABLED)
        self._set_result("Thinking…")

        win = self._window

        def _ask() -> None:
            try:
                result = ask_meetings(question, top_k=top_k)
                text = format_ask_result(result)
            except ValueError:
                text = "Set your Gemini API key in Settings → Transcription"
            except Exception as e:
                logger.exception("Ask meetings failed")
                text = f"Ask error: {e}"

            self._post_to_ui(lambda: self._display_result(text), win)

        threading.Thread(target=_ask, daemon=True).start()

    def _post_to_ui(self, func, win=None) -> None:
        """Schedule a callback on the Tk thread if the window still exists."""
        win = win if win is not None else self._window
        if win is None:
            return
        try:
            exists = win.winfo_exists()
        except tk.TclError:
            return
        except (RuntimeError, AttributeError):
            return
        if not exists:
            return
        try:
            win.after(0, func)
        except tk.TclError:
            pass
        except (RuntimeError, AttributeError):
            pass

    def _display_result(self, text: str) -> None:
        """Display ask results and re-enable the Ask button."""
        self._set_result(text)
        if self._ask_button is not None:
            try:
                self._ask_button.configure(state=tk.NORMAL)
            except tk.TclError:
                pass

    def _set_result(self, text: str) -> None:
        if self._result_text is None:
            return
        try:
            self._result_text.configure(state=tk.NORMAL)
            self._result_text.delete("1.0", tk.END)
            self._result_text.insert("1.0", text)
            self._result_text.configure(state=tk.DISABLED)
            self._result_text.see("1.0")
        except tk.TclError:
            pass

    def close(self) -> None:
        """Close the ask window."""
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None
            self._question_text = None
            self._result_text = None
            self._ask_button = None
            self._top_k_var = None
