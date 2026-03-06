"""Application orchestrator for Meeting Recorder."""

from __future__ import annotations

import copy
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from meeting_recorder.config import Config
from meeting_recorder.audio.process_finder import (
    find_primary_meeting_process,
    find_meeting_processes,
    _find_meeting_window_pid,
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
from meeting_recorder.ui import notifications
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

        # Pre-loaded VAD model (loaded once in run(), reused across recordings)
        self._vad: Optional[VoiceActivityDetector] = None

        # Snapshot of config at recording start (used for post-processing)
        self._recording_config: Optional[Config] = None

        # Post-processing thread (non-daemon, so quit() can wait for it)
        self._post_thread: Optional[threading.Thread] = None

        # Prevents double-invocation of stop_recording() from concurrent callers
        # (e.g. user clicks Stop while process-exit auto-stop fires simultaneously)
        self._stop_lock = threading.Lock()

        # Serialises all metadata.save() calls across threads (mark processing,
        # final save, summary callback, Drive upload callback) to avoid file corruption.
        self._metadata_lock = threading.Lock()

        # Dashboard overlay
        self._dashboard: Optional[GameBarDashboard] = None
        self._capture_mode_reported: bool = False
        self._preview_frame_counter: int = 0

        # Meeting auto-detection scanner
        self._scanner_thread: Optional[threading.Thread] = None
        self._scanner_stop = threading.Event()
        # Minimum window score for auto-start (50 = likely meeting in Teams,
        # 100 = definite meeting in Zoom, avoids idle lobby windows)
        self._auto_start_min_score = 50

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
            auto_start=self.config.recording.auto_start,
            hotkey_recording=self.config.hotkey.toggle_recording,
            hotkey_mute=self.config.hotkey.toggle_mute,
            hotkey_dashboard=self.config.hotkey.toggle_dashboard,
        )

    def _save_metadata(self, metadata: RecordingMetadata, recording_dir: Path) -> None:
        """Thread-safe metadata save."""
        with self._metadata_lock:
            metadata.save(recording_dir)

    def run(self) -> None:
        """Start the application. Blocks until quit."""
        logger.info("Meeting Recorder starting...")
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

        # Start meeting auto-detection scanner
        if self.config.recording.auto_start:
            self._start_meeting_scanner()
            logger.info("Meeting auto-detection enabled.")

        # Tray icon runs on the main thread (blocks)
        self._tray.run()

    def start_recording(self) -> None:
        """Start recording the active meeting.

        If no known meeting app is detected, opens a window picker so the
        user can manually select any window to record.
        """
        cm = self._capture_manager
        if cm and cm.is_recording:
            logger.warning("Already recording.")
            return

        # Find meeting process
        process = find_primary_meeting_process()
        if process is None:
            logger.info("No meeting application found. Opening window picker...")
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
        from meeting_recorder.video.window_finder import list_visible_windows

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
        """Tray menu handler: open a window picker then start recording.

        Always shows the picker regardless of whether a meeting app is
        detected. If the user cancels, returns silently.
        """
        cm = self._capture_manager
        if cm and cm.is_recording:
            logger.warning("Already recording.")
            return

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
            process_name=process.name,
            app_key=process.app_key,
            mute_toggle_hotkey=self.config.hotkey.toggle_mute,
            on_audio_levels=self._on_audio_levels,
            on_live_transcript=self._on_live_transcript,
            live_transcription_enabled=self.config.recording.live_transcription,
            on_mute_changed=self._on_mute_changed,
            vad=self._vad,
            on_health_warning=self._on_health_warning,
            on_capture_mode_changed=self._on_capture_mode_changed,
        )
        self._capture_mode_reported = False
        self._preview_frame_counter = 0
        self._capture_manager.start()

        # Show dashboard overlay
        dash_cfg = self.config.dashboard
        if dash_cfg.enabled and dash_cfg.auto_show:
            self._dashboard = GameBarDashboard(
                on_stop=lambda: threading.Thread(target=self.stop_recording, daemon=True).start(),
                on_toggle_mute=self._toggle_mute,
                on_open_recordings=self._open_recordings_folder,
                on_open_settings=self._open_settings,
                on_list_windows=self._capture_manager.list_capturable_windows,
                on_pick_window=self._on_pick_capture_window,
                on_toggle_audio_mode=self._toggle_audio_mode,
                opacity=dash_cfg.opacity,
                start_collapsed=dash_cfg.start_collapsed,
                show_transcript=dash_cfg.show_transcript,
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
        notifications.notify_recording_started(process.display_name)
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

        capture_manager.stop()

        # Hide/close dashboard and persist position
        self._close_dashboard()

        duration_str = _format_duration(elapsed)
        notifications.notify_recording_stopped(duration_str)
        logger.info("Recording stopped. Duration: %s", duration_str)

        self._post_thread = threading.Thread(
            target=self._post_process,
            args=(recording_dir, metadata, recording_config),
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

        self._unregister_hotkey()
        self._tray.stop()

    def _post_process(
        self,
        recording_dir: Path,
        metadata: RecordingMetadata,
        config: Optional[Config] = None,
    ) -> None:
        """Run post-recording pipeline: mix, transcribe, format.

        Args:
            recording_dir: Directory containing audio files.
            metadata: Recording metadata.
            config: Config snapshot from recording start.  Falls back to
                self.config if None (for backwards compatibility).
        """
        cfg = config or self.config
        try:
            self._tray.set_state("processing", "Transcribing...")
            notifications.notify_transcription_started()
            metadata.status = "processing"
            self._save_metadata(metadata, recording_dir)

            # Validate app audio before mixing / transcription
            app_wav = recording_dir / "app_audio.wav"
            if not _validate_wav(app_wav):
                logger.warning("app_audio.wav is corrupt or empty — skipping transcription")
                notifications.notify_error("App audio file is corrupt — transcription skipped")
                metadata.status = "error"
                metadata.error_message = "App audio file corrupt or empty"
                self._save_metadata(metadata, recording_dir)
                self._tray.set_state("idle")
                return

            # Mix audio tracks
            app_audio = recording_dir / "app_audio.wav"
            mic_audio = recording_dir / "mic_audio.wav"
            mixed_audio = recording_dir / "mixed.wav"

            if app_audio.exists() and mic_audio.exists():
                mix_tracks_streaming(app_audio, mic_audio, mixed_audio)

            # Run transcription pipeline (with speaker resolution if attendees available)
            segments = self._pipeline.process(
                recording_dir,
                attendees=metadata.meeting_attendees,
                organizer=metadata.meeting_organizer,
            )

            # Clean up transient mixed.wav — it's large and only needed for transcription
            if mixed_audio.exists():
                mixed_audio.unlink()
                logger.info("Cleaned up transient mixed.wav")

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
            )

            # Run summary, indexing, and Drive upload in parallel
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="post") as pool:
                futures = []
                if summary_config.enabled:
                    futures.append(pool.submit(
                        self._generate_summary, recording_dir, segments, metadata, summary_config,
                    ))
                futures.append(pool.submit(self._index_recording, recording_dir))
                if cfg.google_drive.enabled:
                    futures.append(pool.submit(
                        self._upload_to_google_drive, recording_dir, metadata, cfg,
                    ))
                for f in as_completed(futures):
                    f.result()  # propagate exceptions (each method catches its own)

            # Final save: parallel tasks (summary, Drive upload) modify metadata
            # fields that finalize() didn't know about. Persist them now.
            self._save_metadata(metadata, recording_dir)

            self._tray.set_state("idle")
            notifications.notify_transcription_complete(str(recording_dir))
            logger.info("Post-processing complete: %s", recording_dir)

        except Exception as e:
            logger.exception("Post-processing failed")
            self._tray.set_state("error", f"Error: {e}")
            notifications.notify_error(f"Transcription failed: {e}")
            if metadata:
                metadata.set_error(str(e), recording_dir)

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
                self._save_metadata(metadata, recording_dir)
                logger.info("Google Drive upload complete: %s", folder_id)
            else:
                logger.warning("Google Drive upload returned no folder ID.")
        except Exception:
            logger.exception("Google Drive upload failed (non-fatal)")

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
            metadata.summary_provider = summary.provider_used
            metadata.summary_model = summary.model_used
            logger.info("AI summary generated successfully.")
        except Exception:
            logger.exception("AI summary generation failed (non-fatal)")

    def _index_recording(self, recording_dir: Path) -> None:
        """Add recording to the search index (non-fatal)."""
        try:
            from meeting_recorder.search.index import RecordingIndex

            index = RecordingIndex()
            index.index_recording(recording_dir)
            index.close()
        except Exception:
            logger.exception("Search indexing failed (non-fatal)")

    def _open_search(self) -> None:
        """Open the search recordings dialog."""
        from meeting_recorder.ui.search_window import SearchWindow

        search = SearchWindow()
        search.show()

    def _on_audio_levels(
        self, app_rms: float, app_peak: float, mic_rms: float, mic_peak: float
    ) -> None:
        """Handle audio level updates from the capture manager."""
        # Capture reference locally — stop_recording() on another thread could
        # set self._capture_manager = None between the check and later accesses.
        cm = self._capture_manager
        if cm and cm.is_recording:
            elapsed = cm.elapsed_seconds
            duration_str = _format_duration(elapsed)
            proc = self._current_process
            app_name = proc.display_name if proc else "Meeting"
            self._tray.set_state(
                "recording",
                f"Recording {app_name} ({duration_str}) | App: {app_rms:.0f}dB Mic: {mic_rms:.0f}dB",
            )

            # One-time check: detect capture mode and warn dashboard if system-wide
            if not self._capture_mode_reported:
                mode = cm.is_app_capture_process_specific
                if mode is not None:
                    self._capture_mode_reported = True
                    if not mode and self._dashboard:
                        self._dashboard.update_capture_mode(False)

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
                        self._dashboard.update_screen_preview(frame)

    def _on_live_transcript(self, text: str) -> None:
        """Handle live transcription preview updates."""
        if text:
            logger.debug("Live preview: %s", text[:80])
            if self._dashboard and self._dashboard.is_visible:
                self._dashboard.update_transcript(text)

    def _on_mute_changed(self, is_muted: bool) -> None:
        """Handle mute state changes from MuteSync."""
        if self._dashboard and self._dashboard.is_visible:
            self._dashboard.update_mute_state(is_muted)

    def _toggle_mute(self) -> None:
        """Toggle mic mute via the capture manager's MuteSync."""
        cm = self._capture_manager
        if cm and cm.mute_sync:
            cm.mute_sync.toggle()

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
        mode_str = "desktop (system-wide)" if is_desktop else "per-process"
        logger.info("Audio capture mode changed to %s", mode_str)
        if self._dashboard and self._dashboard.is_visible:
            self._dashboard.update_audio_mode(is_desktop)

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
                self._dashboard.close()
                self._dashboard = None

    def _on_health_warning(self, warning_key: str) -> None:
        """Called when a capture issue is detected."""
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
        notifications.notify_info(msg)
        if self._dashboard and self._dashboard.is_visible:
            self._dashboard.update_transcript(f"[\u26a0 {msg}]")

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

    def _open_recordings_folder(self) -> None:
        """Open the recordings folder in the file explorer."""
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(output_dir))

    def _on_pick_capture_window(self, hwnd: int) -> None:
        """Switch screen capture (if active) and app audio capture to the selected window."""
        cm = self._capture_manager
        if cm is None:
            return
        from meeting_recorder.video.window_finder import get_window_title
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

            self._hotkey_registered = True
            logger.info("Global hotkeys registered: %s, %s", hotkey, dash_hotkey)
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
