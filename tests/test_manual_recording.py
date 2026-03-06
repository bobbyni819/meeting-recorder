"""Tests for manual (any-window) recording mode."""

from __future__ import annotations


class TestManualMuteSync:
    """MuteSync should not hook any app shortcut for manual recordings."""

    def test_no_app_shortcut_for_manual(self):
        """APP_MUTE_SHORTCUTS should not have a 'manual' entry."""
        from meeting_recorder.audio.mute_sync import APP_MUTE_SHORTCUTS

        assert "manual" not in APP_MUTE_SHORTCUTS


class TestCallbackNullSafety:
    """Callbacks that read _capture_manager should handle None safely."""

    def test_toggle_mute_when_no_capture_manager(self):
        from meeting_recorder.app import MeetingRecorderApp

        app = MeetingRecorderApp.__new__(MeetingRecorderApp)
        app._capture_manager = None
        app._toggle_mute()  # should not raise

    def test_toggle_audio_mode_when_no_capture_manager(self):
        from meeting_recorder.app import MeetingRecorderApp

        app = MeetingRecorderApp.__new__(MeetingRecorderApp)
        app._capture_manager = None
        app._toggle_audio_mode()  # should not raise

    def test_on_pick_capture_window_when_no_capture_manager(self):
        from meeting_recorder.app import MeetingRecorderApp

        app = MeetingRecorderApp.__new__(MeetingRecorderApp)
        app._capture_manager = None
        app._on_pick_capture_window(12345)  # should not raise
