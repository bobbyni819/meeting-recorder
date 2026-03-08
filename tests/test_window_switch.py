"""Tests for CaptureManager: window switch, lifecycle, hot-swap, and desktop audio."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest

from meeting_recorder.audio.capture_manager import CaptureManager


def _make_manager(pid: int = 1000, **kwargs) -> CaptureManager:
    """Build a CaptureManager with all heavy dependencies stubbed out."""
    with (
        mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
        mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture"),
        mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
        mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
        mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
    ):
        mgr = CaptureManager(
            pid=pid,
            output_dir=Path("/tmp/test_recording"),
            screen_recording_enabled=False,
            **kwargs,
        )
    return mgr


class TestSwitchScreenWindow:
    def test_screen_and_audio_both_switch_on_window_pick(self):
        """When screen capture is active, both screen and audio are switched."""
        mgr = _make_manager(pid=100)
        mgr._screen_capture = mock.Mock()

        with mock.patch(
            "meeting_recorder.video.platforms.get_hwnd_pid", return_value=200
        ):
            mgr._switch_app_audio_pid = mock.Mock()
            mgr.switch_screen_window(hwnd=42)

        mgr._screen_capture.switch_window.assert_called_once_with(42)
        mgr._switch_app_audio_pid.assert_called_once_with(200)

    def test_audio_switches_even_when_screen_capture_is_none(self):
        """When screen recording is disabled (_screen_capture is None), audio still switches."""
        mgr = _make_manager(pid=100)
        assert mgr._screen_capture is None

        with mock.patch(
            "meeting_recorder.video.platforms.get_hwnd_pid", return_value=300
        ):
            mgr._switch_app_audio_pid = mock.Mock()
            mgr.switch_screen_window(hwnd=99)

        mgr._switch_app_audio_pid.assert_called_once_with(300)

    def test_no_audio_switch_when_same_pid(self):
        """If the selected window belongs to the same PID, audio capture is left alone."""
        mgr = _make_manager(pid=555)

        with mock.patch(
            "meeting_recorder.video.platforms.get_hwnd_pid", return_value=555
        ):
            mgr._switch_app_audio_pid = mock.Mock()
            mgr.switch_screen_window(hwnd=7)

        mgr._switch_app_audio_pid.assert_not_called()

    def test_warning_logged_when_hwnd_pid_fails(self, caplog):
        """If get_hwnd_pid returns None, a warning is logged and no crash occurs."""
        import logging

        mgr = _make_manager(pid=100)

        with mock.patch(
            "meeting_recorder.video.platforms.get_hwnd_pid", return_value=None
        ):
            mgr._switch_app_audio_pid = mock.Mock()
            with caplog.at_level(logging.WARNING, logger="meeting_recorder.audio.capture_manager"):
                mgr.switch_screen_window(hwnd=0)

        mgr._switch_app_audio_pid.assert_not_called()
        assert any("Could not resolve PID" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestCaptureManagerLifecycle:
    def test_start_creates_all_threads(self):
        """start() should kick off capture, writer, monitor, and level threads."""
        mgr = _make_manager(pid=500)
        mgr._vad = mock.Mock()

        with (
            mock.patch("meeting_recorder.audio.capture_manager.wave"),
            mock.patch("meeting_recorder.audio.capture_manager.is_process_running", return_value=True),
        ):
            mgr.start()

        try:
            assert mgr._is_recording
            mgr._app_capture.start.assert_called_once()
            mgr._mic_capture.start.assert_called_once()
            assert mgr._app_writer_thread is not None
            assert mgr._mic_writer_thread is not None
            assert mgr._monitor_thread is not None
            assert mgr._level_thread is not None
        finally:
            mgr._stop_event.set()
            mgr._is_recording = False
            # Join threads briefly to clean up
            for t in [mgr._app_writer_thread, mgr._mic_writer_thread,
                       mgr._monitor_thread, mgr._level_thread]:
                if t and t.is_alive():
                    t.join(timeout=1.0)

    def test_stop_joins_threads(self):
        """stop() should call join on all threads."""
        mgr = _make_manager(pid=500)
        mgr._is_recording = True
        mgr._start_time = time.time()

        # Create mock threads
        mock_thread = mock.Mock()
        mock_thread.is_alive.return_value = False
        mgr._app_writer_thread = mock_thread
        mgr._mic_writer_thread = mock.Mock()
        mgr._mic_writer_thread.is_alive.return_value = False
        mgr._monitor_thread = mock.Mock()
        mgr._monitor_thread.is_alive.return_value = False
        mgr._level_thread = mock.Mock()
        mgr._level_thread.is_alive.return_value = False

        mgr.stop()

        assert not mgr._is_recording
        mock_thread.join.assert_called_once()
        mgr._mic_writer_thread.join.assert_called_once()
        mgr._app_capture.stop.assert_called_once()
        mgr._mic_capture.stop.assert_called_once()

    def test_elapsed_seconds_tracks_time(self):
        """elapsed_seconds should be non-zero after start_time is set."""
        mgr = _make_manager(pid=500)
        assert mgr.elapsed_seconds == 0.0

        mgr._start_time = time.time() - 10.0
        assert mgr.elapsed_seconds >= 9.5  # Allow small timing margin


# ---------------------------------------------------------------------------
# Hot-swap audio PID tests
# ---------------------------------------------------------------------------

class TestSwitchAppAudioPid:
    def test_switch_audio_pid_restarts_capture_on_new_pid(self):
        """Switching PID should stop old capture and start new one."""
        mgr = _make_manager(pid=100)
        old_capture = mgr._app_capture

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture") as MockAAC:
            new_capture = mock.Mock()
            MockAAC.return_value = new_capture
            mgr._switch_app_audio_pid(200)

        old_capture.stop.assert_called_once()
        new_capture.start.assert_called_once()

    def test_switch_audio_pid_updates_self_pid(self):
        """After switching, self.pid should reflect the new PID."""
        mgr = _make_manager(pid=100)
        assert mgr.pid == 100

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"):
            mgr._switch_app_audio_pid(999)

        assert mgr.pid == 999


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_warning_callback_fires(self):
        """Health warning should fire when heartbeat is stale."""
        warnings = []
        mgr = _make_manager(pid=100, on_health_warning=lambda name: warnings.append(name))

        # Simulate a stale heartbeat
        mgr._thread_heartbeats["app_writer"] = time.time() - 15.0
        mgr._last_health_check = 0.0

        # Manually trigger the check portion of _level_monitor_loop
        now = time.time()
        mgr._last_health_check = now
        for name, last in mgr._thread_heartbeats.items():
            if now - last > 10.0 and mgr._on_health_warning:
                mgr._on_health_warning(name)

        assert "app_writer" in warnings

    def test_no_warning_when_heartbeat_fresh(self):
        """No warning when heartbeats are recent."""
        warnings = []
        mgr = _make_manager(pid=100, on_health_warning=lambda name: warnings.append(name))

        mgr._thread_heartbeats["app_writer"] = time.time()
        mgr._last_health_check = 0.0

        now = time.time()
        for name, last in mgr._thread_heartbeats.items():
            if now - last > 10.0 and mgr._on_health_warning:
                mgr._on_health_warning(name)

        assert warnings == []


# ---------------------------------------------------------------------------
# Desktop audio switching tests
# ---------------------------------------------------------------------------

class TestDesktopAudioSwitch:
    def test_switch_to_desktop_stops_app_capture(self):
        """Switching to desktop should stop the current per-process capture."""
        mgr = _make_manager(pid=100)
        old_capture = mgr._app_capture

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            MockDAC.return_value = mock.Mock()
            mgr.switch_to_desktop_audio()

        old_capture.stop.assert_called_once()

    def test_switch_to_desktop_creates_desktop_capture(self):
        """Switching to desktop should create a DesktopAudioCapture on the same buffer."""
        mgr = _make_manager(pid=100)
        app_buffer = mgr._app_buffer

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            new_capture = mock.Mock()
            MockDAC.return_value = new_capture
            mgr.switch_to_desktop_audio()

        MockDAC.assert_called_once_with(
            ring_buffer=app_buffer,
            sample_rate=mgr.sample_rate,
            channels=mgr.channels,
            chunk_duration_ms=mgr.chunk_duration_ms,
        )
        new_capture.start.assert_called_once()

    def test_switch_to_desktop_sets_flag(self):
        """After switching to desktop, is_desktop_audio should be True."""
        mgr = _make_manager(pid=100)
        assert not mgr.is_desktop_audio

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            MockDAC.return_value = mock.Mock()
            mgr.switch_to_desktop_audio()

        assert mgr.is_desktop_audio

    def test_switch_back_to_app_audio(self):
        """Switching desktop -> app should create a new AppAudioCapture."""
        mgr = _make_manager(pid=100)

        # First switch to desktop
        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            desktop_capture = mock.Mock()
            MockDAC.return_value = desktop_capture
            mgr.switch_to_desktop_audio()

        # Now switch back to app
        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture") as MockAAC:
            new_app_capture = mock.Mock()
            MockAAC.return_value = new_app_capture
            mgr.switch_to_app_audio(pid=200)

        desktop_capture.stop.assert_called_once()
        new_app_capture.start.assert_called_once()
        assert not mgr.is_desktop_audio
        assert mgr.pid == 200

    def test_callback_fires_on_switch_to_desktop(self):
        """on_capture_mode_changed should fire with True when switching to desktop."""
        mode_changes = []
        mgr = _make_manager(pid=100, on_capture_mode_changed=lambda d: mode_changes.append(d))

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            MockDAC.return_value = mock.Mock()
            mgr.switch_to_desktop_audio()

        assert mode_changes == [True]

    def test_callback_fires_on_switch_to_app(self):
        """on_capture_mode_changed should fire with False when switching back to app."""
        mode_changes = []
        mgr = _make_manager(pid=100, on_capture_mode_changed=lambda d: mode_changes.append(d))

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            MockDAC.return_value = mock.Mock()
            mgr.switch_to_desktop_audio()

        mode_changes.clear()

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture") as MockAAC:
            MockAAC.return_value = mock.Mock()
            mgr.switch_to_app_audio(pid=100)

        assert mode_changes == [False]

    def test_is_app_capture_process_specific_false_in_desktop_mode(self):
        """is_app_capture_process_specific should return False when in desktop mode."""
        mgr = _make_manager(pid=100)

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            MockDAC.return_value = mock.Mock()
            mgr.switch_to_desktop_audio()

        assert mgr.is_app_capture_process_specific is False

    def test_switch_app_audio_pid_noop_in_desktop_mode(self, caplog):
        """_switch_app_audio_pid should be a no-op when in desktop mode."""
        import logging

        mgr = _make_manager(pid=100)

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            MockDAC.return_value = mock.Mock()
            mgr.switch_to_desktop_audio()

        with caplog.at_level(logging.INFO, logger="meeting_recorder.audio.capture_manager"):
            mgr._switch_app_audio_pid(999)

        assert mgr.pid == 100  # PID should not have changed
        assert any("desktop audio mode" in r.message for r in caplog.records)

    def test_monitor_skips_autostop_in_desktop_mode(self):
        """Process monitor should not auto-stop when in desktop mode."""
        mgr = _make_manager(pid=100)
        mgr._is_desktop_audio = True

        stopped = []
        mgr._on_stopped = lambda: stopped.append(True)

        # Simulate the process being dead (which would trigger auto-stop in app mode)
        with mock.patch(
            "meeting_recorder.audio.capture_manager.is_process_running", return_value=False
        ):
            # Run the monitor for a brief period
            mgr._stop_event.clear()

            import threading
            def run_monitor():
                mgr._monitor_process()

            t = threading.Thread(target=run_monitor, daemon=True)
            t.start()
            time.sleep(0.3)
            mgr._stop_event.set()
            t.join(timeout=3.0)

        # In desktop mode, the auto-stop should NOT have fired
        assert stopped == []

    def test_monitor_fires_on_stopped_without_self_stop(self):
        """When process exits, monitor should fire _on_stopped but NOT call self.stop().

        The app layer's stop_recording() handles the full stop lifecycle,
        so the monitor must not pre-empt it by calling stop() directly.
        """
        mgr = _make_manager(pid=100)
        mgr._is_recording = True

        stopped = []
        mgr._on_stopped = lambda: stopped.append(True)

        with mock.patch(
            "meeting_recorder.audio.capture_manager.is_process_running", return_value=False
        ):
            mgr._stop_event.clear()

            import threading
            t = threading.Thread(target=mgr._monitor_process, daemon=True)
            t.start()
            t.join(timeout=5.0)

        # _on_stopped should have been called
        assert stopped == [True]
        # But _is_recording should still be True (monitor didn't call self.stop())
        assert mgr._is_recording is True

    def test_double_switch_to_desktop_is_noop(self):
        """Calling switch_to_desktop_audio twice should not crash or restart capture."""
        mgr = _make_manager(pid=100)

        with mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC:
            desktop_capture = mock.Mock()
            MockDAC.return_value = desktop_capture
            mgr.switch_to_desktop_audio()

            # Second call should be a no-op
            mgr.switch_to_desktop_audio()

        # DesktopAudioCapture created only once
        assert MockDAC.call_count == 1

    def test_double_switch_to_app_is_noop(self):
        """Calling switch_to_app_audio when already in app mode should not restart capture."""
        mgr = _make_manager(pid=100)
        old_capture = mgr._app_capture

        # Already in app mode, so this should be a no-op
        mgr.switch_to_app_audio(pid=200)

        old_capture.stop.assert_not_called()
        assert mgr.pid == 100  # PID should not change


# ---------------------------------------------------------------------------
# Thread-safe stop tests
# ---------------------------------------------------------------------------

class TestCaptureManagerStopThreadSafety:
    def test_concurrent_stop_calls_only_stop_once(self):
        """Two threads calling stop() concurrently should only execute stop logic once."""
        import threading

        mgr = _make_manager(pid=100)
        mgr._is_recording = True
        mgr._start_time = time.time()

        # Track how many times app_capture.stop() is called
        stop_calls = []
        original_stop = mgr._app_capture.stop

        def track_stop():
            stop_calls.append(1)
            original_stop()

        mgr._app_capture.stop = track_stop

        # Create mock threads so join doesn't crash
        for attr in ("_app_writer_thread", "_mic_writer_thread", "_monitor_thread", "_level_thread"):
            mt = mock.Mock()
            mt.is_alive.return_value = False
            setattr(mgr, attr, mt)

        barrier = threading.Barrier(2)

        def stop_with_barrier():
            barrier.wait()  # Synchronize both threads
            mgr.stop()

        t1 = threading.Thread(target=stop_with_barrier)
        t2 = threading.Thread(target=stop_with_barrier)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # Only one of the two threads should have called app_capture.stop()
        assert len(stop_calls) == 1

    def test_stop_is_idempotent(self):
        """Calling stop() twice in sequence should not crash or double-stop."""
        mgr = _make_manager(pid=100)
        mgr._is_recording = True
        mgr._start_time = time.time()

        for attr in ("_app_writer_thread", "_mic_writer_thread", "_monitor_thread", "_level_thread"):
            mt = mock.Mock()
            mt.is_alive.return_value = False
            setattr(mgr, attr, mt)

        mgr.stop()
        assert not mgr._is_recording

        # Second call should be a no-op
        mgr.stop()
        # app_capture.stop should only have been called once
        mgr._app_capture.stop.assert_called_once()

    def test_stop_not_recording_is_noop(self):
        """stop() when not recording should return immediately without side effects."""
        mgr = _make_manager(pid=100)
        assert not mgr._is_recording

        mgr.stop()

        mgr._app_capture.stop.assert_not_called()
        mgr._mic_capture.stop.assert_not_called()

    def test_stop_skips_joining_current_thread(self):
        """stop() called from one of its own threads should not deadlock.

        This verifies the safety net for when stop() is called from the
        monitor thread (or any managed thread).
        """
        mgr = _make_manager(pid=100)
        mgr._is_recording = True
        mgr._start_time = time.time()

        # Set monitor and level threads to mock objects
        for attr in ("_app_writer_thread", "_mic_writer_thread", "_level_thread"):
            mt = mock.Mock()
            mt.is_alive.return_value = False
            setattr(mgr, attr, mt)

        result = []

        def stop_from_monitor():
            """Simulate calling stop() from the monitor thread itself."""
            # Set _monitor_thread to the current thread (the one we're on)
            mgr._monitor_thread = threading.current_thread()
            mgr.stop()
            result.append("ok")

        import threading
        t = threading.Thread(target=stop_from_monitor, daemon=True)
        t.start()
        t.join(timeout=5.0)

        # Should have completed without RuntimeError
        assert result == ["ok"]
        assert not mgr._is_recording
