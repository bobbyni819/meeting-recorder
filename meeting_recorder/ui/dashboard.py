"""Game-Bar-style floating recording dashboard overlay."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional

from meeting_recorder.audio.level_monitor import MIN_DB
from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_CONTROLS,
    TEXT_COLOR, TEXT_DIM,
    RED_DOT, RED_DOT_OFF, AMBER,
    GREEN_VU, YELLOW_VU, RED_VU, VU_BG,
    BUTTON_BG, BUTTON_HOVER, MUTED_COLOR, UNMUTED_COLOR,
    db_to_fraction, vu_color, format_elapsed as _format_elapsed,
)

logger = logging.getLogger(__name__)

# Layout constants
EXPANDED_WIDTH = 380
EXPANDED_HEIGHT = 280
COLLAPSED_WIDTH = 380
COLLAPSED_HEIGHT = 44
PREVIEW_HEIGHT = 115  # 101px thumbnail + 14px padding

# Dashboard uses AMBER as warning color
AMBER_WARNING = AMBER


@dataclass
class DashboardContext:
    """Data passed to the dashboard when recording starts."""
    app_name: str = "Meeting"
    meeting_subject: str = ""
    is_muted: bool = True
    is_process_specific: bool = True
    show_screen_preview: bool = False


class GameBarDashboard:
    """Floating overlay dashboard shown during recording.

    Displays live VU meters, elapsed time, mute state, transcript preview,
    and quick-action buttons. Follows the same overlay pattern as
    LiveTranscriptWindow (overrideredirect, topmost, alpha, draggable).
    """

    def __init__(
        self,
        on_stop: Optional[Callable[[], None]] = None,
        on_toggle_pause: Optional[Callable[[], None]] = None,
        on_toggle_mute: Optional[Callable[[], None]] = None,
        on_resume_auto_sync: Optional[Callable[[], None]] = None,
        on_open_recordings: Optional[Callable[[], None]] = None,
        on_open_settings: Optional[Callable[[], None]] = None,
        on_list_windows: Optional[Callable[[], list]] = None,
        on_pick_window: Optional[Callable[[int], None]] = None,
        on_toggle_audio_mode: Optional[Callable[[], None]] = None,
        opacity: float = 0.92,
        start_collapsed: bool = False,
        show_transcript: bool = True,
        position_x: int = -1,
        position_y: int = -1,
        position: str = "top-right",
    ):
        self._on_stop = on_stop
        self._on_toggle_pause = on_toggle_pause
        self._on_toggle_mute = on_toggle_mute
        self._on_resume_auto_sync = on_resume_auto_sync
        self._on_open_recordings = on_open_recordings
        self._on_open_settings = on_open_settings
        self._on_list_windows = on_list_windows
        self._on_pick_window = on_pick_window
        self._on_toggle_audio_mode = on_toggle_audio_mode
        self._opacity = opacity
        self._start_collapsed = start_collapsed
        self._show_transcript = show_transcript
        self._position_x = position_x
        self._position_y = position_y
        self._position = position

        self._window: Optional[tk.Tk] = None
        self._is_visible = False
        self._is_collapsed = start_collapsed
        self._context: Optional[DashboardContext] = None

        # Widget references
        self._red_dot_label: Optional[tk.Label] = None
        self._header_label: Optional[tk.Label] = None
        self._elapsed_label: Optional[tk.Label] = None
        self._app_vu_canvas: Optional[tk.Canvas] = None
        self._mic_vu_canvas: Optional[tk.Canvas] = None
        self._app_db_label: Optional[tk.Label] = None
        self._mic_db_label: Optional[tk.Label] = None
        self._mute_btn: Optional[tk.Label] = None
        self._mute_tooltip: Optional[tk.Toplevel] = None
        self._pause_btn: Optional[tk.Label] = None
        self._transcript_label: Optional[tk.Label] = None
        self._capture_warning_label: Optional[tk.Label] = None
        self._audio_mode_btn: Optional[tk.Label] = None
        self._collapsed_elapsed: Optional[tk.Label] = None
        self._preview_label: Optional[tk.Label] = None
        self._preview_photo = None  # ImageTk.PhotoImage ref (prevent Tk GC)
        self._show_screen_preview: bool = False
        self._ctrl_elapsed_label: Optional[tk.Label] = None
        self._collapsed_dot: Optional[tk.Label] = None
        self._collapsed_status_label: Optional[tk.Label] = None

        # VU state
        self._app_vu_fraction = 0.0
        self._mic_vu_fraction = 0.0

        # Pause state
        self._is_paused = False

        # Pulsing red dot state
        self._dot_visible = True
        self._pulse_after_id: Optional[str] = None

        # Expanded/collapsed frames
        self._expanded_frame: Optional[tk.Frame] = None
        self._collapsed_frame: Optional[tk.Frame] = None

        # Tk thread management
        self._tk_thread: Optional[threading.Thread] = None
        self._tk_ready = threading.Event()

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    @property
    def is_collapsed(self) -> bool:
        return self._is_collapsed

    @property
    def position_xy(self) -> tuple[int, int]:
        """Return the current window position (for persisting)."""
        if self._window is not None:
            try:
                return self._window.winfo_x(), self._window.winfo_y()
            except tk.TclError:
                pass
        return self._position_x, self._position_y

    def show(self, context: Optional[DashboardContext] = None) -> None:
        """Show the dashboard overlay.

        Creates the window in a dedicated thread running mainloop() so that
        tkinter has an event loop for rendering and after() callbacks.
        """
        if context is not None:
            self._context = context

        if self._window is not None:
            # Re-show an existing hidden window
            try:
                self._is_visible = True
                self._window.after(0, self._do_reshow)
                return
            except tk.TclError:
                self._window = None

        # Spin up a dedicated Tk thread
        self._tk_ready.clear()
        self._is_visible = True
        self._tk_thread = threading.Thread(
            target=self._run_tk, name="dashboard-tk", daemon=True,
        )
        self._tk_thread.start()
        self._tk_ready.wait(timeout=5.0)

    def _run_tk(self) -> None:
        """Create the window and run the Tk event loop (dedicated thread)."""
        try:
            self._build_window()
            self._start_pulse()
            self._tk_ready.set()
            self._window.mainloop()
        except Exception:
            logger.exception("Dashboard Tk thread error")
        finally:
            self._window = None
            self._is_visible = False
            self._tk_ready.set()  # unblock show() on error

    def _do_reshow(self) -> None:
        """Deiconify and restart pulse (must be called on Tk thread)."""
        if self._window:
            self._window.deiconify()
            self._start_pulse()

    def hide(self) -> None:
        """Hide the dashboard (does NOT stop recording)."""
        self._is_visible = False
        if self._window is not None:
            try:
                self._window.after(0, self._do_hide)
            except tk.TclError:
                pass

    def _do_hide(self) -> None:
        """Withdraw and stop pulse (must be called on Tk thread)."""
        self._stop_pulse()
        if self._window:
            self._window.withdraw()

    def close(self) -> None:
        """Destroy the dashboard window entirely."""
        self._is_visible = False
        w = self._window
        self._window = None  # prevent further after() calls from other threads
        if w is not None:
            try:
                w.after(0, w.destroy)
            except tk.TclError:
                pass

    def update_audio_levels(
        self, app_rms_db: float, app_peak_db: float, mic_rms_db: float, mic_peak_db: float
    ) -> None:
        """Update VU meters with new audio levels (thread-safe)."""
        if self._window is None or not self._is_visible or self._is_collapsed:
            return
        app_frac = db_to_fraction(app_rms_db)
        mic_frac = db_to_fraction(mic_rms_db)
        try:
            self._window.after(0, self._draw_vu_meters, app_frac, mic_frac, app_rms_db, mic_rms_db)
        except tk.TclError:
            pass

    def update_elapsed(self, elapsed_seconds: float) -> None:
        """Update the elapsed time display (thread-safe)."""
        if self._window is None or not self._is_visible:
            return
        text = _format_elapsed(elapsed_seconds)
        try:
            self._window.after(0, self._set_elapsed, text)
        except tk.TclError:
            pass

    def update_paused(self, is_paused: bool) -> None:
        """Update the paused state display (thread-safe)."""
        if self._window is None or not self._is_visible:
            return
        self._is_paused = is_paused
        try:
            self._window.after(0, self._set_paused_display, is_paused)
        except tk.TclError:
            pass

    def update_mute_state(self, is_muted: bool) -> None:
        """Update the mute button appearance (thread-safe)."""
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._set_mute_display, is_muted)
        except tk.TclError:
            pass

    def update_capture_mode(self, is_process_specific: bool) -> None:
        """Show or hide the capture mode warning (thread-safe)."""
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._set_capture_warning, is_process_specific)
        except tk.TclError:
            pass

    def update_audio_mode(self, is_desktop: bool) -> None:
        """Update the audio mode toggle button appearance (thread-safe)."""
        if self._window is None or not self._is_visible:
            return
        try:
            self._window.after(0, self._set_audio_mode, is_desktop)
        except tk.TclError:
            pass

    def update_transcript(self, text: str) -> None:
        """Update the transcript preview text (thread-safe)."""
        if self._window is None or not self._is_visible or self._is_collapsed:
            return
        if not self._show_transcript:
            return
        try:
            if len(text) > 200:
                text = "..." + text[-197:]
            self._window.after(0, self._set_transcript, text)
        except tk.TclError:
            pass

    def update_screen_preview(self, frame) -> None:
        """Update the screen preview thumbnail (thread-safe).

        Args:
            frame: BGR numpy array from screen capture, or None.
        """
        if self._window is None or not self._is_visible or self._is_collapsed:
            return
        if not self._show_screen_preview or self._preview_label is None:
            return
        if frame is None:
            return
        try:
            self._window.after(0, lambda f=frame: self._set_screen_preview(f))
        except tk.TclError:
            pass

    def _set_screen_preview(self, frame) -> None:
        """Resize frame to thumbnail and display it (must be called on Tk thread)."""
        if self._preview_label is None:
            return
        try:
            import cv2
            from PIL import Image, ImageTk

            thumb = cv2.resize(frame, (180, 101))
            rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            photo = ImageTk.PhotoImage(img)
            self._preview_photo = photo  # keep ref alive before configure
            self._preview_label.configure(image=photo)
        except Exception:
            logger.debug("Screen preview update failed", exc_info=True)

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        """Create the overlay window and all widgets."""
        self._window = tk.Tk()
        self._window.title("Recording Dashboard")
        self._window.attributes("-topmost", True)
        self._window.attributes("-alpha", self._opacity)
        self._window.overrideredirect(True)
        self._window.configure(bg=BG_COLOR)

        # Position
        self._apply_position()

        # Track screen preview setting from context
        ctx = self._context or DashboardContext()
        self._show_screen_preview = ctx.show_screen_preview

        # Build both frames
        self._expanded_frame = tk.Frame(self._window, bg=BG_COLOR)
        self._collapsed_frame = tk.Frame(self._window, bg=BG_COLOR)

        self._build_expanded(self._expanded_frame)
        self._build_collapsed(self._collapsed_frame)

        # Show appropriate frame
        if self._start_collapsed:
            self._is_collapsed = True
            self._collapsed_frame.pack(fill=tk.BOTH, expand=True)
            self._window.geometry(f"{COLLAPSED_WIDTH}x{COLLAPSED_HEIGHT}")
        else:
            self._is_collapsed = False
            self._expanded_frame.pack(fill=tk.BOTH, expand=True)
            height = EXPANDED_HEIGHT
            if not self._show_transcript:
                height -= 60
            if self._show_screen_preview:
                height += PREVIEW_HEIGHT
            self._window.geometry(f"{EXPANDED_WIDTH}x{height}")

        # Dragging
        self._window.bind("<Button-1>", self._start_drag)
        self._window.bind("<B1-Motion>", self._do_drag)

        # Right-click context menu
        self._window.bind("<Button-3>", self._show_context_menu)

    def _build_expanded(self, parent: tk.Frame) -> None:
        """Build the expanded view widgets."""
        ctx = self._context or DashboardContext()

        # --- Header (36px) ---
        header_frame = tk.Frame(parent, bg=BG_HEADER, height=36)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        self._red_dot_label = tk.Label(
            header_frame, text="\u2b24", font=("Segoe UI", 10),
            fg=RED_DOT, bg=BG_HEADER,
        )
        self._red_dot_label.pack(side=tk.LEFT, padx=(8, 4))

        title = ctx.app_name
        if ctx.meeting_subject:
            title += f" - {ctx.meeting_subject}"
        if len(title) > 35:
            title = title[:32] + "..."
        self._header_label = tk.Label(
            header_frame, text=title, font=("Segoe UI", 10, "bold"),
            fg=TEXT_COLOR, bg=BG_HEADER, anchor=tk.W,
        )
        self._header_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Close button (hide, NOT stop)
        close_btn = tk.Label(
            header_frame, text="\u2715", font=("Segoe UI", 10),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT, padx=(0, 8))
        close_btn.bind("<Button-1>", lambda e: self.hide())

        # Collapse button
        collapse_btn = tk.Label(
            header_frame, text="\u2015", font=("Segoe UI", 10),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        collapse_btn.pack(side=tk.RIGHT, padx=(0, 4))
        collapse_btn.bind("<Button-1>", lambda e: self._toggle_collapse())

        # Capture mode warning (hidden by default, shown if system-wide fallback)
        self._capture_warning_label = tk.Label(
            parent, text="\u26a0 System volume affects recording",
            font=("Segoe UI", 8, "bold"),
            fg=AMBER_WARNING, bg=BG_COLOR, anchor=tk.W,
        )
        if not ctx.is_process_specific:
            self._capture_warning_label.pack(fill=tk.X, padx=10, pady=(2, 0))

        # Recording sub-header
        self._elapsed_label = tk.Label(
            parent, text="Recording 00:00:00", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_COLOR, anchor=tk.W,
        )
        self._elapsed_label.pack(fill=tk.X, padx=10, pady=(2, 4))

        # --- VU Meters ---
        vu_frame = tk.Frame(parent, bg=BG_COLOR)
        vu_frame.pack(fill=tk.X, padx=10, pady=2)

        # App VU
        app_row = tk.Frame(vu_frame, bg=BG_COLOR)
        app_row.pack(fill=tk.X, pady=1)
        tk.Label(app_row, text="App", font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR, width=4, anchor=tk.W).pack(side=tk.LEFT)
        self._app_vu_canvas = tk.Canvas(app_row, width=260, height=14, bg=VU_BG, highlightthickness=0)
        self._app_vu_canvas.pack(side=tk.LEFT, padx=4)
        self._app_db_label = tk.Label(app_row, text=f"{MIN_DB:.0f} dB", font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR, width=7, anchor=tk.E)
        self._app_db_label.pack(side=tk.LEFT)

        # Mic VU
        mic_row = tk.Frame(vu_frame, bg=BG_COLOR)
        mic_row.pack(fill=tk.X, pady=1)
        tk.Label(mic_row, text="Mic", font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR, width=4, anchor=tk.W).pack(side=tk.LEFT)
        self._mic_vu_canvas = tk.Canvas(mic_row, width=260, height=14, bg=VU_BG, highlightthickness=0)
        self._mic_vu_canvas.pack(side=tk.LEFT, padx=4)
        self._mic_db_label = tk.Label(mic_row, text=f"{MIN_DB:.0f} dB", font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR, width=7, anchor=tk.E)
        self._mic_db_label.pack(side=tk.LEFT)

        # --- Controls (40px) ---
        ctrl_frame = tk.Frame(parent, bg=BG_CONTROLS, height=40)
        ctrl_frame.pack(fill=tk.X, pady=(4, 0))
        ctrl_frame.pack_propagate(False)

        stop_btn = tk.Label(
            ctrl_frame, text=" Stop ", font=("Segoe UI", 9, "bold"),
            fg="#ffffff", bg="#c0392b", cursor="hand2", padx=8, pady=4,
        )
        stop_btn.pack(side=tk.LEFT, padx=(10, 2), pady=6)
        stop_btn.bind("<Button-1>", lambda e: self._handle_stop())
        stop_btn.bind("<Enter>", lambda e: stop_btn.configure(bg="#e74c3c"))
        stop_btn.bind("<Leave>", lambda e: stop_btn.configure(bg="#c0392b"))

        self._pause_btn = tk.Label(
            ctrl_frame, text=" \u23f8 ", font=("Segoe UI", 9),
            fg="#ffffff", bg="#7f8c8d", cursor="hand2", padx=6, pady=4,
        )
        self._pause_btn.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        self._pause_btn.bind("<Button-1>", lambda e: self._handle_pause_toggle())
        self._pause_btn.bind("<Enter>", lambda e: self._pause_btn.configure(
            bg="#ffa500" if self._is_paused else "#95a5a6"))
        self._pause_btn.bind("<Leave>", lambda e: self._pause_btn.configure(
            bg="#e67e22" if self._is_paused else "#7f8c8d"))

        mute_text = "Muted" if (ctx.is_muted) else "Unmuted"
        mute_fg = MUTED_COLOR if ctx.is_muted else UNMUTED_COLOR
        self._mute_btn = tk.Label(
            ctrl_frame, text=f" {mute_text} ", font=("Segoe UI", 9),
            fg=mute_fg, bg=BG_CONTROLS, cursor="hand2",
            relief=tk.GROOVE, padx=6, pady=2,
        )
        self._mute_btn.pack(side=tk.LEFT, padx=4, pady=6)
        self._mute_btn.bind("<Button-1>", lambda e: self._handle_mute_toggle())
        # Right-click hands mute control back to auto-detection after a
        # manual (left-click) correction made the override sticky.
        self._mute_btn.bind("<Button-3>", self._on_mute_right_click)
        self._mute_btn.bind("<Enter>", lambda e: self._show_mute_tooltip())
        self._mute_btn.bind("<Leave>", lambda e: self._hide_mute_tooltip())

        # Window picker button (only when screen recording is active)
        if self._on_list_windows and self._on_pick_window:
            pick_btn = tk.Label(
                ctrl_frame, text=" \u29bf Window ", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_CONTROLS, cursor="hand2",
                relief=tk.GROOVE, padx=6, pady=2,
            )
            pick_btn.pack(side=tk.LEFT, padx=4, pady=6)
            pick_btn.bind("<Button-1>", lambda e: self._open_window_picker())
            pick_btn.bind("<Enter>", lambda e: pick_btn.configure(fg=TEXT_COLOR))
            pick_btn.bind("<Leave>", lambda e: pick_btn.configure(fg=TEXT_DIM))

        # Audio mode toggle button (app ↔ desktop)
        if self._on_toggle_audio_mode:
            self._audio_mode_btn = tk.Label(
                ctrl_frame, text=" App Audio ", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_CONTROLS, cursor="hand2",
                relief=tk.GROOVE, padx=6, pady=2,
            )
            self._audio_mode_btn.pack(side=tk.LEFT, padx=4, pady=6)
            self._audio_mode_btn.bind("<Button-1>", lambda e: self._on_toggle_audio_mode())
            self._audio_mode_btn.bind(
                "<Enter>", lambda e: self._audio_mode_btn.configure(fg=TEXT_COLOR)
                if self._audio_mode_btn.cget("fg") != AMBER_WARNING else None
            )
            self._audio_mode_btn.bind(
                "<Leave>", lambda e: self._audio_mode_btn.configure(fg=TEXT_DIM)
                if self._audio_mode_btn.cget("fg") != AMBER_WARNING else None
            )

        # Elapsed on the right side of controls
        ctrl_elapsed = tk.Label(
            ctrl_frame, text="00:00:00", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_CONTROLS,
        )
        ctrl_elapsed.pack(side=tk.RIGHT, padx=10, pady=6)
        self._ctrl_elapsed_label = ctrl_elapsed

        # --- Screen preview thumbnail (optional) ---
        if self._show_screen_preview:
            preview_frame = tk.Frame(parent, bg=BG_COLOR, height=PREVIEW_HEIGHT)
            preview_frame.pack(fill=tk.X, padx=10, pady=(4, 0))
            preview_frame.pack_propagate(False)
            self._preview_label = tk.Label(
                preview_frame,
                text="No preview",
                font=("Segoe UI", 8),
                fg=TEXT_DIM,
                bg="#0d0d1a",
                width=180,
                height=101,
                anchor=tk.CENTER,
            )
            self._preview_label.pack(expand=True)

        # --- Transcript preview (60px) ---
        if self._show_transcript:
            transcript_frame = tk.Frame(parent, bg=BG_COLOR, height=60)
            transcript_frame.pack(fill=tk.X, padx=10, pady=(4, 2))
            transcript_frame.pack_propagate(False)

            self._transcript_label = tk.Label(
                transcript_frame,
                text="Waiting for speech...",
                font=("Segoe UI", 9),
                fg=TEXT_COLOR, bg=BG_COLOR,
                wraplength=355, justify=tk.LEFT, anchor=tk.NW,
            )
            self._transcript_label.pack(fill=tk.BOTH, expand=True)

        # --- Footer (32px) ---
        footer_frame = tk.Frame(parent, bg=BG_COLOR, height=32)
        footer_frame.pack(fill=tk.X, side=tk.BOTTOM)
        footer_frame.pack_propagate(False)

        if self._on_open_recordings:
            rec_btn = tk.Label(
                footer_frame, text=" Open Recordings ", font=("Segoe UI", 8),
                fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2", padx=4, pady=2,
            )
            rec_btn.pack(side=tk.LEFT, padx=(10, 4), pady=4)
            rec_btn.bind("<Button-1>", lambda e: self._on_open_recordings())
            rec_btn.bind("<Enter>", lambda e: rec_btn.configure(bg=BUTTON_HOVER))
            rec_btn.bind("<Leave>", lambda e: rec_btn.configure(bg=BUTTON_BG))

        if self._on_open_settings:
            set_btn = tk.Label(
                footer_frame, text=" Settings ", font=("Segoe UI", 8),
                fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2", padx=4, pady=2,
            )
            set_btn.pack(side=tk.LEFT, padx=4, pady=4)
            set_btn.bind("<Button-1>", lambda e: self._on_open_settings())
            set_btn.bind("<Enter>", lambda e: set_btn.configure(bg=BUTTON_HOVER))
            set_btn.bind("<Leave>", lambda e: set_btn.configure(bg=BUTTON_BG))

    def _build_collapsed(self, parent: tk.Frame) -> None:
        """Build the collapsed mini-indicator view."""
        ctx = self._context or DashboardContext()

        inner = tk.Frame(parent, bg=BG_HEADER, height=COLLAPSED_HEIGHT)
        inner.pack(fill=tk.BOTH, expand=True)
        inner.pack_propagate(False)

        # Pulsing red dot
        dot = tk.Label(
            inner, text="\u2b24", font=("Segoe UI", 10),
            fg=RED_DOT, bg=BG_HEADER,
        )
        dot.pack(side=tk.LEFT, padx=(8, 4))
        self._collapsed_dot = dot

        self._collapsed_status_label = tk.Label(
            inner, text="Recording", font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR, bg=BG_HEADER,
        )
        self._collapsed_status_label.pack(side=tk.LEFT)

        self._collapsed_elapsed = tk.Label(
            inner, text="00:00:00", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG_HEADER,
        )
        self._collapsed_elapsed.pack(side=tk.LEFT, padx=6)

        # Expand button
        expand_btn = tk.Label(
            inner, text="\u25b2", font=("Segoe UI", 10),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        expand_btn.pack(side=tk.RIGHT, padx=(0, 8))
        expand_btn.bind("<Button-1>", lambda e: self._toggle_collapse())

        # Close
        close_btn = tk.Label(
            inner, text="\u2715", font=("Segoe UI", 10),
            fg=TEXT_DIM, bg=BG_HEADER, cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT, padx=(0, 4))
        close_btn.bind("<Button-1>", lambda e: self.hide())

    # ------------------------------------------------------------------
    # VU meter drawing
    # ------------------------------------------------------------------

    def _draw_vu_meters(
        self, app_frac: float, mic_frac: float, app_db: float, mic_db: float
    ) -> None:
        """Redraw VU meter bars on the canvas (must be called from main thread)."""
        self._app_vu_fraction = app_frac
        self._mic_vu_fraction = mic_frac

        if self._app_vu_canvas:
            self._draw_single_vu(self._app_vu_canvas, app_frac)
        if self._mic_vu_canvas:
            self._draw_single_vu(self._mic_vu_canvas, mic_frac)
        if self._app_db_label:
            self._app_db_label.configure(text=f"{app_db:.0f} dB")
        if self._mic_db_label:
            self._mic_db_label.configure(text=f"{mic_db:.0f} dB")

    def _draw_single_vu(self, canvas: tk.Canvas, fraction: float) -> None:
        """Draw a single VU bar on a canvas."""
        canvas.delete("vu")
        w = canvas.winfo_width() or 260
        h = canvas.winfo_height() or 14
        bar_w = int(w * fraction)
        if bar_w > 0:
            color = vu_color(fraction)
            canvas.create_rectangle(0, 0, bar_w, h, fill=color, outline="", tags="vu")

    # ------------------------------------------------------------------
    # State updates (called from window.after)
    # ------------------------------------------------------------------

    def _set_elapsed(self, text: str) -> None:
        """Set elapsed time on all relevant labels."""
        if self._is_paused:
            prefix = "\u23f8 PAUSED"
            color = "#ffa500"  # amber
        else:
            prefix = "Recording"
            color = "#a0a0a0"
        if self._elapsed_label:
            self._elapsed_label.configure(text=f"{prefix} {text}", fg=color)
        if self._ctrl_elapsed_label:
            self._ctrl_elapsed_label.configure(text=text)
        if self._collapsed_elapsed:
            self._collapsed_elapsed.configure(text=text)

    def _set_paused_display(self, is_paused: bool) -> None:
        """Update visual indicators for paused state."""
        self._is_paused = is_paused
        if self._red_dot_label:
            self._red_dot_label.configure(fg="#ffa500" if is_paused else "#ff3b3b")
        if self._pause_btn:
            if is_paused:
                self._pause_btn.configure(text=" \u25b6 ", bg="#e67e22")  # play icon, amber
            else:
                self._pause_btn.configure(text=" \u23f8 ", bg="#7f8c8d")  # pause icon, grey
        # Update collapsed view
        if self._collapsed_dot:
            self._collapsed_dot.configure(fg="#ffa500" if is_paused else RED_DOT)
        if self._collapsed_status_label:
            if is_paused:
                self._collapsed_status_label.configure(text="\u23f8 Paused", fg="#ffa500")
            else:
                self._collapsed_status_label.configure(text="Recording", fg=TEXT_COLOR)

    def _set_capture_warning(self, is_process_specific: bool) -> None:
        """Show or hide the capture mode warning label."""
        if self._capture_warning_label:
            if is_process_specific:
                self._capture_warning_label.pack_forget()
            else:
                self._capture_warning_label.pack(fill=tk.X, padx=10, pady=(2, 0))

    def _set_audio_mode(self, is_desktop: bool) -> None:
        """Update the audio mode button text, color, and capture warning."""
        if self._audio_mode_btn:
            if is_desktop:
                self._audio_mode_btn.configure(text=" Desktop Audio ", fg=AMBER_WARNING)
            else:
                self._audio_mode_btn.configure(text=" App Audio ", fg=TEXT_DIM)
        # Also toggle the capture warning label
        self._set_capture_warning(not is_desktop)

    def _set_mute_display(self, is_muted: bool) -> None:
        """Update the mute button text and color."""
        if self._mute_btn:
            text = "Muted" if is_muted else "Unmuted"
            fg = MUTED_COLOR if is_muted else UNMUTED_COLOR
            self._mute_btn.configure(text=f" {text} ", fg=fg)

    def _set_transcript(self, text: str) -> None:
        """Update the transcript preview label."""
        if self._transcript_label:
            self._transcript_label.configure(text=f'"{text}"')

    # ------------------------------------------------------------------
    # Pulsing red dot
    # ------------------------------------------------------------------

    def _start_pulse(self) -> None:
        """Start the pulsing red dot animation."""
        self._dot_visible = True
        self._pulse_tick()

    def _stop_pulse(self) -> None:
        """Stop the pulsing red dot animation."""
        if self._pulse_after_id and self._window:
            try:
                self._window.after_cancel(self._pulse_after_id)
            except tk.TclError:
                pass
            self._pulse_after_id = None

    def _pulse_tick(self) -> None:
        """Toggle the recording dot visibility every 500ms.

        Pulses red when recording, amber when paused.
        """
        if not self._is_visible or self._window is None:
            return
        self._dot_visible = not self._dot_visible
        if self._is_paused:
            color = "#ffa500" if self._dot_visible else "#5a3500"  # amber / dark amber
        else:
            color = RED_DOT if self._dot_visible else RED_DOT_OFF
        try:
            if self._red_dot_label:
                self._red_dot_label.configure(fg=color)
            if self._collapsed_dot:
                self._collapsed_dot.configure(fg=color)
            self._pulse_after_id = self._window.after(500, self._pulse_tick)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Collapse / Expand
    # ------------------------------------------------------------------

    def _toggle_collapse(self) -> None:
        """Toggle between expanded and collapsed views."""
        if self._window is None:
            return

        if self._is_collapsed:
            # Expand
            self._collapsed_frame.pack_forget()
            self._expanded_frame.pack(fill=tk.BOTH, expand=True)
            height = EXPANDED_HEIGHT
            if not self._show_transcript:
                height -= 60
            if self._show_screen_preview:
                height += PREVIEW_HEIGHT
            self._window.geometry(f"{EXPANDED_WIDTH}x{height}")
            self._is_collapsed = False
        else:
            # Collapse
            self._expanded_frame.pack_forget()
            self._collapsed_frame.pack(fill=tk.BOTH, expand=True)
            self._window.geometry(f"{COLLAPSED_WIDTH}x{COLLAPSED_HEIGHT}")
            self._is_collapsed = True

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _open_window_picker(self) -> None:
        """Open the window selection dialog (must be called on Tk thread)."""
        if not self._on_list_windows or not self._on_pick_window or not self._window:
            return

        windows = self._on_list_windows()  # [(hwnd, title), ...]
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

        # Listbox + scrollbar
        list_frame = tk.Frame(picker, bg=BG_COLOR)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            bg="#0d0d1a",
            fg=TEXT_COLOR,
            selectbackground=BG_CONTROLS,
            selectforeground=TEXT_COLOR,
            activestyle="none",
            font=("Segoe UI", 9),
            bd=0,
            highlightthickness=1,
            highlightcolor=BG_CONTROLS,
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=listbox.yview)

        for _hwnd, title in windows:
            listbox.insert(tk.END, f"  {title}")

        def _confirm():
            sel = listbox.curselection()
            if not sel:
                return
            chosen_hwnd, chosen_title = windows[sel[0]]
            picker.destroy()
            logger.info("User picked capture window: '%s' (HWND %d)", chosen_title, chosen_hwnd)
            self._on_pick_window(chosen_hwnd)

        listbox.bind("<Double-Button-1>", lambda e: _confirm())

        # Buttons
        btn_frame = tk.Frame(picker, bg=BG_COLOR)
        btn_frame.pack(fill=tk.X, padx=12, pady=10)

        sel_btn = tk.Label(
            btn_frame, text=" Capture This Window ",
            font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#0f3460",
            cursor="hand2", padx=8, pady=4,
        )
        sel_btn.pack(side=tk.LEFT)
        sel_btn.bind("<Button-1>", lambda e: _confirm())
        sel_btn.bind("<Enter>", lambda e: sel_btn.configure(bg=BUTTON_HOVER))
        sel_btn.bind("<Leave>", lambda e: sel_btn.configure(bg=BG_CONTROLS))

        cancel_btn = tk.Label(
            btn_frame, text=" Cancel ",
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BUTTON_BG,
            cursor="hand2", padx=8, pady=4,
        )
        cancel_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn.bind("<Button-1>", lambda e: picker.destroy())

    def _handle_stop(self) -> None:
        """Handle the Stop button click."""
        if self._on_stop:
            self._on_stop()

    def _handle_pause_toggle(self) -> None:
        """Handle the pause button click."""
        if self._on_toggle_pause:
            self._on_toggle_pause()

    def _handle_mute_toggle(self) -> None:
        """Handle the mute toggle click."""
        if self._on_toggle_mute:
            self._on_toggle_mute()

    def _on_mute_right_click(self, _event) -> str:
        """Right-click on the mute button: resume auto mute sync.

        Returns "break" so the window-level context menu binding does
        not also fire.
        """
        self._handle_resume_auto_sync()
        return "break"

    def _handle_resume_auto_sync(self) -> None:
        """Hand mute control back to auto-detection."""
        if self._on_resume_auto_sync:
            self._on_resume_auto_sync()

    def _show_mute_tooltip(self) -> None:
        """Show a small hint below the mute button on hover."""
        if self._window is None or self._mute_btn is None:
            return
        if self._mute_tooltip is not None:
            return
        try:
            tip = tk.Toplevel(self._window)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(
                tip,
                text="Click: mute/unmute recording · Right-click: resume auto-sync",
                font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_HEADER,
                padx=6, pady=2,
            ).pack()
            x = self._mute_btn.winfo_rootx()
            y = self._mute_btn.winfo_rooty() + self._mute_btn.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
            self._mute_tooltip = tip
        except Exception:
            logger.debug("Mute tooltip failed", exc_info=True)

    def _hide_mute_tooltip(self) -> None:
        """Destroy the mute button hover hint."""
        tip = self._mute_tooltip
        self._mute_tooltip = None
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass

    def _show_context_menu(self, event) -> None:
        """Show right-click context menu."""
        if self._window is None:
            return
        menu = tk.Menu(self._window, tearoff=0, bg=BG_COLOR, fg=TEXT_COLOR,
                       activebackground=BUTTON_HOVER, activeforeground=TEXT_COLOR)
        menu.add_command(label="Hide Dashboard", command=self.hide)
        if self._on_open_settings:
            menu.add_command(label="Settings", command=self._on_open_settings)
        menu.add_separator()
        menu.add_command(label="Stop Recording", command=self._handle_stop)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            if self._window:
                self._window.after(100, menu.destroy)

    # ------------------------------------------------------------------
    # Positioning
    # ------------------------------------------------------------------

    def _apply_position(self) -> None:
        """Set the initial window position."""
        if self._window is None:
            return

        # Use saved coordinates if available
        if self._position_x >= 0 and self._position_y >= 0:
            self._window.geometry(f"+{self._position_x}+{self._position_y}")
            return

        # Otherwise use preset position
        screen_w = self._window.winfo_screenwidth()
        screen_h = self._window.winfo_screenheight()
        win_w = EXPANDED_WIDTH
        win_h = EXPANDED_HEIGHT

        positions = {
            "top-left": (20, 20),
            "top-right": (screen_w - win_w - 20, 20),
            "bottom-left": (20, screen_h - win_h - 80),
            "bottom-right": (screen_w - win_w - 20, screen_h - win_h - 80),
            "center": ((screen_w - win_w) // 2, (screen_h - win_h) // 2),
        }
        x, y = positions.get(self._position, positions["top-right"])
        self._window.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def _start_drag(self, event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event) -> None:
        if self._window:
            x = self._window.winfo_x() + event.x - self._drag_x
            y = self._window.winfo_y() + event.y - self._drag_y
            self._window.geometry(f"+{x}+{y}")
