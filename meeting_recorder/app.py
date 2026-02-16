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
from meeting_recorder.audio.mixer import mix_tracks
from meeting_recorder.transcription.pipeline import TranscriptionPipeline
from meeting_recorder.storage.recording_store import RecordingStore
from meeting_recorder.storage.metadata import RecordingMetadata
from meeting_recorder.storage.transcript_formatter import save_all_formats
from meeting_recorder.ui.tray import TrayIcon
from meeting_recorder.ui import notifications

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

        # System tray
        self._tray = TrayIcon(
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            on_quit=self.quit,
            on_settings=self._open_settings,
            on_open_recordings=self._open_recordings_folder,
        )

    def run(self) -> None:
        """Start the application. Blocks until quit."""
        logger.info("Meeting Recorder starting...")
        self._recording_store.ensure_base_dir()
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

        # Create recording directory
        self._current_recording_dir = self._recording_store.create_recording_dir(
            app_name=process.display_name
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
        self._current_metadata.save(self._current_recording_dir)

        # Resolve mic device index
        mic_device = None
        if self.config.audio.mic_device:
            mic_device = self._find_mic_device_index(self.config.audio.mic_device)

        # Start capture manager
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
        )
        self._capture_manager.start()

        # Update UI
        self._tray.set_state("recording", f"Recording {process.display_name}")
        notifications.notify_recording_started(process.display_name)
        logger.info("Recording started for %s", process.display_name)

    def stop_recording(self) -> None:
        """Stop recording and begin post-processing."""
        if not self._capture_manager or not self._capture_manager.is_recording:
            logger.warning("Not currently recording.")
            return

        elapsed = self._capture_manager.elapsed_seconds
        self._capture_manager.stop()
        self._capture_manager = None

        duration_str = _format_duration(elapsed)
        notifications.notify_recording_stopped(duration_str)
        logger.info("Recording stopped. Duration: %s", duration_str)

        # Start post-processing in background
        recording_dir = self._current_recording_dir
        metadata = self._current_metadata
        self._current_recording_dir = None
        self._current_metadata = None
        self._current_process = None

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
                mix_tracks(app_audio, mic_audio, mixed_audio)

            # Run transcription pipeline
            segments = self._pipeline.process(recording_dir)

            # Save transcripts in all configured formats
            save_all_formats(
                segments,
                recording_dir,
                formats=self.config.output.formats,
            )

            # Finalize metadata
            speakers = set(s.speaker for s in segments if s.speaker)
            metadata.finalize(
                recording_dir,
                speaker_count=len(speakers),
                segment_count=len(segments),
            )

            self._tray.set_state("idle")
            notifications.notify_transcription_complete(str(recording_dir))
            logger.info("Post-processing complete: %s", recording_dir)

        except Exception as e:
            logger.exception("Post-processing failed")
            self._tray.set_state("error", f"Error: {e}")
            notifications.notify_error(f"Transcription failed: {e}")
            if metadata:
                metadata.set_error(str(e), recording_dir)

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

    def _register_hotkey(self) -> None:
        """Register the global hotkey for toggling recording."""
        try:
            import keyboard

            hotkey = self.config.hotkey.toggle_recording
            keyboard.add_hotkey(hotkey, self._toggle_recording)
            self._hotkey_registered = True
            logger.info("Global hotkey registered: %s", hotkey)
        except ImportError:
            logger.warning("keyboard module not installed. Global hotkey disabled.")
        except Exception:
            logger.exception("Failed to register global hotkey")

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
