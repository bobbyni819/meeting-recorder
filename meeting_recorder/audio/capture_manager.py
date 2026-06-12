"""Capture manager coordinating app audio, mic, and screen capture threads."""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path
from typing import Optional

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.audio.platforms import (
    AppAudioCapture,
    DesktopAudioCapture,
    MicAudioCapture,
    MuteSync,
    get_all_pids_for_process,
    detect_initial_mute_state,
    is_process_running,
    check_system_volume as _platform_check_system_volume,
)
from meeting_recorder.audio.vad import VoiceActivityDetector
from meeting_recorder.audio.level_monitor import AudioLevelMonitor

logger = logging.getLogger(__name__)

# How long to wait after recording starts before deciding the app audio is silent.
_SILENCE_CHECK_SECONDS = 3.0

# RMS threshold for int16 PCM: anything below this is considered silent.
_SILENCE_RMS_THRESHOLD = 10

# How long continuous silence on the app audio channel before warning the user.
_SILENCE_WARNING_SECONDS = 10.0

# Grace period after PID exits in desktop mode before auto-stopping.
_DESKTOP_EXIT_GRACE_SECONDS = 5.0


def _is_buffer_silent(data: bytes) -> bool:
    """Check whether raw int16 PCM audio data is effectively silent.

    Decodes int16 samples via numpy and computes RMS amplitude.
    Returns True when the RMS is below ``_SILENCE_RMS_THRESHOLD``.
    """
    if not data or len(data) < 2:
        return True

    import numpy as np

    # Trim to even length (int16 = 2 bytes per sample)
    usable = len(data) & ~1
    samples = np.frombuffer(data[:usable], dtype=np.int16)
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    return bool(rms < _SILENCE_RMS_THRESHOLD)


def _patch_wav_header(wav_path: Path, max_retries: int = 3) -> bool:
    """Patch a WAV file header so the data size matches the actual file size.

    Python's ``wave`` module writes the RIFF chunk size and data chunk size
    only when ``close()`` is called.  If the process crashes before that,
    the header says "0 bytes of data" even though audio samples are present.
    This function fixes the header in-place based on the real file size.

    On Windows the file may be briefly locked by antivirus / Windows Search
    / Explorer preview; we retry a few times with short backoff so a
    transient lock doesn't leave the header stale.

    Returns True if the header was patched successfully.
    """
    import struct
    import time as _time

    size = wav_path.stat().st_size
    if size < 44:
        return False  # too small to hold a complete header yet

    for attempt in range(1, max_retries + 1):
        try:
            with open(wav_path, "r+b") as f:
                header = f.read(44)
                if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                    return False

                data_size = size - 44  # audio payload
                riff_size = size - 8  # RIFF chunk size = file size - 8

                # Patch RIFF chunk size (offset 4, little-endian uint32)
                f.seek(4)
                f.write(struct.pack("<I", riff_size))

                # Patch data chunk size (offset 40, little-endian uint32)
                f.seek(40)
                f.write(struct.pack("<I", data_size))
            return True
        except (PermissionError, OSError) as e:
            if attempt == max_retries:
                logger.warning(
                    "WAV header patch failed after %d attempts for %s: %s",
                    max_retries, wav_path.name, e,
                )
                return False
            _time.sleep(0.2 * attempt)  # 0.2s, 0.4s, 0.6s
    return False


def _check_system_volume() -> Optional[float]:
    """Return the system master volume (0.0 - 1.0), or None if unavailable."""
    return _platform_check_system_volume()


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
        screen_recording_fps: float = 30.0,
        video_encoder_preference: str = "nvenc",
        capture_speaker_events: bool = False,
        process_name: str = "",
        app_key: str = "",
        mute_toggle_hotkey: str = "ctrl+shift+u",
        on_audio_levels: Optional[callable] = None,
        on_live_transcript: Optional[callable] = None,
        live_transcription_enabled: bool = False,
        live_transcript_mic: bool = True,
        on_live_insight: Optional[callable] = None,
        live_transcription_device: str = "cpu",
        live_transcription_compute_type: str = "int8",
        live_transcription_interval: float = 3.0,
        start_muted_default: bool = True,
        on_mute_changed: Optional[callable] = None,
        vad: Optional[VoiceActivityDetector] = None,
        on_health_warning: Optional[callable] = None,
        on_capture_mode_changed: Optional[callable] = None,
    ):
        self.pid = pid
        self.output_dir = output_dir
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self._app_key = app_key
        self._on_stopped = on_stopped
        self._on_health_warning = on_health_warning
        self._on_capture_mode_changed = on_capture_mode_changed
        self._is_desktop_audio = False

        # Thread heartbeats for health monitoring
        self._thread_heartbeats: dict[str, float] = {}
        self._last_health_check: float = 0.0
        # Ongoing silence detection (Task 7)
        self._app_silence_start: Optional[float] = None

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
        self._live_transcript_mic = live_transcript_mic
        self._on_live_insight = on_live_insight
        self._live_transcription_device = live_transcription_device
        self._live_transcription_compute_type = live_transcription_compute_type
        self._live_transcription_interval = live_transcription_interval

        # VAD (accept pre-loaded instance to avoid loading in background threads)
        self._vad = vad if vad is not None else VoiceActivityDetector(threshold=vad_threshold)

        # Mute sync — detects when user mutes in meeting app.
        # Default to MUTED when initial state can't be detected.  This is
        # safer because most users join meetings muted, and if detection
        # fails (or the user clicks the mute button with the mouse — which
        # doesn't trigger the hotkey that mute-sync hooks into) the mic
        # would otherwise stay unmuted and capture everything the user
        # says, including things not said to the meeting.
        self._mute_sync = None
        self._mute_toggle_hotkey = mute_toggle_hotkey
        if app_key and process_name:
            target_pids = get_all_pids_for_process(process_name)
            if target_pids:
                if start_muted_default:
                    # Always start MUTED: users typically join meetings muted,
                    # and starting muted means the recorder never captures the
                    # room before the user actively unmutes. Auto-detection
                    # (UIA poller) unmutes within ~1.5s if they are in fact
                    # already unmuted with the meeting toolbar visible.
                    start_muted = True
                else:
                    detected = detect_initial_mute_state(pid)
                    start_muted = detected if detected is not None else True
                self._mute_sync = MuteSync(
                    app_key=app_key,
                    target_pids=target_pids,
                    start_muted=start_muted,
                    on_mute_changed=on_mute_changed,
                )

        # Active-speaker event capture (experimental, opt-in).
        self._speaker_capture = None
        if capture_speaker_events and process_name:
            try:
                from meeting_recorder.audio.speaker_events import SpeakerEventCapture

                spk_pids = get_all_pids_for_process(process_name) or {pid}
                self._speaker_capture = SpeakerEventCapture(
                    pids=set(spk_pids),
                    output_path=output_dir / "speaker_events.jsonl",
                )
            except Exception:
                logger.debug("Speaker-event capture unavailable", exc_info=True)

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
                from meeting_recorder.video.platforms import ScreenCapture
                self._screen_capture = ScreenCapture(
                    pid=pid,
                    process_name=process_name,
                    output_path=output_dir / "screen.mp4",
                    fps=screen_recording_fps,
                    encoder_preference=video_encoder_preference,
                )
            except ImportError:
                logger.warning("Screen capture dependencies not available.")

        # Writer state
        self._write_error = False
        self._stop_event = threading.Event()
        self._app_writer_thread: Optional[threading.Thread] = None
        self._mic_writer_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._is_recording = False
        self._start_time: Optional[float] = None
        self._silence_thread: Optional[threading.Thread] = None
        # Guards stop() against concurrent callers (e.g. user clicks Stop
        # while process-exit auto-stop fires on a different thread).
        self._stop_lock = threading.Lock()
        # Guards _live_transcriber access across writer, start, and stop threads.
        self._transcriber_lock = threading.Lock()

        # Pause/resume state
        self._paused = False
        self._pause_lock = threading.Lock()
        self._total_paused_seconds: float = 0.0
        self._pause_start_time: Optional[float] = None

    def start(self) -> None:
        """Start all capture and writer threads."""
        if self._is_recording:
            logger.warning("Already recording.")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._start_time = time.time()

        # Load VAD model (non-fatal — recording works without VAD)
        try:
            self._vad.load()
        except Exception:
            logger.exception("VAD model failed to load. Recording will continue without VAD.")

        # Start mute sync (hooks meeting app's mute shortcut + manual toggle)
        if self._mute_sync is not None:
            self._mute_sync.start(manual_hotkey=self._mute_toggle_hotkey)

        # Start active-speaker event capture (experimental, opt-in)
        if self._speaker_capture is not None:
            self._speaker_capture.start()

        # Start capture threads
        self._app_capture.start()
        self._mic_capture.start()

        # Teams: ProcTap cannot capture audio from any Teams PID, so
        # auto-switch to desktop (system-wide) loopback immediately.
        if self._app_key == "teams":
            logger.info(
                "Teams detected — auto-switching to desktop audio "
                "(ProcTap cannot capture Teams audio)"
            )
            self.switch_to_desktop_audio()

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

        # For non-Teams apps, monitor app audio for silence after start.
        # If still silent after _SILENCE_CHECK_SECONDS, auto-switch to
        # desktop audio so the user doesn't get a blank recording.
        if self._app_key != "teams":
            self._silence_thread = threading.Thread(
                target=self._silence_auto_switch,
                name="silence-detector",
                daemon=True,
            )
            self._silence_thread.start()

        # Check system volume when using desktop audio
        if self._is_desktop_audio:
            vol = _check_system_volume()
            if vol is not None and vol < 0.01:
                logger.warning("System volume is muted/zero — desktop audio will be silent!")
                if self._on_health_warning:
                    try:
                        self._on_health_warning("system_volume_muted")
                    except Exception:
                        logger.exception("on_health_warning callback error")

        # Start live transcription if enabled
        if self._live_transcription_enabled:
            try:
                from meeting_recorder.transcription.live_transcriber import LiveTranscriber

                lt = LiveTranscriber(
                    on_transcript=self._on_live_transcript,
                    output_path=self.output_dir / "live_transcript.txt",
                    on_insight=self._on_live_insight,
                    device=self._live_transcription_device,
                    compute_type=self._live_transcription_compute_type,
                    transcribe_interval=self._live_transcription_interval,
                )
                lt.start()
                with self._transcriber_lock:
                    self._live_transcriber = lt
            except ImportError:
                logger.warning("Live transcription dependencies not available.")
            except Exception:
                logger.exception("Failed to start live transcription")

        self._is_recording = True
        logger.info("Recording started. Output: %s", self.output_dir)

    def stop(self) -> None:
        """Stop all capture and writer threads.

        Thread-safe: uses an internal lock to prevent concurrent callers
        from double-stopping (e.g. user clicks Stop while process-exit
        auto-stop fires simultaneously on another thread).
        """
        with self._stop_lock:
            if not self._is_recording:
                return
            self._is_recording = False

        logger.info("Stopping recording...")
        self._stop_event.set()

        # Stop screen capture
        if self._screen_capture is not None:
            self._screen_capture.stop()

        # Stop mute sync
        if self._mute_sync is not None:
            self._mute_sync.stop()

        if self._speaker_capture is not None:
            self._speaker_capture.stop()

        # Stop capture threads (stops producing new chunks)
        self._app_capture.stop()
        self._mic_capture.stop()
        self._vad.reset()

        # Wait for writers to flush remaining chunks and exit.
        # Writers must finish BEFORE stopping the live transcriber,
        # because the app writer thread feeds the transcriber.
        threads_to_join = [
            (self._app_writer_thread, 5.0, "app WAV writer"),
            (self._mic_writer_thread, 5.0, "mic WAV writer"),
            (self._monitor_thread, 2.0, "process monitor"),
            (self._level_thread, 2.0, "audio level monitor"),
            (self._silence_thread, 1.0, "silence detector"),
        ]
        current = threading.current_thread()
        for thread, timeout, label in threads_to_join:
            if thread is not None and thread is not current:
                thread.join(timeout=timeout)
                if thread.is_alive():
                    logger.warning(
                        "%s thread did not terminate within %.0fs.", label, timeout
                    )

        # Stop live transcription after writers have drained
        with self._transcriber_lock:
            lt = self._live_transcriber
            self._live_transcriber = None
        if lt is not None:
            lt.stop()

        duration = time.time() - self._start_time if self._start_time else 0
        logger.info("Recording stopped. Duration: %.1fs", duration)

    def _writer_loop(self, buffer: RingBuffer, wav_path: Path, label: str) -> None:
        """Write audio chunks from a ring buffer to a WAV file."""
        wf = None
        try:
            wf = wave.open(str(wav_path), "wb")
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)

            logger.info("WAV writer started: %s", wav_path.name)

            level_update = (
                self._level_monitor.update_app_level if label == "app"
                else self._level_monitor.update_mic_level
            )
            is_app = label == "app"
            # Periodic header flush: WAV header contains the frame count,
            # which is only written on close(). If the process crashes, the
            # header is stale and the file appears empty.  We flush by
            # closing and re-opening in append mode every ~30 seconds.
            last_flush = time.monotonic()
            _FLUSH_INTERVAL = 30.0
            frames_since_flush = 0

            while not self._stop_event.is_set():
                chunks = buffer.get_all()
                if chunks:
                    feeds_transcriber = is_app or self._live_transcript_mic
                    # When paused, drain the buffer but don't write to disk.
                    # Still feed the live transcriber so it stays time-aligned.
                    if self._paused:
                        if feeds_transcriber:
                            with self._transcriber_lock:
                                lt = self._live_transcriber
                            if lt is not None:
                                for chunk in chunks:
                                    lt.feed_audio(chunk, source=label)
                        self._thread_heartbeats[f"{label}_writer"] = time.time()
                    else:
                        for chunk in chunks:
                            wf.writeframes(chunk)
                            frames_since_flush += len(chunk) // 2
                            level_update(chunk)
                            if feeds_transcriber:
                                with self._transcriber_lock:
                                    lt = self._live_transcriber
                                if lt is not None:
                                    lt.feed_audio(chunk, source=label)
                        self._thread_heartbeats[f"{label}_writer"] = time.time()

                    # Periodic WAV header flush: patch the RIFF/data chunk
                    # sizes in-place so the file is playable even if the
                    # process crashes before wave.close() finalizes the header.
                    now = time.monotonic()
                    if frames_since_flush > 0 and now - last_flush >= _FLUSH_INTERVAL:
                        try:
                            wf._ensure_header_written(0)  # force header
                            wf._file.flush()
                            _patch_wav_header(wav_path)
                        except Exception:
                            logger.debug("WAV header flush failed (%s)", label, exc_info=True)
                        last_flush = now
                        frames_since_flush = 0
                else:
                    # Wait with event so stop() wakes us immediately
                    self._stop_event.wait(0.01)

            # Flush remaining
            remaining = buffer.get_all()
            if wf is not None:
                for chunk in remaining:
                    wf.writeframes(chunk)

            logger.info("WAV writer finished: %s", wav_path.name)

        except Exception:
            logger.exception("WAV writer error (%s)", label)
            self._write_error = True
            if self._on_health_warning:
                try:
                    self._on_health_warning(f"{label}_write_error")
                except Exception:
                    pass
        finally:
            if wf is not None:
                try:
                    wf.close()
                except Exception:
                    logger.debug("Error closing WAV file (%s)", label, exc_info=True)
            # Final header patch: ensure the WAV header reflects actual data
            try:
                _patch_wav_header(wav_path)
            except Exception:
                logger.debug("Final WAV header patch failed (%s)", label, exc_info=True)

    def _monitor_process(self) -> None:
        """Monitor the target process and auto-stop if it exits.

        When the process exits, fires _on_stopped so the app layer can
        orchestrate a full stop (capture_manager.stop + post-processing).
        Does NOT call self.stop() directly — doing so would set
        _is_recording = False before the app gets a chance to run its
        stop_recording, causing post-processing to be skipped.

        In desktop audio mode, PID exit is still detected but a grace period
        of _DESKTOP_EXIT_GRACE_SECONDS is applied before auto-stopping,
        in case the meeting app restarts or the user switches windows.
        """
        while not self._stop_event.is_set():
            if not is_process_running(self.pid):
                if self._is_desktop_audio:
                    logger.info(
                        "Target PID %d exited (desktop mode) — waiting %.0fs grace.",
                        self.pid, _DESKTOP_EXIT_GRACE_SECONDS,
                    )
                    self._stop_event.wait(_DESKTOP_EXIT_GRACE_SECONDS)
                    if self._stop_event.is_set():
                        return
                logger.info("Target process (PID %d) exited. Auto-stopping.", self.pid)
                if self._on_stopped:
                    try:
                        self._on_stopped()
                    except Exception:
                        logger.exception("on_stopped callback error")
                return
            self._stop_event.wait(2.0)

    def _level_monitor_loop(self) -> None:
        """Periodically notify the audio level callback and check thread health."""
        from meeting_recorder.audio.level_monitor import MIN_DB

        # A dB threshold just above pure silence.
        silence_db_threshold = MIN_DB + 1.0  # -59.0 dB

        while not self._stop_event.is_set():
            try:
                self._level_monitor.notify()
            except Exception:
                logger.exception("on_levels callback error")

            # Check thread heartbeats every 5 seconds
            now = time.time()
            if now - self._last_health_check >= 5.0:
                self._last_health_check = now
                for name, last in self._thread_heartbeats.items():
                    if now - last > 10.0 and self._on_health_warning:
                        try:
                            self._on_health_warning(name)
                        except Exception:
                            logger.exception("on_health_warning callback error")

            # Check for prolonged app audio silence
            try:
                app_rms_db, _app_peak_db = self._level_monitor.app_level
            except (TypeError, ValueError):
                app_rms_db = MIN_DB
            if app_rms_db <= silence_db_threshold:
                if self._app_silence_start is None:
                    self._app_silence_start = now
                elif (now - self._app_silence_start >= _SILENCE_WARNING_SECONDS
                      and self._on_health_warning):
                    try:
                        self._on_health_warning("app_audio_silent")
                    except Exception:
                        logger.exception("on_health_warning callback error")
                    self._app_silence_start = now  # reset to avoid spamming
            else:
                self._app_silence_start = None

            self._stop_event.wait(0.1)

    def _silence_auto_switch(self) -> None:
        """Monitor app audio after start; switch to desktop if silent.

        For non-Teams apps, per-process audio (ProcTap) may fail silently
        (e.g. the app renders audio via a child process or a system bus).
        This method polls the level monitor for ``_SILENCE_CHECK_SECONDS``
        and auto-switches to desktop loopback if the app channel stays
        silent the whole time.
        """
        if self._app_key == "teams" or self._is_desktop_audio:
            return

        from meeting_recorder.audio.level_monitor import MIN_DB

        # A dB threshold just above pure silence.  -59 dB is almost
        # inaudible and well below any real audio content.
        silence_db_threshold = MIN_DB + 1.0  # -59.0 dB

        elapsed = 0.0
        poll_interval = 0.5

        while elapsed < _SILENCE_CHECK_SECONDS:
            if self._stop_event.is_set() or self._is_desktop_audio:
                return
            self._stop_event.wait(poll_interval)
            elapsed += poll_interval

            if self._stop_event.is_set() or self._is_desktop_audio:
                return

            # Check current app audio level (rms_db, peak_db)
            rms_db, _peak_db = self._level_monitor.app_level
            if rms_db > silence_db_threshold:
                # Non-silent audio detected — per-process capture is working.
                logger.debug(
                    "Silence detector: app audio detected (%.1f dB) at %.1fs — "
                    "per-process capture is working.",
                    rms_db,
                    elapsed,
                )
                return

        # Still silent after the full check period — switch to desktop audio.
        if self._is_desktop_audio:
            return

        logger.warning(
            "App audio silent for %.1fs — auto-switching to desktop audio.",
            _SILENCE_CHECK_SECONDS,
        )
        self.switch_to_desktop_audio()

        if self._on_health_warning:
            try:
                self._on_health_warning("silence_auto_switch")
            except Exception:
                logger.exception("on_health_warning callback error")

    @property
    def level_monitor(self) -> AudioLevelMonitor:
        """Access the audio level monitor for current levels."""
        return self._level_monitor

    @property
    def mute_sync(self) -> Optional[MuteSync]:
        """Access the mute sync instance (if available)."""
        return self._mute_sync

    @property
    def is_desktop_audio(self) -> bool:
        """Whether capture is in desktop (system-wide) mode."""
        return self._is_desktop_audio

    @property
    def is_app_capture_process_specific(self) -> Optional[bool]:
        """Whether app audio capture is using per-process or system-wide loopback."""
        if self._is_desktop_audio:
            return False
        if self._app_capture is None:
            return None
        return self._app_capture.is_process_specific

    def get_screen_frame(self):
        """Return the latest captured screen frame, or None."""
        if self._screen_capture is not None:
            return self._screen_capture.latest_frame
        return None

    def list_capturable_windows(self) -> list[tuple[int, str]]:
        """Return (hwnd, display_title) pairs for all visible top-level windows.

        Display titles are formatted as ``"title -- process_name"`` so the user
        can distinguish windows from different applications.

        Used to populate the window picker in the recording dashboard.
        """
        from meeting_recorder.video.platforms import list_visible_windows
        return [
            (hwnd, f"{title} \u2014 {proc_name}" if proc_name != "unknown" else title)
            for hwnd, title, _pid, proc_name in list_visible_windows()
        ]

    def switch_screen_window(self, hwnd: int) -> None:
        """Switch screen capture AND audio capture to the window's owning process.

        Safe to call at any time during recording. Screen switch takes effect on
        the next capture frame; audio restarts within ~500 ms.
        """
        if self._screen_capture is not None:
            self._screen_capture.switch_window(hwnd)

        # Also switch audio to the process that owns the new window
        from meeting_recorder.video.platforms import get_hwnd_pid
        new_pid = get_hwnd_pid(hwnd)
        if new_pid is None:
            logger.warning("Could not resolve PID for HWND %d; audio not switched.", hwnd)
            if self._on_health_warning:
                try:
                    self._on_health_warning("window_pid_failed")
                except Exception:
                    pass
            return
        if new_pid == self.pid:
            # The visible window is owned by the PID we're already capturing.
            # For multi-process apps (Teams, Zoom) the audio subprocess may be
            # a sibling with no visible window.  Try to find a better match.
            better_pid = self._find_audio_sibling_pid(new_pid)
            if better_pid is None or better_pid == self.pid:
                logger.info(
                    "Window picker: already capturing PID %d — audio unchanged. "
                    "If the meeting is silent, check that participants are unmuted.",
                    self.pid,
                )
                return
            logger.info(
                "Window picker: audio is on sibling PID %d (not the window PID %d) — switching.",
                better_pid,
                new_pid,
            )
            self._switch_app_audio_pid(better_pid)
            return
        logger.info(
            "Window switch: also switching audio capture from PID %d to PID %d",
            self.pid,
            new_pid,
        )
        self._switch_app_audio_pid(new_pid)

    def _find_audio_sibling_pid(self, window_pid: int) -> Optional[int]:
        """For multi-process apps, return a related PID that has an active audio session.

        Checks both siblings (same exe name) and child processes (e.g. Teams'
        msedgewebview2.exe subprocesses) for active audio sessions.  Returns
        None when pycaw is unavailable, no candidates exist, or no audio session
        is found (e.g. the meeting is currently silent).
        """
        try:
            import psutil
        except ImportError:
            return None

        try:
            proc_name = psutil.Process(window_pid).name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        # Collect all sibling PIDs (same executable name)
        sibling_pids: set[int] = set()
        try:
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if p.info["name"] and p.info["name"].lower() == proc_name:
                        sibling_pids.add(p.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            return None

        # Also include child processes — Teams renders audio through
        # msedgewebview2.exe children, not ms-teams.exe itself.
        from meeting_recorder.audio.process_finder import (
            _get_audio_rendering_pids,
            _get_descendant_pids,
        )
        child_pids = _get_descendant_pids(sibling_pids)
        all_candidate_pids = sibling_pids | child_pids

        if len(all_candidate_pids) <= 1:
            return None  # single-process app, nothing to resolve

        # Check which candidates have active audio sessions (requires pycaw)
        audio_pids = _get_audio_rendering_pids(all_candidate_pids)
        if not audio_pids:
            logger.debug(
                "No active audio session found among %d candidates for %s — "
                "meeting may be silent or pycaw cannot see this app's sessions.",
                len(all_candidate_pids),
                proc_name,
            )
            return None

        # Prefer a candidate that IS audio-active and is NOT the current window PID
        non_window_audio = audio_pids - {window_pid}
        if non_window_audio:
            chosen = next(iter(non_window_audio))
            logger.info(
                "Found audio-active related PID %d (window is on PID %d)",
                chosen, window_pid,
            )
            return chosen

        # Window PID itself has the audio session — already correct, nothing to do
        return None

    def _switch_app_audio_pid(self, new_pid: int) -> None:
        """Hot-swap app audio capture to a different process PID.

        Stops the current capture, creates a new AppAudioCapture on the same
        ring buffer (so the WAV writer continues uninterrupted), and starts it.
        """
        if self._is_desktop_audio:
            logger.info("Ignoring PID switch — currently in desktop audio mode.")
            return
        old_capture = self._app_capture
        old_capture.stop()
        self._app_capture = AppAudioCapture(
            pid=new_pid,
            ring_buffer=self._app_buffer,
            sample_rate=self.sample_rate,
            channels=self.channels,
            chunk_duration_ms=self.chunk_duration_ms,
        )
        self._app_capture.start()
        self.pid = new_pid
        logger.info("Audio capture hot-swapped to PID %d", new_pid)

    def switch_to_desktop_audio(self) -> None:
        """Switch from per-process capture to system-wide desktop loopback.

        Stops the current AppAudioCapture, creates a DesktopAudioCapture on
        the same ring buffer, and starts it. The WAV writer continues
        uninterrupted because the ring buffer is shared.
        """
        if self._is_desktop_audio:
            logger.info("Already in desktop audio mode.")
            return
        old_capture = self._app_capture
        old_capture.stop()
        self._app_capture = DesktopAudioCapture(
            ring_buffer=self._app_buffer,
            sample_rate=self.sample_rate,
            channels=self.channels,
            chunk_duration_ms=self.chunk_duration_ms,
        )
        self._app_capture.start()
        self._is_desktop_audio = True
        logger.info("Switched to desktop audio (system-wide loopback).")
        if self._on_capture_mode_changed:
            try:
                self._on_capture_mode_changed(True)
            except Exception:
                logger.exception("on_capture_mode_changed callback error")

    def switch_to_app_audio(self, pid: int) -> None:
        """Switch from desktop loopback back to per-process capture.

        Stops the current capture (DesktopAudioCapture or AppAudioCapture),
        creates a new AppAudioCapture for the given PID on the same ring
        buffer, and starts it.
        """
        if not self._is_desktop_audio:
            logger.info("Already in per-process audio mode.")
            return
        old_capture = self._app_capture
        old_capture.stop()
        self._app_capture = AppAudioCapture(
            pid=pid,
            ring_buffer=self._app_buffer,
            sample_rate=self.sample_rate,
            channels=self.channels,
            chunk_duration_ms=self.chunk_duration_ms,
        )
        self._app_capture.start()
        self.pid = pid
        self._is_desktop_audio = False
        logger.info("Switched to per-process audio (PID %d).", pid)
        if self._on_capture_mode_changed:
            try:
                self._on_capture_mode_changed(False)
            except Exception:
                logger.exception("on_capture_mode_changed callback error")

    def pause(self) -> None:
        """Pause recording — audio capture continues but data is discarded."""
        with self._pause_lock:
            if self._paused or not self._is_recording:
                return
            self._paused = True
            self._pause_start_time = time.time()
            if self._screen_capture is not None:
                self._screen_capture.paused = True
            logger.info("Recording paused.")

    def resume(self) -> None:
        """Resume a paused recording."""
        with self._pause_lock:
            if not self._paused or not self._is_recording:
                return
            if self._pause_start_time is not None:
                self._total_paused_seconds += time.time() - self._pause_start_time
                self._pause_start_time = None
            self._paused = False
            if self._screen_capture is not None:
                self._screen_capture.paused = False
            logger.info("Recording resumed.")

    def toggle_pause(self) -> None:
        """Toggle between paused and recording states."""
        if self._paused:
            self.resume()
        else:
            self.pause()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed recording time, excluding paused duration."""
        if self._start_time is None:
            return 0.0
        with self._pause_lock:
            total = time.time() - self._start_time
            paused = self._total_paused_seconds
            if self._paused and self._pause_start_time is not None:
                paused += time.time() - self._pause_start_time
        return max(0.0, total - paused)
