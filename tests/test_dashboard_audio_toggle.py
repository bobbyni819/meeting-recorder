"""Tests for the dashboard audio mode toggle button."""

from __future__ import annotations

from unittest import mock

import pytest


class TestDashboardAudioModeButton:
    def test_audio_mode_button_created_when_callback_provided(self):
        """The audio mode button should be created when on_toggle_audio_mode is set."""
        with mock.patch("meeting_recorder.ui.dashboard.tk") as mock_tk:
            mock_window = mock.Mock()
            mock_tk.Tk.return_value = mock_window
            mock_window.winfo_screenwidth.return_value = 1920
            mock_window.winfo_screenheight.return_value = 1080

            from meeting_recorder.ui.dashboard import GameBarDashboard, DashboardContext

            callback = mock.Mock()
            dash = GameBarDashboard(on_toggle_audio_mode=callback)
            assert dash._on_toggle_audio_mode is callback

    def test_audio_mode_button_not_created_without_callback(self):
        """Without the callback, _audio_mode_btn should remain None."""
        from meeting_recorder.ui.dashboard import GameBarDashboard

        dash = GameBarDashboard()
        assert dash._audio_mode_btn is None

    def test_update_audio_mode_desktop(self):
        """update_audio_mode(True) should schedule _set_audio_mode on the Tk thread."""
        from meeting_recorder.ui.dashboard import GameBarDashboard

        dash = GameBarDashboard()
        dash._window = mock.Mock()
        dash._is_visible = True

        dash.update_audio_mode(is_desktop=True)

        dash._window.after.assert_called_once()
        call_args = dash._window.after.call_args
        assert call_args[0][0] == 0  # scheduled immediately

    def test_update_audio_mode_noop_when_hidden(self):
        """update_audio_mode should do nothing when the dashboard is hidden."""
        from meeting_recorder.ui.dashboard import GameBarDashboard

        dash = GameBarDashboard()
        dash._window = mock.Mock()
        dash._is_visible = False

        dash.update_audio_mode(is_desktop=True)

        dash._window.after.assert_not_called()

    def test_set_audio_mode_desktop_updates_button(self):
        """_set_audio_mode(True) should set button text to Desktop Audio with amber color."""
        from meeting_recorder.ui.dashboard import GameBarDashboard, AMBER_WARNING

        dash = GameBarDashboard(on_toggle_audio_mode=mock.Mock())
        dash._audio_mode_btn = mock.Mock()
        dash._capture_warning_label = mock.Mock()

        dash._set_audio_mode(is_desktop=True)

        dash._audio_mode_btn.configure.assert_called_once_with(
            text=" Desktop Audio ", fg=AMBER_WARNING,
        )

    def test_set_audio_mode_app_updates_button(self):
        """_set_audio_mode(False) should set button text to App Audio with dim color."""
        from meeting_recorder.ui.dashboard import GameBarDashboard, TEXT_DIM

        dash = GameBarDashboard(on_toggle_audio_mode=mock.Mock())
        dash._audio_mode_btn = mock.Mock()
        dash._capture_warning_label = mock.Mock()

        dash._set_audio_mode(is_desktop=False)

        dash._audio_mode_btn.configure.assert_called_once_with(
            text=" App Audio ", fg=TEXT_DIM,
        )

    def test_set_audio_mode_toggles_capture_warning(self):
        """_set_audio_mode should show capture warning in desktop mode, hide in app mode."""
        from meeting_recorder.ui.dashboard import GameBarDashboard

        dash = GameBarDashboard(on_toggle_audio_mode=mock.Mock())
        dash._audio_mode_btn = mock.Mock()
        dash._capture_warning_label = mock.Mock()

        # Desktop mode: warning should be shown
        dash._set_audio_mode(is_desktop=True)
        dash._capture_warning_label.pack.assert_called()

        dash._capture_warning_label.reset_mock()

        # App mode: warning should be hidden
        dash._set_audio_mode(is_desktop=False)
        dash._capture_warning_label.pack_forget.assert_called()
