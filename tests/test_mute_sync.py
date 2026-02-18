"""Tests for MuteSync mute-state toggling logic (without keyboard hooks)."""

from __future__ import annotations

import threading
from unittest import mock

import winreg

import psutil
import pytest

from meeting_recorder.audio.mute_sync import (
    MuteSync,
    APP_MUTE_SHORTCUTS,
    detect_initial_mute_state,
)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestMuteSyncInitialState:
    """Verify initial mute state."""

    def test_default_start_unmuted(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        assert ms.is_muted is False

    def test_start_muted(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        assert ms.is_muted is True


# ---------------------------------------------------------------------------
# Toggle logic
# ---------------------------------------------------------------------------

class TestMuteSyncToggle:
    """Test the manual toggle() method."""

    def test_toggle_once(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        ms.toggle()
        assert ms.is_muted is False

    def test_toggle_twice(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        ms.toggle()
        ms.toggle()
        assert ms.is_muted is True

    def test_toggle_from_unmuted(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms.toggle()
        assert ms.is_muted is True

    def test_toggle_multiple(self):
        ms = MuteSync(app_key="teams", target_pids={200}, start_muted=True)
        states = []
        for _ in range(5):
            ms.toggle()
            states.append(ms.is_muted)
        assert states == [False, True, False, True, False]


# ---------------------------------------------------------------------------
# Thread safety of toggle
# ---------------------------------------------------------------------------

class TestMuteSyncConcurrency:
    """Verify toggle is thread-safe."""

    def test_concurrent_toggles_even(self):
        """Even number of toggles should return to initial state."""
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        num_threads = 10
        toggles_per_thread = 100  # Each thread toggles 100 times (even)
        barrier = threading.Barrier(num_threads)

        def toggle_worker():
            barrier.wait()
            for _ in range(toggles_per_thread):
                ms.toggle()

        threads = [threading.Thread(target=toggle_worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Total toggles: 10 * 100 = 1000 (even), so should be back to original
        assert ms.is_muted is True


# ---------------------------------------------------------------------------
# Internal callback: _on_mute_shortcut_pressed
# ---------------------------------------------------------------------------

class TestMuteSyncShortcutCallback:
    """Test the _on_mute_shortcut_pressed internal method."""

    def test_toggles_when_meeting_focused(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        # Mock the focus check to return True
        ms._is_meeting_app_focused = mock.MagicMock(return_value=True)

        ms._on_mute_shortcut_pressed()
        assert ms.is_muted is False

    def test_no_toggle_when_not_focused(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        # Mock the focus check to return False
        ms._is_meeting_app_focused = mock.MagicMock(return_value=False)

        ms._on_mute_shortcut_pressed()
        assert ms.is_muted is True  # unchanged


# ---------------------------------------------------------------------------
# Internal callback: _on_manual_toggle
# ---------------------------------------------------------------------------

class TestMuteSyncManualCallback:
    """Test the _on_manual_toggle internal method."""

    def test_manual_toggle_works_regardless_of_focus(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=True)
        ms._on_manual_toggle()
        assert ms.is_muted is False

    def test_manual_toggle_double(self):
        ms = MuteSync(app_key="teams", target_pids={200}, start_muted=False)
        ms._on_manual_toggle()
        ms._on_manual_toggle()
        assert ms.is_muted is False


# ---------------------------------------------------------------------------
# APP_MUTE_SHORTCUTS mapping
# ---------------------------------------------------------------------------

class TestAppMuteShortcuts:
    """Verify the shortcut mapping for known apps."""

    def test_zoom_shortcut(self):
        assert APP_MUTE_SHORTCUTS["zoom"] == "alt+a"

    def test_teams_shortcut(self):
        assert APP_MUTE_SHORTCUTS["teams"] == "ctrl+shift+m"

    def test_webex_shortcut(self):
        assert APP_MUTE_SHORTCUTS["webex"] == "ctrl+m"


# ---------------------------------------------------------------------------
# start / stop (mocked keyboard module)
# ---------------------------------------------------------------------------

class TestMuteSyncStartStop:
    """Test start/stop with a mocked keyboard module."""

    @mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()})
    def test_start_registers_hotkeys(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        ms.start()
        assert ms._started is True

    @mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()})
    def test_start_idempotent(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        ms.start()
        ms.start()  # second call should be no-op
        assert ms._started is True

    @mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()})
    def test_stop_after_start(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        ms.start()
        ms.stop()
        assert ms._started is False

    def test_stop_without_start(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        ms.stop()  # should not raise
        assert ms._started is False


# ---------------------------------------------------------------------------
# Unknown app key
# ---------------------------------------------------------------------------

class TestMuteSyncUnknownApp:
    """Test behavior with an unknown app key."""

    def test_no_shortcut_for_unknown_app(self):
        ms = MuteSync(app_key="unknown_app", target_pids={999})
        # toggle should still work (default is unmuted, toggle -> muted)
        ms.toggle()
        assert ms.is_muted is True


# ---------------------------------------------------------------------------
# detect_initial_mute_state
# ---------------------------------------------------------------------------

class TestDetectInitialMuteState:
    """Test registry-based initial mute detection."""

    ZOOM_EXE = r"C:\Program Files\Zoom\bin\Zoom.exe"
    ZOOM_REGISTRY = "C:#Program Files#Zoom#bin#Zoom.exe"
    BASE_KEY_PATH = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion"
        r"\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"
    )

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_detect_unmuted_when_mic_in_use(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """LastUsedTimeStop == 0 means mic in use -> unmuted (False)."""
        mock_process.return_value.exe.return_value = self.ZOOM_EXE

        mock_base_key = mock.MagicMock()
        mock_sub_key = mock.MagicMock()
        mock_open_key.side_effect = [mock_base_key, mock_sub_key]

        subkey_name = f"{self.ZOOM_REGISTRY}"
        mock_enum_key.side_effect = [subkey_name, OSError]

        mock_query.return_value = (0, winreg.REG_QWORD)

        result = detect_initial_mute_state(1234)

        assert result is False
        mock_process.assert_called_once_with(1234)

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_detect_muted_when_mic_not_in_use(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """LastUsedTimeStop > 0 means mic not in use -> muted (True)."""
        mock_process.return_value.exe.return_value = self.ZOOM_EXE

        mock_base_key = mock.MagicMock()
        mock_sub_key = mock.MagicMock()
        mock_open_key.side_effect = [mock_base_key, mock_sub_key]

        subkey_name = f"{self.ZOOM_REGISTRY}"
        mock_enum_key.side_effect = [subkey_name, OSError]

        mock_query.return_value = (133200000000000000, winreg.REG_QWORD)

        result = detect_initial_mute_state(1234)

        assert result is True

    @mock.patch("psutil.Process")
    def test_returns_none_when_process_not_found(self, mock_process):
        """NoSuchProcess exception -> None."""
        mock_process.side_effect = psutil.NoSuchProcess(9999)

        result = detect_initial_mute_state(9999)

        assert result is None

    @mock.patch("psutil.Process")
    @mock.patch("winreg.OpenKey")
    def test_returns_none_when_registry_key_missing(
        self, mock_open_key, mock_process,
    ):
        """OSError opening base registry key -> None."""
        mock_process.return_value.exe.return_value = self.ZOOM_EXE
        mock_open_key.side_effect = OSError("Registry key not found")

        result = detect_initial_mute_state(1234)

        assert result is None

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_returns_none_when_no_matching_subkey(
        self, mock_open_key, mock_enum_key, mock_close, mock_process,
    ):
        """No subkey matches the exe path -> None."""
        mock_process.return_value.exe.return_value = self.ZOOM_EXE

        mock_base_key = mock.MagicMock()
        mock_open_key.return_value = mock_base_key

        # Return subkeys that don't match Zoom's exe path
        mock_enum_key.side_effect = [
            "C:#Program Files#Teams#teams.exe",
            "C:#Program Files#Webex#webex.exe",
            OSError,
        ]

        result = detect_initial_mute_state(1234)

        assert result is None
