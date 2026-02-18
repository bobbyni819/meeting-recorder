"""Capture manager coordinating app audio, mic, and screen capture threads."""

from __future__ import annotations

import logging
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Optional

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.audio.app_audio import AppAudioCapture
from meeting_recorder.audio.mic_audio import MicAudioCapture
from meeting_recorder.audio.vad import VoiceActivityDetector
from meeting_recorder.audio.mute_sync import (
    MuteSync,
    get_all_pids_for_process,
    detect_initial_mute_state,
)
from meeting_recorder.audio.process_finder import is_process_running
from meeting_recorder.audio.level_monitor import AudioLevelMonitor

logger = logging.getLogger(__name__)


class CaptureManager:
    """Manages audio capture from app and mic, writing to WAV files.

    Coordinates:
    - App audio capture thread (ProcTap)
    - Mic capture thread (PyAudioWPatch + VAD)
    - Screen capture thread (mss + OpenCV)
    - WAV writer threads for each track
    - Process exit monitoring
    """

    def __init__(
        self,
        pid: int,
        output_dir: Path,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        vad_threshold: float = 0.5,
        mic_device_index: Optional[int] = None,
        on_stopped: Optional[callable] = None,
        screen_recording_enabled: bool = False,
        screen_recording_fps: float = 5.0,
        process_name: str = "",
        app_key: str = "",
        mute_toggle_hotkey: str = "ctrl+shift+u",
        on_audio_levels: Optional[callable] = None,
        on_live_transcript: Optional[callable] = None,
        live_transcription_enabled: bool = False,
        on_mute_changed: Optional[callable] = None,
        vad: Optional[VoiceActivityDetector] = None,
    ):
        self.pid = pid
        self.output_dir = output_dir
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self._on_stopped = on_stopped

        # Ring buffers
        self._app_buffer = RingBuffer(max_chunks=2000)
        self._mic_buffer = RingBuffer(max_chunks=2000)

        # Audio level monitor
        self._level_monitor = AudioLevelMonitor(on_levels=on_audio_levels)
        self._level_thread: Optional[threading.Thread] = None

        # Live transcription
        self._live_transcriber = None
        self._on_live_transcript = on_live_transcript
        self._live_transcription_enabled = live_transcription_enabled

        # VAD (accept pre-loaded instance to avoid loading in background threads)
        self._vad = vad if vad is not None else VoiceActivityDetector(threshold=vad_threshold)

        # Mute sync — detects when user mutes in meeting app
        self._mute_sync = None
        self._mute_toggle_hotkey = mute_toggle_hotkey
        if app_key and process_name:
            target_pids = get_all_pids_for_process(process_name)
            if target_pids:
                detected = detect_initial_mute_state(pid)
                start_muted = detected if detected is not None else False
                self._mute_sync = MuteSync(
                    app_key=app_key,
                    target_pids=target_pids,
                    start_muted=start_muted,
                    on_mute_changed=on_mute_changed,
                )

        # Capture instances
        self._app_capture = AppAudioCapture(
            pid=pid,
            ring_buffer=self._app_buffer,
            sample_rate=sample_rate,
            channels=channels,
            chunk_duration_ms=chunk_duration_ms,
        )
        self._mic_capture = MicAudioCapture(
            ring_buffer=self._mic_buffer,
            vad=self._vad,
            sample_rate=sample_rate,
            channels=channels,
            chunk_duration_ms=chunk_duration_ms,
            device_index=mic_device_index,
            mute_sync=self._mute_sync,
        )

        # Screen capture
        self._screen_capture = None
        if screen_recording_enabled:
            try:
                from meeting_recorder.video.screen_capture import ScreenCapture
                self._screen_capture = ScreenCapture(
                    pid=pid,
                    process_name=process_name,
                    output_path=output_dir / "screen.mp4",
                    fps=screen_recording_fps,
                )
            except ImportError:
                logger.warning("Screen capture dependencies not available.")

        # Writer state
        self._stop_event = threading.Event()
        self._app_writer_thread: Optional[threading.Thread] = None
        self._mic_writer_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._is_recording = False
        self._start_time: Optional[float] = None

    def start(self) -> None:
        """Start all capture and writer threads."""
        if self._is_recording:
            logger.warning("Already recording.")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._start_time = time.time()

        # Load VAD model
        self._vad.load()

        # Start mute sync (hooks meeting app's mute shortcut + manual toggle)
        if self._mute_sync is not None:
            self._mute_sync.start(manual_hotkey=self._mute_toggle_hotkey)

        # Start capture threads
        self._app_capture.start()
        self._mic_capture.start()

        # Start writer threads
        self._app_writer_thread = threading.Thread(
            target=self._writer_loop,
            args=(self._app_buffer, self.output_dir / "app_audio.wav", "app"),
            name="app-wav-writer",
            daemon=True,
        )
        self._mic_writer_thread = threading.Thread(
            target=self._writer_loop,
            args=(self._mic_buffer, self.output_dir / "mic_audio.wav", "mic"),
            name="mic-wav-writer",
            daemon=True,
        )
        self._app_writer_thread.start()
        self._mic_writer_thread.start()

        # Start screen capture
        if self._screen_capture is not None:
            self._screen_capture.start()

        # Start process monitor
        self._monitor_thread = threading.Thread(
            target=self._monitor_process,
            name="process-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

        # Start audio level monitoring
        self._level_monitor.reset()
        self._level_thread = threading.Thread(
            target=self._level_monitor_loop,
            name="audio-level-monitor",
            daemon=True,
        )
        self._level_thread.start()

        # Start live transcription if enabled
        if self._live_transcription_enabled:
            try:
                from meeting_recorder.transcription.live_transcriber import LiveTranscriber

                self._live_transcriber = LiveTranscriber(
                    on_transcript=self._on_live_transcript,
                )
                self._live_transcriber.start()
            except ImportError:
                logger.warning("Live transcription dependencies not available.")
            except Exception:
                logger.exception("Failed to start live transcription")

        self._is_recording = True
        logger.info("Recording started. Output: %s", self.output_dir)

    def stop(self) -> None:
        """Stop all capture and writer threads."""
        if not self._is_recording:
            return

        logger.info("Stopping recording...")
        self._stop_event.set()

        # Stop screen capture
        if self._screen_capture is not None:
            self._screen_capture.stop()

        # Stop mute sync
        if self._mute_sync is not None:
            self._mute_sync.stop()

        # Stop live transcription
        if self._live_transcriber is not None:
            self._live_transcriber.stop()
            self._live_transcriber = None

        # Stop capture threads
        self._app_capture.stop()
        self._mic_capture.stop()
        self._vad.reset()

        # Wait for writers to flush and check for zombies
        threads_to_join = [
            (self._app_writer_thread, 10.0, "app WAV writer"),
            (self._mic_writer_thread, 10.0, "mic WAV writer"),
            (self._monitor_thread, 5.0, "process monitor"),
            (self._level_thread, 3.0, "audio level monitor"),
        ]
        for thread, timeout, label in threads_to_join:
            if thread is not None:
                thread.join(timeout=timeout)
                if thread.is_alive():
                    logger.warning("%s thread did not terminate (zombie).", label)

        self._is_recording = False
        duration = time.time() - self._start_time if self._start_time else 0
        logger.info("Recording stopped. Duration: %.1fs", duration)

    def _writer_loop(self, buffer: RingBuffer, wav_path: Path, label: str) -> None:
        """Write audio chunks from a ring buffer to a WAV file."""
        try:
            wf = wave.open(str(wav_path), "wb")
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)

            logger.info("WAV writer started: %s", wav_path.name)

            while not self._stop_event.is_set():
                chunks = buffer.get_all()
                if chunks:
                    for chunk in chunks:
                        wf.writeframes(chunk)
                        # Feed audio level monitor
                        if label == "app":
                            self._level_monitor.update_app_level(chunk)
                        else:
                            self._level_monitor.update_mic_level(chunk)
                        # Feed live transcriber (app track only — feeding both
                        # tracks interleaves chunks and corrupts the audio stream)
                        if label == "app" and self._live_transcriber is not None:
                            self._live_transcriber.feed_audio(chunk)
                else:
                    time.sleep(0.01)

            # Flush remaining
            remaining = buffer.get_all()
            for chunk in remaining:
                wf.writeframes(chunk)

            wf.close()
            logger.info("WAV writer finished: %s", wav_path.name)

        except Exception:
            logger.exception("WAV writer error (%s)", label)

    def _monitor_process(self) -> None:
        """Monitor the target process and auto-stop if it exits."""
        while not self._stop_event.is_set():
            if not is_process_running(self.pid):
                logger.info("Target process (PID %d) exited. Auto-stopping.", self.pid)
                self.stop()
                if self._on_stopped:
                    self._on_stopped()
                return
            time.sleep(2.0)

    def _level_monitor_loop(self) -> None:
        """Periodically notify the audio level callback."""
        while not self._stop_event.is_set():
            self._level_monitor.notify()
            self._stop_event.wait(0.1)

    @property
    def level_monitor(self) -> AudioLevelMonitor:
        """Access the audio level monitor for current levels."""
        return self._level_monitor

    @property
    def mute_sync(self) -> Optional[MuteSync]:
        """Access the mute sync instance (if available)."""
        return self._mute_sync

    @property
    def is_app_capture_process_specific(self) -> Optional[bool]:
        """Whether app audio capture is using per-process or system-wide loopback."""
        if self._app_capture is None:
            return None
        return self._app_capture.is_process_specific

    def get_screen_frame(self):
        """Return the latest captured screen frame, or None."""
        if self._screen_capture is not None:
            return self._screen_capture.latest_frame
        return None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time
