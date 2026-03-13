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
from meeting_recorder.ui.notification_center import NotificationStore, NotificationWindow
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
        on_reprocess: Optional[Callable] = None,
        on_reprocess_all_failed: Optional[Callable] = None,
        on_import_audio: Optional[Callable] = None,
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
        self._on_reprocess = on_reprocess
        self._on_reprocess_all_failed = on_reprocess_all_failed
        self._on_import_audio = on_import_audio
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
        self._current_detail_path: Optional[Path] = None
        self._help_overlay: Optional[tk.Frame] = None
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

        # Notification center
        self.notification_store = NotificationStore()
        self._notification_badge: Optional[tk.Label] = None

        # Keyboard navigation for history
        self._history_card_paths: list[Path] = []
        self._selected_card_idx: int = -1

        # Bulk selection mode
        self._bulk_mode: bool = False
        self._bulk_selected: set[Path] = set()
        self._bulk_bar: Optional[tk.Frame] = None
        self._bulk_toggle_btn: Optional[tk.Label] = None

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
        self.notification_store.add("warn", message, source="health")
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._display_warning, message, duration_ms)
            self._window.after(0, self._update_notification_badge)
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
        self._window.bind("<Up>", lambda e: self._nav_history(-1))
        self._window.bind("<Down>", lambda e: self._nav_history(1))
        self._window.bind("<Return>", lambda e: self._open_selected_card())
        self._window.bind("<Left>", lambda e: self._navigate_detail(-1))
        self._window.bind("<Right>", lambda e: self._navigate_detail(1))
        self._window.bind("<Control-question>", lambda e: self._show_hotkey_help())
        self._window.bind("<F1>", lambda e: self._show_hotkey_help())

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

        # Notification bell (right side, next to gear)
        self._notification_badge = tk.Label(
            header, text="\U0001f514", font=("Segoe UI", 12),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        self._notification_badge.pack(side=tk.RIGHT, padx=(0, 4))
        self._notification_badge.bind("<Button-1>", lambda e: self._show_notifications())
        self._notification_badge.bind("<Enter>", lambda e: self._notification_badge.configure(fg=TEXT_COLOR))
        self._notification_badge.bind("<Leave>", lambda e: self._update_notification_badge())

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

        # Disk space indicator in idle view
        try:
            base = self.config.output_dir if hasattr(self, "config") else None
            if base is None:
                from meeting_recorder.config import Config
                base = Config.load().output_dir
            base.mkdir(parents=True, exist_ok=True)
            free_gb = shutil.disk_usage(base).free / (1024 ** 3)
            # ~150 MB/hour for audio + screen capture
            est_hours = free_gb * 1024 / 150
            if free_gb < 1.0:
                disk_color = RED_DOT
                disk_text = f"\u26a0 Low disk: {free_gb:.1f} GB free (~{est_hours:.0f}h recording)"
            elif free_gb < 5.0:
                disk_color = AMBER
                disk_text = f"{free_gb:.1f} GB free (~{est_hours:.0f}h recording)"
            else:
                disk_color = TEXT_DIM
                disk_text = f"{free_gb:.0f} GB free (~{est_hours:.0f}h recording)"
            tk.Label(
                parent, text=disk_text, font=("Segoe UI", 8),
                fg=disk_color, bg=BG_COLOR,
            ).pack(pady=(0, 2))
        except Exception:
            pass

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
            ("\U0001f4e5 Import Audio...", self._import_audio_dialog),
            ("\U0001f50d Search", self._on_search),
            ("\U0001f4c2 Open Folder", self._on_open_recordings),
            ("\U0001f4e6 Export All", self._export_transcripts),
            ("\U0001f4ca Stats", self._show_stats),
            ("\U0001f464 Profiles", self._show_voice_profiles),
            ("\U0001f4c5 Calendar", self._show_calendar),
            ("\U0001f9ea Diagnostics", self._show_diagnostics),
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

        # Bulk select toggle
        self._bulk_toggle_btn = tk.Label(
            section_row, text="  Select  ", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2",
        )
        self._bulk_toggle_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._bulk_toggle_btn.bind("<Button-1>", lambda e: self._toggle_bulk_mode())
        self._bulk_toggle_btn.bind(
            "<Enter>", lambda e: self._bulk_toggle_btn.configure(fg=TEXT_BRIGHT, bg=BUTTON_HOVER))
        self._bulk_toggle_btn.bind(
            "<Leave>", lambda e: self._bulk_toggle_btn.configure(
                fg=TEXT_BRIGHT if self._bulk_mode else TEXT_DIM,
                bg=BLUE_DARK if self._bulk_mode else BUTTON_BG))

        # Re-process failed link (shown dynamically when failures exist)
        self._reprocess_failed_label = tk.Label(
            section_row, text="", font=("Segoe UI", 8),
            fg=AMBER, bg=BG_COLOR, cursor="hand2",
        )
        if self._on_reprocess_all_failed:
            self._reprocess_failed_label.bind(
                "<Button-1>", lambda e: self._fire(self._on_reprocess_all_failed))
            self._reprocess_failed_label.bind(
                "<Enter>", lambda e: self._reprocess_failed_label.configure(fg=TEXT_BRIGHT))
            self._reprocess_failed_label.bind(
                "<Leave>", lambda e: self._reprocess_failed_label.configure(fg=AMBER))

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
            "Ctrl+F Search  |  Ctrl+, Settings  |  F1 Help  |  Esc Hide"
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

    def _show_warning_banner(self, message: str, duration_ms: int = 5000) -> None:
        """Show a warning message — works in both idle and recording views."""
        self.show_warning(message, duration_ms)

    def _toggle_auto_start_click(self) -> None:
        """Handle click on auto-record label to toggle."""
        new_state = not self._auto_start
        self._auto_start = new_state
        self._apply_auto_start()
        if self._on_toggle_auto_start:
            self._fire(lambda: self._on_toggle_auto_start(new_state))

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
            if free_gb < 0.1:
                # Critical: auto-stop recording to prevent data loss
                self._disk_label.configure(
                    text="\u26a0 DISK FULL — stopping", fg=RED_DOT)
                logger.error("Disk space critically low (%.0f MB) — auto-stopping recording",
                             usage.free / (1024 ** 2))
                self._fire(self._on_stop)
                return
            elif free_gb < 1.0:
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
            # Keep old photo ref alive until new one is assigned to prevent
            # GC flicker; set image only (not text) to avoid double redraw
            self._preview_photo = photo
            self._preview_label.configure(image=photo)
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

        self._history_card_paths = []
        self._selected_card_idx = -1

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
        failed_count = 0
        quality_scores: list[int] = []
        shown = 0

        # Sort pinned recordings to the top
        pinned: list[Path] = []
        unpinned: list[Path] = []
        for rec_path in recordings[:50]:
            meta = {}
            try:
                meta_path = rec_path / "metadata.json"
                if meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
            except Exception:
                pass
            if meta.get("pinned"):
                pinned.append(rec_path)
            else:
                unpinned.append(rec_path)
        sorted_recordings = pinned + unpinned

        for rec_path in sorted_recordings:
            meta = {}
            try:
                meta_path = rec_path / "metadata.json"
                if meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
            except Exception:
                pass

            if meta.get("status") == "error":
                failed_count += 1

            qs = meta.get("quality_scores", {})
            if qs and qs.get("overall_score") is not None:
                quality_scores.append(qs["overall_score"])

            # Filter: match against folder name, subject, app name, attendees
            if filter_text:
                searchable = " ".join([
                    rec_path.name.lower(),
                    meta.get("meeting_subject", "").lower(),
                    meta.get("app_name", "").lower(),
                    meta.get("meeting_organizer", "").lower(),
                    " ".join(meta.get("meeting_attendees", [])).lower(),
                    " ".join(meta.get("tags", [])).lower(),
                ])
                if filter_text not in searchable:
                    continue

            if shown < 20:
                self._build_history_card(rec_path)
                self._history_card_paths.append(rec_path)
                shown += 1
            total_duration += meta.get("duration_seconds", 0)

        avg_quality = round(sum(quality_scores) / len(quality_scores)) if quality_scores else None
        self._update_stats_label(len(recordings), total_duration, failed_count, avg_quality)

    def _update_stats_label(self, count: int, total_seconds: float,
                           failed: int = 0, avg_quality: int | None = None) -> None:
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
        text = f"{count} recordings  \u2022  {time_str} total"
        if avg_quality is not None:
            text += f"  \u2022  Avg quality: {avg_quality}"
        if failed > 0:
            text += f"  \u2022  {failed} failed"
        self._stats_label.configure(text=text)

        # Show/hide "Re-process failed" link
        if hasattr(self, '_reprocess_failed_label') and self._reprocess_failed_label:
            if failed > 0 and self._on_reprocess_all_failed:
                self._reprocess_failed_label.configure(
                    text=f"\u21bb Re-process {failed} failed",
                )
                self._reprocess_failed_label.pack(side=tk.RIGHT, padx=(8, 0))
            else:
                self._reprocess_failed_label.pack_forget()

    def _build_history_card(self, rec_path: Path) -> None:
        """Build a single recording card in the history list."""
        is_selected = rec_path in self._bulk_selected
        card_bg = BLUE_DARK if (self._bulk_mode and is_selected) else BG_CARD
        card = tk.Frame(self._history_frame, bg=card_bg, cursor="hand2")
        card.pack(fill=tk.X, pady=2, ipady=6)

        # Bulk selection checkbox indicator
        if self._bulk_mode:
            check_text = "\u2611" if is_selected else "\u2610"
            check_lbl = tk.Label(
                card, text=check_text, font=("Segoe UI", 12),
                fg=BLUE_ACCENT if is_selected else TEXT_DIM, bg=card_bg,
            )
            check_lbl.pack(side=tk.LEFT, padx=(8, 0))

        # Parse name for display
        name = rec_path.name
        # Extract date and subject from "2026-03-06_14-30-00_Subject_App"
        date_str = name[:10] if len(name) >= 10 else name
        time_str = name[11:16].replace("-", ":") if len(name) >= 16 else ""

        # Try to get metadata for more info
        meta = {}
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

        # Thumbnail (if available)
        thumb_path = rec_path / "thumbnail.jpg"
        if thumb_path.exists():
            try:
                from PIL import Image, ImageTk
                img = Image.open(thumb_path)
                # Scale to 64px wide
                tw = 64
                th = int(img.height * tw / img.width) if img.width > 0 else 36
                img = img.resize((tw, th))
                photo = ImageTk.PhotoImage(img)
                thumb_label = tk.Label(card, image=photo, bg=BG_CARD, bd=0)
                thumb_label.image = photo  # prevent GC
                thumb_label.pack(side=tk.LEFT, padx=(8, 0), pady=2)
            except Exception:
                pass

        # Layout: info on left, duration badge on right
        left = tk.Frame(card, bg=BG_CARD)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 4), pady=2)

        title = subject if subject else name[20:].replace("_", " ").strip() if len(name) > 20 else "Recording"
        if len(title) > 40:
            title = title[:37] + "..."

        is_pinned = meta.get("pinned", False)
        pin_prefix = "\U0001f4cc " if is_pinned else ""
        tk.Label(
            left, text=f"{pin_prefix}{status_icon}  {title}",
            font=("Segoe UI", 9, "bold"), fg=TEXT_COLOR, bg=card_bg,
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

        # Transcript preview (first ~80 chars of transcript.txt)
        preview = ""
        try:
            txt_path = rec_path / "transcript.txt"
            if txt_path.exists():
                raw = txt_path.read_text(encoding="utf-8")[:200]
                # Strip speaker labels and timestamps, get just text
                preview = " ".join(raw.split())[:80]
                if len(preview) >= 80:
                    preview = preview[:77] + "..."
        except Exception:
            pass
        if preview:
            tk.Label(
                left, text=preview,
                font=("Segoe UI", 8), fg="#607080", bg=BG_CARD,
                anchor=tk.W,
            ).pack(fill=tk.X)

        # Tag pills on cards
        card_tags = meta.get("tags", [])
        if card_tags:
            tag_row = tk.Frame(left, bg=BG_CARD)
            tag_row.pack(fill=tk.X, pady=(1, 0))
            for tag in card_tags[:5]:  # max 5 visible on card
                tk.Label(
                    tag_row, text=f" {tag} ", font=("Segoe UI", 7),
                    fg=TEXT_DIM, bg=BG_CONTROLS,
                ).pack(side=tk.LEFT, padx=(0, 3))

        # Quality indicator + duration badge on right side
        quality = meta.get("quality_scores", {})
        q_score = quality.get("overall_score") if quality else None
        if q_score is not None:
            q_color = GREEN if q_score >= 75 else AMBER if q_score >= 50 else RED_DOT
            tk.Label(
                card, text=f"{q_score}",
                font=("Segoe UI", 8), fg=q_color, bg=BG_CARD,
                anchor=tk.E,
            ).pack(side=tk.RIGHT, padx=(0, 4))

        if duration_str:
            tk.Label(
                card, text=duration_str,
                font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_CARD,
                anchor=tk.E,
            ).pack(side=tk.RIGHT, padx=(0, 12))

        # Hover effects
        base_bg = card_bg

        def _enter(e, bg=BG_CARD_HOVER):
            card.configure(bg=bg)
            for child in card.winfo_children():
                child.configure(bg=bg)
                for grandchild in child.winfo_children():
                    grandchild.configure(bg=bg)

        def _leave(e, bg=base_bg):
            card.configure(bg=bg)
            for child in card.winfo_children():
                child.configure(bg=bg)
                for grandchild in child.winfo_children():
                    grandchild.configure(bg=bg)

        def _click(e, path=rec_path):
            if self._bulk_mode:
                self._toggle_bulk_select(path)
            else:
                self._show_recording_detail(path)

        def _right_click(e, path=rec_path):
            menu = tk.Menu(card, tearoff=0, bg=BG_CARD, fg=TEXT_COLOR,
                           activebackground=BG_CONTROLS, activeforeground=TEXT_BRIGHT,
                           font=("Segoe UI", 9))
            menu.add_command(label="Open Details", command=lambda: self._show_recording_detail(path))
            # Pin / Unpin
            pin_label = "Unpin" if is_pinned else "Pin to Top"
            menu.add_command(label=pin_label, command=lambda: self._toggle_pin(path))
            menu.add_command(label="Open in Explorer",
                             command=lambda: threading.Thread(
                                 target=lambda: open_in_explorer(str(path)), daemon=True).start())
            if self._on_reprocess:
                menu.add_command(label="Re-process",
                                 command=lambda: self._fire(
                                     lambda: self._on_reprocess(path)))
            # Copy transcript to clipboard
            def _copy_single_transcript(p=path):
                txt = self._read_file(p / "transcript.txt")
                if not txt:
                    txt = self._read_file(p / "summary.md")
                if txt and self._window:
                    self._window.clipboard_clear()
                    self._window.clipboard_append(txt)
            menu.add_command(label="Copy Transcript", command=_copy_single_transcript)
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
        # Propagate hover, scroll + click to children so they work when hovering over labels
        mw_handler = getattr(self, "_history_mousewheel_handler", None)
        for child in card.winfo_children():
            child.bind("<Enter>", _enter)
            child.bind("<Leave>", _leave)
            child.bind("<Button-1>", _click)
            child.bind("<Button-3>", _right_click)
            if mw_handler:
                child.bind("<MouseWheel>", mw_handler)
            for grandchild in child.winfo_children():
                grandchild.bind("<Enter>", _enter)
                grandchild.bind("<Leave>", _leave)
                grandchild.bind("<Button-1>", _click)
                grandchild.bind("<Button-3>", _right_click)
                if mw_handler:
                    grandchild.bind("<MouseWheel>", mw_handler)

    # ------------------------------------------------------------------
    # History keyboard navigation
    # ------------------------------------------------------------------

    def _nav_history(self, delta: int) -> None:
        """Move selection up or down in history list."""
        if self._is_recording or not self._history_card_paths:
            return
        if self._detail_frame and self._detail_frame.winfo_viewable():
            return  # Don't navigate while viewing a detail
        n = len(self._history_card_paths)
        if self._selected_card_idx < 0:
            new_idx = 0 if delta > 0 else n - 1
        else:
            new_idx = max(0, min(n - 1, self._selected_card_idx + delta))
        self._select_card(new_idx)

    def _select_card(self, idx: int) -> None:
        """Visually select a history card by index."""
        if not self._history_frame:
            return
        cards = [w for w in self._history_frame.winfo_children()
                 if isinstance(w, tk.Frame)]
        if not cards or idx < 0 or idx >= len(cards):
            return
        # Deselect old
        if 0 <= self._selected_card_idx < len(cards):
            old = cards[self._selected_card_idx]
            old.configure(bg=BG_CARD)
            for child in old.winfo_children():
                child.configure(bg=BG_CARD)
                for gc in child.winfo_children():
                    gc.configure(bg=BG_CARD)
        # Select new
        self._selected_card_idx = idx
        card = cards[idx]
        card.configure(bg=BG_CARD_HOVER)
        for child in card.winfo_children():
            child.configure(bg=BG_CARD_HOVER)
            for gc in child.winfo_children():
                gc.configure(bg=BG_CARD_HOVER)
        # Scroll to keep selected card visible
        self._scroll_to_card(card)

    def _scroll_to_card(self, card: tk.Frame) -> None:
        """Scroll the history canvas so the given card is visible."""
        canvas = getattr(self, "_history_canvas", None)
        if not canvas or not self._history_frame:
            return
        self._history_frame.update_idletasks()
        card_y = card.winfo_y()
        card_h = card.winfo_height()
        canvas_h = canvas.winfo_height()
        # Get current scroll position in pixels
        bbox = canvas.bbox("all")
        if not bbox:
            return
        total_h = bbox[3] - bbox[1]
        if total_h <= canvas_h:
            return  # Everything fits, no scrolling needed
        # Calculate desired scroll fraction
        top = card_y / total_h
        bottom = (card_y + card_h) / total_h
        view_top, view_bottom = canvas.yview()
        if top < view_top:
            canvas.yview_moveto(top)
        elif bottom > view_bottom:
            canvas.yview_moveto(bottom - (canvas_h / total_h))

    def _open_selected_card(self) -> None:
        """Open the detail view for the currently selected history card."""
        if (self._selected_card_idx >= 0
                and self._selected_card_idx < len(self._history_card_paths)):
            self._show_recording_detail(
                self._history_card_paths[self._selected_card_idx])

    # ------------------------------------------------------------------
    # Pin/unpin recordings
    # ------------------------------------------------------------------

    def _toggle_pin(self, rec_path: Path) -> None:
        """Toggle the pinned state of a recording."""
        try:
            meta_path = rec_path / "metadata.json"
            meta = {}
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            meta["pinned"] = not meta.get("pinned", False)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to toggle pin for %s", rec_path)
        self._refresh_history()

    # ------------------------------------------------------------------
    # Bulk selection mode
    # ------------------------------------------------------------------

    def _toggle_bulk_mode(self) -> None:
        """Toggle bulk selection mode on/off."""
        self._bulk_mode = not self._bulk_mode
        if not self._bulk_mode:
            self._bulk_selected.clear()
            self._hide_bulk_bar()
        # Update toggle button appearance
        if self._bulk_toggle_btn:
            if self._bulk_mode:
                self._bulk_toggle_btn.configure(
                    text="  Cancel  ", fg=TEXT_BRIGHT, bg=BLUE_DARK)
            else:
                self._bulk_toggle_btn.configure(
                    text="  Select  ", fg=TEXT_DIM, bg=BUTTON_BG)
        self._refresh_history()

    def _toggle_bulk_select(self, path: Path) -> None:
        """Toggle selection of a recording path in bulk mode."""
        if path in self._bulk_selected:
            self._bulk_selected.discard(path)
        else:
            self._bulk_selected.add(path)
        if self._bulk_selected:
            self._show_bulk_bar()
        else:
            self._hide_bulk_bar()
        self._refresh_history()

    def _show_bulk_bar(self) -> None:
        """Show the bulk action bar at the bottom of the window."""
        if self._bulk_bar is not None:
            self._update_bulk_bar()
            return
        if not self._window:
            return
        self._bulk_bar = tk.Frame(self._window, bg=BG_CONTROLS, height=40)
        self._bulk_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self._bulk_bar.pack_propagate(False)
        self._update_bulk_bar()

    def _update_bulk_bar(self) -> None:
        """Refresh the bulk action bar contents."""
        if not self._bulk_bar:
            return
        for w in self._bulk_bar.winfo_children():
            w.destroy()
        count = len(self._bulk_selected)
        tk.Label(
            self._bulk_bar, text=f"{count} selected", font=("Segoe UI", 9, "bold"),
            fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(side=tk.LEFT, padx=(12, 8), pady=6)

        for text, cmd in [
            ("Delete", self._bulk_delete),
            ("Export", self._bulk_export),
            ("Re-process", self._bulk_reprocess),
            ("Select All", self._bulk_select_all),
            ("Deselect All", self._bulk_deselect_all),
        ]:
            btn = tk.Label(
                self._bulk_bar, text=f"  {text}  ", font=("Segoe UI", 8),
                fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2", padx=4,
            )
            btn.pack(side=tk.LEFT, padx=2, pady=6)
            btn.bind("<Button-1>", lambda e, c=cmd: c())
            btn.bind("<Enter>", lambda e, b=btn: b.configure(fg=TEXT_BRIGHT, bg=BUTTON_HOVER))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(fg=TEXT_DIM, bg=BUTTON_BG))

    def _hide_bulk_bar(self) -> None:
        """Remove the bulk action bar."""
        if self._bulk_bar:
            self._bulk_bar.destroy()
            self._bulk_bar = None

    def _bulk_select_all(self) -> None:
        """Select all visible recordings."""
        self._bulk_selected = set(self._history_card_paths)
        self._show_bulk_bar()
        self._refresh_history()

    def _bulk_deselect_all(self) -> None:
        """Deselect all recordings."""
        self._bulk_selected.clear()
        self._hide_bulk_bar()
        self._refresh_history()

    def _bulk_delete(self) -> None:
        """Delete all selected recordings after confirmation."""
        count = len(self._bulk_selected)
        if count == 0:
            return
        # Use a simple confirmation dialog
        confirm = tk.Toplevel(self._window)
        confirm.title("Confirm Delete")
        confirm.geometry("350x120")
        confirm.configure(bg=BG_COLOR)
        confirm.resizable(False, False)
        confirm.attributes("-topmost", True)
        confirm.transient(self._window)
        confirm.grab_set()

        tk.Label(
            confirm, text=f"Delete {count} recording{'s' if count != 1 else ''}?",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_COLOR,
        ).pack(pady=(16, 4))
        tk.Label(
            confirm, text="This cannot be undone.",
            font=("Segoe UI", 9), fg=AMBER, bg=BG_COLOR,
        ).pack(pady=(0, 12))

        btn_row = tk.Frame(confirm, bg=BG_COLOR)
        btn_row.pack(pady=4)

        def _do_delete():
            confirm.destroy()
            deleted = 0
            for path in list(self._bulk_selected):
                try:
                    shutil.rmtree(path)
                    deleted += 1
                except Exception:
                    logger.exception("Failed to delete %s", path)
            self._bulk_selected.clear()
            self._bulk_mode = False
            self._hide_bulk_bar()
            if self._bulk_toggle_btn:
                self._bulk_toggle_btn.configure(
                    text="  Select  ", fg=TEXT_DIM, bg=BUTTON_BG)
            self._refresh_history()
            self.add_notification("info", f"Deleted {deleted} recording(s)", source="bulk")

        cancel_btn = tk.Label(
            btn_row, text="  Cancel  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2", padx=12, pady=4,
        )
        cancel_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn.bind("<Button-1>", lambda e: confirm.destroy())

        delete_btn = tk.Label(
            btn_row, text="  Delete  ", font=("Segoe UI", 9, "bold"),
            fg=TEXT_BRIGHT, bg="#5c1a1a", cursor="hand2", padx=12, pady=4,
        )
        delete_btn.pack(side=tk.LEFT, padx=8)
        delete_btn.bind("<Button-1>", lambda e: _do_delete())

    def _bulk_export(self) -> None:
        """Export transcripts from all selected recordings to a folder."""
        if not self._bulk_selected:
            return
        from tkinter import filedialog
        dest_dir = filedialog.askdirectory(
            parent=self._window, title="Export Transcripts To...")
        if not dest_dir:
            return
        dest = Path(dest_dir)
        exported = 0
        for path in sorted(self._bulk_selected):
            for fname in ("transcript.txt", "summary.md"):
                src = path / fname
                if src.exists():
                    try:
                        target = dest / f"{path.name}_{fname}"
                        shutil.copy2(src, target)
                        exported += 1
                    except Exception:
                        logger.exception("Failed to export %s", src)
        self.add_notification(
            "success", f"Exported {exported} file(s) to {dest.name}", source="bulk")

    def _bulk_reprocess(self) -> None:
        """Re-process all selected recordings."""
        if not self._bulk_selected or not self._on_reprocess:
            return
        paths = sorted(self._bulk_selected)
        count = len(paths)
        self._bulk_selected.clear()
        self._bulk_mode = False
        self._hide_bulk_bar()
        if self._bulk_toggle_btn:
            self._bulk_toggle_btn.configure(
                text="  Select  ", fg=TEXT_DIM, bg=BUTTON_BG)
        self._refresh_history()
        self.add_notification(
            "info", f"Re-processing {count} recording(s)...", source="bulk")

        def _reprocess_batch():
            for i, path in enumerate(paths, 1):
                self.update_status_bar(f"Re-processing {i}/{count}: {path.name}")
                self._on_reprocess(path)
                # Wait briefly for the post thread to start and finish
                import time
                time.sleep(0.5)

        threading.Thread(target=_reprocess_batch, daemon=True).start()

    # ------------------------------------------------------------------
    # Recording detail view
    # ------------------------------------------------------------------

    def _show_recording_detail(self, rec_path: Path) -> None:
        """Show a detail view for a recording, replacing the idle view."""
        if self._is_recording or self._window is None:
            return

        self._current_detail_path = rec_path

        # Hide idle frame
        if self._idle_frame:
            self._idle_frame.pack_forget()

        # Build detail frame
        if self._detail_frame:
            self._detail_frame.destroy()
        self._detail_frame = tk.Frame(self._window, bg=BG_COLOR)
        self._detail_frame.pack(fill=tk.BOTH, expand=True)

        self._build_detail_content(self._detail_frame, rec_path)

    def _navigate_detail(self, direction: int) -> None:
        """Navigate to the next (+1) or previous (-1) recording in history."""
        if not self._current_detail_path or not self._history_card_paths:
            return
        try:
            idx = self._history_card_paths.index(self._current_detail_path)
        except ValueError:
            return
        new_idx = idx + direction
        if 0 <= new_idx < len(self._history_card_paths):
            self._show_recording_detail(self._history_card_paths[new_idx])

    def _close_detail(self) -> None:
        """Close the detail view and return to idle/history."""
        if self._detail_frame:
            self._detail_frame.destroy()
            self._detail_frame = None
        self._current_detail_path = None
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

        # Prev/Next navigation
        nav_frame = tk.Frame(top_bar, bg=BG_HEADER)
        nav_frame.pack(side=tk.LEFT, padx=4, pady=6)
        # Determine if prev/next exist
        _has_prev = False
        _has_next = False
        try:
            _idx = self._history_card_paths.index(rec_path)
            _has_prev = _idx > 0
            _has_next = _idx < len(self._history_card_paths) - 1
        except (ValueError, AttributeError):
            pass

        prev_btn = tk.Label(
            nav_frame, text="\u25c0", font=("Segoe UI", 9),
            fg=TEXT_DIM if _has_prev else BG_HEADER, bg=BG_HEADER,
            cursor="hand2" if _has_prev else "", padx=4,
        )
        prev_btn.pack(side=tk.LEFT)
        if _has_prev:
            prev_btn.bind("<Button-1>", lambda e: self._navigate_detail(-1))
            prev_btn.bind("<Enter>", lambda e: prev_btn.configure(fg=TEXT_COLOR))
            prev_btn.bind("<Leave>", lambda e: prev_btn.configure(fg=TEXT_DIM))

        next_btn = tk.Label(
            nav_frame, text="\u25b6", font=("Segoe UI", 9),
            fg=TEXT_DIM if _has_next else BG_HEADER, bg=BG_HEADER,
            cursor="hand2" if _has_next else "", padx=4,
        )
        next_btn.pack(side=tk.LEFT)
        if _has_next:
            next_btn.bind("<Button-1>", lambda e: self._navigate_detail(1))
            next_btn.bind("<Enter>", lambda e: next_btn.configure(fg=TEXT_COLOR))
            next_btn.bind("<Leave>", lambda e: next_btn.configure(fg=TEXT_DIM))

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

        # Play audio button (if audio file exists)
        audio_file = None
        for fname in ("mixed.wav", "app_audio.wav", "mic_audio.wav"):
            if (rec_path / fname).exists():
                audio_file = rec_path / fname
                break
        if audio_file:
            play_btn = tk.Label(
                top_bar, text="\u25b6  Play", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
            )
            play_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)
            play_btn.bind("<Button-1>", lambda e, f=audio_file: (
                threading.Thread(target=lambda: os.startfile(str(f)), daemon=True).start()
            ))
            play_btn.bind("<Enter>", lambda e: play_btn.configure(fg=TEXT_COLOR))
            play_btn.bind("<Leave>", lambda e: play_btn.configure(fg=TEXT_DIM))

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
            elif self._window:
                copy_btn.configure(text="No transcript yet", fg=AMBER)
                self._window.after(2000, lambda: copy_btn.configure(
                    text="\U0001f4cb  Copy", fg=TEXT_DIM))

        copy_btn.bind("<Button-1>", lambda e: _copy_transcript())
        copy_btn.bind("<Enter>", lambda e: copy_btn.configure(fg=TEXT_COLOR))
        copy_btn.bind("<Leave>", lambda e: copy_btn.configure(fg=TEXT_DIM))

        # Re-process button (only when not currently recording)
        if self._on_reprocess and not self._is_recording:
            reprocess_btn = tk.Label(
                top_bar, text="\u21bb  Re-process", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
            )
            reprocess_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)

            def _do_reprocess():
                reprocess_btn.configure(text="\u21bb  Processing...", fg=AMBER)
                self._fire(lambda: self._on_reprocess(rec_path))

            reprocess_btn.bind("<Button-1>", lambda e: _do_reprocess())
            reprocess_btn.bind("<Enter>", lambda e: reprocess_btn.configure(fg=TEXT_COLOR))
            reprocess_btn.bind("<Leave>", lambda e: reprocess_btn.configure(fg=TEXT_DIM))

        # Share Notes button
        notes_btn = tk.Label(
            top_bar, text="\U0001f4dd  Notes", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
        )
        notes_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)

        def _copy_meeting_notes():
            notes = self._generate_meeting_notes(rec_path, meta)
            if notes and self._window:
                self._window.clipboard_clear()
                self._window.clipboard_append(notes)
                notes_btn.configure(text="\u2713  Copied!", fg=GREEN)
                self._window.after(2000, lambda: notes_btn.configure(
                    text="\U0001f4dd  Notes", fg=TEXT_DIM))

        notes_btn.bind("<Button-1>", lambda e: _copy_meeting_notes())
        notes_btn.bind("<Enter>", lambda e: notes_btn.configure(fg=TEXT_COLOR))
        notes_btn.bind("<Leave>", lambda e: notes_btn.configure(fg=TEXT_DIM))

        # Timeline + Speaker editor buttons (only if transcript.json exists)
        if (rec_path / "transcript.json").exists():
            timeline_btn = tk.Label(
                top_bar, text="\u23e9  Timeline", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
            )
            timeline_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)

            def _open_timeline():
                from meeting_recorder.ui.timeline_view import TimelineWindow
                tw = TimelineWindow(rec_path)
                tw.show(self._window)

            timeline_btn.bind("<Button-1>", lambda e: _open_timeline())
            timeline_btn.bind("<Enter>", lambda e: timeline_btn.configure(fg=TEXT_COLOR))
            timeline_btn.bind("<Leave>", lambda e: timeline_btn.configure(fg=TEXT_DIM))

            speakers_btn = tk.Label(
                top_bar, text="\U0001f465  Speakers", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2", padx=8,
            )
            speakers_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=6)

            def _open_speaker_editor():
                from meeting_recorder.ui.speaker_editor import SpeakerEditorDialog
                def _on_saved():
                    # Refresh the detail view after speaker map changes
                    if self._window:
                        self._window.after(100, lambda: self._open_detail(rec_path))
                dialog = SpeakerEditorDialog(rec_path, on_saved=_on_saved)
                dialog.show(self._window)

            speakers_btn.bind("<Button-1>", lambda e: _open_speaker_editor())
            speakers_btn.bind("<Enter>", lambda e: speakers_btn.configure(fg=TEXT_COLOR))
            speakers_btn.bind("<Leave>", lambda e: speakers_btn.configure(fg=TEXT_DIM))

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

        # --- Error banner (shown for failed recordings) ---
        error_msg = meta.get("error_message", "")
        if status == "error" and error_msg:
            err_frame = tk.Frame(parent, bg="#3d1414")
            err_frame.pack(fill=tk.X, padx=16, pady=(2, 4))
            tk.Label(
                err_frame, text=f"\u2717  Error: {error_msg}",
                font=("Segoe UI", 9), fg="#ff6b6b", bg="#3d1414",
                anchor=tk.W, wraplength=500, justify=tk.LEFT,
            ).pack(fill=tk.X, padx=10, pady=6)

            if self._on_reprocess:
                retry_btn = tk.Label(
                    err_frame, text="  \u21bb Retry  ", font=("Segoe UI", 9, "bold"),
                    fg=TEXT_BRIGHT, bg="#5a2020", cursor="hand2", padx=8, pady=2,
                )
                retry_btn.pack(side=tk.RIGHT, padx=8, pady=4)

                def _retry_processing(btn=retry_btn):
                    btn.configure(text="  Processing...  ", fg=AMBER)
                    self._fire(lambda: self._on_reprocess(rec_path))

                retry_btn.bind("<Button-1>", lambda e: _retry_processing())
                retry_btn.bind("<Enter>", lambda e: retry_btn.configure(bg="#6a2c2c"))
                retry_btn.bind("<Leave>", lambda e: retry_btn.configure(bg="#5a2020"))

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

        # --- Tags ---
        tags_frame = tk.Frame(parent, bg=BG_COLOR)
        tags_frame.pack(fill=tk.X, padx=20, pady=(0, 4))
        self._build_tag_bar(tags_frame, rec_path, meta)

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
        notes_text = self._read_file(rec_path / "notes.md")
        details_text = self._build_details_text(rec_path, meta)

        # Edit mode state
        edit_state = {"active": False, "current_tab": "transcript"}
        edit_bar = tk.Frame(parent, bg=BG_CONTROLS)
        # Not packed until edit mode is entered

        def _enter_edit_mode():
            if edit_state["active"]:
                return
            edit_state["active"] = True
            text_widget.configure(state=tk.NORMAL, bg="#0f1a2e")
            text_widget.focus_set()
            edit_bar.pack(fill=tk.X, padx=16, pady=(0, 2), before=content_frame)
            edit_btn.configure(text="Editing...", fg=AMBER)

        def _save_edit():
            if not edit_state["active"]:
                return
            new_text = text_widget.get("1.0", tk.END).rstrip("\n")
            tab = edit_state["current_tab"]
            if tab == "transcript":
                target = rec_path / "transcript.txt"
            elif tab == "summary":
                target = rec_path / "summary.md"
            elif tab == "notes":
                target = rec_path / "notes.md"
            else:
                _cancel_edit()
                return
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(new_text)
                if tab == "transcript":
                    nonlocal transcript_text
                    transcript_text = new_text
                elif tab == "summary":
                    nonlocal summary_text
                    summary_text = new_text
                elif tab == "notes":
                    nonlocal notes_text
                    notes_text = new_text
                logger.info("Saved edited %s for %s", tab, rec_path.name)
            except Exception:
                logger.exception("Failed to save edited %s", tab)
            _cancel_edit()

        def _cancel_edit():
            edit_state["active"] = False
            text_widget.configure(state=tk.DISABLED, bg=BG_PANEL)
            edit_bar.pack_forget()
            edit_btn.configure(text="\u270e Edit", fg=TEXT_DIM)

        save_btn = tk.Label(
            edit_bar, text=" \u2713 Save ", font=("Segoe UI", 9, "bold"),
            fg=GREEN, bg=BG_CONTROLS, cursor="hand2", padx=8, pady=2,
        )
        save_btn.pack(side=tk.LEFT, padx=(8, 4), pady=4)
        save_btn.bind("<Button-1>", lambda e: _save_edit())

        cancel_edit_btn = tk.Label(
            edit_bar, text=" \u2717 Cancel ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_CONTROLS, cursor="hand2", padx=8, pady=2,
        )
        cancel_edit_btn.pack(side=tk.LEFT, padx=4, pady=4)
        cancel_edit_btn.bind("<Button-1>", lambda e: _cancel_edit())

        tk.Label(
            edit_bar, text="Editing — changes saved to disk",
            font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_CONTROLS,
        ).pack(side=tk.LEFT, padx=8, pady=4)

        # Tab buttons (N-tab system)
        tab_buttons: list[tk.Label] = []

        def _show_tab(content: str, active_btn: tk.Label):
            if edit_state["active"]:
                _cancel_edit()
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

        notes_btn = tk.Label(
            tab_frame, text="  Notes  ", font=("Segoe UI", 9, "bold"),
            fg=TEXT_DIM, bg=BG_COLOR, cursor="hand2", padx=6, pady=3,
        )
        notes_btn.pack(side=tk.LEFT, padx=(0, 4))
        tab_buttons.append(notes_btn)

        def _switch_tab(tab_name, content, btn):
            edit_state["current_tab"] = tab_name
            _show_tab(content, btn)
            # Show/hide edit button based on tab
            if tab_name in ("transcript", "summary", "notes"):
                edit_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=3)
                # Auto-enter edit mode for notes if empty
                if tab_name == "notes" and not content:
                    _enter_edit_mode()
            else:
                edit_btn.pack_forget()

        # Edit button (in tab bar, right side)
        edit_btn = tk.Label(
            tab_frame, text="\u270e Edit", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_COLOR, cursor="hand2", padx=6, pady=3,
        )
        edit_btn.bind("<Button-1>", lambda e: _enter_edit_mode())
        edit_btn.bind("<Enter>", lambda e: edit_btn.configure(fg=TEXT_COLOR))
        edit_btn.bind("<Leave>", lambda e: edit_btn.configure(
            fg=AMBER if edit_state["active"] else TEXT_DIM))
        # Only show edit btn for transcript/summary tabs with content
        if transcript_text:
            edit_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=3)

        transcript_btn.bind("<Button-1>", lambda e: _switch_tab("transcript", transcript_text, transcript_btn))
        summary_btn.bind("<Button-1>", lambda e: _switch_tab("summary", summary_text, summary_btn))
        details_btn.bind("<Button-1>", lambda e: _switch_tab("details", details_text, details_btn))
        notes_btn.bind("<Button-1>", lambda e: _switch_tab("notes", notes_text, notes_btn))

        # --- In-content search bar (hidden by default) ---
        search_frame = tk.Frame(parent, bg=BG_CONTROLS)
        # Don't pack yet — toggled by Ctrl+F
        search_var = tk.StringVar()
        search_match_label = tk.Label(
            search_frame, text="", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_CONTROLS,
        )
        search_current_idx = [0]
        search_match_positions: list[str] = []

        # Configure highlight tag
        text_widget.tag_configure("search_hl", background="#665500", foreground="#ffffff")
        text_widget.tag_configure("search_current", background="#cc9900", foreground="#000000")

        def _do_search(*_args):
            """Highlight all occurrences of the search query."""
            text_widget.tag_remove("search_hl", "1.0", tk.END)
            text_widget.tag_remove("search_current", "1.0", tk.END)
            search_match_positions.clear()
            search_current_idx[0] = 0

            query = search_var.get().strip()
            if not query:
                search_match_label.configure(text="")
                return

            # Find all matches
            start = "1.0"
            while True:
                pos = text_widget.search(query, start, stopindex=tk.END, nocase=True)
                if not pos:
                    break
                end_pos = f"{pos}+{len(query)}c"
                text_widget.tag_add("search_hl", pos, end_pos)
                search_match_positions.append(pos)
                start = end_pos

            count = len(search_match_positions)
            if count > 0:
                search_match_label.configure(text=f"1 of {count}")
                _highlight_current(0)
            else:
                search_match_label.configure(text="No matches")

        def _highlight_current(idx: int):
            """Highlight the current match and scroll to it."""
            text_widget.tag_remove("search_current", "1.0", tk.END)
            if not search_match_positions:
                return
            idx = idx % len(search_match_positions)
            search_current_idx[0] = idx
            pos = search_match_positions[idx]
            end_pos = f"{pos}+{len(search_var.get())}c"
            text_widget.tag_add("search_current", pos, end_pos)
            text_widget.see(pos)
            search_match_label.configure(
                text=f"{idx + 1} of {len(search_match_positions)}"
            )

        def _next_match(*_):
            if search_match_positions:
                _highlight_current(search_current_idx[0] + 1)

        def _prev_match(*_):
            if search_match_positions:
                _highlight_current(search_current_idx[0] - 1)

        def _close_search(*_):
            search_frame.pack_forget()
            text_widget.tag_remove("search_hl", "1.0", tk.END)
            text_widget.tag_remove("search_current", "1.0", tk.END)
            search_match_positions.clear()
            search_match_label.configure(text="")

        def _toggle_search(*_):
            if search_frame.winfo_ismapped():
                _close_search()
            else:
                search_frame.pack(fill=tk.X, padx=16, pady=(0, 2),
                                  before=content_frame)
                search_entry.focus_set()
                search_entry.select_range(0, tk.END)

        search_entry = tk.Entry(
            search_frame, textvariable=search_var,
            font=("Segoe UI", 9), bg=BG_PANEL, fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR, bd=0,
            highlightthickness=0, width=25,
        )
        search_entry.pack(side=tk.LEFT, padx=(8, 4), pady=4, ipady=2)
        search_var.trace_add("write", _do_search)
        search_entry.bind("<Return>", _next_match)
        search_entry.bind("<Shift-Return>", _prev_match)
        search_entry.bind("<Escape>", _close_search)

        for btn_text, btn_cmd in [("\u25b2", _prev_match), ("\u25bc", _next_match)]:
            b = tk.Label(
                search_frame, text=btn_text, font=("Segoe UI", 8),
                fg=TEXT_DIM, bg=BG_CONTROLS, cursor="hand2", padx=4,
            )
            b.pack(side=tk.LEFT, padx=1, pady=4)
            b.bind("<Button-1>", lambda e, c=btn_cmd: c())
            b.bind("<Enter>", lambda e, lb=b: lb.configure(fg=TEXT_COLOR))
            b.bind("<Leave>", lambda e, lb=b: lb.configure(fg=TEXT_DIM))

        search_match_label.pack(side=tk.LEFT, padx=6)

        close_search_btn = tk.Label(
            search_frame, text="\u2715", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_CONTROLS, cursor="hand2", padx=4,
        )
        close_search_btn.pack(side=tk.RIGHT, padx=4, pady=4)
        close_search_btn.bind("<Button-1>", _close_search)
        close_search_btn.bind("<Enter>", lambda e: close_search_btn.configure(fg=TEXT_COLOR))
        close_search_btn.bind("<Leave>", lambda e: close_search_btn.configure(fg=TEXT_DIM))

        # Bind Ctrl+F to toggle search within the detail view
        if self._window:
            # Use a detail-specific binding that's cleaned up when detail closes
            parent.bind_all("<Control-f>", _toggle_search)
            # Clean up the binding when detail is destroyed
            def _on_detail_destroy(e):
                if e.widget is parent:
                    try:
                        parent.unbind_all("<Control-f>")
                        # Re-bind the outer Ctrl+F for search window
                        if self._window:
                            self._window.bind("<Control-f>",
                                              lambda e: self._fire(self._on_search))
                    except tk.TclError:
                        pass
            parent.bind("<Destroy>", _on_detail_destroy)

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

        # --- Speaker Stats ---
        # Calculate per-speaker speaking time from transcript.json
        transcript_json_path = rec_path / "transcript.json"
        speaker_times: dict[str, float] = {}
        if transcript_json_path.exists():
            try:
                import json as _json
                with open(transcript_json_path, "r", encoding="utf-8") as _f:
                    _tdata = _json.load(_f)
                for seg in _tdata.get("segments", []):
                    spk = seg.get("speaker", "Unknown")
                    start_t = seg.get("start", 0.0)
                    end_t = seg.get("end", 0.0)
                    duration = max(0.0, end_t - start_t)
                    speaker_times[spk] = speaker_times.get(spk, 0.0) + duration
            except Exception:
                pass

        if speaker_times:
            total_speaking = sum(speaker_times.values())
            lines.append("SPEAKER STATS")
            lines.append("-" * 40)
            # Sort by speaking time descending
            for spk, secs in sorted(speaker_times.items(), key=lambda x: -x[1]):
                pct = (secs / total_speaking * 100) if total_speaking > 0 else 0
                mins = int(secs // 60)
                remaining_secs = int(secs % 60)
                bar_len = int(pct / 5)  # 20 chars = 100%
                bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
                lines.append(f"  {spk:<16} {mins:2d}:{remaining_secs:02d}  {bar}  {pct:.0f}%")
            lines.append(f"  {'Total':<16} {int(total_speaking // 60):2d}:{int(total_speaking % 60):02d}")
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

        # --- Attendance verification ---
        attendees = meta.get("meeting_attendees", [])
        speaker_map = meta.get("speaker_map", {})
        if attendees and speaker_map:
            lines.append("ATTENDANCE")
            lines.append("-" * 40)
            identified_names = set(v.lower() for v in speaker_map.values())
            # Match attendees to identified speakers
            spoke = []
            silent = []
            for att in attendees:
                # Match by first name, last name, or full name
                att_lower = att.lower()
                att_parts = att_lower.split()
                matched = any(
                    part in name or name in att_lower
                    for part in att_parts
                    for name in identified_names
                )
                if matched:
                    spoke.append(att)
                else:
                    silent.append(att)
            # Unmatched speakers (not in attendee list)
            attendee_lower = " ".join(a.lower() for a in attendees)
            unknown_speakers = [
                name for name in speaker_map.values()
                if not any(p in attendee_lower for p in name.lower().split())
            ]
            for att in spoke:
                lines.append(f"  \u2705  {att}")
            for att in silent:
                lines.append(f"  \u274c  {att}  (didn't speak)")
            for name in unknown_speakers:
                lines.append(f"  \u2753  {name}  (not on invite)")
            if spoke:
                lines.append(f"  Spoke: {len(spoke)}/{len(attendees)}")
            lines.append("")

        # --- Quality ---
        quality = meta.get("quality_scores", {})
        if quality and quality.get("overall_score") is not None:
            from meeting_recorder.storage.quality import quality_label, quality_bar
            lines.append("QUALITY")
            lines.append("-" * 40)
            overall = quality["overall_score"]
            lines.append(f"  Overall:      {quality_bar(overall)}  {overall}/100  {quality_label(overall)}")
            audio_s = quality.get("audio_score")
            if audio_s is not None:
                lines.append(f"  Audio:        {quality_bar(audio_s)}  {audio_s}/100  {quality_label(audio_s)}")
                ad = quality.get("audio_details", {})
                if ad.get("app_rms_db") is not None:
                    lines.append(f"    RMS: {ad['app_rms_db']:.1f} dB  Peak: {ad.get('app_peak_db', 0):.1f} dB  Silence: {ad.get('app_silence_ratio', 0):.0%}")
                elif ad.get("mic_rms_db") is not None:
                    lines.append(f"    RMS: {ad['mic_rms_db']:.1f} dB  Peak: {ad.get('mic_peak_db', 0):.1f} dB  Silence: {ad.get('mic_silence_ratio', 0):.0%}")
            trans_s = quality.get("transcript_score")
            if trans_s is not None:
                lines.append(f"  Transcript:   {quality_bar(trans_s)}  {trans_s}/100  {quality_label(trans_s)}")
                td = quality.get("transcript_details", {})
                if td.get("wpm"):
                    lines.append(f"    {td['word_count']} words  {td['wpm']:.0f} WPM  {td.get('large_gaps', 0)} gaps")
            video_s = quality.get("video_score")
            if video_s is not None:
                lines.append(f"  Video:        {quality_bar(video_s)}  {video_s}/100  {quality_label(video_s)}")
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

    def _build_tag_bar(self, parent: tk.Frame, rec_path: Path, meta: dict) -> None:
        """Build an inline tag display with add/remove."""
        tags = list(meta.get("tags", []))

        def _redraw():
            for w in parent.winfo_children():
                w.destroy()

            for tag in tags:
                pill = tk.Frame(parent, bg=BLUE_ACCENT, padx=1, pady=1)
                pill.pack(side=tk.LEFT, padx=(0, 4), pady=1)
                tk.Label(
                    pill, text=f" {tag} ", font=("Segoe UI", 8),
                    fg=TEXT_BRIGHT, bg=BLUE_ACCENT,
                ).pack(side=tk.LEFT)
                x_btn = tk.Label(
                    pill, text="\u00d7", font=("Segoe UI", 8, "bold"),
                    fg=TEXT_DIM, bg=BLUE_ACCENT, cursor="hand2", padx=2,
                )
                x_btn.pack(side=tk.LEFT)
                x_btn.bind("<Button-1>", lambda e, t=tag: _remove_tag(t))
                x_btn.bind("<Enter>", lambda e, b=x_btn: b.configure(fg=TEXT_BRIGHT))
                x_btn.bind("<Leave>", lambda e, b=x_btn: b.configure(fg=TEXT_DIM))

            add_btn = tk.Label(
                parent, text="+ tag", font=("Segoe UI", 8),
                fg=TEXT_DIM, bg=BG_COLOR, cursor="hand2",
            )
            add_btn.pack(side=tk.LEFT, padx=2)
            add_btn.bind("<Button-1>", lambda e: _add_tag_inline())
            add_btn.bind("<Enter>", lambda e: add_btn.configure(fg=TEXT_COLOR))
            add_btn.bind("<Leave>", lambda e: add_btn.configure(fg=TEXT_DIM))

        def _add_tag_inline():
            # Replace "+ tag" with an entry
            for w in parent.winfo_children():
                if isinstance(w, tk.Label) and w.cget("text") == "+ tag":
                    w.destroy()
                    break

            entry = tk.Entry(
                parent, font=("Segoe UI", 8), width=12,
                bg=BG_PANEL, fg=TEXT_BRIGHT, insertbackground=TEXT_BRIGHT,
                bd=0, highlightthickness=1, highlightcolor=BLUE_ACCENT,
            )
            entry.pack(side=tk.LEFT, padx=2, ipady=1)
            entry.focus_set()

            def _commit(event=None):
                new_tag = entry.get().strip()
                if new_tag and new_tag not in tags:
                    tags.append(new_tag)
                    _save_tags()
                entry.destroy()
                _redraw()

            def _cancel(event=None):
                entry.destroy()
                _redraw()

            entry.bind("<Return>", _commit)
            entry.bind("<Escape>", _cancel)
            entry.bind("<FocusOut>", _commit)

        def _remove_tag(tag: str):
            if tag in tags:
                tags.remove(tag)
                _save_tags()
                _redraw()

        def _save_tags():
            meta["tags"] = tags
            try:
                meta_path = rec_path / "metadata.json"
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
            except Exception:
                logger.exception("Failed to save tags")

        _redraw()

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
        """Load saved geometry, or return empty string.

        Validates that the saved position is at least partially on-screen
        (e.g., after unplugging a monitor). Returns empty string if not.
        """
        try:
            if cls._GEOMETRY_FILE.exists():
                geo = cls._GEOMETRY_FILE.read_text(encoding="utf-8").strip()
                # Basic validation: WxH+X+Y or WxH-X-Y patterns
                if "x" in geo and ("+" in geo or "-" in geo):
                    return cls._validate_geometry_on_screen(geo)
        except Exception:
            pass
        return ""

    @staticmethod
    def _validate_geometry_on_screen(geo: str) -> str:
        """Check if geometry position is on a visible monitor.

        Returns the geometry string if valid, or just the size portion
        (letting the WM place it) if the position is off-screen.
        """
        import re
        m = re.match(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geo)
        if not m:
            return geo
        w, h = int(m.group(1)), int(m.group(2))
        # Strip leading '+' before int() so '+-2000' parses as -2000
        x = int(m.group(3).lstrip("+"))
        y = int(m.group(4).lstrip("+"))

        try:
            import ctypes
            user32 = ctypes.windll.user32
            # Get virtual screen bounds (spans all monitors)
            virt_left = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            virt_top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
            virt_w = user32.GetSystemMetrics(78)      # SM_CXVIRTUALSCREEN
            virt_h = user32.GetSystemMetrics(79)      # SM_CYVIRTUALSCREEN
            virt_right = virt_left + virt_w
            virt_bottom = virt_top + virt_h

            # Check if at least 100px of the window is on-screen
            margin = 100
            if (x + margin > virt_right or x + w < virt_left + margin
                    or y + margin > virt_bottom or y < virt_top):
                # Off-screen: return just the size, let WM place it
                return f"{w}x{h}"
        except Exception:
            pass  # Not on Windows or ctypes unavailable — trust the geometry
        return geo

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

            # Pre-select first item
            if windows:
                listbox.selection_set(0)
                listbox.activate(0)

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

        # Run Win32 enumeration off the Tk thread to avoid UI freeze
        def _do_pick():
            windows = self._on_list_windows()
            if not windows or not self._window:
                return
            try:
                self._window.after(0, lambda: self._show_capture_picker(windows))
            except tk.TclError:
                pass

        threading.Thread(target=_do_pick, daemon=True).start()

    def _show_capture_picker(self, windows: list) -> None:
        if not self._window:
            return
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

        # Pre-select first item
        if windows:
            listbox.selection_set(0)
            listbox.activate(0)

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

    @staticmethod
    def _generate_meeting_notes(rec_path: Path, meta: dict) -> str:
        """Generate formatted meeting notes for sharing."""
        lines: list[str] = []

        # Title
        subject = meta.get("meeting_subject", "")
        name = rec_path.name
        title = subject if subject else (
            name[20:].replace("_", " ").strip() if len(name) > 20 else "Meeting"
        )
        lines.append(f"# {title}")
        lines.append("")

        # Date and duration
        date_str = name[:10] if len(name) >= 10 else ""
        time_str = name[11:19].replace("-", ":") if len(name) >= 19 else ""
        dur = meta.get("duration_seconds", 0)
        info_parts = []
        if date_str:
            info_parts.append(f"**Date:** {date_str}")
        if time_str:
            info_parts.append(f"**Time:** {time_str}")
        if dur > 0:
            m, s = divmod(int(dur), 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}h {m}m" if h else f"{m}m"
            info_parts.append(f"**Duration:** {dur_str}")
        app = meta.get("app_name", "")
        if app:
            info_parts.append(f"**Platform:** {app}")
        if info_parts:
            lines.append(" | ".join(info_parts))
            lines.append("")

        # Attendees
        attendees = meta.get("meeting_attendees", [])
        organizer = meta.get("meeting_organizer", "")
        if organizer or attendees:
            lines.append("## Attendees")
            if organizer:
                lines.append(f"- **{organizer}** (organizer)")
            for att in attendees:
                if att != organizer:
                    lines.append(f"- {att}")
            lines.append("")

        # Summary
        summary_path = rec_path / "summary.md"
        if summary_path.exists():
            try:
                summary = summary_path.read_text(encoding="utf-8").strip()
                lines.append("## Summary")
                lines.append("")
                lines.append(summary)
                lines.append("")
            except Exception:
                pass

        # Footer
        lines.append("---")
        lines.append(f"*Generated by Meeting Recorder — {date_str}*")

        return "\n".join(lines)

    def _show_stats(self) -> None:
        """Open the cross-recording statistics window."""
        if not self._window:
            return
        base = self.config.output_dir if hasattr(self, "config") else None
        if base is None:
            from meeting_recorder.config import Config
            base = Config.load().output_dir
        from meeting_recorder.ui.stats_window import StatsWindow
        if not hasattr(self, "_stats_window"):
            self._stats_window = StatsWindow(base)
        self._stats_window.show(self._window)

    def _show_voice_profiles(self) -> None:
        """Open the voice profiles management window."""
        if not self._window:
            return
        from meeting_recorder.ui.voice_profiles_window import VoiceProfilesWindow
        if not hasattr(self, "_voice_profiles_window"):
            self._voice_profiles_window = VoiceProfilesWindow()
        self._voice_profiles_window.show(self._window)

    def _show_calendar(self) -> None:
        """Open the recording calendar view."""
        if not self._window:
            return
        base = self.config.output_dir if hasattr(self, "config") else None
        if base is None:
            from meeting_recorder.config import Config
            base = Config.load().output_dir
        from meeting_recorder.ui.calendar_view import CalendarWindow

        def _on_date_click(date_str: str):
            """Filter history to the selected date."""
            if hasattr(self, "_filter_var") and self._filter_var:
                self._filter_var.set(date_str)
                self._refresh_history()

        if not hasattr(self, "_calendar_window"):
            self._calendar_window = CalendarWindow(base, on_date_click=_on_date_click)
        self._calendar_window.show(self._window)

    def _import_audio_dialog(self) -> None:
        """Open a file dialog and pass selected audio file to import callback."""
        if not self._window or not self._on_import_audio:
            return
        from tkinter import filedialog
        file_path = filedialog.askopenfilename(
            parent=self._window,
            title="Import Audio File",
            filetypes=[
                ("Audio files", "*.wav *.mp3 *.m4a *.ogg *.flac *.wma *.aac"),
                ("WAV files", "*.wav"),
                ("MP3 files", "*.mp3"),
                ("All files", "*.*"),
            ],
        )
        if file_path:
            threading.Thread(
                target=self._on_import_audio,
                args=(file_path,),
                daemon=True,
            ).start()

    def _show_diagnostics(self) -> None:
        """Open the system diagnostics window."""
        if not self._window:
            return
        from meeting_recorder.ui.diagnostics_window import DiagnosticsWindow
        if not hasattr(self, "_diagnostics_window"):
            self._diagnostics_window = DiagnosticsWindow()
        self._diagnostics_window.show(self._window)

    def _show_notifications(self) -> None:
        """Open the notification center window."""
        if not self._window:
            return
        if not hasattr(self, "_notification_window"):
            self._notification_window = NotificationWindow(self.notification_store)
        self._notification_window.show(self._window)
        self._update_notification_badge()

    def _update_notification_badge(self) -> None:
        """Update the notification bell to show unread count."""
        if not self._notification_badge:
            return
        unread = self.notification_store.unread_count
        if unread > 0:
            self._notification_badge.configure(fg=AMBER)
        else:
            self._notification_badge.configure(fg=TEXT_DIM)

    def add_notification(self, level: str, message: str, source: str = "") -> None:
        """Add a notification and update badge (thread-safe)."""
        self.notification_store.add(level, message, source=source)
        if self._window:
            try:
                self._window.after(0, self._update_notification_badge)
            except tk.TclError:
                pass

    def _export_transcripts(self) -> None:
        """Export all transcripts as a ZIP file."""
        if not self._window or not hasattr(self, "_history_card_paths"):
            return

        from tkinter import filedialog
        import zipfile

        # Gather all recording dirs with transcripts
        base = self.config.output_dir if hasattr(self, "config") else None
        if base is None:
            from meeting_recorder.config import Config
            base = Config.load().output_dir

        if not base.exists():
            return

        recordings = sorted(
            [d for d in base.iterdir() if d.is_dir()],
            key=lambda p: p.name, reverse=True,
        )

        # Count available transcripts
        transcript_paths = []
        for rec in recordings:
            txt = rec / "transcript.txt"
            summary = rec / "summary.md"
            if txt.exists():
                transcript_paths.append((rec.name, txt, summary if summary.exists() else None))
            elif summary.exists():
                transcript_paths.append((rec.name, None, summary))

        if not transcript_paths:
            self._show_warning_banner("No transcripts found to export.", duration_ms=3000)
            return

        dest = filedialog.asksaveasfilename(
            title=f"Export {len(transcript_paths)} Transcripts",
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip"), ("All files", "*.*")],
            initialfile="meeting_transcripts.zip",
            parent=self._window,
        )
        if not dest:
            return

        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, txt_path, sum_path in transcript_paths:
                    if txt_path:
                        zf.write(txt_path, f"{name}/transcript.txt")
                    if sum_path:
                        zf.write(sum_path, f"{name}/summary.md")

            file_count = len(transcript_paths)
            self._show_warning_banner(
                f"Exported {file_count} recording(s) to {Path(dest).name}",
                duration_ms=4000,
            )
            logger.info("Exported %d transcripts to %s", file_count, dest)
        except Exception:
            logger.exception("Failed to export transcripts")
            self._show_warning_banner("Export failed — check logs.", duration_ms=5000)

    def _on_escape(self) -> None:
        """Handle Escape key: close detail view, dismiss help, exit bulk, or hide window."""
        if hasattr(self, "_help_overlay") and self._help_overlay:
            self._help_overlay.destroy()
            self._help_overlay = None
        elif self._bulk_mode:
            self._toggle_bulk_mode()
        elif self._detail_frame:
            self._close_detail()
        else:
            self.hide()

    def _show_hotkey_help(self) -> None:
        """Show a keyboard shortcuts overlay."""
        if not self._window:
            return
        # Toggle: dismiss if already showing
        if hasattr(self, "_help_overlay") and self._help_overlay:
            self._help_overlay.destroy()
            self._help_overlay = None
            return

        overlay = tk.Frame(self._window, bg=BG_PANEL, bd=2, relief=tk.RAISED)
        overlay.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self._help_overlay = overlay

        tk.Label(
            overlay, text="Keyboard Shortcuts", font=("Segoe UI", 12, "bold"),
            fg=TEXT_BRIGHT, bg=BG_PANEL,
        ).pack(padx=20, pady=(12, 8))

        shortcuts = [
            ("Global", [
                (self._hotkey_recording, "Start / Stop recording"),
                (self._hotkey_pause, "Pause / Resume"),
            ]),
            ("Window", [
                ("Ctrl+F", "Search recordings"),
                ("Ctrl+,", "Open settings"),
                ("F5", "Refresh history"),
                ("F1 / Ctrl+?", "This help"),
                ("Escape", "Close / Hide"),
                ("\u2191 / \u2193", "Navigate history"),
                ("Enter", "Open selected recording"),
            ]),
            ("Detail View", [
                ("\u25c0 / \u25b6", "Previous / Next recording"),
                ("Ctrl+F", "Search in transcript"),
                ("Escape", "Close detail / search"),
            ]),
        ]

        for section, bindings in shortcuts:
            tk.Label(
                overlay, text=section, font=("Segoe UI", 9, "bold"),
                fg=AMBER, bg=BG_PANEL, anchor=tk.W,
            ).pack(fill=tk.X, padx=20, pady=(8, 2))
            for key, desc in bindings:
                row = tk.Frame(overlay, bg=BG_PANEL)
                row.pack(fill=tk.X, padx=20, pady=1)
                tk.Label(
                    row, text=key, font=("Consolas", 9), fg=TEXT_BRIGHT,
                    bg=BG_PANEL, width=18, anchor=tk.W,
                ).pack(side=tk.LEFT)
                tk.Label(
                    row, text=desc, font=("Segoe UI", 9), fg=TEXT_COLOR,
                    bg=BG_PANEL, anchor=tk.W,
                ).pack(side=tk.LEFT)

        tk.Label(
            overlay, text="Press Escape or F1 to dismiss",
            font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_PANEL,
        ).pack(pady=(8, 12))

    def _fire(self, callback) -> None:
        """Fire a callback in a background thread."""
        if callback:
            def _safe():
                try:
                    callback()
                except Exception:
                    logger.exception("Callback %s failed", callback)
            threading.Thread(target=_safe, daemon=True).start()
