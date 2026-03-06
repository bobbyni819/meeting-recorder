"""Tests for meeting auto-detection and auto-start recording."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from meeting_recorder.audio.process_finder import MeetingProcess


class TestAutoStartScanner:
    """Test the meeting scanner thread in MeetingRecorderApp."""

    def _make_app(self, auto_start: bool = False):
        """Create a MeetingRecorderApp with auto_start config."""
        with patch("meeting_recorder.app.Config") as MockConfig:
            config = MagicMock()
            config.recording.auto_start = auto_start
            config.recording.output_dir = "~/MeetingRecordings"
            config.recording.language = "en"
            config.recording.user_name = "User"
            config.recording.live_transcription = False
            config.audio.sample_rate = 16000
            config.audio.channels = 1
            config.audio.chunk_duration_ms = 30
            config.audio.mic_device = ""
            config.vad.threshold = 0.5
            config.hotkey.toggle_recording = "ctrl+shift+r"
            config.hotkey.toggle_mute = "ctrl+shift+u"
            config.hotkey.toggle_dashboard = "ctrl+shift+d"
            config.screen_recording.enabled = False
            config.screen_recording.fps = 30.0
            config.outlook.enabled = False
            config.outlook.buffer_minutes = 10
            config.dashboard.enabled = False
            config.output_dir = MagicMock()
            config.output.formats = ["json", "txt"]

            from meeting_recorder.app import MeetingRecorderApp
            app = MeetingRecorderApp(config=config)
            return app

    def test_scanner_not_started_when_disabled(self):
        """Scanner thread should not be created when auto_start=False."""
        app = self._make_app(auto_start=False)
        assert app._scanner_thread is None

    def test_scanner_stops_on_signal(self):
        """Scanner thread should exit when stop event is set."""
        app = self._make_app(auto_start=False)
        # Manually start scanner with no meeting processes
        with patch(
            "meeting_recorder.app.find_meeting_processes",
            return_value=[],
        ):
            app._start_meeting_scanner()
            assert app._scanner_thread is not None
            assert app._scanner_thread.is_alive()
            app._stop_meeting_scanner()
            app._scanner_thread.join(timeout=3.0)
            assert not app._scanner_thread.is_alive()

    def test_scanner_triggers_auto_start(self):
        """Scanner should call _start_recording_for_process when meeting detected."""
        app = self._make_app(auto_start=False)

        fake_proc = MeetingProcess(
            pid=1234, name="zoom.exe", app_key="zoom", display_name="Zoom",
        )

        with (
            patch(
                "meeting_recorder.app.find_meeting_processes",
                return_value=[fake_proc],
            ),
            patch(
                "meeting_recorder.app._find_meeting_window_pid",
                return_value=(1234, 100),
            ),
            patch.object(app, "_start_recording_for_process") as mock_start,
            patch("meeting_recorder.app.notifications"),
        ):
            app._start_meeting_scanner()
            # Wait for scanner to detect and trigger
            for _ in range(20):
                time.sleep(0.5)
                if mock_start.called:
                    break
            app._stop_meeting_scanner()

            mock_start.assert_called_once()
            called_proc = mock_start.call_args[0][0]
            assert called_proc.pid == 1234
            assert called_proc.app_key == "zoom"

    def test_scanner_ignores_low_score(self):
        """Scanner should NOT auto-start when window score is below threshold."""
        app = self._make_app(auto_start=False)

        fake_proc = MeetingProcess(
            pid=5678, name="zoom.exe", app_key="zoom", display_name="Zoom",
        )

        with (
            patch(
                "meeting_recorder.app.find_meeting_processes",
                return_value=[fake_proc],
            ),
            patch(
                "meeting_recorder.app._find_meeting_window_pid",
                return_value=(5678, 10),  # Low score = idle lobby
            ),
            patch.object(app, "_start_recording_for_process") as mock_start,
        ):
            app._start_meeting_scanner()
            time.sleep(3.0)
            app._stop_meeting_scanner()
            mock_start.assert_not_called()

    def test_scanner_skips_when_recording(self):
        """Scanner should skip detection when already recording."""
        app = self._make_app(auto_start=False)
        mock_cm = MagicMock()
        mock_cm.is_recording = True
        app._capture_manager = mock_cm

        fake_proc = MeetingProcess(
            pid=1234, name="zoom.exe", app_key="zoom", display_name="Zoom",
        )

        with (
            patch(
                "meeting_recorder.app.find_meeting_processes",
                return_value=[fake_proc],
            ) as mock_find,
            patch(
                "meeting_recorder.app._find_meeting_window_pid",
                return_value=(1234, 100),
            ),
            patch.object(app, "_start_recording_for_process") as mock_start,
        ):
            app._start_meeting_scanner()
            time.sleep(3.0)
            app._stop_meeting_scanner()
            mock_start.assert_not_called()

    def test_toggle_auto_start_saves_config(self):
        """Toggling auto-start should persist to config."""
        app = self._make_app(auto_start=False)

        with patch(
            "meeting_recorder.app.find_meeting_processes",
            return_value=[],
        ):
            app._toggle_auto_start(True)
            assert app.config.recording.auto_start is True
            app.config.save.assert_called()

            app._stop_meeting_scanner()
            app._toggle_auto_start(False)
            assert app.config.recording.auto_start is False
