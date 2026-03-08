"""Main application window for Meeting Recorder.

A proper desktop GUI that replaces the tray-icon-only experience.
Shows recording controls, live VU meters, recording history, and status.
Coexists with the system tray icon and the overlay dashboard.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

from meeting_recorder.audio.level_monitor import MIN_DB
from meeting_recorder.utils import open_in_explorer
from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CONTROLS, BG_CARD, BG_CARD_HOVER,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    RED_DOT, RED_DOT_OFF, GREEN, GREEN_DARK, AMBER, BLUE_ACCENT, BLUE_DARK,
    GREEN_VU, YELLOW_VU, RED_VU, VU_BG,
    BUTTON_BG, BUTTON_HOVER, MUTED_COLOR, UNMUTED_COLOR,
    db_to_fraction as _db_to_fraction,
    vu_color as _vu_color,
    format_elapsed as _format_elapsed,
)

logger = logging.getLogger(__name__)

WIN_WIDTH = 520
WIN_HEIGHT = 620


def _format_duration_short(seconds: float) -> str:
    """Format seconds for recording list display."""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    elif m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class MainWindow:
    """Full-featured main application window.

    Two visual modes:
    - Idle: Big record button + recording history
    - Recording: Live VU meters, controls, transcript preview
    """

    def __init__(
        self,
        on_start: Optional[Callable] = None,
        on_stop: Optional[Callable] = None,
        on_pause: Optional[Callable] = None,
        on_toggle_mute: Optional[Callable] = None,
        on_record_window: Optional[Callable] = None,
        on_search: Optional[Callable] = None,
        on_settings: Optional[Callable] = None,
        on_open_recordings: Optional[Callable] = None,
        on_open_recording: Optional[Callable] = None,
        on_list_recent: Optional[Callable] = None,
        on_list_windows: Optional[Callable] = None,
        on_pick_window: Optional[Callable] = None,
        on_toggle_audio_mode: Optional[Callable] = None,
        on_toggle_auto_start: Optional[Callable] = None,
        on_quit: Optional[Callable] = None,
        auto_start: bool = False,
        hotkey_recording: str = "ctrl+shift+r",
        hotkey_pause: str = "ctrl+shift+p",
    ):
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_pause = on_pause
        self._on_toggle_mute = on_toggle_mute
        self._on_record_window = on_record_window
        self._on_search = on_search
        self._on_settings = on_settings
        self._on_open_recordings = on_open_recordings
        self._on_open_recording = on_open_recording
        self._on_list_recent = on_list_recent
        self._on_list_windows = on_list_windows
        self._on_pick_window = on_pick_window
        self._on_toggle_audio_mode = on_toggle_audio_mode
        self._on_toggle_auto_start = on_toggle_auto_start
        self._on_quit = on_quit
        self._auto_start = auto_start
        self._hotkey_recording = hotkey_recording
        self._hotkey_pause = hotkey_pause

        self._window: Optional[tk.Tk] = None
        self._tk_thread: Optional[threading.Thread] = None
        self._tk_ready = threading.Event()
        self._is_visible = False

        # State
        self._is_recording = False
        self._is_paused = False
        self._recording_app_name = ""

        # Widget refs
        self._status_dot: Optional[tk.Label] = None
        self._status_label: Optional[tk.Label] = None
        self._elapsed_label: Optional[tk.Label] = None
        self._idle_frame: Optional[tk.Frame] = None
        self._recording_frame: Optional[tk.Frame] = None
        self._app_vu_canvas: Optional[tk.Canvas] = None
        self._mic_vu_canvas: Optional[tk.Canvas] = None
        self._app_db_label: Optional[tk.Label] = None
        self._mic_db_label: Optional[tk.Label] = None
        self._mute_btn: Optional[tk.Label] = None
        self._pause_btn: Optional[tk.Label] = None
        self._stop_btn: Optional[tk.Label] = None
        self._start_btn: Optional[tk.Label] = None
        self._transcript_label: Optional[tk.Label] = None
        self._preview_label: Optional[tk.Label] = None
        self._audio_mode_label: Optional[tk.Label] = None
        self._preview_photo = None
        self._history_frame: Optional[tk.Frame] = None
        self._detail_frame: Optional[tk.Frame] = None
        self._auto_label: Optional[tk.Label] = None
        self._statusbar_label: Optional[tk.Label] = None

        # Pulse animation
        self._dot_visible = True
        self._pulse_after_id: Optional[str] = None

        # VU peak hold and smoothing
        self._app_peak_frac = 0.0
        self._mic_peak_frac = 0.0
        self._app_smooth_frac = 0.0
        self._mic_smooth_frac = 0.0

        # Disk space update counter (update every ~5 seconds at 10Hz)
        self._disk_update_counter = 0
        self._disk_label: Optional[tk.Label] = None

        # Health warning banner
        self._warning_frame: Optional[tk.Frame] = None
        self._warning_label: Optional[tk.Label] = None
        self._warning_dismiss_id: Optional[str] = None

    def show(self) -> None:
        """Show the main window. Creates it in a dedicated thread if needed."""
        if self._window is not None:
            try:
                self._is_visible = True
                self._window.after(0, self._do_reshow)
                return
            except tk.TclError:
                self._window = None

        self._tk_ready.clear()
        self._is_visible = True
        self._tk_thread = threading.Thread(
            target=self._run_tk, name="main-window-tk", daemon=True,
        )
        self._tk_thread.start()
        self._tk_ready.wait(timeout=5.0)

    def _run_tk(self) -> None:
        try:
            self._build_window()
            self._start_pulse()
            self._refresh_history()
            self._tk_ready.set()
            self._window.mainloop()
        except Exception:
            logger.exception("Main window Tk thread error")
        finally:
            self._window = None
            self._is_visible = False
            self._tk_ready.set()

    def _do_reshow(self) -> None:
        if self._window:
            self._window.deiconify()
            self._window.lift()

    def hide(self) -> None:
        """Hide (minimize to tray), don't destroy."""
        self._is_visible = False
        if self._window is not None:
            try:
                self._save_geometry()
                self._window.after(0, self._window.withdraw)
            except tk.TclError:
                pass

    def close(self) -> None:
        """Destroy the window entirely."""
        self._is_visible = False
        w = self._window
        self._window = None
        if w is not None:
            try:
                self._save_geometry(w)
                w.after(0, w.destroy)
            except tk.TclError:
                pass

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    # ------------------------------------------------------------------
    # Thread-safe update methods (called from app.py callbacks)
    # ------------------------------------------------------------------

    def set_recording_state(self, is_recording: bool, app_name: str = "") -> None:
        """Switch between idle and recording views."""
        self._is_recording = is_recording
        self._recording_app_name = app_name
        if is_recording:
            self._app_peak_frac = 0.0
            self._mic_peak_frac = 0.0
            self._app_smooth_frac = 0.0
            self._mic_smooth_frac = 0.0
        if self._window is None:
            return
        try:
            self._window.after(0, self._apply_recording_state)
        except tk.TclError:
            pass

    def update_audio_levels(
        self, app_rms_db: float, app_peak_db: float, mic_rms_db: float, mic_peak_db: float
    ) -> None:
        if self._window is None or not self._is_visible or not self._is_recording:
            return
        app_frac = _db_to_fraction(app_rms_db)
        mic_frac = _db_to_fraction(mic_rms_db)
        # Smooth VU (fast attack, slow release)
        attack = 0.6
        release = 0.85
        self._app_smooth_frac = max(
            app_frac * attack + self._app_smooth_frac * (1 - attack),
            self._app_smooth_frac * release,
        ) if app_frac >= self._app_smooth_frac else self._app_smooth_frac * release
        self._mic_smooth_frac = max(
            mic_frac * attack + self._mic_smooth_frac * (1 - attack),
            self._mic_smooth_frac * release,
        ) if mic_frac >= self._mic_smooth_frac else self._mic_smooth_frac * release
        # Track peak hold (decays slowly)
        self._app_peak_frac = max(app_frac, self._app_peak_frac * 0.92)
        self._mic_peak_frac = max(mic_frac, self._mic_peak_frac * 0.92)
        try:
            self._window.after(
                0, self._draw_vu_meters,
                self._app_smooth_frac, self._mic_smooth_frac,
                app_rms_db, mic_rms_db,
                self._app_peak_frac, self._mic_peak_frac,
            )
        except tk.TclError:
            pass

    def update_elapsed(self, elapsed_seconds: float) -> None:
        if self._window is None or not self._is_visible:
            return
        text = _format_elapsed(elapsed_seconds)
        try:
            self._window.after(0, self._set_elapsed, text)
        except tk.TclError:
            pass
        # Update disk space every ~5 seconds (at 10Hz call rate)
        self._disk_update_counter += 1
        if self._disk_update_counter >= 50:
            self._disk_update_counter = 0
            try:
                self._window.after(0, self._update_disk_space)
            except tk.TclError:
                pass

    def update_paused(self, is_paused: bool) -> None:
        self._is_paused = is_paused
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._apply_paused_state)
        except tk.TclError:
            pass

    def update_mute_state(self, is_muted: bool) -> None:
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._set_mute_display, is_muted)
        except tk.TclError:
            pass

    def update_transcript(self, text: str) -> None:
        if self._window is None or not self._is_visible or not self._is_recording:
            return
        if len(text) > 300:
            text = "..." + text[-297:]
        try:
            self._window.after(0, self._set_transcript, text)
        except tk.TclError:
            pass

    def update_screen_preview(self, frame) -> None:
        if self._window is None or not self._is_visible or not self._is_recording:
            return
        if self._preview_label is None or frame is None:
            return
        try:
            self._window.after(0, lambda f=frame: self._set_screen_preview(f))
        except tk.TclError:
            pass

    def update_audio_mode(self, is_desktop: bool) -> None:
        """Update the audio mode indicator in the recording view."""
        if self._window is None or not self._is_visible:
            return
        label = "\U0001f50a Desktop Audio" if is_desktop else "\U0001f3a4 App Audio"
        color = AMBER if is_desktop else TEXT_DIM
        try:
            self._window.after(0, lambda: (
                self._audio_mode_label.configure(text=label, fg=color)
                if self._audio_mode_label else None
            ))
        except tk.TclError:
            pass

    def update_status_bar(self, text: str) -> None:
        if self._window is None:
            return
        try:
            self._window.after(0, lambda: (
                self._statusbar_label.configure(text=text) if self._statusbar_label else None
            ))
        except tk.TclError:
            pass

    def update_auto_start(self, enabled: bool) -> None:
        """Update the auto-record indicator (thread-safe)."""
        self._auto_start = enabled
        if self._window is None:
            return
        try:
            self._window.after(0, self._apply_auto_start)
        except tk.TclError:
            pass

    def show_warning(self, message: str, duration_ms: int = 8000) -> None:
        """Show a warning banner in the recording view (thread-safe)."""
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._display_warning, message, duration_ms)
        except tk.TclError:
            pass

    def refresh_history(self) -> None:
        """Trigger a history list refresh (thread-safe)."""
        if self._window is None:
            return
        try:
            self._window.after(0, self._refresh_history)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        self._window = tk.Tk()
        self._window.title("Meeting Recorder")
        # Restore saved position or use default geometry
        saved_geo = self._load_geometry()
        self._window.geometry(saved_geo or f"{WIN_WIDTH}x{WIN_HEIGHT}")
        self._window.minsize(460, 500)
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.hide)

        # Log Tk callback exceptions instead of silently printing to stderr
        def _tk_error(exc_type, exc_value, exc_tb):
            logger.error(
                "Tkinter callback error", exc_info=(exc_type, exc_value, exc_tb)
            )
        self._window.report_callback_exception = _tk_error

        # Try to set window icon
        try:
            from meeting_recorder.ui.icons import create_idle_icon
            icon = create_idle_icon()
            import io
            from PIL import ImageTk
            self._window.iconphoto(False, ImageTk.PhotoImage(icon))
        except Exception:
            pass

        # --- Header ---
        self._build_header()

        # --- Idle frame (shown when not recording) ---
        self._idle_frame = tk.Frame(self._window, bg=BG_COLOR)
        self._build_idle_view(self._idle_frame)

        # --- Recording frame (shown during recording) ---
        self._recording_frame = tk.Frame(self._window, bg=BG_COLOR)
        self._build_recording_view(self._recording_frame)

        # --- Status bar ---
        self._build_statusbar()

        # Show idle by default
        self._idle_frame.pack(fill=tk.BOTH, expand=True)

        # Keyboard shortcuts
        self._window.bind("<Escape>", lambda e: self._on_escape())
        self._window.bind("<F5>", lambda e: self._refresh_history())
        self._window.bind("<Control-f>", lambda e: self._fire(self._on_search))
        self._window.bind("<Control-comma>", lambda e: self._fire(self._on_settings))

    def _build_header(self) -> None:
        header = tk.Frame(self._window, bg=BG_HEADER, height=52)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        # Status dot
        self._status_dot = tk.Label(
            header, text="\u2b24", font=("Segoe UI", 12),
            fg=TEXT_DIM, bg=BG_HEADER,
        )
        self._status_dot.pack(side=tk.LEFT, padx=(16, 6))

        # Status text
        self._status_label = tk.Label(
            header, text="Idle", font=("Segoe UI", 12, "bold"),
            fg=TEXT_COLOR, bg=BG_HEADER,
        )
        self._status_label.pack(side=tk.LEFT)

        # Elapsed time (right side, only visible during recording)
        self._elapsed_label = tk.Label(
            header, text="", font=("Segoe UI Semibold", 14),
            fg=TEXT_BRIGHT, bg=BG_HEADER,
        )
        self._elapsed_label.pack(side=tk.RIGHT, padx=16)

        # Settings gear (right side)
        gear_btn = tk.Label(
            header, text="\u2699", font=("Segoe UI", 14),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        gear_btn.pack(side=tk.RIGHT, padx=(0, 8))
        gear_btn.bind("<Button-1>", lambda e: self._fire(self._on_settings))
        gear_btn.bind("<Enter>", lambda e: gear_btn.configure(fg=TEXT_COLOR))
        gear_btn.bind("<Leave>", lambda e: gear_btn.configure(fg=TEXT_DIM))

    def _build_idle_view(self, parent: tk.Frame) -> None:
        """Build the idle mode view: big record button + history."""

        # Auto-record indicator (clickable to toggle)
        auto_text = "\u2713 Auto-record ON" if self._auto_start else "\u2717 Auto-record OFF"
        auto_color = GREEN if self._auto_start else TEXT_DIM
        self._auto_label = tk.Label(
            parent, text=auto_text, font=("Segoe UI", 9),
            fg=auto_color, bg=BG_COLOR,
            cursor="hand2" if self._on_toggle_auto_start else "",
        )
        self._auto_label.pack(pady=(12, 4))
        if self._on_toggle_auto_start:
            self._auto_label.bind("<Button-1>", lambda e: self._toggle_auto_start_click())
            self._auto_label.bind("<Enter>", lambda e: self._auto_label.configure(
                fg=TEXT_BRIGHT))
            self._auto_label.bind("<Leave>", lambda e: self._auto_label.configure(
                fg=GREEN if self._auto_start else TEXT_DIM))

        # Big start button
        btn_frame = tk.Frame(parent, bg=BG_COLOR)
        btn_frame.pack(pady=(4, 8))

        self._start_btn = tk.Label(
            btn_frame, text="  \u23fa  Start Recording  ",
            font=("Segoe UI", 13, "bold"),
            fg=TEXT_BRIGHT, bg=GREEN_DARK, cursor="hand2",
            padx=24, pady=10,
        )
        self._start_btn.pack()
        self._start_btn.bind("<Button-1>", lambda e: self._fire(self._on_start))
        self._start_btn.bind("<Enter>", lambda e: self._start_btn.configure(bg=GREEN))
        self._start_btn.bind("<Leave>", lambda e: self._start_btn.configure(bg=GREEN_DARK))

        # Secondary buttons row
        sec_frame = tk.Frame(parent, bg=BG_COLOR)
        sec_frame.pack(pady=(0, 12))

        for text, callback in [
            ("\u29bf Record Window...", self._on_record_window),
            ("\U0001f50d Search", self._on_search),
            ("\U0001f4c2 Open Folder", self._on_open_recordings),
        ]:
            btn = tk.Label(
                sec_frame, text=f"  {text}  ", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2", padx=8, pady=4,
            )
            btn.pack(side=tk.LEFT, padx=4)
            btn.bind("<Button-1>", lambda e, cb=callback: self._fire(cb))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=BUTTON_HOVER, fg=TEXT_COLOR))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=BUTTON_BG, fg=TEXT_DIM))

        # Divider
        tk.Frame(parent, bg=BG_HEADER, height=1).pack(fill=tk.X, padx=16)

        # Section label with stats
        section_row = tk.Frame(parent, bg=BG_COLOR)
        section_row.pack(fill=tk.X, padx=20, pady=(10, 4))
        tk.Label(
            section_row, text="Recent Recordings", font=("Segoe UI", 10, "bold"),
            fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.W,
        ).pack(side=tk.LEFT)
        self._stats_label = tk.Label(
            section_row, text="", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.E,
        )
        self._stats_label.pack(side=tk.RIGHT)

        # Inline filter
        filter_row = tk.Frame(parent, bg=BG_COLOR)
        filter_row.pack(fill=tk.X, padx=20, pady=(0, 4))
        self._filter_var = tk.StringVar()
        filter_entry = tk.Entry(
            filter_row, textvariable=self._filter_var,
            font=("Segoe UI", 9), bg=BG_PANEL, fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR, bd=0, highlightthickness=1,
            highlightcolor=BG_CONTROLS, highlightbackground=BG_HEADER,
        )
        filter_entry.pack(fill=tk.X, ipady=4, padx=0)
        filter_entry.insert(0, "")
        # Placeholder behavior
        _placeholder = "\U0001f50d Filter recordings..."

        def _on_focus_in(e):
            if filter_entry.get() == _placeholder:
                filter_entry.delete(0, tk.END)
                filter_entry.configure(fg=TEXT_COLOR)

        def _on_focus_out(e):
            if not filter_entry.get():
                filter_entry.insert(0, _placeholder)
                filter_entry.configure(fg=TEXT_DIM)

        filter_entry.insert(0, _placeholder)
        filter_entry.configure(fg=TEXT_DIM)
        filter_entry.bind("<FocusIn>", _on_focus_in)
        filter_entry.bind("<FocusOut>", _on_focus_out)
        self._filter_var.trace_add("write", lambda *_: self._refresh_history())

        # Scrollable history
        history_container = tk.Frame(parent, bg=BG_COLOR)
        history_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        canvas = tk.Canvas(history_container, bg=BG_COLOR, highlightthickness=0)
        scrollbar = tk.Scrollbar(history_container, orient=tk.VERTICAL, command=canvas.yview)
        self._history_frame = tk.Frame(canvas, bg=BG_COLOR)

        self._history_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        self._history_canvas_id = canvas.create_window(
            (0, 0), window=self._history_frame, anchor=tk.NW,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        self._history_canvas = canvas

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Resize inner frame to fill canvas width
        def _on_canvas_configure(event):
            canvas.itemconfigure(self._history_canvas_id, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse wheel scrolling — bind to canvas only (not bind_all)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        # Also bind to the inner frame so scrolling works when hovering over cards
        self._history_frame.bind("<MouseWheel>", _on_mousewheel)
        self._history_mousewheel_handler = _on_mousewheel

    def _build_recording_view(self, parent: tk.Frame) -> None:
        """Build the recording mode view: VU meters, controls, transcript."""

        # App name / meeting subject + audio mode
        title_row = tk.Frame(parent, bg=BG_COLOR)
        title_row.pack(fill=tk.X, padx=20, pady=(12, 4))

        self._recording_title = tk.Label(
            title_row, text="Recording...", font=("Segoe UI", 10),
            fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.W,
        )
        self._recording_title.pack(side=tk.LEFT)

        self._audio_mode_label = tk.Label(
            title_row, text="", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.E,
            cursor="hand2" if self._on_toggle_audio_mode else "",
        )
        self._audio_mode_label.pack(side=tk.RIGHT)
        if self._on_toggle_audio_mode:
            self._audio_mode_label.bind(
                "<Button-1>", lambda e: self._fire(self._on_toggle_audio_mode))
            self._audio_mode_label.bind(
                "<Enter>", lambda e: self._audio_mode_label.configure(fg=TEXT_BRIGHT))
            self._audio_mode_label.bind(
                "<Leave>", lambda e: self._audio_mode_label.configure(
                    fg=AMBER if "Desktop" in (self._audio_mode_label.cget("text") or "") else TEXT_DIM))

        # Health warning banner (hidden by default)
        self._warning_frame = tk.Frame(parent, bg="#5a2000")
        # Don't pack — shown/hidden dynamically
        self._warning_label = tk.Label(
            self._warning_frame, text="", font=("Segoe UI", 9),
            fg="#ffcc00", bg="#5a2000", anchor=tk.W, wraplength=440,
        )
        self._warning_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 4), pady=4)
        dismiss_btn = tk.Label(
            self._warning_frame, text="\u2715", font=("Segoe UI", 9),
            fg="#ffcc00", bg="#5a2000", cursor="hand2",
        )
        dismiss_btn.pack(side=tk.RIGHT, padx=8, pady=4)
        dismiss_btn.bind("<Button-1>", lambda e: self._dismiss_warning())

        # VU meters
        vu_frame = tk.Frame(parent, bg=BG_COLOR)
        vu_frame.pack(fill=tk.X, padx=20, pady=(4, 8))

        # App VU
        app_row = tk.Frame(vu_frame, bg=BG_COLOR)
        app_row.pack(fill=tk.X, pady=2)
        tk.Label(app_row, text="App", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR, width=4, anchor=tk.W).pack(side=tk.LEFT)
        self._app_vu_canvas = tk.Canvas(app_row, height=16, bg=VU_BG, highlightthickness=0)
        self._app_vu_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._app_db_label = tk.Label(app_row, text=f"{MIN_DB:.0f} dB", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR, width=7, anchor=tk.E)
        self._app_db_label.pack(side=tk.LEFT)

        # Mic VU
        mic_row = tk.Frame(vu_frame, bg=BG_COLOR)
        mic_row.pack(fill=tk.X, pady=2)
        tk.Label(mic_row, text="Mic", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR, width=4, anchor=tk.W).pack(side=tk.LEFT)
        self._mic_vu_canvas = tk.Canvas(mic_row, height=16, bg=VU_BG, highlightthickness=0)
        self._mic_vu_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._mic_db_label = tk.Label(mic_row, text=f"{MIN_DB:.0f} dB", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR, width=7, anchor=tk.E)
        self._mic_db_label.pack(side=tk.LEFT)

        # Controls
        ctrl_frame = tk.Frame(parent, bg=BG_CONTROLS, height=50)
        ctrl_frame.pack(fill=tk.X, padx=16, pady=(4, 0))
        ctrl_frame.pack_propagate(False)

        self._stop_btn = tk.Label(
            ctrl_frame, text="  Stop  ", font=("Segoe UI", 10, "bold"),
            fg=TEXT_BRIGHT, bg="#c0392b", cursor="hand2", padx=12, pady=6,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(12, 4), pady=8)
        self._stop_btn.bind("<Button-1>", lambda e: self._fire(self._on_stop))
        self._stop_btn.bind("<Enter>", lambda e: self._stop_btn.configure(bg="#e74c3c"))
        self._stop_btn.bind("<Leave>", lambda e: self._stop_btn.configure(bg="#c0392b"))

        self._pause_btn = tk.Label(
            ctrl_frame, text="  \u23f8  ", font=("Segoe UI", 10),
            fg=TEXT_BRIGHT, bg="#7f8c8d", cursor="hand2", padx=8, pady=6,
        )
        self._pause_btn.pack(side=tk.LEFT, padx=4, pady=8)
        self._pause_btn.bind("<Button-1>", lambda e: self._fire(self._on_pause))
        self._pause_btn.bind("<Enter>", lambda e: self._pause_btn.configure(
            bg=AMBER if self._is_paused else "#95a5a6"))
        self._pause_btn.bind("<Leave>", lambda e: self._pause_btn.configure(
            bg="#e67e22" if self._is_paused else "#7f8c8d"))

        self._mute_btn = tk.Label(
            ctrl_frame, text="  Unmuted  ", font=("Segoe UI", 9),
            fg=UNMUTED_COLOR, bg=BG_CONTROLS, cursor="hand2",
            relief=tk.GROOVE, padx=8, pady=4,
        )
        self._mute_btn.pack(side=tk.LEFT, padx=8, pady=8)
        self._mute_btn.bind("<Button-1>", lambda e: self._fire(self._on_toggle_mute))

        # Window picker button
        if self._on_list_windows and self._on_pick_window:
            pick_btn = tk.Label(
                ctrl_frame, text="  \u29bf Window  ", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_CONTROLS, cursor="hand2",
                relief=tk.GROOVE, padx=6, pady=4,
            )
            pick_btn.pack(side=tk.LEFT, padx=4, pady=8)
            pick_btn.bind("<Button-1>", lambda e: self._open_window_picker())
            pick_btn.bind("<Enter>", lambda e: pick_btn.configure(fg=TEXT_COLOR))
            pick_btn.bind("<Leave>", lambda e: pick_btn.configure(fg=TEXT_DIM))

        # Screen preview
        preview_container = tk.Frame(parent, bg=BG_COLOR)
        preview_container.pack(fill=tk.X, padx=20, pady=(8, 4))
        self._preview_label = tk.Label(
            preview_container, text="", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg="#0d0d1a",
        )
        self._preview_label.pack(fill=tk.X)

        # Transcript preview
        transcript_frame = tk.Frame(parent, bg=BG_PANEL, height=80)
        transcript_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 8))
        transcript_frame.pack_propagate(False)

        tk.Label(
            transcript_frame, text="Live Transcript", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_PANEL, anchor=tk.W,
        ).pack(fill=tk.X, padx=10, pady=(6, 0))

        self._transcript_label = tk.Label(
            transcript_frame, text="Waiting for speech...",
            font=("Segoe UI", 10), fg=TEXT_COLOR, bg=BG_PANEL,
            wraplength=460, justify=tk.LEFT, anchor=tk.NW,
        )
        self._transcript_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=(2, 6))

        # Auto-adjust wraplength on resize
        def _on_transcript_resize(event):
            new_wrap = max(200, event.width - 24)
            self._transcript_label.configure(wraplength=new_wrap)

        transcript_frame.bind("<Configure>", _on_transcript_resize)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self._window, bg=BG_HEADER, height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)

        hotkey_text = (
            f"{self._hotkey_recording} Record  |  "
            f"{self._hotkey_pause} Pause  |  "
            "Ctrl+F Search  |  Ctrl+, Settings  |  F5 Refresh  |  Esc Hide"
        )
        self._statusbar_label = tk.Label(
            bar, text=hotkey_text, font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_HEADER, anchor=tk.W,
        )
        self._statusbar_label.pack(side=tk.LEFT, padx=12)

        # Disk space label (right side, visible during recording)
        self._disk_label = tk.Label(
            bar, text="", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_HEADER,
        )
        self._disk_label.pack(side=tk.RIGHT, padx=(0, 4))

        # Quit button (right side of status bar)
        if self._on_quit:
            quit_btn = tk.Label(
                bar, text="Quit", font=("Segoe UI", 8),
                fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
            )
            quit_btn.pack(side=tk.RIGHT, padx=12)
            quit_btn.bind("<Button-1>", lambda e: self._fire(self._on_quit))
            quit_btn.bind("<Enter>", lambda e: quit_btn.configure(fg="#e74c3c"))
            quit_btn.bind("<Leave>", lambda e: quit_btn.configure(fg=TEXT_DIM))

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _apply_recording_state(self) -> None:
        """Switch between idle and recording frames."""
        # Close detail view if open
        if self._detail_frame:
            self._detail_frame.destroy()
            self._detail_frame = None

        if self._is_recording:
            self._idle_frame.pack_forget()
            self._recording_frame.pack(fill=tk.BOTH, expand=True)
            self._status_dot.configure(fg=RED_DOT)
            self._status_label.configure(text=f"Recording \u2014 {self._recording_app_name}")
            self._elapsed_label.configure(text="00:00:00")
            if hasattr(self, '_recording_title') and self._recording_title:
                self._recording_title.configure(text=f"Recording {self._recording_app_name}")
            self._update_disk_space()
        else:
            self._recording_frame.pack_forget()
            self._idle_frame.pack(fill=tk.BOTH, expand=True)
            self._status_dot.configure(fg=TEXT_DIM)
            self._status_label.configure(text="Idle")
            self._elapsed_label.configure(text="")
            self._is_paused = False
            if self._window:
                self._window.title("Meeting Recorder")
            if self._disk_label:
                self._disk_label.configure(text="")
            self._dismiss_warning()
            self._refresh_history()

    def _apply_paused_state(self) -> None:
        if self._is_paused:
            self._status_dot.configure(fg=AMBER)
            self._status_label.configure(text=f"\u23f8 Paused \u2014 {self._recording_app_name}")
            if self._pause_btn:
                self._pause_btn.configure(text="  \u25b6  ", bg="#e67e22")
        else:
            self._status_dot.configure(fg=RED_DOT)
            self._status_label.configure(text=f"Recording \u2014 {self._recording_app_name}")
            if self._pause_btn:
                self._pause_btn.configure(text="  \u23f8  ", bg="#7f8c8d")

    def _apply_auto_start(self) -> None:
        if self._auto_label:
            if self._auto_start:
                self._auto_label.configure(text="\u2713 Auto-record ON", fg=GREEN)
            else:
                self._auto_label.configure(text="\u2717 Auto-record OFF", fg=TEXT_DIM)

    def _display_warning(self, message: str, duration_ms: int) -> None:
        """Show the warning banner in the recording view."""
        if not self._warning_frame or not self._warning_label:
            return
        self._warning_label.configure(text=f"\u26a0  {message}")
        # Pack right after the title row (before VU meters)
        if not self._warning_frame.winfo_ismapped():
            self._warning_frame.pack(fill=tk.X, padx=16, pady=(0, 4),
                                     after=self._warning_frame.master.winfo_children()[0])
        # Cancel previous auto-dismiss
        if self._warning_dismiss_id:
            try:
                self._window.after_cancel(self._warning_dismiss_id)
            except (tk.TclError, ValueError):
                pass
        # Auto-dismiss after duration
        if duration_ms > 0 and self._window:
            self._warning_dismiss_id = self._window.after(duration_ms, self._dismiss_warning)

    def _dismiss_warning(self) -> None:
        """Hide the warning banner."""
        if self._warning_frame and self._warning_frame.winfo_ismapped():
            self._warning_frame.pack_forget()
        self._warning_dismiss_id = None

    def _toggle_auto_start_click(self) -> None:
        """Handle click on auto-record label to toggle."""
        new_state = not self._auto_start
        self._auto_start = new_state
        self._apply_auto_start()
        if self._on_toggle_auto_start:
            threading.Thread(
                target=self._on_toggle_auto_start, args=(new_state,), daemon=True,
            ).start()

    def _set_elapsed(self, text: str) -> None:
        if self._elapsed_label:
            self._elapsed_label.configure(text=text)
        if self._window and self._is_recording:
            self._window.title(f"Meeting Recorder \u2014 {text}")

    def _update_disk_space(self) -> None:
        if not self._disk_label or not self._is_recording:
            return
        try:
            home = Path.home()
            usage = shutil.disk_usage(home)
            free_gb = usage.free / (1024 ** 3)
            if free_gb < 1.0:
                free_mb = usage.free / (1024 ** 2)
                self._disk_label.configure(
                    text=f"\u26a0 {free_mb:.0f} MB free", fg=RED_DOT)
            elif free_gb < 5.0:
                self._disk_label.configure(
                    text=f"{free_gb:.1f} GB free", fg=AMBER)
            else:
                self._disk_label.configure(
                    text=f"{free_gb:.0f} GB free", fg=TEXT_DIM)
        except Exception:
            pass

    def _set_mute_display(self, is_muted: bool) -> None:
        if self._mute_btn:
            label = "  Muted  " if is_muted else "  Unmuted  "
            fg = MUTED_COLOR if is_muted else UNMUTED_COLOR
            self._mute_btn.configure(text=label, fg=fg)

    def _set_transcript(self, text: str) -> None:
        if self._transcript_label:
            self._transcript_label.configure(text=f'\u201c{text}\u201d')

    def _set_screen_preview(self, frame) -> None:
        if self._preview_label is None:
            return
        try:
            import cv2
            from PIL import Image, ImageTk

            h, w = frame.shape[:2]
            target_w = 480
            target_h = int(h * target_w / w)
            thumb = cv2.resize(frame, (target_w, target_h))
            rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            photo = ImageTk.PhotoImage(img)
            self._preview_label.configure(image=photo, text="")
            self._preview_photo = photo
        except Exception:
            pass

    # ------------------------------------------------------------------
    # VU meters
    # ------------------------------------------------------------------

    def _draw_vu_meters(
        self, app_frac, mic_frac, app_db, mic_db,
        app_peak=0.0, mic_peak=0.0,
    ) -> None:
        if self._app_vu_canvas:
            self._draw_single_vu(self._app_vu_canvas, app_frac, app_peak)
        if self._mic_vu_canvas:
            self._draw_single_vu(self._mic_vu_canvas, mic_frac, mic_peak)
        if self._app_db_label:
            self._app_db_label.configure(text=f"{app_db:.0f} dB")
        if self._mic_db_label:
            self._mic_db_label.configure(text=f"{mic_db:.0f} dB")

    def _draw_single_vu(self, canvas: tk.Canvas, fraction: float, peak: float = 0.0) -> None:
        canvas.delete("vu")
        w = canvas.winfo_width() or 300
        h = canvas.winfo_height() or 16
        bar_w = int(w * fraction)
        if bar_w > 0:
            color = _vu_color(fraction)
            canvas.create_rectangle(0, 0, bar_w, h, fill=color, outline="", tags="vu")
        # Peak hold marker (thin 2px line)
        peak_x = int(w * peak)
        if peak_x > 2:
            peak_color = _vu_color(peak)
            canvas.create_rectangle(
                peak_x - 2, 0, peak_x, h,
                fill=peak_color, outline="", tags="vu",
            )

    # ------------------------------------------------------------------
    # Pulse animation
    # ------------------------------------------------------------------

    def _start_pulse(self) -> None:
        self._dot_visible = True
        self._pulse_tick()

    def _pulse_tick(self) -> None:
        if self._window is None:
            return
        self._dot_visible = not self._dot_visible
        if self._is_recording:
            if self._is_paused:
                color = AMBER if self._dot_visible else "#5a3500"
            else:
                color = RED_DOT if self._dot_visible else RED_DOT_OFF
            if self._status_dot:
                self._status_dot.configure(fg=color)
        try:
            self._pulse_after_id = self._window.after(500, self._pulse_tick)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Recording history
    # ------------------------------------------------------------------

    def _refresh_history(self) -> None:
        """Reload recording history list."""
        if self._history_frame is None:
            return

        # Clear existing
        for widget in self._history_frame.winfo_children():
            widget.destroy()

        if not self._on_list_recent:
            return

        try:
            recordings = self._on_list_recent()
        except Exception:
            recordings = []

        if not recordings:
            tk.Label(
                self._history_frame, text="No recordings yet. Start your first recording!",
                font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR,
            ).pack(pady=20)
            self._update_stats_label(0, 0)
            return

        # Apply filter
        filter_text = ""
        placeholder = "\U0001f50d Filter recordings..."
        if hasattr(self, "_filter_var"):
            raw = self._filter_var.get()
            if raw != placeholder:
                filter_text = raw.strip().lower()

        # Compute stats from metadata
        total_duration = 0.0
        shown = 0
        for rec_path in recordings[:50]:  # Check up to 50
            meta = {}
            try:
                meta_path = rec_path / "metadata.json"
                if meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
            except Exception:
                pass

            # Filter: match against folder name, subject, app name, attendees
            if filter_text:
                searchable = " ".join([
                    rec_path.name.lower(),
                    meta.get("meeting_subject", "").lower(),
                    meta.get("app_name", "").lower(),
                    meta.get("meeting_organizer", "").lower(),
                    " ".join(meta.get("meeting_attendees", [])).lower(),
                ])
                if filter_text not in searchable:
                    continue

            if shown < 20:
                self._build_history_card(rec_path)
                shown += 1
            total_duration += meta.get("duration_seconds", 0)
        self._update_stats_label(len(recordings), total_duration)

    def _update_stats_label(self, count: int, total_seconds: float) -> None:
        """Update the stats label next to 'Recent Recordings'."""
        if not hasattr(self, '_stats_label') or not self._stats_label:
            return
        if count == 0:
            self._stats_label.configure(text="")
            return
        hours = total_seconds / 3600
        if hours >= 1:
            time_str = f"{hours:.1f}h"
        else:
            time_str = f"{total_seconds / 60:.0f}m"
        self._stats_label.configure(text=f"{count} recordings  \u2022  {time_str} total")

    def _build_history_card(self, rec_path: Path) -> None:
        """Build a single recording card in the history list."""
        card = tk.Frame(self._history_frame, bg=BG_CARD, cursor="hand2")
        card.pack(fill=tk.X, pady=2, ipady=6)

        # Parse name for display
        name = rec_path.name
        # Extract date and subject from "2026-03-06_14-30-00_Subject_App"
        date_str = name[:10] if len(name) >= 10 else name
        time_str = name[11:16].replace("-", ":") if len(name) >= 16 else ""

        # Try to get metadata for more info
        duration_str = ""
        subject = ""
        app_label = ""
        status = ""
        status_icon = "\U0001f4c1"  # folder icon
        try:
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                dur = meta.get("duration_seconds", 0)
                if dur > 0:
                    duration_str = _format_duration_short(dur)
                subject = meta.get("meeting_subject", "")
                app_label = meta.get("app_name", "")
                status = meta.get("status", "")
                if status == "completed":
                    status_icon = "\u2705"  # checkmark
                elif status == "error":
                    status_icon = "\u26a0"  # warning
                elif status == "processing":
                    status_icon = "\u23f3"  # hourglass
                elif status == "recording":
                    status_icon = "\u26a0"  # interrupted
        except Exception:
            pass

        # Layout: info on left, duration badge on right
        left = tk.Frame(card, bg=BG_CARD)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 4), pady=2)

        title = subject if subject else name[20:].replace("_", " ").strip() if len(name) > 20 else "Recording"
        if len(title) > 40:
            title = title[:37] + "..."

        tk.Label(
            left, text=f"{status_icon}  {title}",
            font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=BG_CARD,
            anchor=tk.W,
        ).pack(fill=tk.X)

        detail_parts = [f"{date_str}  {time_str}"]
        if app_label:
            detail_parts.append(app_label)
        # Show status for non-completed recordings
        if status and status not in ("completed", ""):
            detail_parts.append(status.upper())
        detail = "  \u2022  ".join(detail_parts)
        detail_color = AMBER if status == "error" else TEXT_DIM
        tk.Label(
            left, text=detail,
            font=("Segoe UI", 8), fg=detail_color, bg=BG_CARD,
            anchor=tk.W,
        ).pack(fill=tk.X)

        # Duration badge on right side
        if duration_str:
            tk.Label(
                card, text=duration_str,
                font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_CARD,
                anchor=tk.E,
            ).pack(side=tk.RIGHT, padx=(0, 12))

        # Hover effects
        def _enter(e):
            card.configure(bg=BG_CARD_HOVER)
            for child in card.winfo_children():
                child.configure(bg=BG_CARD_HOVER)
                for grandchild in child.winfo_children():
                    grandchild.configure(bg=BG_CARD_HOVER)

        def _leave(e):
            card.configure(bg=BG_CARD)
            for child in card.winfo_children():
                child.configure(bg=BG_CARD)
                for grandchild in child.winfo_children():
                    grandchild.configure(bg=BG_CARD)

        def _click(e, path=rec_path):
            self._show_recording_detail(path)

        def _right_click(e, path=rec_path):
            menu = tk.Menu(card, tearoff=0, bg=BG_CARD, fg=TEXT_COLOR,
                           activebackground=BG_CONTROLS, activeforeground=TEXT_BRIGHT,
                           font=("Segoe UI", 9))
            menu.add_command(label="Open Details", command=lambda: self._show_recording_detail(path))
            menu.add_command(label="Open in Explorer",
                             command=lambda: threading.Thread(
                                 target=lambda: open_in_explorer(str(path)), daemon=True).start())
            menu.add_separator()
            menu.add_command(label="Copy Path",
                             command=lambda: (
                                 self._window.clipboard_clear(),
                                 self._window.clipboard_append(str(path)),
                             ) if self._window else None)
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()

        card.bind("<Enter>", _enter)
        card.bind("<Leave>", _leave)
        card.bind("<Button-1>", _click)
        card.bind("<Button-3>", _right_click)
        # Propagate scroll + click to children so they work when hovering over labels
        mw_handler = getattr(self, "_history_mousewheel_handler", None)
        for child in card.winfo_children():
            child.bind("<Button-1>", _click)
            child.bind("<Button-3>", _right_click)
            if mw_handler:
                child.bind("<MouseWheel>", mw_handler)
            for grandchild in child.winfo_children():
                grandchild.bind("<Button-1>", _click)
                grandchild.bind("<Button-3>", _right_click)
                if mw_handler:
                    grandchild.bind("<MouseWheel>", mw_handler)

    # ------------------------------------------------------------------
    # Recording detail view
    # ------------------------------------------------------------------

    def _show_recording_detail(self, rec_path: Path) -> None:
        """Show a detail view for a recording, replacing the idle view."""
        if self._is_recording or self._window is None:
            return

        # Hide idle frame
        if self._idle_frame:
            self._idle_frame.pack_forget()

        # Build detail frame
        if self._detail_frame:
            self._detail_frame.destroy()
        self._detail_frame = tk.Frame(self._window, bg=BG_COLOR)
        self._detail_frame.pack(fill=tk.BOTH, expand=True)

        self._build_detail_content(self._detail_frame, rec_path)

    def _close_detail(self) -> None:
        """Close the detail view and return to idle/history."""
        if self._detail_frame:
            self._detail_frame.destroy()
            self._detail_frame = None
        if self._idle_frame:
            self._idle_frame.pack(fill=tk.BOTH, expand=True)

    def _build_detail_content(self, parent: tk.Frame, rec_path: Path) -> None:
        """Build the content of the recording detail view."""
        name = rec_path.name

        # Load metadata
        meta = {}
        try:
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
        except Exception:
            pass

        # --- Top bar: Back + Open Folder ---
        top_bar = tk.Frame(parent, bg=BG_HEADER, height=40)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        back_btn = tk.Label(
            top_bar, text="\u2190  Back", font=("Segoe UI", 9),
            fg=TEXT_COLOR, bg=BG_HEADER, cursor="hand2", padx=8,
        )
        back_btn.pack(side=tk.LEFT, padx=8, pady=6)
        back_btn.bind("<Button-1>", lambda e: self._close_detail())
        back_btn.bind("<Enter>", lambda e: back_btn.configure(fg=TEXT_BRIGHT))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(fg=TEXT_COLOR))

        open_btn = tk.Label(
            top_bar, text="\U0001f4c2  Open Folder", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
        )
        open_btn.pack(side=tk.RIGHT, padx=8, pady=6)
        open_btn.bind("<Button-1>", lambda e, p=rec_path: (
            threading.Thread(target=lambda: open_in_explorer(str(p)), daemon=True).start()
        ))
        open_btn.bind("<Enter>", lambda e: open_btn.configure(fg=TEXT_COLOR))
        open_btn.bind("<Leave>", lambda e: open_btn.configure(fg=TEXT_DIM))

        # Google Drive button (if uploaded)
        drive_id = meta.get("google_drive_folder_id", "")
        if drive_id:
            drive_url = f"https://drive.google.com/drive/folders/{drive_id}"
            drive_btn = tk.Label(
                top_bar, text="\u2601  Drive", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
            )
            drive_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)
            drive_btn.bind("<Button-1>", lambda e: (
                threading.Thread(
                    target=lambda: __import__("webbrowser").open(drive_url),
                    daemon=True,
                ).start()
            ))
            drive_btn.bind("<Enter>", lambda e: drive_btn.configure(fg=TEXT_COLOR))
            drive_btn.bind("<Leave>", lambda e: drive_btn.configure(fg=TEXT_DIM))

        # Copy transcript button
        copy_btn = tk.Label(
            top_bar, text="\U0001f4cb  Copy", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
        )
        copy_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)

        def _copy_transcript():
            text = self._read_file(rec_path / "transcript.txt")
            if not text:
                text = self._read_file(rec_path / "summary.md")
            if text and self._window:
                self._window.clipboard_clear()
                self._window.clipboard_append(text)
                copy_btn.configure(text="\u2713  Copied!", fg=GREEN)
                self._window.after(1500, lambda: copy_btn.configure(
                    text="\U0001f4cb  Copy", fg=TEXT_DIM))

        copy_btn.bind("<Button-1>", lambda e: _copy_transcript())
        copy_btn.bind("<Enter>", lambda e: copy_btn.configure(fg=TEXT_COLOR))
        copy_btn.bind("<Leave>", lambda e: copy_btn.configure(fg=TEXT_DIM))

        # Delete button
        del_btn = tk.Label(
            top_bar, text="\U0001f5d1  Delete", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
        )
        del_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)

        def _delete_recording():
            self._confirm_delete(rec_path, del_btn)

        del_btn.bind("<Button-1>", lambda e: _delete_recording())
        del_btn.bind("<Enter>", lambda e: del_btn.configure(fg="#e74c3c"))
        del_btn.bind("<Leave>", lambda e: del_btn.configure(fg=TEXT_DIM))

        # --- Title ---
        subject = meta.get("meeting_subject", "")
        title = subject if subject else name[20:].replace("_", " ").strip() if len(name) > 20 else "Recording"
        app_name = meta.get("app_name", "")

        tk.Label(
            parent, text=title, font=("Segoe UI", 12, "bold"),
            fg=TEXT_BRIGHT, bg=BG_COLOR, anchor=tk.W,
        ).pack(fill=tk.X, padx=20, pady=(12, 2))

        # --- Info line ---
        date_str = name[:10] if len(name) >= 10 else name
        time_str = name[11:19].replace("-", ":") if len(name) >= 19 else ""
        dur = meta.get("duration_seconds", 0)
        dur_str = _format_duration_short(dur) if dur > 0 else ""
        status = meta.get("status", "")
        speakers = meta.get("speaker_count", 0)

        # Compute folder size
        folder_size_str = ""
        try:
            total_bytes = sum(f.stat().st_size for f in rec_path.rglob("*") if f.is_file())
            if total_bytes >= 1024 ** 3:
                folder_size_str = f"{total_bytes / (1024 ** 3):.1f} GB"
            elif total_bytes >= 1024 ** 2:
                folder_size_str = f"{total_bytes / (1024 ** 2):.0f} MB"
            elif total_bytes >= 1024:
                folder_size_str = f"{total_bytes / 1024:.0f} KB"
        except Exception:
            pass

        info_parts = [f"{date_str} {time_str}"]
        if dur_str:
            info_parts.append(dur_str)
        if folder_size_str:
            info_parts.append(folder_size_str)
        if app_name:
            info_parts.append(app_name)
        if speakers > 0:
            info_parts.append(f"{speakers} speaker{'s' if speakers != 1 else ''}")
        if status:
            info_parts.append(status)

        tk.Label(
            parent, text="  \u2022  ".join(info_parts),
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.W,
        ).pack(fill=tk.X, padx=20, pady=(0, 4))

        # --- Attendees ---
        attendees = meta.get("meeting_attendees", [])
        if attendees:
            att_text = ", ".join(attendees[:8])
            if len(attendees) > 8:
                att_text += f" +{len(attendees) - 8} more"
            tk.Label(
                parent, text=f"Attendees: {att_text}",
                font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.W,
                wraplength=480,
            ).pack(fill=tk.X, padx=20, pady=(0, 4))

        # Divider
        tk.Frame(parent, bg=BG_HEADER, height=1).pack(fill=tk.X, padx=16, pady=4)

        # --- Tabs: Transcript / Summary / Details ---
        tab_frame = tk.Frame(parent, bg=BG_COLOR)
        tab_frame.pack(fill=tk.X, padx=16)

        # Content area (scrollable text)
        content_frame = tk.Frame(parent, bg=BG_COLOR)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 8))

        text_widget = tk.Text(
            content_frame, wrap=tk.WORD,
            bg=BG_PANEL, fg=TEXT_COLOR,
            font=("Segoe UI", 9),
            bd=0, highlightthickness=1, highlightcolor=BG_CONTROLS,
            insertbackground=TEXT_COLOR,
            selectbackground=BG_CONTROLS,
            padx=12, pady=8,
        )
        scrollbar = tk.Scrollbar(content_frame, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Read available content
        transcript_text = self._read_file(rec_path / "transcript.txt")
        summary_text = self._read_file(rec_path / "summary.md")
        details_text = self._build_details_text(rec_path, meta)

        # Tab buttons (N-tab system)
        tab_buttons: list[tk.Label] = []

        def _show_tab(content: str, active_btn: tk.Label):
            text_widget.configure(state=tk.NORMAL)
            text_widget.delete("1.0", tk.END)
            text_widget.insert("1.0", content or "(not available)")
            text_widget.configure(state=tk.DISABLED)
            for btn in tab_buttons:
                if btn is active_btn:
                    btn.configure(fg=TEXT_BRIGHT, bg=BG_CONTROLS)
                else:
                    btn.configure(fg=TEXT_DIM, bg=BG_COLOR)

        transcript_btn = tk.Label(
            tab_frame, text="  Transcript  ", font=("Segoe UI", 9, "bold"),
            fg=TEXT_BRIGHT, bg=BG_CONTROLS, cursor="hand2", padx=6, pady=3,
        )
        transcript_btn.pack(side=tk.LEFT, padx=(0, 4))
        tab_buttons.append(transcript_btn)

        summary_btn = tk.Label(
            tab_frame, text="  Summary  ", font=("Segoe UI", 9, "bold"),
            fg=TEXT_DIM, bg=BG_COLOR, cursor="hand2", padx=6, pady=3,
        )
        summary_btn.pack(side=tk.LEFT, padx=(0, 4))
        tab_buttons.append(summary_btn)

        details_btn = tk.Label(
            tab_frame, text="  Details  ", font=("Segoe UI", 9, "bold"),
            fg=TEXT_DIM, bg=BG_COLOR, cursor="hand2", padx=6, pady=3,
        )
        details_btn.pack(side=tk.LEFT, padx=(0, 4))
        tab_buttons.append(details_btn)

        transcript_btn.bind("<Button-1>", lambda e: _show_tab(transcript_text, transcript_btn))
        summary_btn.bind("<Button-1>", lambda e: _show_tab(summary_text, summary_btn))
        details_btn.bind("<Button-1>", lambda e: _show_tab(details_text, details_btn))

        # Show transcript by default, fall back to summary
        if transcript_text:
            _show_tab(transcript_text, transcript_btn)
        elif summary_text:
            _show_tab(summary_text, summary_btn)
        else:
            _show_tab("No transcript or summary available yet.", transcript_btn)

    @staticmethod
    def _build_details_text(rec_path: Path, meta: dict) -> str:
        """Build a formatted text summary of recording details."""
        lines: list[str] = []

        # --- Files ---
        lines.append("FILES")
        lines.append("-" * 40)
        file_checks = [
            ("app_audio.wav", "App Audio"),
            ("mic_audio.wav", "Microphone"),
            ("mixed.wav", "Mixed Audio"),
            ("screen.mp4", "Screen Recording"),
            ("transcript.json", "Transcript (JSON)"),
            ("transcript.txt", "Transcript (Text)"),
            ("summary.md", "Summary"),
        ]
        for fname, label in file_checks:
            fpath = rec_path / fname
            if fpath.exists():
                try:
                    size_b = fpath.stat().st_size
                    if size_b >= 1024 ** 3:
                        size_str = f"{size_b / (1024 ** 3):.1f} GB"
                    elif size_b >= 1024 ** 2:
                        size_str = f"{size_b / (1024 ** 2):.1f} MB"
                    elif size_b >= 1024:
                        size_str = f"{size_b / 1024:.0f} KB"
                    else:
                        size_str = f"{size_b} B"
                except Exception:
                    size_str = "?"
                lines.append(f"  \u2713  {label:<22} {size_str}")
            else:
                lines.append(f"  \u2717  {label:<22} (missing)")
        lines.append("")

        # --- Processing ---
        lines.append("PROCESSING")
        lines.append("-" * 40)
        backend = meta.get("transcription_backend", "")
        if backend:
            lines.append(f"  Transcription backend:  {backend}")
        status = meta.get("status", "")
        if status:
            lines.append(f"  Status:                 {status}")
        seg_count = meta.get("segment_count", 0)
        if seg_count:
            lines.append(f"  Transcript segments:    {seg_count}")
        spk_count = meta.get("speaker_count", 0)
        if spk_count:
            lines.append(f"  Speakers detected:      {spk_count}")
        if meta.get("has_summary"):
            provider = meta.get("summary_provider", "")
            model = meta.get("summary_model", "")
            summary_info = provider
            if model:
                summary_info += f" ({model})" if provider else model
            if summary_info:
                lines.append(f"  Summary provider:       {summary_info}")
        error = meta.get("error_message", "")
        if error:
            lines.append(f"  Error:                  {error}")
        lines.append("")

        # --- Speaker Map ---
        speaker_map = meta.get("speaker_map", {})
        if speaker_map:
            lines.append("SPEAKER MAP")
            lines.append("-" * 40)
            method = meta.get("speaker_map_method", "")
            confidence = meta.get("speaker_map_confidence", "")
            if method or confidence:
                parts = []
                if method:
                    parts.append(f"method: {method}")
                if confidence:
                    parts.append(f"confidence: {confidence}")
                lines.append(f"  ({', '.join(parts)})")
            for spk_id, spk_name in speaker_map.items():
                lines.append(f"  {spk_id:<12} \u2192  {spk_name}")
            lines.append("")

        # --- Google Drive ---
        drive_id = meta.get("google_drive_folder_id", "")
        if drive_id:
            lines.append("GOOGLE DRIVE")
            lines.append("-" * 40)
            lines.append(f"  Folder ID:  {drive_id}")
            lines.append(f"  Link:       https://drive.google.com/drive/folders/{drive_id}")
            lines.append("")

        # --- Calendar ---
        organizer = meta.get("meeting_organizer", "")
        location = meta.get("meeting_location", "")
        if organizer or location:
            lines.append("CALENDAR")
            lines.append("-" * 40)
            if organizer:
                lines.append(f"  Organizer:  {organizer}")
            if location:
                lines.append(f"  Location:   {location}")
            lines.append("")

        # --- Technical ---
        lines.append("TECHNICAL")
        lines.append("-" * 40)
        sr = meta.get("sample_rate", 0)
        if sr:
            lines.append(f"  Sample rate:   {sr} Hz")
        ch = meta.get("channels", 0)
        if ch:
            lines.append(f"  Channels:      {ch}")
        lang = meta.get("language", "")
        if lang:
            lines.append(f"  Language:      {lang}")
        pid = meta.get("app_pid", 0)
        if pid:
            lines.append(f"  App PID:       {pid}")
        start = meta.get("start_time", "")
        if start:
            lines.append(f"  Start:         {start}")
        end = meta.get("end_time", "")
        if end:
            lines.append(f"  End:           {end}")

        return "\n".join(lines)

    def _confirm_delete(self, rec_path: Path, trigger_btn: tk.Label) -> None:
        """Show inline delete confirmation."""
        if not self._window:
            return
        trigger_btn.configure(text="Click again to confirm", fg="#e74c3c")

        def _do_delete(e):
            try:
                import shutil as _shutil
                _shutil.rmtree(rec_path)
            except Exception:
                logger.exception("Failed to delete %s", rec_path)
                return
            self._close_detail()
            self._refresh_history()

        # Rebind to actually delete on second click
        trigger_btn.unbind("<Button-1>")
        trigger_btn.bind("<Button-1>", _do_delete)
        # Auto-reset after 3 seconds
        self._window.after(3000, lambda: (
            trigger_btn.configure(text="\U0001f5d1  Delete", fg=TEXT_DIM),
            trigger_btn.unbind("<Button-1>"),
            trigger_btn.bind("<Button-1>", lambda e: self._confirm_delete(rec_path, trigger_btn)),
        ))

    @staticmethod
    def _read_file(path: Path) -> str:
        """Read a text file, returning empty string on failure."""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return ""

    _GEOMETRY_FILE = Path.home() / ".meeting_recorder" / "window_geometry.txt"

    def _save_geometry(self, window=None) -> None:
        """Save window geometry to disk."""
        w = window or self._window
        if w is None:
            return
        try:
            geo = w.geometry()
            self._GEOMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._GEOMETRY_FILE.write_text(geo, encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def _load_geometry(cls) -> str:
        """Load saved geometry, or return empty string."""
        try:
            if cls._GEOMETRY_FILE.exists():
                geo = cls._GEOMETRY_FILE.read_text(encoding="utf-8").strip()
                # Basic validation: WxH+X+Y or WxH-X-Y patterns
                if "x" in geo and ("+" in geo or "-" in geo):
                    return geo
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Window picker
    # ------------------------------------------------------------------

    def pick_window_for_recording(self, windows: list) -> tuple | None:
        """Show a window picker dialog and return the chosen entry.

        Called from a background thread (e.g. _record_window). Schedules the
        dialog on the Tk thread and blocks until the user picks or cancels.

        Args:
            windows: list of (hwnd, title, pid, proc_name) tuples from
                     list_visible_windows().

        Returns:
            (hwnd, title, pid, proc_name) or None if cancelled.
        """
        if not self._window or not windows:
            return None

        result: list = [None]
        done = threading.Event()

        def _show():
            picker = tk.Toplevel(self._window)
            picker.title("Record Window")
            picker.configure(bg=BG_COLOR)
            picker.attributes("-topmost", True)
            picker.geometry("500x400")
            picker.resizable(False, False)
            picker.grab_set()  # Modal

            tk.Label(
                picker, text="Select a window to record:",
                font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
            ).pack(padx=12, pady=(10, 4), anchor=tk.W)

            list_frame = tk.Frame(picker, bg=BG_COLOR)
            list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

            scrollbar = tk.Scrollbar(list_frame)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            listbox = tk.Listbox(
                list_frame, yscrollcommand=scrollbar.set,
                bg="#0d0d1a", fg=TEXT_COLOR, selectbackground=BG_CONTROLS,
                selectforeground=TEXT_COLOR, activestyle="none",
                font=("Segoe UI", 9), bd=0,
                highlightthickness=1, highlightcolor=BG_CONTROLS,
            )
            listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.configure(command=listbox.yview)

            for _hwnd, title, _pid, proc_name in windows:
                label = f"  {title} \u2014 {proc_name}" if proc_name != "unknown" else f"  {title}"
                listbox.insert(tk.END, label)

            def _confirm():
                sel = listbox.curselection()
                if not sel:
                    return
                result[0] = windows[sel[0]]
                picker.destroy()
                done.set()

            def _cancel():
                picker.destroy()
                done.set()

            listbox.bind("<Double-Button-1>", lambda e: _confirm())
            picker.protocol("WM_DELETE_WINDOW", _cancel)

            btn_frame = tk.Frame(picker, bg=BG_COLOR)
            btn_frame.pack(fill=tk.X, padx=12, pady=10)

            sel_btn = tk.Label(
                btn_frame, text=" Record This Window ",
                font=("Segoe UI", 9, "bold"), fg=TEXT_BRIGHT, bg=BG_CONTROLS,
                cursor="hand2", padx=8, pady=4,
            )
            sel_btn.pack(side=tk.LEFT)
            sel_btn.bind("<Button-1>", lambda e: _confirm())

            cancel_btn = tk.Label(
                btn_frame, text=" Cancel ",
                font=("Segoe UI", 9), fg=TEXT_DIM, bg=BUTTON_BG,
                cursor="hand2", padx=8, pady=4,
            )
            cancel_btn.pack(side=tk.LEFT, padx=8)
            cancel_btn.bind("<Button-1>", lambda e: _cancel())

        try:
            self._window.after(0, _show)
        except tk.TclError:
            return None

        done.wait()  # Block the calling thread until picker closes
        return result[0]

    def _open_window_picker(self) -> None:
        if not self._on_list_windows or not self._on_pick_window or not self._window:
            return

        windows = self._on_list_windows()
        if not windows:
            return

        picker = tk.Toplevel(self._window)
        picker.title("Pick Capture Window")
        picker.configure(bg=BG_COLOR)
        picker.attributes("-topmost", True)
        picker.geometry("420x320")
        picker.resizable(False, False)

        tk.Label(
            picker, text="Select the window to capture:",
            font=("Segoe UI", 9), fg=TEXT_COLOR, bg=BG_COLOR,
        ).pack(padx=12, pady=(10, 4), anchor=tk.W)

        list_frame = tk.Frame(picker, bg=BG_COLOR)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set,
            bg="#0d0d1a", fg=TEXT_COLOR, selectbackground=BG_CONTROLS,
            selectforeground=TEXT_COLOR, activestyle="none",
            font=("Segoe UI", 9), bd=0,
            highlightthickness=1, highlightcolor=BG_CONTROLS,
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=listbox.yview)

        for _hwnd, title in windows:
            listbox.insert(tk.END, f"  {title}")

        def _confirm():
            sel = listbox.curselection()
            if not sel:
                return
            chosen_hwnd, _ = windows[sel[0]]
            picker.destroy()
            self._on_pick_window(chosen_hwnd)

        listbox.bind("<Double-Button-1>", lambda e: _confirm())

        btn_frame = tk.Frame(picker, bg=BG_COLOR)
        btn_frame.pack(fill=tk.X, padx=12, pady=10)

        sel_btn = tk.Label(
            btn_frame, text=" Capture This Window ",
            font=("Segoe UI", 9, "bold"), fg=TEXT_BRIGHT, bg=BG_CONTROLS,
            cursor="hand2", padx=8, pady=4,
        )
        sel_btn.pack(side=tk.LEFT)
        sel_btn.bind("<Button-1>", lambda e: _confirm())

        cancel_btn = tk.Label(
            btn_frame, text=" Cancel ",
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BUTTON_BG,
            cursor="hand2", padx=8, pady=4,
        )
        cancel_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn.bind("<Button-1>", lambda e: picker.destroy())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_escape(self) -> None:
        """Handle Escape key: close detail view, or hide window."""
        if self._detail_frame:
            self._close_detail()
        else:
            self.hide()

    def _fire(self, callback) -> None:
        """Fire a callback in a background thread."""
        if callback:
            threading.Thread(target=callback, daemon=True).start()
