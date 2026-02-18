"""Application orchestrator for Meeting Recorder."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from meeting_recorder.config import Config
from meeting_recorder.audio.process_finder import find_primary_meeting_process, MeetingProcess
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

        # Prevents double-invocation of stop_recording() from concurrent callers
        # (e.g. user clicks Stop while process-exit auto-stop fires simultaneously)
        self._stop_lock = threading.Lock()

        # Dashboard overlay
        self._dashboard: Optional[GameBarDashboard] = None
        self._capture_mode_reported: bool = False
        self._preview_frame_counter: int = 0

        # System tray
        self._tray = TrayIcon(
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_quit=self.quit,
            on_settings=self._open_settings,
            on_open_recordings=self._open_recordings_folder,
            on_search=self._open_search,
            on_show_dashboard=self._show_dashboard,
        )

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
        # Tray icon runs on the main thread (blocks)
        self._tray.run()

    def start_recording(self) -> None:
        """Start recording the active meeting."""
        if self._capture_manager and self._capture_manager.is_recording:
            logger.warning("Already recording.")
            return

        # Find meeting process
        process = find_primary_meeting_process()
        if process is None:
            logger.warning("No meeting application found.")
            notifications.notify_no_meeting_found()
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

    def _start_recording_for_process(self, process: MeetingProcess) -> None:
        """Inner method that performs all recording setup. Raises on failure."""
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
        self._current_metadata.save(self._current_recording_dir)

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
                on_list_windows=(
                    self._capture_manager.list_capturable_windows
                    if self.config.screen_recording.enabled else None
                ),
                on_pick_window=(
                    self._on_pick_capture_window
                    if self.config.screen_recording.enabled else None
                ),
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
            elapsed = capture_manager.elapsed_seconds
            self._capture_manager = None
            self._current_recording_dir = None
            self._current_metadata = None
            self._current_process = None

        capture_manager.stop()

        # Hide/close dashboard and persist position
        self._close_dashboard()

        duration_str = _format_duration(elapsed)
        notifications.notify_recording_stopped(duration_str)
        logger.info("Recording stopped. Duration: %s", duration_str)

        threading.Thread(
            target=self._post_process,
            args=(recording_dir, metadata),
            name="post-processing",
            daemon=True,
        ).start()

    def quit(self) -> None:
        """Quit the application."""
        logger.info("Quitting Meeting Recorder...")
        if self._capture_manager and self._capture_manager.is_recording:
            self._capture_manager.stop()
        self._close_dashboard()
        self._unregister_hotkey()
        self._tray.stop()

    def _post_process(self, recording_dir: Path, metadata: RecordingMetadata) -> None:
        """Run post-recording pipeline: mix, transcribe, format."""
        try:
            self._tray.set_state("processing", "Transcribing...")
            notifications.notify_transcription_started()
            metadata.status = "processing"
            metadata.save(recording_dir)

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
                formats=self.config.output.formats,
            )

            # Generate AI summary if enabled
            if self.config.summary.enabled:
                self._generate_summary(recording_dir, segments, metadata)

            # Finalize metadata
            speakers = set(s.speaker for s in segments if s.speaker)
            metadata.finalize(
                recording_dir,
                speaker_count=len(speakers),
                segment_count=len(segments),
            )

            # Index recording for search
            self._index_recording(recording_dir)

            # Upload to Google Drive if enabled
            if self.config.google_drive.enabled:
                self._upload_to_google_drive(recording_dir, metadata)

            self._tray.set_state("idle")
            notifications.notify_transcription_complete(str(recording_dir))
            logger.info("Post-processing complete: %s", recording_dir)

        except Exception as e:
            logger.exception("Post-processing failed")
            self._tray.set_state("error", f"Error: {e}")
            notifications.notify_error(f"Transcription failed: {e}")
            if metadata:
                metadata.set_error(str(e), recording_dir)

    def _upload_to_google_drive(self, recording_dir: Path, metadata: RecordingMetadata) -> None:
        """Upload recording to Google Drive."""
        try:
            creds_path = Path(self.config.google_drive.credentials_path).expanduser()
            if not is_google_drive_available(creds_path):
                logger.info("Google Drive not available (missing credentials or libraries).")
                return

            self._tray.set_state("processing", "Uploading to Drive...")
            uploader = GoogleDriveUploader(
                credentials_path=creds_path,
                folder_id=self.config.google_drive.folder_id,
            )
            folder_id = uploader.upload_recording(recording_dir)
            if folder_id:
                metadata.google_drive_folder_id = folder_id
                metadata.save(recording_dir)
                logger.info("Google Drive upload complete: %s", folder_id)
            else:
                logger.warning("Google Drive upload returned no folder ID.")
        except Exception:
            logger.exception("Google Drive upload failed (non-fatal)")

    def _generate_summary(
        self, recording_dir: Path, segments, metadata: RecordingMetadata
    ) -> None:
        """Generate AI meeting summary (non-fatal)."""
        try:
            from meeting_recorder.summary.summarizer import generate_summary, save_summary

            self._tray.set_state("processing", "Generating summary...")
            summary = generate_summary(
                segments=segments,
                config=self.config.summary,
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
        # Update tray tooltip with current audio levels during recording
        if self._capture_manager and self._capture_manager.is_recording:
            elapsed = self._capture_manager.elapsed_seconds
            duration_str = _format_duration(elapsed)
            app_name = self._current_process.display_name if self._current_process else "Meeting"
            self._tray.set_state(
                "recording",
                f"Recording {app_name} ({duration_str}) | App: {app_rms:.0f}dB Mic: {mic_rms:.0f}dB",
            )

            # One-time check: detect capture mode and warn dashboard if system-wide
            if not self._capture_mode_reported:
                mode = self._capture_manager.is_app_capture_process_specific
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
                    frame = self._capture_manager.get_screen_frame()
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
        if self._capture_manager and self._capture_manager.mute_sync:
            self._capture_manager.mute_sync.toggle()

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

    def _on_capture_auto_stopped(self) -> None:
        """Called when capture stops automatically (e.g., meeting app exits)."""
        logger.info("Capture auto-stopped (meeting app exited).")
        self.stop_recording()

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
        """Switch the screen capture to the window the user selected."""
        if self._capture_manager is not None:
            self._capture_manager.switch_screen_window(hwnd)

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
        if self._capture_manager and self._capture_manager.is_recording:
            threading.Thread(target=self.stop_recording, daemon=True).start()
        else:
            threading.Thread(target=self.start_recording, daemon=True).start()

    def _find_mic_device_index(self, device_name: str) -> Optional[int]:
        """Find microphone device index by name."""
        try:
            import pyaudiowpatch as pyaudio

            p = pyaudio.PyAudio()
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if device_name.lower() in info["name"].lower() and info["maxInputChannels"] > 0:
                    p.terminate()
                    return i
            p.terminate()
        except Exception:
            logger.exception("Failed to find mic device: %s", device_name)
        return None


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
