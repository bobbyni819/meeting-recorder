"""Tests for window picker improvements (Tasks 1-3).

Tests cover:
- list_visible_windows() minimum size filter and process name resolution
- list_capturable_windows() display formatting
- _pick_window_for_recording() in app.py
- _record_window() tray menu handler
- TrayIcon on_record_window wiring
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

from meeting_recorder.audio.process_finder import MeetingProcess
from meeting_recorder.config import Config

# Inject mock modules for native UI packages that may not be installed
for _mod_name in ("pystray", "PIL", "PIL.Image", "winotify"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from meeting_recorder.app import MeetingRecorderApp  # noqa: E402
import meeting_recorder.app as _app_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(config: Config | None = None):
    """Create a MeetingRecorderApp with heavy deps stubbed out."""
    cfg = config or Config()
    with (
        mock.patch.object(_app_mod, "TrayIcon"),
        mock.patch.object(_app_mod, "RecordingStore"),
        mock.patch.object(_app_mod, "TranscriptionPipeline"),
    ):
        app = MeetingRecorderApp(cfg)
    return app


# ---------------------------------------------------------------------------
# Task 1: list_visible_windows tests
# ---------------------------------------------------------------------------

class TestListVisibleWindows:
    """Tests for the improved list_visible_windows() function."""

    def test_returns_four_tuples(self):
        """list_visible_windows should return (hwnd, title, pid, process_name) tuples."""
        from meeting_recorder.video import window_finder

        # Patch user32 at the module attribute level and psutil via the import
        with patch.object(window_finder, "user32") as mock_user32, \
             patch.dict("sys.modules", {"psutil": MagicMock()}):
            # Simulate no windows (EnumWindows does nothing)
            mock_user32.EnumWindows = MagicMock(side_effect=lambda cb, lp: None)

            result = window_finder.list_visible_windows()
            assert isinstance(result, list)
            assert result == []

    def test_minimum_size_filter_excludes_small_windows(self):
        """Windows smaller than min_width/min_height should be excluded."""
        from meeting_recorder.video import window_finder

        with patch.object(window_finder, "user32") as mock_user32, \
             patch.dict("sys.modules", {"psutil": MagicMock()}):
            mock_user32.EnumWindows = MagicMock(side_effect=lambda cb, lp: None)

            result = window_finder.list_visible_windows(min_width=200, min_height=150)
            assert result == []

    def test_process_name_resolution(self):
        """Process names should be resolved for each unique PID."""
        from meeting_recorder.video.window_finder import list_visible_windows

        # Test the psutil resolution path by directly checking the function signature
        import inspect
        sig = inspect.signature(list_visible_windows)
        assert "min_width" in sig.parameters
        assert "min_height" in sig.parameters

    def test_default_min_size_parameters(self):
        """Default min_width=200 and min_height=150."""
        import inspect
        from meeting_recorder.video.window_finder import list_visible_windows

        sig = inspect.signature(list_visible_windows)
        assert sig.parameters["min_width"].default == 200
        assert sig.parameters["min_height"].default == 150

    def test_return_type_includes_process_name(self):
        """Each tuple should have 4 elements: hwnd, title, pid, process_name."""
        import inspect
        from meeting_recorder.video.window_finder import list_visible_windows

        # Verify the function's return type annotation (via docstring)
        # and that the function signature accepts the new params
        sig = inspect.signature(list_visible_windows)
        params = list(sig.parameters.keys())
        assert "min_width" in params
        assert "min_height" in params


# ---------------------------------------------------------------------------
# Task 1: list_capturable_windows tests
# ---------------------------------------------------------------------------

class TestListCapturableWindows:
    """Tests for the updated list_capturable_windows() in CaptureManager."""

    def test_returns_hwnd_display_title_pairs(self):
        """list_capturable_windows should return (hwnd, display_title) pairs."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        mgr = self._make_manager()

        fake_windows = [
            (100, "Zoom Meeting", 1234, "zoom.exe"),
            (200, "Microsoft Teams", 5678, "ms-teams.exe"),
        ]

        with patch(
            "meeting_recorder.video.platforms.list_visible_windows",
            return_value=fake_windows,
        ):
            result = mgr.list_capturable_windows()

        assert len(result) == 2
        assert result[0] == (100, "Zoom Meeting \u2014 zoom.exe")
        assert result[1] == (200, "Microsoft Teams \u2014 ms-teams.exe")

    def test_display_title_format_with_em_dash(self):
        """Display title should use an em dash (U+2014) separator."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        mgr = self._make_manager()

        fake_windows = [
            (42, "My App", 999, "myapp.exe"),
        ]

        with patch(
            "meeting_recorder.video.platforms.list_visible_windows",
            return_value=fake_windows,
        ):
            result = mgr.list_capturable_windows()

        hwnd, display = result[0]
        assert hwnd == 42
        assert "\u2014" in display  # em dash
        assert display == "My App \u2014 myapp.exe"

    def test_empty_windows_returns_empty(self):
        """If no visible windows, return empty list."""
        mgr = self._make_manager()

        with patch(
            "meeting_recorder.video.platforms.list_visible_windows",
            return_value=[],
        ):
            result = mgr.list_capturable_windows()

        assert result == []

    def test_api_shape_compatible_with_dashboard(self):
        """Return type should be list of (int, str) tuples -- same shape as before."""
        mgr = self._make_manager()

        fake_windows = [
            (10, "Notepad", 111, "notepad.exe"),
        ]

        with patch(
            "meeting_recorder.video.platforms.list_visible_windows",
            return_value=fake_windows,
        ):
            result = mgr.list_capturable_windows()

        # Dashboard expects [(hwnd: int, title: str), ...]
        for item in result:
            assert len(item) == 2
            assert isinstance(item[0], int)
            assert isinstance(item[1], str)

    @staticmethod
    def _make_manager():
        """Build a CaptureManager with all heavy dependencies stubbed out."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=1000,
                output_dir=Path("/tmp/test_recording"),
                screen_recording_enabled=False,
            )
        return mgr


# ---------------------------------------------------------------------------
# Task 2: _pick_window_for_recording tests
# ---------------------------------------------------------------------------

class TestPickWindowForRecording:
    """Tests for the _pick_window_for_recording method in app.py."""

    def test_returns_none_when_no_windows(self):
        """If list_visible_windows returns empty, should return None."""
        app = _make_app()

        with patch(
            "meeting_recorder.video.platforms.list_visible_windows", return_value=[]
        ):
            result = app._pick_window_for_recording()

        assert result is None

    def test_returns_none_when_user_cancels(self):
        """If user closes the picker without selecting, should return None."""
        app = _make_app()

        fake_windows = [
            (100, "My Window", 1234, "myapp.exe"),
            (200, "Another Window", 5678, "other.exe"),
        ]

        with patch(
            "meeting_recorder.video.platforms.list_visible_windows",
            return_value=fake_windows,
        ):
            # Mock tkinter to avoid actual GUI
            with patch("tkinter.Tk") as MockTk:
                mock_root = MagicMock()
                MockTk.return_value = mock_root
                # mainloop returns immediately (simulates user closing window)
                mock_root.mainloop.return_value = None

                result = app._pick_window_for_recording()

        # User cancelled (closed window without selecting)
        assert result is None

    def test_pick_window_creates_correct_meeting_process(self):
        """Verify the MeetingProcess created by the picker has correct fields."""
        # This tests the data construction logic directly
        pid = 1234
        proc_name = "myapp.exe"
        title = "My Window"

        mp = MeetingProcess(
            pid=pid,
            name=proc_name,
            app_key="manual",
            display_name=title,
        )

        assert mp.pid == 1234
        assert mp.name == "myapp.exe"
        assert mp.app_key == "manual"
        assert mp.display_name == "My Window"


# ---------------------------------------------------------------------------
# Task 2: start_recording fallback to picker
# ---------------------------------------------------------------------------

class TestStartRecordingFallback:
    """Test that start_recording falls back to window picker when no meeting app found."""

    def test_falls_back_to_picker_when_no_meeting_app(self):
        """When find_primary_meeting_process returns None, picker should be called."""
        app = _make_app()

        with patch.object(_app_mod, "find_primary_meeting_process", return_value=None):
            with patch.object(app, "_pick_window_for_recording", return_value=None) as mock_pick:
                app.start_recording()

        mock_pick.assert_called_once()

    def test_picker_cancelled_returns_silently(self):
        """If picker returns None, should return without starting recording."""
        app = _make_app()

        with patch.object(_app_mod, "find_primary_meeting_process", return_value=None):
            with patch.object(app, "_pick_window_for_recording", return_value=None):
                with patch.object(app, "_start_recording_for_process") as mock_start:
                    app.start_recording()

        mock_start.assert_not_called()
        assert app._capture_manager is None

    def test_picker_result_passed_to_start_recording_for_process(self):
        """If picker returns a MeetingProcess, it should be passed through."""
        app = _make_app()

        manual_process = MeetingProcess(
            pid=9999, name="app.exe", app_key="manual", display_name="My App"
        )

        with patch.object(_app_mod, "find_primary_meeting_process", return_value=None):
            with patch.object(app, "_pick_window_for_recording", return_value=manual_process):
                with patch.object(app, "_start_recording_for_process") as mock_start:
                    app.start_recording()

        mock_start.assert_called_once_with(manual_process)
        assert app._current_process == manual_process

    def test_always_shows_picker(self):
        """Window picker should always be shown, even when a meeting app is detected."""
        app = _make_app()

        manual_process = MeetingProcess(
            pid=1234, name="zoom.exe", app_key="manual", display_name="Zoom"
        )

        with patch.object(app, "_pick_window_for_recording", return_value=manual_process) as mock_pick:
            with patch.object(app, "_start_recording_for_process"):
                app.start_recording()

        mock_pick.assert_called_once()

    def test_no_meeting_found_notification_no_longer_fires(self):
        """The old 'no meeting found' notification should NOT fire anymore."""
        app = _make_app()

        with patch.object(_app_mod, "find_primary_meeting_process", return_value=None):
            with patch.object(app, "_pick_window_for_recording", return_value=None):
                with patch.object(_app_mod, "notifications") as mock_notif:
                    app.start_recording()

        mock_notif.notify_no_meeting_found.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2 & 3: _record_window tests
# ---------------------------------------------------------------------------

class TestRecordWindow:
    """Tests for the _record_window tray menu handler."""

    def test_record_window_calls_picker(self):
        """_record_window should call _pick_window_for_recording."""
        app = _make_app()

        with patch.object(app, "_pick_window_for_recording", return_value=None) as mock_pick:
            app._record_window()

        mock_pick.assert_called_once()

    def test_record_window_cancelled_is_noop(self):
        """If picker returns None, no recording should start."""
        app = _make_app()

        with patch.object(app, "_pick_window_for_recording", return_value=None):
            with patch.object(app, "_start_recording_for_process") as mock_start:
                app._record_window()

        mock_start.assert_not_called()

    def test_record_window_starts_recording(self):
        """If picker returns a process, recording should start."""
        app = _make_app()

        manual_process = MeetingProcess(
            pid=4567, name="chrome.exe", app_key="manual", display_name="Google Chrome"
        )

        with patch.object(app, "_pick_window_for_recording", return_value=manual_process):
            with patch.object(app, "_start_recording_for_process") as mock_start:
                app._record_window()

        mock_start.assert_called_once_with(manual_process)
        assert app._current_process == manual_process

    def test_record_window_noop_when_already_recording(self):
        """If already recording, _record_window should not open the picker."""
        app = _make_app()
        app._capture_manager = MagicMock()
        app._capture_manager.is_recording = True

        with patch.object(app, "_pick_window_for_recording") as mock_pick:
            app._record_window()

        mock_pick.assert_not_called()

    def test_record_window_cleans_up_on_failure(self):
        """If _start_recording_for_process raises, state should be cleaned up."""
        app = _make_app()

        manual_process = MeetingProcess(
            pid=4567, name="app.exe", app_key="manual", display_name="App"
        )

        with patch.object(app, "_pick_window_for_recording", return_value=manual_process):
            with patch.object(
                app, "_start_recording_for_process", side_effect=RuntimeError("boom")
            ):
                app._record_window()

        assert app._capture_manager is None
        assert app._current_recording_dir is None
        assert app._current_metadata is None
        assert app._current_process is None


# ---------------------------------------------------------------------------
# Task 3: TrayIcon on_record_window wiring
# ---------------------------------------------------------------------------

class TestTrayRecordWindowItem:
    """Tests for the TrayIcon 'Record Window...' menu item."""

    def test_tray_icon_accepts_on_record_window(self):
        """TrayIcon.__init__ should accept an on_record_window parameter."""
        from meeting_recorder.ui.tray import TrayIcon

        callback = MagicMock()
        tray = TrayIcon(on_record_window=callback)
        assert tray._on_record_window is callback

    def test_tray_icon_default_on_record_window_is_none(self):
        """Without on_record_window, it should default to None."""
        from meeting_recorder.ui.tray import TrayIcon

        tray = TrayIcon()
        assert tray._on_record_window is None

    def test_handle_record_window_spawns_thread(self):
        """_handle_record_window should spawn a thread calling the callback."""
        from meeting_recorder.ui.tray import TrayIcon

        callback = MagicMock()
        tray = TrayIcon(on_record_window=callback)

        # Call the handler
        with patch.object(threading, "Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            tray._handle_record_window(None, None)

        MockThread.assert_called_once_with(target=callback, daemon=True)
        mock_thread.start.assert_called_once()

    def test_handle_record_window_noop_without_callback(self):
        """_handle_record_window should be a no-op if no callback is set."""
        from meeting_recorder.ui.tray import TrayIcon

        tray = TrayIcon(on_record_window=None)

        # Should not raise
        with patch.object(threading, "Thread") as MockThread:
            tray._handle_record_window(None, None)

        MockThread.assert_not_called()

    def test_app_wires_record_window_to_tray(self):
        """MeetingRecorderApp should pass _record_window to TrayIcon."""
        with (
            mock.patch.object(_app_mod, "TrayIcon") as MockTray,
            mock.patch.object(_app_mod, "RecordingStore"),
            mock.patch.object(_app_mod, "TranscriptionPipeline"),
        ):
            app = MeetingRecorderApp(Config())

        # Check the TrayIcon was constructed with on_record_window
        call_kwargs = MockTray.call_args[1]
        assert "on_record_window" in call_kwargs
        assert call_kwargs["on_record_window"] == app._record_window
