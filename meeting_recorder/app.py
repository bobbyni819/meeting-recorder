"""Application orchestrator for Meeting Recorder."""

from __future__ import annotations

import copy
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_recorder.utils import open_in_explorer

from meeting_recorder.config import Config
from meeting_recorder.audio.platforms import (
    find_primary_meeting_process,
    find_meeting_processes,
    MeetingProcess,
)
from meeting_recorder.audio.capture_manager import CaptureManager
from meeting_recorder.audio.vad import VoiceActivityDetector
from meeting_recorder.audio.mixer import mix_tracks_streaming
from meeting_recorder.transcription.pipeline import TranscriptionPipeline
from meeting_recorder.storage.recording_store import RecordingStore
from meeting_recorder.storage.metadata import RecordingMetadata
from meeting_recorder.storage.transcript_formatter import save_all_formats
from meeting_recorder.ui.tray import TrayIcon
from meeting_recorder.ui.dashboard import GameBarDashboard, DashboardContext
from meeting_recorder.ui.main_window import MainWindow
from meeting_recorder.ui import platforms as notifications
from meeting_recorder.integrations.outlook import find_current_meeting, CalendarEvent
from meeting_recorder.integrations.google_drive import GoogleDriveUploader, is_google_drive_available

logger = logging.getLogger(__name__)


class MeetingRecorderApp:
    """Main application orchestrator.

    Coordinates:
    - System tray UI
    - Audio capture (app + mic)
    - Post-recording transcription pipeline
    - Recording storage and metadata
    - Global hotkey
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self._capture_manager: Optional[CaptureManager] = None
        self._current_recording_dir: Optional[Path] = None
        self._current_metadata: Optional[RecordingMetadata] = None
        self._current_process: Optional[MeetingProcess] = None
        self._recording_store = RecordingStore(self.config.output_dir)
        self._pipeline = TranscriptionPipeline(self.config)
        self._hotkey_registered = False

        # Resolve the per-machine performance tier once (auto-detects GPU/CPU).
        from meeting_recorder.performance import resolve_tier
        self._perf_tier = resolve_tier(self.config.performance.profile)
        logger.info("Performance tier: %s", self._perf_tier.name)

        # Pre-loaded VAD model (loaded once in run(), reused across recordings)
        self._vad: Optional[VoiceActivityDetector] = None

        # Snapshot of config at recording start (used for post-processing)
        self._recording_config: Optional[Config] = None

        # Post-processing thread (non-daemon, so quit() can wait for it)
        self._post_thread: Optional[threading.Thread] = None

        # Prevents double-invocation of stop_recording() from concurrent callers
        # (e.g. user clicks Stop while process-exit auto-stop fires simultaneously)
        self._stop_lock = threading.Lock()
        # Flag set when stop_recording has entered its cleanup phase but
        # capture_manager.stop() hasn't finished yet.  Any concurrent
        # start_recording call must wait until this clears to avoid
        # spawning a second CaptureManager that writes to the same files.
        self._is_stopping = False

        # Serialises all metadata.save() calls across threads (mark processing,
        # final save, summary callback, Drive upload callback) to avoid file corruption.
        self._metadata_lock = threading.Lock()

        # Dashboard overlay
        self._dashboard: Optional[GameBarDashboard] = None
        self._capture_mode_reported: bool = False
        self._health_warnings_shown: set[str] = set()
        self._preview_frame_counter: int = 0
        self._tray_update_counter: int = 0

        # Meeting auto-detection scanner
        self._scanner_thread: Optional[threading.Thread] = None
        self._scanner_stop = threading.Event()
        # Minimum window score for auto-start (50 = likely meeting in Teams,
        # 100 = definite meeting in Zoom, avoids idle lobby windows)
        self._auto_start_min_score = 50

        # Main application window
        self._main_window = MainWindow(
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_pause=self._toggle_pause,
            on_toggle_mute=self._toggle_mute,
            on_record_window=self._record_window,
            on_search=self._open_search,
            on_settings=self._open_settings,
            on_open_recordings=self._open_recordings_folder,
            on_open_recording=self._open_recording,
            on_list_recent=self._list_recent_recordings_full,
            on_list_windows=self._list_capturable_windows,
            on_pick_window=self._on_pick_capture_window,
            on_toggle_audio_mode=self._toggle_audio_mode,
            on_toggle_auto_start=self._toggle_auto_start,
            on_reprocess=self.reprocess_recording,
            on_reprocess_all_failed=self.reprocess_all_failed,
            on_import_audio=self.import_audio,
            on_quit=self.quit,
            auto_start=self.config.recording.auto_start,
            hotkey_recording=self.config.hotkey.toggle_recording,
            hotkey_pause=self.config.hotkey.toggle_pause,
        )

        # System tray
        self._tray = TrayIcon(
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_quit=self.quit,
            on_settings=self._open_settings,
            on_open_recordings=self._open_recordings_folder,
            on_search=self._open_search,
            on_show_dashboard=self._show_dashboard,
            on_record_window=self._record_window,
            on_toggle_auto_start=self._toggle_auto_start,
            on_pause=self._toggle_pause,
            on_open_last_recording=self._open_last_recording,
            on_list_recent=self._list_recent_recordings,
            on_open_recording=self._open_recording,
            on_show_main_window=self._show_main_window,
            on_import_audio=self._import_audio_from_tray,
            auto_start=self.config.recording.auto_start,
            hotkey_recording=self.config.hotkey.toggle_recording,
            hotkey_mute=self.config.hotkey.toggle_mute,
            hotkey_dashboard=self.config.hotkey.toggle_dashboard,
            hotkey_pause=self.config.hotkey.toggle_pause,
        )

    def _save_metadata(self, metadata: RecordingMetadata, recording_dir: Path) -> None:
        """Thread-safe metadata save."""
        with self._metadata_lock:
            metadata.save(recording_dir)

    def run(self) -> None:
        """Start the application. Blocks until quit."""
        logger.info("Meeting Recorder starting...")
        self.config.validate()
        self._recording_store.ensure_base_dir()

        # Pre-load VAD model on the main thread before pystray blocks.
        # Loading in a background thread after pystray starts can deadlock
        # due to torch.hub.load + keyboard hooks + pystray threading interaction.
        try:
            self._vad = VoiceActivityDetector(threshold=self.config.vad.threshold)
            self._vad.load()
            logger.info("VAD model pre-loaded successfully.")
        except Exception:
            logger.exception("Failed to pre-load VAD model. Recording will attempt lazy load.")
            self._vad = None

        self._register_hotkey()

        # Run retention cleanup at startup
        self._run_retention_cleanup()

        # Sync search index in background (indexes new recordings, removes deleted)
        threading.Thread(
            target=self._sync_search_index, daemon=True, name="search-index-sync"
        ).start()

        # Heal failed/stuck/partially-processed recordings in background
        if self.config.recording.auto_retry_failed:
            threading.Thread(
                target=self._startup_retry_sweep, daemon=True,
                name="startup-retry-sweep",
            ).start()

        # Start meeting auto-detection scanner
        if self.config.recording.auto_start:
            self._start_meeting_scanner()
            logger.info("Meeting auto-detection enabled.")

        # Show startup notification
        hotkeys = self.config.hotkey
        auto = "Auto-record ON" if self.config.recording.auto_start else "Auto-record OFF"
        notifications.notify_info(
            f"Ready! {auto}\n"
            f"{hotkeys.toggle_recording} = Record  |  {hotkeys.toggle_pause} = Pause"
        )

        # Show main window (runs in its own Tk thread)
        self._main_window.show()

        # Tray icon runs on the main thread (blocks)
        logger.info("Starting tray icon event loop on main thread...")
        try:
            self._tray.run()
        except Exception:
            logger.exception("Tray icon event loop crashed!")
        logger.info("Tray icon event loop exited.")

    def start_recording(self) -> None:
        """Start recording — always opens a window picker.

        The picker pre-selects a meeting app (Zoom/Teams/Webex) if one is
        detected, but the user always gets to confirm or change the target.
        """
        cm = self._capture_manager
        if cm and cm.is_recording:
            logger.warning("Already recording.")
            return
        if self._is_stopping:
            logger.warning("Previous recording is still stopping; try again in a moment.")
            notifications.notify_info("Still finishing previous recording — wait a second.")
            return

        # Always open the window picker so the user chooses what to record.
        # If a meeting app is running, it'll appear in the list for easy selection.
        process = None
        logger.info("Opening window picker...")
        if self._main_window and self._main_window._window:
            from meeting_recorder.video.platforms import list_visible_windows
            windows = list_visible_windows()
            if not windows:
                logger.warning("No visible windows found for picker.")
                notifications.notify_error("No visible windows found to record.")
                return
            chosen = self._main_window.pick_window_for_recording(windows)
            if chosen is not None:
                hwnd, title, pid, proc_name = chosen
                process = MeetingProcess(
                    pid=pid,
                    name=proc_name,
                    app_key="manual",
                    display_name=title,
                )
        else:
            process = self._pick_window_for_recording()
        if process is None:
            logger.info("Window picker cancelled by user.")
            return

        self._current_process = process
        logger.info("Found %s (PID %d)", process.display_name, process.pid)

        try:
            self._start_recording_for_process(process)
        except Exception:
            logger.exception("Failed to start recording for %s", process.display_name)
            notifications.notify_error(f"Failed to start recording: see log for details")
            self._capture_manager = None
            self._current_recording_dir = None
            self._current_metadata = None
            self._current_process = None
            self._tray.set_state("idle")

    def _pick_window_for_recording(self) -> Optional[MeetingProcess]:
        """Show a standalone window picker dialog and return a MeetingProcess.

        Creates a blocking tkinter Tk() window (not Toplevel) so it works
        even when no other tkinter root exists. Returns None if the user
        cancels or no windows are available.
        """
        import tkinter as tk
        from meeting_recorder.video.platforms import list_visible_windows

        windows = list_visible_windows()
        if not windows:
            logger.warning("No visible windows found for picker.")
            return None

        result: list[Optional[MeetingProcess]] = [None]

        root = tk.Tk()
        root.title("Record Window")
        root.configure(bg="#1a1a2e")
        root.attributes("-topmost", True)
        root.geometry("500x400")
        root.resizable(False, False)

        tk.Label(
            root,
            text="No meeting app detected. Select a window to record:",
            font=("Segoe UI", 9),
            fg="#e0e0e0",
            bg="#1a1a2e",
        ).pack(padx=12, pady=(10, 4), anchor=tk.W)

        # Listbox + scrollbar
        list_frame = tk.Frame(root, bg="#1a1a2e")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            bg="#0d0d1a",
            fg="#e0e0e0",
            selectbackground="#0f3460",
            selectforeground="#e0e0e0",
            activestyle="none",
            font=("Segoe UI", 9),
            bd=0,
            highlightthickness=1,
            highlightcolor="#0f3460",
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
            hwnd, title, pid, proc_name = windows[sel[0]]
            result[0] = MeetingProcess(
                pid=pid,
                name=proc_name,
                app_key="manual",
                display_name=title,
            )
            root.destroy()

        listbox.bind("<Double-Button-1>", lambda e: _confirm())

        # Buttons
        btn_frame = tk.Frame(root, bg="#1a1a2e")
        btn_frame.pack(fill=tk.X, padx=12, pady=10)

        sel_btn = tk.Label(
            btn_frame,
            text=" Record This Window ",
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg="#0f3460",
            cursor="hand2",
            padx=8,
            pady=4,
        )
        sel_btn.pack(side=tk.LEFT)
        sel_btn.bind("<Button-1>", lambda e: _confirm())
        sel_btn.bind("<Enter>", lambda e: sel_btn.configure(bg="#1a5276"))
        sel_btn.bind("<Leave>", lambda e: sel_btn.configure(bg="#0f3460"))

        cancel_btn = tk.Label(
            btn_frame,
            text=" Cancel ",
            font=("Segoe UI", 9),
            fg="#888888",
            bg="#0f3460",
            cursor="hand2",
            padx=8,
            pady=4,
        )
        cancel_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn.bind("<Button-1>", lambda e: root.destroy())

        root.mainloop()
        return result[0]

    def _record_window(self) -> None:
        """Open a window picker then start recording.

        Always shows the picker regardless of whether a meeting app is
        detected. Uses the MainWindow's Tk thread when available to avoid
        creating a conflicting Tk root. Falls back to standalone Tk() for
        the tray-only path.
        """
        cm = self._capture_manager
        if cm and cm.is_recording:
            logger.warning("Already recording.")
            return
        if self._is_stopping:
            logger.warning("Previous recording is still stopping; try again in a moment.")
            notifications.notify_info("Still finishing previous recording — wait a second.")
            return

        # Try the main window's picker first (runs on its Tk thread)
        process = None
        if self._main_window and self._main_window._window:
            from meeting_recorder.video.platforms import list_visible_windows
            windows = list_visible_windows()
            if not windows:
                logger.warning("No visible windows found for picker.")
                notifications.notify_error("No visible windows found to record.")
                return
            chosen = self._main_window.pick_window_for_recording(windows)
            if chosen is not None:
                hwnd, title, pid, proc_name = chosen
                process = MeetingProcess(
                    pid=pid,
                    name=proc_name,
                    app_key="manual",
                    display_name=title,
                )
        else:
            # Fallback: standalone Tk picker (tray-only mode)
            process = self._pick_window_for_recording()

        if process is None:
            logger.info("Record Window cancelled by user.")
            return

        self._current_process = process
        logger.info("Recording window: %s (PID %d)", process.display_name, process.pid)

        try:
            self._start_recording_for_process(process)
        except Exception:
            logger.exception("Failed to start recording for %s", process.display_name)
            notifications.notify_error("Failed to start recording: see log for details")
            self._capture_manager = None
            self._current_recording_dir = None
            self._current_metadata = None
            self._current_process = None
            self._tray.set_state("idle")

    def _start_recording_for_process(self, process: MeetingProcess) -> None:
        """Inner method that performs all recording setup. Raises on failure."""
        # Reject if another recording is still in the stop/cleanup phase
        # — prevents two CaptureManagers from running simultaneously.
        if self._is_stopping:
            logger.warning("Refusing to start: previous recording still stopping.")
            return
        # Check disk space before starting
        import shutil
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(output_dir).free
        free_gb = free_bytes / (1024 ** 3)
        if free_gb < 0.1:  # < 100 MB
            msg = f"Not enough disk space ({free_gb:.1f} GB free). Recording aborted."
            logger.error(msg)
            notifications.notify_error(msg)
            return
        if free_gb < 1.0:
            logger.warning("Low disk space: %.1f GB free", free_gb)
            notifications.notify_info(f"Low disk space: {free_gb:.1f} GB free")

        # Snapshot config so mid-recording settings changes don't affect post-processing
        self._recording_config = copy.deepcopy(self.config)

        # Query Outlook calendar for meeting context
        calendar_event = None
        meeting_subject = ""
        if self.config.outlook.enabled:
            calendar_event = find_current_meeting(
                buffer_minutes=self.config.outlook.buffer_minutes
            )
            if calendar_event:
                meeting_subject = calendar_event.subject
                logger.info("Calendar match: '%s'", meeting_subject)

        # Create recording directory (with meeting subject if available)
        self._current_recording_dir = self._recording_store.create_recording_dir(
            app_name=process.display_name,
            meeting_subject=meeting_subject,
        )

        # Create metadata
        self._current_metadata = RecordingMetadata.create(
            app_name=process.display_name,
            app_pid=process.pid,
            sample_rate=self.config.audio.sample_rate,
            channels=self.config.audio.channels,
            language=self.config.recording.language,
            transcription_backend=self.config.transcription.backend,
        )
        # Attach calendar info to metadata
        if calendar_event:
            self._current_metadata.meeting_subject = calendar_event.subject
            self._current_metadata.meeting_organizer = calendar_event.organizer
            self._current_metadata.meeting_attendees = calendar_event.attendees
            self._current_metadata.meeting_location = calendar_event.location
        self._save_metadata(self._current_metadata, self._current_recording_dir)

        # Resolve mic device index
        mic_device = None
        if self.config.audio.mic_device:
            mic_device = self._find_mic_device_index(self.config.audio.mic_device)

        # Start capture manager (pass pre-loaded VAD to avoid threading deadlock)
        self._capture_manager = CaptureManager(
            pid=process.pid,
            output_dir=self._current_recording_dir,
            sample_rate=self.config.audio.sample_rate,
            channels=self.config.audio.channels,
            chunk_duration_ms=self.config.audio.chunk_duration_ms,
            vad_threshold=self.config.vad.threshold,
            mic_device_index=mic_device,
            on_stopped=self._on_capture_auto_stopped,
            screen_recording_enabled=self.config.screen_recording.enabled,
            screen_recording_fps=self.config.screen_recording.fps,
            video_encoder_preference=self._perf_tier.video_encoder,
            capture_speaker_events=self.config.recording.capture_speaker_events,
            process_name=process.name,
            app_key=process.app_key,
            mute_toggle_hotkey=self.config.hotkey.toggle_mute,
            on_audio_levels=self._on_audio_levels,
            on_live_transcript=self._on_live_transcript,
            # The performance tier can only RESTRICT, never force-enable: a
            # light machine turns live preview off even if the synced config
            # has it on, but a strong machine never overrides a user's "off".
            live_transcription_enabled=(
                self.config.recording.live_transcription
                and self._perf_tier.live_transcription
            ),
            live_transcript_mic=(
                self.config.recording.live_transcript_mic
                and self._perf_tier.live_transcript_mic
            ),
            on_live_insight=(
                self._on_live_insight if self._perf_tier.live_insights else None
            ),
            live_transcription_device=self._perf_tier.live_device,
            live_transcription_compute_type=self._perf_tier.live_compute_type,
            live_transcription_interval=self._perf_tier.live_interval,
            start_muted_default=self.config.recording.start_muted,
            on_mute_changed=self._on_mute_changed,
            vad=self._vad,
            on_health_warning=self._on_health_warning,
            on_capture_mode_changed=self._on_capture_mode_changed,
        )
        self._capture_mode_reported = False
        self._health_warnings_shown = set()
        self._preview_frame_counter = 0
        self._capture_manager.start()

        # Show dashboard overlay (skip when main window is active — two Tk roots
        # on separate threads corrupt each other and cause windows to vanish)
        dash_cfg = self.config.dashboard
        main_window_active = self._main_window and self._main_window._window is not None
        if dash_cfg.enabled and dash_cfg.auto_show and not main_window_active:
            self._dashboard = GameBarDashboard(
                on_stop=lambda: threading.Thread(target=self.stop_recording, daemon=True).start(),
                on_toggle_pause=self._toggle_pause,
                on_toggle_mute=self._toggle_mute,
                on_resume_auto_sync=self._resume_mute_auto_sync,
                on_open_recordings=self._open_recordings_folder,
                on_open_settings=self._open_settings,
                on_list_windows=self._capture_manager.list_capturable_windows,
                on_pick_window=self._on_pick_capture_window,
                on_toggle_audio_mode=self._toggle_audio_mode,
                opacity=dash_cfg.opacity,
                start_collapsed=dash_cfg.start_collapsed,
                show_transcript=dash_cfg.show_transcript,
                transcript_font_size=dash_cfg.transcript_font_size,
                transcript_lines=dash_cfg.transcript_lines,
                position_x=dash_cfg.position_x,
                position_y=dash_cfg.position_y,
                position=dash_cfg.position,
            )
            ctx = DashboardContext(
                app_name=process.display_name,
                meeting_subject=meeting_subject,
                is_muted=(
                    self._capture_manager.mute_sync.is_muted
                    if self._capture_manager.mute_sync
                    else False
                ),
                show_screen_preview=(
                    dash_cfg.show_screen_preview
                    and self.config.screen_recording.enabled
                ),
            )
            self._dashboard.show(ctx)

        # Update UI
        self._tray.set_state("recording", f"Recording {process.display_name}")
        self._main_window.set_recording_state(True, process.display_name)
        notifications.notify_recording_started(process.display_name)
        self._main_window.add_notification("info", f"Recording started: {process.display_name}", source="recorder")
        logger.info("Recording started for %s", process.display_name)

    def stop_recording(self) -> None:
        """Stop recording and begin post-processing."""
        # Lock prevents two concurrent callers (e.g. Stop button + process-exit
        # auto-stop) from both passing the guard and spawning duplicate
        # post-processing pipelines.
        with self._stop_lock:
            if not self._capture_manager or not self._capture_manager.is_recording:
                logger.warning("Not currently recording.")
                return
            # Grab refs and clear state atomically before releasing the lock so
            # any racing caller sees _capture_manager = None and bails out.
            capture_manager = self._capture_manager
            recording_dir = self._current_recording_dir
            metadata = self._current_metadata
            recording_config = self._recording_config
            elapsed = capture_manager.elapsed_seconds
            self._capture_manager = None
            self._current_recording_dir = None
            self._current_metadata = None
            self._current_process = None
            self._recording_config = None
            # Set stopping flag so start_recording won't spawn a second
            # CaptureManager while capture_manager.stop() is still draining
            # writers / closing WAV files.
            self._is_stopping = True

        try:
            # Save a thumbnail from the last captured frame before stopping
            self._save_thumbnail(capture_manager, recording_dir)
            capture_manager.stop()
        finally:
            # Cleared only after stop() has fully finished, so the next
            # start_recording sees a clean slate.
            self._is_stopping = False

        # Hide/close dashboard and persist position
        self._close_dashboard()

        # Update main window
        self._main_window.set_recording_state(False)

        duration_str = _format_duration(elapsed)
        notifications.notify_recording_stopped(duration_str, str(recording_dir))
        self._main_window.add_notification("info", f"Recording stopped ({duration_str})", source="recorder")
        logger.info("Recording stopped. Duration: %s", duration_str)

        def _post_wrapper() -> None:
            try:
                self._post_process(recording_dir, metadata, recording_config, elapsed)
            finally:
                # Clear the thread reference once finished so subsequent
                # reprocess calls see a clean slate (avoids stale references).
                self._post_thread = None

        self._post_thread = threading.Thread(
            target=_post_wrapper,
            name="post-processing",
            daemon=False,
        )
        self._post_thread.start()

    def quit(self) -> None:
        """Quit the application."""
        logger.info("Quitting Meeting Recorder...")
        self._stop_meeting_scanner()
        cm = self._capture_manager
        if cm and cm.is_recording:
            # Use stop_recording() so post-processing (mix + transcribe) runs.
            # Just calling _capture_manager.stop() would save audio but skip
            # transcription, leaving the recording in "recording" status.
            self.stop_recording()
        self._close_dashboard()
        # Force-close dashboard on quit even if auto_hide is False.
        # _close_dashboard() only closes when auto_hide=True, but on quit we
        # always want to destroy the window to avoid a brief zombie overlay.
        if self._dashboard:
            self._dashboard.close()
            self._dashboard = None

        # Wait for post-processing to finish so transcriptions are not lost
        if self._post_thread and self._post_thread.is_alive():
            logger.info("Waiting for post-processing to complete...")
            notifications.notify_info("Finishing transcription, please wait...")
            self._post_thread.join(timeout=300)  # 5 min max
            if self._post_thread.is_alive():
                logger.warning("Post-processing still running after timeout.")

        self._main_window.close()
        self._unregister_hotkey()
        self._tray.stop()

    def _post_process(
        self,
        recording_dir: Path,
        metadata: RecordingMetadata,
        config: Optional[Config] = None,
        elapsed_seconds: float = 0.0,
    ) -> None:
        """Run post-recording pipeline: mix, transcribe, format.

        Args:
            recording_dir: Directory containing audio files.
            metadata: Recording metadata.
            config: Config snapshot from recording start.  Falls back to
                self.config if None (for backwards compatibility).
            elapsed_seconds: Pause-adjusted recording duration from CaptureManager.
        """
        import time as _time
        _pp_start = _time.monotonic()

        # Ordered list of pipeline steps the user will see.  Updated by
        # each _update_progress() call so the progress banner can show a
        # live checklist (✓ done / • current / · pending).
        _pipeline_steps = [
            "Validating audio", "Mixing audio", "Transcribing",
            "Summary", "Indexing", "Uploading", "Finalizing",
        ]
        _steps_done: list[str] = []

        def _update_progress(stage: str) -> None:
            elapsed = _time.monotonic() - _pp_start
            mins, secs = divmod(int(elapsed), 60)
            time_str = f"{mins}:{secs:02d}" if mins else f"{secs}s"
            msg = f"Processing ({time_str}): {stage}"
            self._tray.set_state("processing", msg)
            self._main_window.update_status_bar(msg)
            # Match the stage string to a known pipeline step and mark
            # earlier steps as done.  Keeps the banner checklist fresh.
            stage_lower = stage.lower()
            current = None
            for step_name in _pipeline_steps:
                if step_name.lower() in stage_lower or stage_lower.startswith(
                    step_name.lower()
                ):
                    current = step_name
                    break
            if current:
                # Mark everything before `current` as done
                idx = _pipeline_steps.index(current)
                for prev in _pipeline_steps[:idx]:
                    if prev not in _steps_done:
                        _steps_done.append(prev)
            self._main_window.update_processing_progress(
                step=stage,
                elapsed_seconds=elapsed,
                steps_done=list(_steps_done),
                step_names=_pipeline_steps,
            )

        def _hide_progress() -> None:
            """Clear the progress banner — call when post-processing finishes."""
            self._main_window.update_processing_progress(
                step="", elapsed_seconds=0, steps_done=[], step_names=[],
            )

        cfg = config or self.config
        try:
            _update_progress("validating audio...")
            notifications.notify_transcription_started()
            metadata.status = "processing"
            self._save_metadata(metadata, recording_dir)

            # Validate app audio before mixing / transcription
            app_wav = recording_dir / "app_audio.wav"
            if not _validate_wav(app_wav):
                logger.warning("app_audio.wav is corrupt or empty — skipping transcription")
                notifications.notify_error(
                    "App audio corrupt or empty — try re-recording. "
                    "Use Re-process in the recording's detail view to retry."
                )
                metadata.status = "error"
                metadata.error_message = "App audio file corrupt or empty"
                self._save_metadata(metadata, recording_dir)
                self._tray.set_state("idle")
                return

            # Mix audio tracks
            app_audio = recording_dir / "app_audio.wav"
            mic_audio = recording_dir / "mic_audio.wav"
            mixed_audio = recording_dir / "mixed.wav"

            _update_progress("mixing audio tracks...")
            if app_audio.exists() and mic_audio.exists():
                mix_tracks_streaming(app_audio, mic_audio, mixed_audio)

            # Run transcription pipeline (with speaker resolution if attendees available)
            _update_progress("transcribing audio...")
            try:
                segments = self._pipeline.process(
                    recording_dir,
                    attendees=metadata.meeting_attendees,
                    organizer=metadata.meeting_organizer,
                )
            finally:
                # Clean up transient mixed.wav — it's large and only needed for transcription.
                # Must run even if pipeline.process() fails, to avoid ~90 MB leaked per recording.
                if mixed_audio.exists():
                    mixed_audio.unlink()
                    logger.info("Cleaned up transient mixed.wav")

            # Persist which backend actually produced the transcript — the
            # pipeline may have fallen back from Gemini to local Whisper.
            backend_used = getattr(self._pipeline, "last_backend_used", None)
            if isinstance(backend_used, str) and backend_used:
                if (
                    backend_used != cfg.transcription.backend
                    and cfg.transcription.backend == "gemini"
                ):
                    self._main_window.add_notification(
                        "warning",
                        "Gemini unavailable — transcribed with local Whisper instead",
                        source="pipeline",
                    )
                metadata.transcription_backend = backend_used

            # Store speaker mapping from pipeline
            mapping = self._pipeline.last_speaker_mapping
            if mapping is not None:
                metadata.speaker_map = mapping.speaker_map
                metadata.speaker_map_confidence = mapping.confidence
                metadata.speaker_map_method = mapping.method

            # Save transcripts in all configured formats
            save_all_formats(
                segments,
                recording_dir,
                formats=cfg.output.formats,
            )

            # Auto-share Gemini API key with summary provider if needed
            summary_config = copy.deepcopy(cfg.summary)
            if (
                summary_config.provider == "gemini"
                and not summary_config.api_key
                and cfg.transcription.gemini_api_key
            ):
                summary_config.api_key = cfg.transcription.gemini_api_key

            # Finalize metadata before parallel tail tasks
            speakers = set(s.speaker for s in segments if s.speaker)
            metadata.finalize(
                recording_dir,
                speaker_count=len(speakers),
                segment_count=len(segments),
                elapsed_seconds=elapsed_seconds,
            )

            # Smart rename from transcript content. Runs BEFORE the parallel
            # pool so the search index and Drive upload see the final name.
            if self.config.recording.smart_rename:
                new_dir = self._smart_rename_recording(
                    recording_dir, segments, metadata, summary_config,
                )
                if new_dir is not None:
                    recording_dir = new_dir

            # Run summary, indexing, quality scoring, and Drive upload in parallel
            _update_progress("saving & indexing...")
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="post") as pool:
                futures = []
                if summary_config.enabled:
                    futures.append(pool.submit(
                        self._generate_summary, recording_dir, segments, metadata, summary_config,
                    ))
                futures.append(pool.submit(self._index_recording, recording_dir))
                futures.append(pool.submit(
                    self._score_quality, recording_dir, metadata,
                ))
                if cfg.google_drive.enabled:
                    futures.append(pool.submit(
                        self._upload_to_google_drive, recording_dir, metadata, cfg,
                    ))
                futures.append(pool.submit(
                    self._auto_tag_recording, recording_dir, metadata,
                ))
                futures.append(pool.submit(
                    self._extract_action_items, recording_dir,
                ))
                futures.append(pool.submit(
                    self._extract_decisions, recording_dir,
                ))
                for f in as_completed(futures):
                    f.result()  # propagate exceptions (each method catches its own)

            # Final save: parallel tasks (summary, Drive upload) modify metadata
            # fields that finalize() didn't know about. Persist them now.
            self._save_metadata(metadata, recording_dir)

            self._tray.set_state("idle")
            hotkeys = self.config.hotkey
            self._main_window.update_status_bar(
                f"{hotkeys.toggle_recording} Record  |  {hotkeys.toggle_pause} Pause"
            )
            self._main_window.refresh_history()

            # Build a richer notification with key stats
            notify_parts = []
            dur = metadata.duration_seconds or elapsed_seconds
            if dur:
                m, s = divmod(int(dur), 60)
                h, m = divmod(m, 60)
                notify_parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
            if metadata.speaker_count:
                notify_parts.append(f"{metadata.speaker_count} speaker{'s' if metadata.speaker_count != 1 else ''}")
            if metadata.segment_count:
                notify_parts.append(f"{metadata.segment_count} segments")
            q_scores = metadata.quality_scores
            if q_scores and q_scores.get("overall_score") is not None:
                from meeting_recorder.storage.quality import quality_label
                qs = q_scores["overall_score"]
                notify_parts.append(f"Quality: {qs}/100 ({quality_label(qs)})")
            summary_line = " \u2022 ".join(notify_parts) if notify_parts else ""
            notifications.notify_transcription_complete(str(recording_dir), summary_line)
            self._main_window.add_notification("success", f"Transcription complete: {recording_dir.name}", source="pipeline")
            logger.info("Post-processing complete: %s", recording_dir)

            # Run retention cleanup after each recording
            self._run_retention_cleanup(exclude=recording_dir)
            _hide_progress()

        except Exception as e:
            logger.exception("Post-processing failed")
            self._tray.set_state("error", f"Error: {e}")
            self._main_window.update_status_bar(f"Error: {e}")
            notifications.notify_error(f"Transcription failed: {e}")
            self._main_window.add_notification("error", f"Post-processing failed: {e}", source="pipeline")
            if metadata:
                metadata.set_error(str(e), recording_dir)
            _hide_progress()

    def _upload_to_google_drive(
        self,
        recording_dir: Path,
        metadata: RecordingMetadata,
        config: Optional[Config] = None,
    ) -> None:
        """Upload recording to Google Drive."""
        cfg = config or self.config
        try:
            creds_path = Path(cfg.google_drive.credentials_path).expanduser()
            if not is_google_drive_available(creds_path):
                logger.info("Google Drive not available (missing credentials or libraries).")
                return

            self._tray.set_state("processing", "Uploading to Drive...")
            uploader = GoogleDriveUploader(
                credentials_path=creds_path,
                folder_id=cfg.google_drive.folder_id,
            )
            folder_id = uploader.upload_recording(recording_dir)
            if folder_id:
                metadata.google_drive_folder_id = folder_id
                metadata.upload_pending = False
                self._save_metadata(metadata, recording_dir)
                logger.info("Google Drive upload complete: %s", folder_id)
            else:
                logger.warning("Google Drive upload returned no folder ID.")
                metadata.upload_pending = True
                self._save_metadata(metadata, recording_dir)
        except Exception:
            # Flag it so the startup retry sweep can re-upload later.
            metadata.upload_pending = True
            self._save_metadata(metadata, recording_dir)
            logger.exception("Google Drive upload failed (non-fatal)")
            self._main_window.add_notification(
                "warning",
                f"Drive upload failed for {recording_dir.name} — will retry at next startup",
                source="pipeline",
            )

    def _smart_rename_recording(
        self, recording_dir: Path, segments, metadata: RecordingMetadata,
        summary_config,
    ) -> Optional[Path]:
        """Rename the folder to match the meeting, disambiguating by content.

        Best-effort and additive: returns the new path on a successful
        rename, else None (folder unchanged). Files inside are untouched;
        only the directory moves, with the timestamp prefix preserved.
        """
        try:
            from meeting_recorder.storage import smart_naming

            # Gather candidate calendar events overlapping this recording.
            candidates = self._calendar_candidates(metadata)
            if metadata.meeting_subject and metadata.meeting_subject not in candidates:
                candidates.append(metadata.meeting_subject)
            # 0 candidates → keep the current name (don't spend quota naming
            # ad-hoc meetings); 1 → use it; 2+ → disambiguate by transcript.
            if not candidates:
                return None

            excerpt = "\n".join(
                f"{s.speaker or 'Speaker'}: {s.text}" for s in segments[:60]
            )
            title, source = smart_naming.select_meeting_title(
                excerpt, candidates, summary_config,
            )
            if not title:
                return None
            # Don't rename if the title already matches the current folder.
            app_name = metadata.app_name or "Meeting"
            if smart_naming.current_subject(
                recording_dir.name, app_name
            ).lower() == smart_naming.sanitize_subject(title).lower():
                return None

            new_dir = smart_naming.rename_recording_dir(
                recording_dir, title, app_name,
            )
            if new_dir is None:
                return None

            metadata.original_dir_name = recording_dir.name
            metadata.meeting_subject = title
            metadata.save(new_dir)
            logger.info(
                "Smart rename (%s): %s -> %s", source, recording_dir.name, new_dir.name,
            )
            self._main_window.add_notification(
                "info", f"Named meeting: {title}", source="naming",
            )
            return new_dir
        except Exception:
            logger.exception("Smart rename failed (non-fatal)")
            return None

    def _calendar_candidates(self, metadata: RecordingMetadata) -> list[str]:
        """Calendar event subjects overlapping this recording's time slot."""
        if not self.config.outlook.enabled:
            return []
        try:
            from datetime import datetime

            from meeting_recorder.integrations.outlook import get_upcoming_meetings

            ref = None
            if metadata.start_time:
                try:
                    ref = datetime.fromisoformat(metadata.start_time)
                except ValueError:
                    ref = None
            events = get_upcoming_meetings(window_minutes=30, reference_time=ref)
            return [e.subject for e in events if e.subject]
        except Exception:
            logger.debug("Calendar candidate fetch failed", exc_info=True)
            return []

    def _generate_summary(
        self, recording_dir: Path, segments, metadata: RecordingMetadata,
        summary_config=None,
    ) -> None:
        """Generate AI meeting summary (non-fatal)."""
        try:
            from meeting_recorder.summary.summarizer import generate_summary, save_summary

            self._tray.set_state("processing", "Generating summary...")
            summary = generate_summary(
                segments=segments,
                config=summary_config or self.config.summary,
                meeting_subject=metadata.meeting_subject,
                attendees=metadata.meeting_attendees,
                duration_seconds=metadata.duration_seconds,
            )
            save_summary(summary, recording_dir)
            metadata.has_summary = True
            metadata.summary_failed = False
            metadata.summary_provider = summary.provider_used
            metadata.summary_model = summary.model_used
            logger.info("AI summary generated successfully.")
        except Exception:
            # Flag it so the startup retry sweep can heal this recording.
            metadata.summary_failed = True
            logger.exception("AI summary generation failed (non-fatal)")
            self._main_window.add_notification(
                "warning",
                f"Summary failed for {recording_dir.name} — will retry at next startup",
                source="pipeline",
            )

    @staticmethod
    def _save_thumbnail(capture_manager: CaptureManager, recording_dir: Path) -> None:
        """Save a thumbnail image from the last screen capture frame (non-fatal)."""
        try:
            frame = capture_manager.get_screen_frame()
            if frame is None:
                return
            import cv2
            thumb_h = 180
            h, w = frame.shape[:2]
            thumb_w = int(w * thumb_h / h) if h > 0 else 320
            thumb = cv2.resize(frame, (thumb_w, thumb_h))
            cv2.imwrite(str(recording_dir / "thumbnail.jpg"), thumb,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
            logger.info("Saved recording thumbnail.")
        except Exception:
            logger.debug("Failed to save thumbnail (non-fatal)", exc_info=True)

    def _sync_search_index(self) -> None:
        """Sync the search index with recordings on disk (background, non-fatal)."""
        try:
            from meeting_recorder.search.index import RecordingIndex

            index = RecordingIndex()
            added, removed = index.sync(self.config.output_dir)
            index.close()
            if added or removed:
                logger.info("Search index synced: %d added, %d removed", added, removed)
        except Exception:
            logger.exception("Search index sync failed (non-fatal)")

    def _index_recording(self, recording_dir: Path) -> None:
        """Add recording to the search index (non-fatal)."""
        try:
            from meeting_recorder.search.index import RecordingIndex

            index = RecordingIndex()
            index.index_recording(recording_dir)
            index.close()
        except Exception:
            logger.exception("Search indexing failed (non-fatal)")

    def _score_quality(self, recording_dir: Path, metadata: RecordingMetadata) -> None:
        """Compute quality scores for the recording (non-fatal)."""
        try:
            from meeting_recorder.storage.quality import score_recording

            scores = score_recording(recording_dir)
            metadata.quality_scores = scores
            logger.info(
                "Quality scores: overall=%s audio=%s transcript=%s video=%s",
                scores.get("overall_score"),
                scores.get("audio_score"),
                scores.get("transcript_score"),
                scores.get("video_score"),
            )
        except Exception:
            logger.exception("Quality scoring failed (non-fatal)")

    @staticmethod
    def _auto_tag_recording(recording_dir: Path, metadata: RecordingMetadata) -> None:
        """Automatically suggest and apply tags based on transcript content (non-fatal)."""
        try:
            from meeting_recorder.storage.auto_tag import suggest_tags_for_recording

            meta_dict = {}
            if metadata.tags:
                meta_dict["tags"] = list(metadata.tags)
            suggestions = suggest_tags_for_recording(recording_dir, meta_dict)
            if suggestions:
                existing = set(metadata.tags or [])
                new_tags = [t for t in suggestions if t not in existing]
                if new_tags:
                    metadata.tags = list(existing) + new_tags
                    logger.info("Auto-tagged recording with: %s", ", ".join(new_tags))
        except Exception:
            logger.exception("Auto-tagging failed (non-fatal)")

    @staticmethod
    def _extract_action_items(recording_dir: Path) -> None:
        """Extract and save action items from transcript (non-fatal)."""
        try:
            from meeting_recorder.storage.action_items import (
                extract_action_items_for_recording,
                save_action_items,
            )
            items = extract_action_items_for_recording(recording_dir)
            if items:
                save_action_items(recording_dir, items)
                logger.info("Extracted %d action items for %s",
                            len(items), recording_dir.name)
        except Exception:
            logger.exception("Action item extraction failed (non-fatal)")

    @staticmethod
    def _extract_decisions(recording_dir: Path) -> None:
        """Extract and save decisions from transcript (non-fatal)."""
        try:
            from meeting_recorder.storage.decision_log import (
                extract_recording_decisions,
                save_decisions,
            )
            log = extract_recording_decisions(recording_dir)
            if log and log.decisions:
                save_decisions(recording_dir, log)
                logger.info("Extracted %d decisions for %s",
                            len(log.decisions), recording_dir.name)
        except Exception:
            logger.exception("Decision extraction failed (non-fatal)")

    def _run_retention_cleanup(self, exclude: Path | None = None) -> None:
        """Run recording retention cleanup (non-fatal)."""
        retention = self.config.retention
        if not retention.enabled:
            return
        try:
            # Also exclude the active recording directory
            active = exclude or self._current_recording_dir
            deleted = self._recording_store.cleanup(
                max_age_days=retention.max_age_days,
                max_total_gb=retention.max_total_gb,
                exclude=active,
            )
            if deleted:
                notifications.notify_info(f"Cleaned up {len(deleted)} old recording(s)")
        except Exception:
            logger.exception("Retention cleanup failed (non-fatal)")

    def _open_search(self) -> None:
        """Open the search recordings dialog."""
        from meeting_recorder.ui.search_window import SearchWindow

        search = SearchWindow()
        search.show()

    def _on_audio_levels(
        self, app_rms: float, app_peak: float, mic_rms: float, mic_peak: float
    ) -> None:
        """Handle audio level updates from the capture manager."""
        try:
            self._on_audio_levels_inner(app_rms, app_peak, mic_rms, mic_peak)
        except Exception:
            logger.exception("Error in _on_audio_levels callback")

    def _on_audio_levels_inner(
        self, app_rms: float, app_peak: float, mic_rms: float, mic_peak: float
    ) -> None:
        # Capture reference locally — stop_recording() on another thread could
        # set self._capture_manager = None between the check and later accesses.
        cm = self._capture_manager
        if cm and cm.is_recording:
            elapsed = cm.elapsed_seconds
            duration_str = _format_duration(elapsed)
            proc = self._current_process
            app_name = proc.display_name if proc else "Meeting"

            # Rate-limit tray icon updates to ~1 Hz (every 10th call at 10 Hz)
            # to avoid pystray threading issues from frequent background updates
            self._tray_update_counter += 1
            if self._tray_update_counter >= 10:
                self._tray_update_counter = 0
                self._tray.set_state(
                    "recording",
                    f"Recording {app_name} ({duration_str}) | App: {app_rms:.0f}dB Mic: {mic_rms:.0f}dB",
                )

            # One-time check: detect capture mode and warn dashboard if system-wide
            if not self._capture_mode_reported:
                mode = cm.is_app_capture_process_specific
                if mode is not None:
                    self._capture_mode_reported = True
                    self._main_window.update_audio_mode(not mode)
                    if not mode and self._dashboard:
                        self._dashboard.update_capture_mode(False)

            # Push to main window
            self._main_window.update_audio_levels(app_rms, app_peak, mic_rms, mic_peak)
            self._main_window.update_elapsed(elapsed)

            # Push to dashboard
            if self._dashboard and self._dashboard.is_visible:
                self._dashboard.update_audio_levels(app_rms, app_peak, mic_rms, mic_peak)
                self._dashboard.update_elapsed(elapsed)

            # Push screen preview at ~2 Hz (every 5th call of 10 Hz levels)
            self._preview_frame_counter += 1
            if self._preview_frame_counter >= 5:
                self._preview_frame_counter = 0
                frame = cm.get_screen_frame()
                if frame is not None:
                    if self._dashboard and self._dashboard.is_visible:
                        self._dashboard.update_screen_preview(frame)
                    self._main_window.update_screen_preview(frame)

    def _on_live_transcript(self, text: str) -> None:
        """Handle live transcription preview updates."""
        if text:
            logger.debug("Live preview: %s", text[:80])
            if self._dashboard and self._dashboard.is_visible:
                self._dashboard.update_transcript(text)
            self._main_window.update_transcript(text)

    def _on_live_insight(self, event: dict) -> None:
        """Handle live concept-extraction events (topic shifts, watchlist hits).

        Fired from the live transcriber thread; keyword hits matter most —
        the watchlist is the user's own "ping me when this comes up" list.
        """
        try:
            kind = event.get("type")
            if kind == "keyword":
                keyword = event.get("keyword", "")
                logger.info("Live keyword alert: %r mentioned", keyword)
                notifications.notify_info(f'Watched keyword mentioned: "{keyword}"')
                self._main_window.add_notification(
                    "info", f'Watched keyword mentioned live: "{keyword}"',
                    source="live",
                )
            elif kind == "topic":
                topic = event.get("topic", "")
                logger.info("Live topic detected: %s", topic)
                self._main_window.add_notification(
                    "info", f"Discussion topic: {topic}", source="live",
                )
        except Exception:
            logger.exception("Live insight handling failed")

    def _on_mute_changed(self, is_muted: bool) -> None:
        """Handle mute state changes from MuteSync."""
        if self._dashboard and self._dashboard.is_visible:
            self._dashboard.update_mute_state(is_muted)
        self._main_window.update_mute_state(is_muted)

    def _toggle_pause(self) -> None:
        """Toggle recording pause state."""
        cm = self._capture_manager
        if cm and cm.is_recording:
            cm.toggle_pause()
            is_paused = cm.is_paused
            proc = self._current_process
            app_name = proc.display_name if proc else "Meeting"
            if is_paused:
                self._tray.set_state("recording", f"Paused — {app_name}")
                notifications.notify_info("Recording paused")
            else:
                self._tray.set_state("recording", f"Recording {app_name}")
                notifications.notify_info("Recording resumed")
            if self._dashboard and self._dashboard.is_visible:
                self._dashboard.update_paused(is_paused)
            self._main_window.update_paused(is_paused)

    def _toggle_mute(self) -> None:
        """Toggle mic mute via the capture manager's MuteSync."""
        cm = self._capture_manager
        if cm and cm.mute_sync:
            cm.mute_sync.toggle()

    def _resume_mute_auto_sync(self) -> None:
        """Resume auto mute sync (clears the sticky manual override)."""
        cm = self._capture_manager
        if cm and cm.mute_sync:
            cm.mute_sync.resume_auto_sync()

    def _toggle_audio_mode(self) -> None:
        """Toggle between per-process and desktop-wide audio capture."""
        cm = self._capture_manager
        if cm is None:
            return
        if cm.is_desktop_audio:
            # Switch back to per-process capture using the original meeting PID
            pid = self._current_process.pid if self._current_process else cm.pid
            cm.switch_to_app_audio(pid)
        else:
            cm.switch_to_desktop_audio()

    def _on_capture_mode_changed(self, is_desktop: bool) -> None:
        """Handle capture mode changes from CaptureManager."""
        mode_str = "Desktop Audio" if is_desktop else "App Audio"
        logger.info("Audio capture mode changed to %s", mode_str)
        if self._dashboard and self._dashboard.is_visible:
            self._dashboard.update_audio_mode(is_desktop)
        self._main_window.update_audio_mode(is_desktop)

    def _toggle_dashboard(self) -> None:
        """Toggle dashboard visibility (hotkey handler)."""
        if self._dashboard:
            if self._dashboard.is_visible:
                self._dashboard.hide()
            else:
                self._dashboard.show()

    def _show_dashboard(self) -> None:
        """Show the dashboard (tray menu handler)."""
        if self._dashboard and not self._dashboard.is_visible:
            self._dashboard.show()

    def _close_dashboard(self) -> None:
        """Close the dashboard and persist position to config."""
        if self._dashboard:
            # Persist position
            x, y = self._dashboard.position_xy
            if x >= 0 and y >= 0:
                self.config.dashboard.position_x = x
                self.config.dashboard.position_y = y
                try:
                    self.config.save()
                except Exception:
                    logger.debug("Failed to persist dashboard position", exc_info=True)

            if self.config.dashboard.auto_hide:
                try:
                    self._dashboard.close()
                except Exception:
                    logger.debug("Dashboard close failed", exc_info=True)
                self._dashboard = None

    def _on_health_warning(self, warning_key: str) -> None:
        """Called when a capture issue is detected.

        Deduplicates: each warning_key fires its notification at most once
        per recording session.  The UI banner always updates (cheap).
        """
        # Deduplicate notifications per recording session
        already_shown = warning_key in self._health_warnings_shown
        self._health_warnings_shown.add(warning_key)

        messages = {
            "system_volume_muted": "System volume is muted \u2014 desktop audio will be silent!",
            "app_audio_silent": "No audio detected for 10s \u2014 check volume or switch audio mode",
            "silence_auto_switch": "No app audio detected \u2014 switched to desktop audio",
            "app_write_error": "Audio write error \u2014 recording may be incomplete",
            "mic_write_error": "Mic write error \u2014 recording may be incomplete",
            "window_pid_failed": "Selected window is no longer available",
        }
        msg = messages.get(warning_key, f"Warning: {warning_key} may have stalled")
        logger.warning("Health warning: %s", msg)

        if not already_shown:
            notifications.notify_info(msg)
            if self._dashboard and self._dashboard.is_visible:
                self._dashboard.update_transcript(f"[\u26a0 {msg}]")
            self._main_window.update_transcript(f"[\u26a0 {msg}]")

        # Always update the warning banner (visible, cheap)
        self._main_window.show_warning(msg)

    def _on_capture_auto_stopped(self) -> None:
        """Called when capture stops automatically (e.g., meeting app exits)."""
        logger.info("Capture auto-stopped (meeting app exited).")
        self.stop_recording()

    # ------------------------------------------------------------------
    # Meeting auto-detection scanner
    # ------------------------------------------------------------------

    def _start_meeting_scanner(self) -> None:
        """Start the background thread that polls for meeting processes."""
        self._scanner_stop.clear()
        self._scanner_thread = threading.Thread(
            target=self._meeting_scanner_loop,
            name="meeting-scanner",
            daemon=True,
        )
        self._scanner_thread.start()

    def _stop_meeting_scanner(self) -> None:
        """Signal the meeting scanner to stop."""
        self._scanner_stop.set()

    def _meeting_scanner_loop(self) -> None:
        """Poll for meeting processes every 5 seconds while idle.

        When a meeting app is found with a window score above the threshold
        (indicating an active meeting, not just the idle app lobby), auto-start
        recording and pause scanning. Scanning resumes when the recording
        stops.
        """
        poll_interval = 5.0
        # After auto-start, wait this many seconds before scanning again
        # (gives time for recording to fully stop + post-process).
        cooldown_after_recording = 10.0

        logger.info("Meeting scanner started (polling every %.0fs).", poll_interval)

        while not self._scanner_stop.is_set():
            self._scanner_stop.wait(poll_interval)
            if self._scanner_stop.is_set():
                break

            # Skip if already recording or processing
            cm = self._capture_manager
            if cm and cm.is_recording:
                continue

            # Scan for meeting processes with high-scoring windows
            try:
                processes = find_meeting_processes()
                if not processes:
                    continue

                # Check each app for an active meeting window
                checked_apps: set[str] = set()
                for proc in processes:
                    if proc.app_key in checked_apps:
                        continue
                    checked_apps.add(proc.app_key)

                    from meeting_recorder.audio.process_finder import _find_meeting_window_pid
                    pid, score = _find_meeting_window_pid(proc.app_key)
                    if pid is not None and score >= self._auto_start_min_score:
                        logger.info(
                            "Auto-detected %s meeting (PID %d, score %d). "
                            "Starting recording...",
                            proc.display_name, pid, score,
                        )
                        # Build a MeetingProcess for the best window PID
                        auto_proc = MeetingProcess(
                            pid=pid,
                            name=proc.name,
                            app_key=proc.app_key,
                            display_name=proc.display_name,
                        )
                        try:
                            self._current_process = auto_proc
                            self._start_recording_for_process(auto_proc)
                            notifications.notify_info(
                                f"Auto-recording: {auto_proc.display_name}"
                            )
                        except Exception:
                            logger.exception(
                                "Auto-start failed for %s",
                                proc.display_name,
                            )
                            self._capture_manager = None
                            self._current_recording_dir = None
                            self._current_metadata = None
                            self._current_process = None
                            self._tray.set_state("idle")
                        break  # Only start one recording at a time

            except Exception:
                logger.exception("Meeting scanner error")

            # If a recording just finished, cool down before scanning again
            if self._post_thread and self._post_thread.is_alive():
                self._scanner_stop.wait(cooldown_after_recording)

        logger.info("Meeting scanner stopped.")

    def _toggle_auto_start(self, enabled: bool) -> None:
        """Toggle meeting auto-detection on/off (tray menu handler)."""
        self.config.recording.auto_start = enabled
        self._main_window.update_auto_start(enabled)
        try:
            self.config.save()
        except Exception:
            logger.debug("Failed to persist auto_start setting", exc_info=True)

        if enabled:
            if self._scanner_thread is None or not self._scanner_thread.is_alive():
                self._start_meeting_scanner()
            logger.info("Meeting auto-detection enabled.")
        else:
            self._stop_meeting_scanner()
            logger.info("Meeting auto-detection disabled.")

    def _open_settings(self) -> None:
        """Open the settings dialog."""
        from meeting_recorder.ui.settings_window import SettingsWindow

        def on_save(new_config: Config):
            self.config = new_config
            self._recording_store = RecordingStore(self.config.output_dir)
            self._pipeline = TranscriptionPipeline(self.config)
            self._unregister_hotkey()
            self._register_hotkey()

        settings = SettingsWindow(self.config, on_save=on_save)
        settings.show()

    def _show_main_window(self) -> None:
        """Show/raise the main application window."""
        self._main_window.show()

    def _list_recent_recordings_full(self) -> list[Path]:
        """Return up to 20 recent recording directories (for main window)."""
        return self._recording_store.list_recordings()[:20]

    def reprocess_recording(self, recording_dir: Path) -> None:
        """Re-run transcription and summary on an existing recording.

        Uses current config (not original recording config).
        Skips if a post-processing thread is already running.
        """
        if self._post_thread and self._post_thread.is_alive():
            notifications.notify_error("Post-processing already running. Please wait.")
            return

        recording_dir = Path(recording_dir)
        if not recording_dir.exists():
            notifications.notify_error("Recording directory not found.")
            return

        # Check for at least one audio file
        has_audio = (
            (recording_dir / "app_audio.wav").exists()
            or (recording_dir / "mic_audio.wav").exists()
        )
        if not has_audio:
            notifications.notify_error("No audio files found in recording directory.")
            return

        # Load existing metadata
        import json
        meta_path = recording_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                metadata = RecordingMetadata(**{
                    k: v for k, v in data.items()
                    if k in RecordingMetadata.__dataclass_fields__
                })
            except Exception:
                metadata = RecordingMetadata()
        else:
            metadata = RecordingMetadata()

        # Refresh pipeline with current config
        self._pipeline = TranscriptionPipeline(self.config)

        logger.info("Re-processing recording: %s", recording_dir)
        notifications.notify_info("Re-processing recording...")
        self._main_window.update_status_bar("Re-processing recording...")

        _rec_dir = recording_dir
        _meta = metadata

        def _reprocess_wrapper() -> None:
            try:
                self._post_process(_rec_dir, _meta, self.config, _meta.duration_seconds)
            finally:
                self._post_thread = None

        self._post_thread = threading.Thread(
            target=_reprocess_wrapper,
            name="reprocess",
            daemon=False,
        )
        self._post_thread.start()

    def _startup_retry_sweep(self) -> None:
        """Heal recordings left broken by crashes or transient failures.

        Runs once in the background shortly after startup: fully
        re-processes retryable failures and stale 'processing' leftovers
        (capped to avoid hour-long startup churn), and re-runs just the
        missing tail steps (summary, Drive upload) for completed
        recordings flagged summary_failed/upload_pending.
        """
        import time as _time

        from meeting_recorder import recovery

        _time.sleep(15.0)  # let startup settle first

        # Never run heavy re-processing while a meeting is being recorded —
        # both would hit the (often free-tier) transcription API at once.
        if self._capture_manager and self._capture_manager.is_recording:
            logger.info("Startup retry sweep skipped: recording in progress")
            return

        try:
            found = recovery.find_recoverable(self.config.output_dir)
        except Exception:
            logger.exception("Startup retry sweep scan failed")
            return

        full = (found.failed_retryable + found.stuck_processing)[:5]
        if not full and not found.incomplete_tail:
            return
        logger.info(
            "Startup retry sweep: %d tail retr%s, %d full re-process(es) %s",
            len(found.incomplete_tail),
            "y" if len(found.incomplete_tail) == 1 else "ies",
            len(full),
            "(enabled)" if self.config.recording.auto_retry_full_reprocess
            else "(manual — auto full re-process is off)",
        )

        # Tail retries (a missing summary / Drive upload on an already-
        # transcribed recording) are cheap and bounded — always run them.
        for rec_dir in found.incomplete_tail:
            if self._capture_manager and self._capture_manager.is_recording:
                return
            try:
                performed = recovery.retry_tail(rec_dir, self.config)
                if performed:
                    self._main_window.add_notification(
                        "success",
                        f"Recovered {' + '.join(performed)} for {rec_dir.name}",
                        source="recovery",
                    )
            except Exception:
                logger.exception("Tail retry failed for %s", rec_dir.name)

        # Full re-processing RE-TRANSCRIBES via the configured backend — on a
        # free-tier Gemini key this can exhaust the daily quota and starve a
        # live meeting's transcription/summary. Off by default; surface the
        # count so the user can reprocess manually (button or CLI) when ready.
        if full and not self.config.recording.auto_retry_full_reprocess:
            self._main_window.add_notification(
                "info",
                f"{len(full)} recording(s) need re-processing — open one and "
                f"click Re-process (auto is off to protect API quota).",
                source="recovery",
            )
        elif self.config.recording.auto_retry_full_reprocess:
            for rec_dir in full:
                if self._post_thread and self._post_thread.is_alive():
                    self._post_thread.join(timeout=600)
                if self._capture_manager and self._capture_manager.is_recording:
                    logger.info("Retry sweep paused: recording in progress")
                    return
                try:
                    self.reprocess_recording(rec_dir)
                    if self._post_thread:
                        self._post_thread.join(timeout=600)
                except Exception:
                    logger.exception("Sweep re-process failed for %s", rec_dir.name)

        self._main_window.refresh_history()

    def reprocess_all_failed(self) -> None:
        """Re-process all recordings with status 'error', one at a time."""
        import json as _json

        recordings = self._recording_store.list_recordings()
        failed: list[Path] = []
        for rec_path in recordings:
            meta_path = rec_path / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = _json.load(f)
                if meta.get("status") == "error":
                    failed.append(rec_path)
            except Exception:
                continue

        if not failed:
            notifications.notify_info("No failed recordings to re-process.")
            return

        logger.info("Batch re-processing %d failed recording(s)", len(failed))
        notifications.notify_info(f"Re-processing {len(failed)} failed recording(s)...")

        processed = 0
        for i, rec_path in enumerate(failed, 1):
            # Wait for any in-flight post-processing to complete first
            if self._post_thread and self._post_thread.is_alive():
                self._main_window.update_status_bar(
                    f"Waiting for previous re-process to finish ({i}/{len(failed)})..."
                )
                self._post_thread.join(timeout=600)  # 10 min max per recording

            self._main_window.update_status_bar(
                f"Re-processing {i}/{len(failed)}: {rec_path.name}"
            )
            self.reprocess_recording(rec_path)
            processed += 1

        # Wait for the last one to finish
        if self._post_thread and self._post_thread.is_alive():
            self._post_thread.join(timeout=600)

        self._main_window.update_status_bar("Batch re-processing complete.")
        self._main_window.refresh_history()
        notifications.notify_info(f"Re-processed {processed}/{len(failed)} recording(s).")

    def import_audio(self, file_path: str | Path) -> None:
        """Import an external audio file and run post-processing on it.

        Copies the file into a new recording directory as app_audio.wav
        (converting from MP3/M4A/etc. if needed), then runs the transcription
        and summary pipeline.
        """
        import shutil
        import wave

        file_path = Path(file_path)
        if not file_path.exists():
            notifications.notify_error(f"File not found: {file_path}")
            return

        if self._post_thread and self._post_thread.is_alive():
            notifications.notify_error("Post-processing already running. Please wait.")
            return

        # Create recording directory
        stem = file_path.stem
        recording_dir = self._recording_store.create_recording_dir(
            app_name="Import",
            meeting_subject=stem,
        )

        # Copy/convert to app_audio.wav
        dest_wav = recording_dir / "app_audio.wav"
        suffix = file_path.suffix.lower()
        if suffix == ".wav":
            shutil.copy2(file_path, dest_wav)
        else:
            # Convert MP3/M4A/OGG/FLAC/etc. to WAV via pydub
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(str(file_path))
                audio.export(str(dest_wav), format="wav")
            except Exception as exc:
                logger.exception("Failed to convert %s to WAV", file_path)
                notifications.notify_error(f"Cannot convert {suffix} file: {exc}")
                shutil.rmtree(recording_dir, ignore_errors=True)
                return

        if not _validate_wav(dest_wav):
            notifications.notify_error("Imported audio is corrupt or empty.")
            shutil.rmtree(recording_dir, ignore_errors=True)
            return

        # Get duration from WAV
        try:
            with wave.open(str(dest_wav), "rb") as wf:
                duration = wf.getnframes() / wf.getframerate()
        except Exception:
            duration = 0.0

        # Create metadata
        metadata = RecordingMetadata(
            app_name="Import",
            start_time=datetime.now().isoformat(),
            has_app_audio=True,
            duration_seconds=duration,
            sample_rate=16000,
            channels=1,
            language=self.config.recording.language,
            transcription_backend=self.config.transcription.backend,
            status="processing",
            meeting_subject=stem,
        )
        self._save_metadata(metadata, recording_dir)

        # Refresh pipeline with current config
        self._pipeline = TranscriptionPipeline(self.config)

        logger.info("Importing audio: %s -> %s", file_path, recording_dir)
        notifications.notify_info(f"Importing: {file_path.name}")
        self._main_window.update_status_bar(f"Importing: {file_path.name}...")
        self._main_window.add_notification("info", f"Imported audio: {file_path.name}", source="import")

        _imp_rec = recording_dir
        _imp_meta = metadata
        _imp_dur = duration

        def _import_wrapper() -> None:
            try:
                self._post_process(_imp_rec, _imp_meta, self.config, _imp_dur)
            finally:
                self._post_thread = None

        self._post_thread = threading.Thread(
            target=_import_wrapper,
            name="import-audio",
            daemon=False,
        )
        self._post_thread.start()

    def _import_audio_from_tray(self) -> None:
        """Open a file dialog and import the selected audio file."""
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        file_path = filedialog.askopenfilename(
            title="Import Audio File",
            filetypes=[
                ("Audio files", "*.wav *.mp3 *.m4a *.ogg *.flac *.wma *.aac"),
                ("WAV files", "*.wav"),
                ("MP3 files", "*.mp3"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        if file_path:
            self.import_audio(file_path)

    def _list_capturable_windows(self) -> list:
        """Return windows available for capture switching (during recording)."""
        cm = self._capture_manager
        if cm and cm.is_recording:
            return cm.list_capturable_windows()
        return []

    def _open_recordings_folder(self) -> None:
        """Open the recordings folder in the file explorer."""
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        open_in_explorer(str(output_dir))

    def _open_last_recording(self) -> None:
        """Open the most recent recording folder in the file explorer."""
        latest = self._recording_store.get_latest_recording()
        if latest and latest.exists():
            open_in_explorer(str(latest))
        else:
            notifications.notify_info("No recordings found.")

    def _list_recent_recordings(self) -> list[Path]:
        """Return the 5 most recent recording directories."""
        return self._recording_store.list_recordings()[:5]

    def _open_recording(self, path: Path) -> None:
        """Open a specific recording folder in the file explorer."""
        if path.exists():
            open_in_explorer(str(path))
        else:
            notifications.notify_info(f"Recording not found: {path.name}")

    def _on_pick_capture_window(self, hwnd: int) -> None:
        """Switch screen capture (if active) and app audio capture to the selected window."""
        cm = self._capture_manager
        if cm is None:
            return
        from meeting_recorder.video.platforms import get_window_title
        title = get_window_title(hwnd) or f"HWND {hwnd}"
        logger.info("User picked window: '%s' — switching capture", title)
        cm.switch_screen_window(hwnd)

    def _register_hotkey(self) -> None:
        """Register global hotkeys for toggling recording and dashboard."""
        try:
            import keyboard

            hotkey = self.config.hotkey.toggle_recording
            keyboard.add_hotkey(hotkey, self._toggle_recording)

            dash_hotkey = self.config.hotkey.toggle_dashboard
            keyboard.add_hotkey(dash_hotkey, self._toggle_dashboard)

            pause_hotkey = self.config.hotkey.toggle_pause
            keyboard.add_hotkey(pause_hotkey, self._toggle_pause)

            self._hotkey_registered = True
            logger.info("Global hotkeys registered: %s, %s, %s", hotkey, dash_hotkey, pause_hotkey)
        except ImportError:
            logger.warning("keyboard module not installed. Global hotkey disabled.")
        except Exception:
            logger.exception("Failed to register global hotkeys")

    def _unregister_hotkey(self) -> None:
        """Unregister the global hotkey."""
        if self._hotkey_registered:
            try:
                import keyboard
                keyboard.unhook_all_hotkeys()
                self._hotkey_registered = False
            except Exception:
                logger.exception("Failed to unregister hotkey")

    def _toggle_recording(self) -> None:
        """Toggle recording on/off (hotkey handler)."""
        cm = self._capture_manager
        if cm and cm.is_recording:
            threading.Thread(target=self.stop_recording, daemon=True).start()
        else:
            threading.Thread(target=self.start_recording, daemon=True).start()

    def _find_mic_device_index(self, device_name: str) -> Optional[int]:
        """Find microphone device index by name."""
        try:
            import pyaudiowpatch as pyaudio
            from meeting_recorder.audio._pyaudio_lock import pyaudio_init_lock

            with pyaudio_init_lock:
                p = pyaudio.PyAudio()
            try:
                for i in range(p.get_device_count()):
                    info = p.get_device_info_by_index(i)
                    if device_name.lower() in info["name"].lower() and info["maxInputChannels"] > 0:
                        return i
            finally:
                p.terminate()
        except Exception:
            logger.exception("Failed to find mic device: %s", device_name)
        return None


def _validate_wav(path: Path) -> bool:
    """Check that a WAV file has a valid header and non-zero duration."""
    try:
        import wave
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if frames <= 0 or rate <= 0:
                return False
            duration = frames / rate
            return duration > 0.1  # at least 100ms
    except Exception:
        return False


def _format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable string."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"
