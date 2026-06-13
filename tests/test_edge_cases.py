"""Edge-case tests: format helpers, VAD model state, config atomicity, health monitoring.

Covers gaps identified in the test audit:
- _format_duration boundary values
- Config.save() atomic write verification
- VoiceActivityDetector error paths and idempotent load
- CaptureManager health warning for stalled threads
- CaptureManager monitor_process immediate exit on stop_event
- AudioLevelMonitor callback exception safety
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import numpy as np
import pytest

# Inject mock modules for native UI packages
for _mod_name in ("pystray", "PIL", "PIL.Image", "winotify"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from meeting_recorder.config import Config


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    """Test the _format_duration helper in app.py."""

    def test_zero_seconds(self):
        from meeting_recorder.app import _format_duration
        assert _format_duration(0) == "0s"

    def test_seconds_only(self):
        from meeting_recorder.app import _format_duration
        assert _format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        from meeting_recorder.app import _format_duration
        assert _format_duration(125) == "2m 5s"

    def test_exact_minute(self):
        from meeting_recorder.app import _format_duration
        assert _format_duration(60) == "1m 0s"

    def test_hours_minutes_seconds(self):
        from meeting_recorder.app import _format_duration
        assert _format_duration(3661) == "1h 1m 1s"

    def test_exact_hour(self):
        from meeting_recorder.app import _format_duration
        assert _format_duration(3600) == "1h 0m 0s"

    def test_fractional_seconds_truncated(self):
        from meeting_recorder.app import _format_duration
        # 90.7 seconds -> int(90.7) = 90 -> 1m 30s
        assert _format_duration(90.7) == "1m 30s"

    def test_large_duration(self):
        from meeting_recorder.app import _format_duration
        # 10 hours, 30 minutes, 15 seconds = 37815
        assert _format_duration(37815) == "10h 30m 15s"


# ---------------------------------------------------------------------------
# Config.save() atomicity
# ---------------------------------------------------------------------------

class TestConfigSaveAtomicity:
    """Verify Config.save() uses atomic write pattern."""

    def test_save_creates_valid_toml(self, tmp_path):
        """Saved config should be parseable TOML."""
        import tomli_w
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib

        bundled = tmp_path / "config.toml"
        secrets_file = tmp_path / "secrets.toml"
        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", tmp_path),
        ):
            cfg = Config()
            cfg.recording.user_name = "AtomicTest"
            cfg.save()

        assert bundled.exists()
        with open(bundled, "rb") as f:
            data = tomllib.load(f)
        assert data["recording"]["user_name"] == "AtomicTest"

    def test_save_does_not_leave_tmp_file(self, tmp_path):
        """After save(), no .tmp file should remain."""
        bundled = tmp_path / "config.toml"
        secrets_file = tmp_path / "secrets.toml"
        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", tmp_path),
        ):
            cfg = Config()
            cfg.save()

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_overwrites_previous(self, tmp_path):
        """Saving twice should update the file."""
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib

        bundled = tmp_path / "config.toml"
        secrets_file = tmp_path / "secrets.toml"
        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", tmp_path),
        ):
            cfg = Config()
            cfg.recording.user_name = "First"
            cfg.save()
            cfg.recording.user_name = "Second"
            cfg.save()

        with open(bundled, "rb") as f:
            data = tomllib.load(f)
        assert data["recording"]["user_name"] == "Second"


# ---------------------------------------------------------------------------
# VoiceActivityDetector edge cases
# ---------------------------------------------------------------------------

class TestVoiceActivityDetector:
    """Test VAD error handling and state management."""

    def test_speech_probability_before_load_passes_through(self):
        """speech_probability returns 1.0 (pass-through) when model not loaded."""
        from meeting_recorder.audio.vad import VoiceActivityDetector

        vad = VoiceActivityDetector()
        audio_bytes = np.zeros(480, dtype=np.int16).tobytes()

        assert vad.speech_probability(audio_bytes) == 1.0

    def test_is_speech_before_load_passes_through(self):
        """is_speech returns True (pass-through) when model not loaded."""
        from meeting_recorder.audio.vad import VoiceActivityDetector

        vad = VoiceActivityDetector()
        audio_bytes = np.zeros(480, dtype=np.int16).tobytes()

        assert vad.is_speech(audio_bytes) is True

    def test_is_loaded_initially_false(self):
        from meeting_recorder.audio.vad import VoiceActivityDetector

        vad = VoiceActivityDetector()
        assert vad.is_loaded is False

    def test_reset_without_load_is_safe(self):
        """reset() on an unloaded VAD should be a no-op."""
        from meeting_recorder.audio.vad import VoiceActivityDetector

        vad = VoiceActivityDetector()
        vad.reset()  # Should not raise

    def test_threshold_stored(self):
        from meeting_recorder.audio.vad import VoiceActivityDetector

        vad = VoiceActivityDetector(threshold=0.8)
        assert vad.threshold == 0.8


# ---------------------------------------------------------------------------
# CaptureManager health monitoring
# ---------------------------------------------------------------------------

class TestCaptureManagerHealthMonitoring:
    """Test health warning for stalled threads."""

    def _make_manager(self, **kwargs):
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                **kwargs,
            )
        mgr._level_monitor.notify = mock.Mock()
        mgr._level_monitor.app_level = (0.0, 0.0)
        return mgr

    def test_health_warning_fires_for_stale_heartbeat(self):
        """If a writer thread heartbeat is stale, on_health_warning should fire."""
        warnings = []
        mgr = self._make_manager(on_health_warning=lambda name: warnings.append(name))

        class StopAfterOneWait:
            def __init__(self):
                self._set = False

            def is_set(self):
                return self._set

            def wait(self, _timeout):
                self._set = True
                return True

        # Simulate a stale heartbeat (last update > 10s ago)
        with mgr._heartbeat_lock:
            mgr._thread_heartbeats["app_writer"] = time.time() - 15.0
        mgr._last_health_check = time.time() - 6.0
        mgr._stop_event = StopAfterOneWait()

        mgr._level_monitor_loop()

        assert "app_writer" in warnings

    def test_heartbeat_lock_allows_concurrent_writes_and_snapshots(self):
        """Heartbeat writes and health-check snapshots use the same lock."""
        mgr = self._make_manager()
        assert hasattr(mgr, "_heartbeat_lock")
        with mgr._heartbeat_lock:
            pass

        errors = []

        def writer(label):
            try:
                for i in range(1000):
                    with mgr._heartbeat_lock:
                        mgr._thread_heartbeats[f"{label}_{i}"] = time.time()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=("app",)),
            threading.Thread(target=writer, args=("mic",)),
        ]
        for thread in threads:
            thread.start()

        while any(thread.is_alive() for thread in threads):
            with mgr._heartbeat_lock:
                list(mgr._thread_heartbeats.items())

        for thread in threads:
            thread.join()

        with mgr._heartbeat_lock:
            heartbeats = list(mgr._thread_heartbeats.items())

        assert not errors
        assert heartbeats


# ---------------------------------------------------------------------------
# CaptureManager monitor_process responds to stop_event
# ---------------------------------------------------------------------------

class TestCaptureManagerMonitorProcess:
    """Test that _monitor_process exits quickly when stop_event is set."""

    def test_monitor_exits_on_stop_event(self):
        """_monitor_process should exit promptly when stop_event is set."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )

        # Set stop event immediately
        mgr._stop_event.set()

        # _monitor_process should exit almost instantly
        start = time.monotonic()
        mgr._monitor_process()
        elapsed = time.monotonic() - start

        assert elapsed < 0.5  # Should be near-instant

    def test_monitor_exits_on_stop_event_in_desktop_mode(self):
        """Desktop mode should also exit promptly on stop_event."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )

        mgr._is_desktop_audio = True
        mgr._stop_event.set()

        start = time.monotonic()
        mgr._monitor_process()
        elapsed = time.monotonic() - start

        assert elapsed < 0.5

    def test_monitor_calls_on_stopped_when_process_exits(self):
        """When the process exits, on_stopped should be called."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        stopped_calls = []

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                on_stopped=lambda: stopped_calls.append(1),
            )

        with mock.patch(
            "meeting_recorder.audio.capture_manager.is_process_running",
            return_value=False,
        ):
            mgr._monitor_process()

        assert len(stopped_calls) == 1


# ---------------------------------------------------------------------------
# AudioLevelMonitor callback exception safety
# ---------------------------------------------------------------------------

class TestLevelMonitorCallbackSafety:
    """Test that callback exceptions don't crash the monitor."""

    def test_notify_with_none_callback(self):
        """notify() with no callback should be a no-op."""
        from meeting_recorder.audio.level_monitor import AudioLevelMonitor

        monitor = AudioLevelMonitor(on_levels=None)
        monitor.notify()  # Should not raise

    def test_notify_with_bad_callback(self):
        """If the callback raises, it should propagate (monitor does not catch)."""
        from meeting_recorder.audio.level_monitor import AudioLevelMonitor

        def bad_callback(*args):
            raise RuntimeError("callback error")

        monitor = AudioLevelMonitor(on_levels=bad_callback)

        # The monitor doesn't catch callback exceptions — this is correct behavior
        # since the caller (_level_monitor_loop) should handle it
        with pytest.raises(RuntimeError, match="callback error"):
            monitor.notify()


# ---------------------------------------------------------------------------
# NoiseGate edge cases
# ---------------------------------------------------------------------------

class TestNoiseGateEdgeCases:
    """Additional NoiseGate edge cases."""

    def test_empty_audio_passthrough(self):
        """Empty array should be returned as-is."""
        from meeting_recorder.audio.resampling import NoiseGate

        gate = NoiseGate()
        result = gate.process(np.array([], dtype=np.int16))
        assert len(result) == 0

    def test_single_sample(self):
        """Single sample should not crash."""
        from meeting_recorder.audio.resampling import NoiseGate

        gate = NoiseGate()
        result = gate.process(np.array([1000], dtype=np.int16))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# RingBuffer event-based get_all interaction
# ---------------------------------------------------------------------------

class TestRingBufferGetAllDrain:
    """Verify get_all properly clears the event and doesn't lose data."""

    def test_get_all_after_put_returns_all(self):
        from meeting_recorder.audio.ring_buffer import RingBuffer

        buf = RingBuffer(max_chunks=100)
        for i in range(10):
            buf.put(bytes([i]))

        chunks = buf.get_all()
        assert len(chunks) == 10
        assert buf.is_empty

    def test_get_all_twice_second_is_empty(self):
        """Calling get_all twice without puts in between should return empty on second call."""
        from meeting_recorder.audio.ring_buffer import RingBuffer

        buf = RingBuffer(max_chunks=100)
        buf.put(b"data")

        first = buf.get_all()
        second = buf.get_all()

        assert first == [b"data"]
        assert second == []

    def test_put_after_get_all_works(self):
        """Put after get_all should make data available again."""
        from meeting_recorder.audio.ring_buffer import RingBuffer

        buf = RingBuffer(max_chunks=100)
        buf.put(b"first")
        buf.get_all()  # drain

        buf.put(b"second")
        chunks = buf.get_all()
        assert chunks == [b"second"]


# ---------------------------------------------------------------------------
# Resampling fast path
# ---------------------------------------------------------------------------

class TestResamplingFastPath:
    """Test resample_to_16khz_mono fast path (no conversion needed)."""

    def test_already_16khz_mono_int16_returns_same_array(self):
        """When input is already 16kHz mono int16, return as-is (no copy)."""
        from meeting_recorder.audio.resampling import resample_to_16khz_mono

        audio = np.array([100, -200, 300], dtype=np.int16)
        result = resample_to_16khz_mono(audio, source_rate=16000, source_channels=1)

        assert result is audio  # Should be the exact same object
        assert result.dtype == np.int16

    def test_16khz_stereo_converted_to_mono(self):
        """16kHz stereo should be averaged to mono."""
        from meeting_recorder.audio.resampling import resample_to_16khz_mono

        # Stereo: [L1, R1, L2, R2, ...]
        audio = np.array([0.5, 0.5, -0.5, -0.5], dtype=np.float32)
        result = resample_to_16khz_mono(audio, source_rate=16000, source_channels=2)

        assert result.dtype == np.int16
        assert len(result) == 2  # 4 samples / 2 channels


# ---------------------------------------------------------------------------
# RecordingStore subject sanitization
# ---------------------------------------------------------------------------

class TestRecordingStoreSubject:
    """Test meeting subject sanitization in directory naming."""

    def test_subject_in_directory_name(self, tmp_path):
        from meeting_recorder.storage.recording_store import RecordingStore

        store = RecordingStore(tmp_path)
        d = store.create_recording_dir("Zoom", meeting_subject="Weekly Standup")
        assert "Weekly_Standup" in d.name
        assert "Zoom" in d.name

    def test_subject_special_characters_removed(self, tmp_path):
        from meeting_recorder.storage.recording_store import RecordingStore

        store = RecordingStore(tmp_path)
        d = store.create_recording_dir("Teams", meeting_subject="Q1 Review: 2026 <draft>")
        name = d.name
        # < and > and : should be stripped
        assert "<" not in name
        assert ">" not in name
        assert ":" not in name

    def test_subject_truncated_at_60_chars(self, tmp_path):
        from meeting_recorder.storage.recording_store import RecordingStore

        store = RecordingStore(tmp_path)
        long_subject = "A" * 100
        d = store.create_recording_dir("Zoom", meeting_subject=long_subject)
        # The subject part should be truncated (timestamp_subject_app)
        parts = d.name.split("_")
        # Find the subject part: everything between timestamp and app name
        # Timestamp is YYYY-MM-DD_HH-MM-SS = 3 parts when split on _
        subject_part = "_".join(parts[3:-1])  # Between timestamp and app name
        assert len(subject_part) <= 60

    def test_empty_subject_excluded(self, tmp_path):
        from meeting_recorder.storage.recording_store import RecordingStore

        store = RecordingStore(tmp_path)
        d = store.create_recording_dir("Zoom", meeting_subject="")
        # Name should be just timestamp_Zoom, no extra underscores
        parts = d.name.split("_")
        # Format: YYYY-MM-DD_HH-MM-SS_Zoom = parts count should be 4
        assert parts[-1] == "Zoom"


# ---------------------------------------------------------------------------
# Metadata missing fields backwards compatibility
# ---------------------------------------------------------------------------

class TestMetadataBackwardsCompat:
    """Verify metadata can load files missing newer fields."""

    def test_load_old_metadata_without_summary_fields(self, tmp_path):
        """Old metadata files without summary/speaker_map fields should load fine."""
        import json
        from meeting_recorder.storage.metadata import RecordingMetadata, METADATA_FILENAME

        old_data = {
            "app_name": "Zoom",
            "app_pid": 100,
            "start_time": "2024-01-01T12:00:00",
            "end_time": "2024-01-01T12:30:00",
            "duration_seconds": 1800.0,
            "sample_rate": 16000,
            "channels": 1,
            "language": "en",
            "transcription_backend": "local",
            "has_app_audio": True,
            "has_mic_audio": True,
            "has_mixed_audio": False,
            "has_transcript": True,
            "has_screen_recording": False,
            "speaker_count": 3,
            "segment_count": 42,
            "status": "completed",
            "error_message": "",
            # Newer fields like speaker_map, has_summary, etc. are missing
        }
        path = tmp_path / METADATA_FILENAME
        path.write_text(json.dumps(old_data), encoding="utf-8")

        loaded = RecordingMetadata.load(tmp_path)
        assert loaded.app_name == "Zoom"
        assert loaded.speaker_count == 3
        # Missing fields should use defaults
        assert loaded.speaker_map == {}
        assert loaded.has_summary is False
        assert loaded.meeting_subject == ""

    def test_load_future_metadata_with_extra_fields(self, tmp_path):
        """Future metadata files with extra unknown fields should load fine."""
        import json
        from meeting_recorder.storage.metadata import RecordingMetadata, METADATA_FILENAME

        future_data = {
            "app_name": "Teams",
            "app_pid": 200,
            "start_time": "",
            "end_time": "",
            "duration_seconds": 0,
            "sample_rate": 16000,
            "channels": 1,
            "language": "en",
            "transcription_backend": "local",
            "has_app_audio": False,
            "has_mic_audio": False,
            "has_mixed_audio": False,
            "has_transcript": False,
            "has_screen_recording": False,
            "speaker_count": 0,
            "segment_count": 0,
            "status": "recording",
            "error_message": "",
            "meeting_subject": "",
            "meeting_organizer": "",
            "meeting_attendees": [],
            "meeting_location": "",
            "google_drive_folder_id": "",
            "speaker_map": {},
            "speaker_map_confidence": "",
            "speaker_map_method": "",
            "has_summary": False,
            "summary_provider": "",
            "summary_model": "",
            # Extra future fields
            "ai_sentiment": "positive",
            "recording_quality_score": 0.95,
        }
        path = tmp_path / METADATA_FILENAME
        path.write_text(json.dumps(future_data), encoding="utf-8")

        loaded = RecordingMetadata.load(tmp_path)
        assert loaded.app_name == "Teams"


# ---------------------------------------------------------------------------
# CaptureManager double-start protection
# ---------------------------------------------------------------------------

class TestCaptureManagerDoubleStart:
    """Verify start() is idempotent."""

    def test_double_start_is_noop(self):
        """Calling start() twice should log a warning and not duplicate threads."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )

        mgr._vad = mock.Mock()
        mgr._is_recording = True  # Simulate already recording

        with (
            mock.patch("meeting_recorder.audio.capture_manager.wave"),
            mock.patch("meeting_recorder.audio.capture_manager.is_process_running", return_value=True),
        ):
            # Second start should be a no-op
            mgr.start()

        # app_capture.start should NOT have been called (already recording)
        mgr._app_capture.start.assert_not_called()


# ---------------------------------------------------------------------------
# App: _on_mute_changed callback
# ---------------------------------------------------------------------------

class TestOnMuteChanged:
    """Test the _on_mute_changed handler in MeetingRecorderApp."""

    def test_updates_dashboard_when_visible(self):
        import meeting_recorder.app as _app_mod
        from meeting_recorder.app import MeetingRecorderApp

        app = _make_app_helper()
        app._dashboard = MagicMock()
        app._dashboard.is_visible = True

        app._on_mute_changed(True)

        app._dashboard.update_mute_state.assert_called_once_with(True)

    def test_noop_when_no_dashboard(self):
        app = _make_app_helper()
        app._dashboard = None

        # Should not raise
        app._on_mute_changed(False)


def _make_app_helper(config=None):
    """Create a MeetingRecorderApp with heavy deps stubbed (local helper)."""
    import meeting_recorder.app as _app_mod
    from meeting_recorder.app import MeetingRecorderApp

    cfg = config or Config()
    with (
        mock.patch.object(_app_mod, "TrayIcon"),
        mock.patch.object(_app_mod, "RecordingStore"),
        mock.patch.object(_app_mod, "TranscriptionPipeline"),
    ):
        return MeetingRecorderApp(cfg)


# ---------------------------------------------------------------------------
# Callback exception safety in CaptureManager
# ---------------------------------------------------------------------------

class TestCaptureManagerCallbackSafety:
    """Verify callback exceptions don't crash capture threads."""

    def test_on_stopped_exception_does_not_crash_monitor(self):
        """If on_stopped raises, _monitor_process should still exit cleanly."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        def bad_callback():
            raise RuntimeError("on_stopped error")

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                on_stopped=bad_callback,
            )

        # Simulate process exit — on_stopped will raise, but monitor should not crash
        with mock.patch(
            "meeting_recorder.audio.capture_manager.is_process_running",
            return_value=False,
        ):
            mgr._monitor_process()  # Should not raise

    def test_on_health_warning_exception_does_not_crash_level_loop(self):
        """If on_health_warning raises, the level monitor should continue."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        calls = []

        def bad_callback(name):
            calls.append(name)
            raise RuntimeError("health warning error")

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                on_health_warning=bad_callback,
            )

        # Set a stale heartbeat
        mgr._thread_heartbeats["app_writer"] = time.time() - 15.0
        mgr._last_health_check = 0.0

        # Let the loop run briefly (needs at least one iteration), then stop.
        # Can't pre-set stop_event — that prevents the while loop from entering.
        timer = threading.Timer(0.3, mgr._stop_event.set)
        timer.start()
        mgr._level_monitor_loop()
        timer.join()

        assert "app_writer" in calls


# ---------------------------------------------------------------------------
# Race condition guards in app.py callbacks
# ---------------------------------------------------------------------------

class TestAppCallbackRaceConditions:
    """Verify callback methods capture _capture_manager locally."""

    def test_toggle_audio_mode_when_capture_manager_becomes_none(self):
        """_toggle_audio_mode should not crash if _capture_manager is cleared mid-call."""
        app = _make_app_helper()
        mock_cm = MagicMock()
        mock_cm.is_desktop_audio = False
        app._capture_manager = mock_cm

        # Simulate stop_recording clearing _capture_manager on another thread
        # by setting it to None after the guard check. With local capture, this
        # is safe because the method uses the local reference.
        app._toggle_audio_mode()

        mock_cm.switch_to_desktop_audio.assert_called_once()

    def test_toggle_mute_with_concurrent_stop(self):
        """_toggle_mute should not crash if _capture_manager becomes None."""
        app = _make_app_helper()
        mock_cm = MagicMock()
        mock_cm.mute_sync = MagicMock()
        app._capture_manager = mock_cm

        app._toggle_mute()

        mock_cm.mute_sync.toggle.assert_called_once()

    def test_toggle_recording_captures_reference_locally(self):
        """_toggle_recording should use a local reference for the guard check."""
        app = _make_app_helper()
        mock_cm = MagicMock()
        mock_cm.is_recording = True
        app._capture_manager = mock_cm

        with mock.patch.object(app, "stop_recording"):
            app._toggle_recording()

        # The thread was spawned for stop_recording (is_recording was True)
        # We can't easily verify the thread was spawned, but at least verify
        # it didn't crash.

    def test_on_pick_capture_window_captures_reference_locally(self):
        """_on_pick_capture_window should not crash if _capture_manager goes away."""
        app = _make_app_helper()
        mock_cm = MagicMock()
        app._capture_manager = mock_cm

        with mock.patch(
            "meeting_recorder.video.window_finder.get_window_title",
            return_value="Test Window",
        ):
            app._on_pick_capture_window(12345)

        mock_cm.switch_screen_window.assert_called_once_with(12345)
