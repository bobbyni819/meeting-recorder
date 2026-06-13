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
        ms.stop()  # don't leave the poller thread running

    @mock.patch.dict("sys.modules", {"keyboard": mock.MagicMock()})
    def test_start_idempotent(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        ms.start()
        ms.start()  # second call should be no-op
        assert ms._started is True
        ms.stop()

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
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_nonpackaged_match_skips_packaged_scan(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """A NonPackaged hit returns immediately (one OpenKey of HKCU)."""
        mock_process.return_value.exe.return_value = self.ZOOM_EXE

        mock_open_key.side_effect = [mock.MagicMock(), mock.MagicMock()]
        mock_enum_key.side_effect = [self.ZOOM_REGISTRY, OSError]
        mock_query.return_value = (0, winreg.REG_QWORD)

        result = detect_initial_mute_state(1234)

        assert result is False
        # Only NonPackaged base + its subkey opened; no packaged scan.
        assert mock_open_key.call_count == 2

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

        # Return subkeys that don't match Zoom's exe path. The second
        # OSError ends the packaged-app scan that follows NonPackaged.
        mock_enum_key.side_effect = [
            "C:#Program Files#Teams#teams.exe",
            "C:#Program Files#Webex#webex.exe",
            OSError,
            OSError,
        ]

        result = detect_initial_mute_state(1234)

        assert result is None


# ---------------------------------------------------------------------------
# Packaged-app (MSIX) registry detection
# ---------------------------------------------------------------------------

class TestDetectPackagedMuteState:
    """Registry detection for packaged apps (e.g. new Teams MSTeams_*)."""

    TEAMS_EXE = (
        r"C:\Program Files\WindowsApps"
        r"\MSTeams_24295.605.3225.8804_x64__8wekyb3d8bbwe\ms-teams.exe"
    )

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_packaged_teams_unmuted(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """LastUsedTimeStop == 0 on the package key -> unmuted (False)."""
        mock_process.return_value.exe.return_value = self.TEAMS_EXE

        # OpenKey: NonPackaged base, microphone base, MSTeams package key
        mock_open_key.side_effect = [
            mock.MagicMock(), mock.MagicMock(), mock.MagicMock(),
        ]
        # EnumKey: NonPackaged scan finds nothing; packaged scan walks
        # NonPackaged (skipped) then the MSTeams family key.
        mock_enum_key.side_effect = [
            OSError,  # NonPackaged subtree empty
            "NonPackaged",
            "MSTeams_8wekyb3d8bbwe",
        ]
        mock_query.return_value = (0, winreg.REG_QWORD)

        result = detect_initial_mute_state(4321, include_packaged=True)

        assert result is False

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_packaged_teams_muted(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """LastUsedTimeStop > 0 on the package key -> muted (True)."""
        mock_process.return_value.exe.return_value = self.TEAMS_EXE

        mock_open_key.side_effect = [
            mock.MagicMock(), mock.MagicMock(), mock.MagicMock(),
        ]
        mock_enum_key.side_effect = [
            OSError,
            "NonPackaged",
            "MSTeams_8wekyb3d8bbwe",
        ]
        mock_query.return_value = (133200000000000000, winreg.REG_QWORD)

        result = detect_initial_mute_state(4321, include_packaged=True)

        assert result is True

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_packaged_value_on_child_subkey(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """LastUsedTimeStop absent on the package key but on a child."""
        mock_process.return_value.exe.return_value = self.TEAMS_EXE

        mock_open_key.side_effect = [
            mock.MagicMock(),  # NonPackaged base
            mock.MagicMock(),  # microphone base
            mock.MagicMock(),  # MSTeams package key
            mock.MagicMock(),  # child subkey
        ]
        mock_enum_key.side_effect = [
            OSError,  # NonPackaged subtree empty
            "MSTeams_8wekyb3d8bbwe",
            "SomeChildKey",  # child of the package key
        ]
        # First query (package key itself) fails, child succeeds.
        mock_query.side_effect = [OSError, (0, winreg.REG_QWORD)]

        result = detect_initial_mute_state(4321, include_packaged=True)

        assert result is False

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_packaged_no_matching_family(
        self, mock_open_key, mock_enum_key, mock_close, mock_process,
    ):
        """Unrelated package family names do not match -> None."""
        mock_process.return_value.exe.return_value = (
            r"C:\Program Files\Zoom\bin\Zoom.exe"
        )

        mock_open_key.side_effect = [mock.MagicMock(), mock.MagicMock()]
        mock_enum_key.side_effect = [
            OSError,  # NonPackaged subtree empty
            "Microsoft.WindowsCamera_8wekyb3d8bbwe",
            OSError,
        ]

        result = detect_initial_mute_state(1234, include_packaged=True)

        assert result is None

    @mock.patch("psutil.Process")
    @mock.patch("winreg.OpenKey")
    def test_packaged_base_key_missing(self, mock_open_key, mock_process):
        """OSError opening both base keys -> None."""
        mock_process.return_value.exe.return_value = self.TEAMS_EXE
        mock_open_key.side_effect = OSError("not found")

        result = detect_initial_mute_state(4321, include_packaged=True)

        assert result is None

    @mock.patch("psutil.Process")
    @mock.patch("winreg.CloseKey")
    @mock.patch("winreg.QueryValueEx")
    @mock.patch("winreg.EnumKey")
    @mock.patch("winreg.OpenKey")
    def test_initial_state_skips_packaged_scan(
        self, mock_open_key, mock_enum_key, mock_query, mock_close, mock_process,
    ):
        """Default call (initial state) must NOT consult packaged keys.

        Teams holds the mic open while soft-muted; if the packaged signal
        reached the initial-state call it would flip the safe MUTED
        default to UNMUTED at recording start (privacy regression).
        """
        mock_process.return_value.exe.return_value = self.TEAMS_EXE

        mock_open_key.side_effect = [
            mock.MagicMock(), mock.MagicMock(), mock.MagicMock(),
        ]
        mock_enum_key.side_effect = [
            OSError,  # NonPackaged subtree empty
            "NonPackaged",
            "MSTeams_8wekyb3d8bbwe",
        ]
        mock_query.return_value = (0, winreg.REG_QWORD)  # would mean unmuted

        result = detect_initial_mute_state(4321)

        assert result is None  # not False — packaged signal must be ignored


# ---------------------------------------------------------------------------
# Poll loop error backoff
# ---------------------------------------------------------------------------

class TestPollBackoffRestore:
    """The poll interval backs off on repeated errors and recovers."""

    def _run_loop(self, ms, fail_count, total_polls):
        intervals = []
        calls = {"n": 0}

        def fake_cycle(apply_held):
            calls["n"] += 1
            if calls["n"] <= fail_count:
                raise RuntimeError("registry unavailable")

        ms._run_detection_cycle = fake_cycle

        def fake_wait(timeout=None):
            intervals.append(timeout)
            if len(intervals) >= total_polls:
                ms._poll_stop.set()
            return ms._poll_stop.is_set()

        ms._poll_stop.wait = fake_wait
        ms._poll_detection_loop()
        return intervals

    def test_backoff_after_repeated_errors(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        intervals = self._run_loop(ms, fail_count=15, total_polls=15)
        # After >10 consecutive errors the interval backs off to 10s.
        assert intervals[-1] == 10.0

    def test_interval_restored_after_recovery(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        intervals = self._run_loop(ms, fail_count=12, total_polls=15)
        assert 10.0 in intervals  # backed off while erroring
        # First successful poll restores the 1s interval permanently.
        assert intervals[12] == 1.0
        assert intervals[-1] == 1.0

    def test_no_backoff_when_healthy(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        intervals = self._run_loop(ms, fail_count=0, total_polls=5)
        assert all(i == 1.0 for i in intervals)


# ---------------------------------------------------------------------------
# Detection precedence: UIA first, held state, registry fallback
# ---------------------------------------------------------------------------

class TestDetectionPrecedence:
    """UIA wins over registry; held UIA state prevents flapping."""

    def test_uia_conclusive_beats_registry(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._detect_via_any_pid = mock.MagicMock(return_value=False)

        ms._run_detection_cycle(apply_held=False)

        assert ms.is_muted is True
        ms._detect_via_any_pid.assert_not_called()

    def test_registry_fallback_when_uia_never_concluded(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._detect_via_any_pid = mock.MagicMock(return_value=True)

        ms._run_detection_cycle(apply_held=False)

        assert ms.is_muted is True
        ms._detect_via_any_pid.assert_called_once()

    def test_held_uia_state_blocks_registry(self):
        """After a conclusive UIA poll, inconclusive polls skip registry."""
        ms = MuteSync(
            app_key="zoom", target_pids={100}, start_muted=False,
            privacy_first=False,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._detect_via_any_pid = mock.MagicMock(return_value=False)

        ms._run_detection_cycle(apply_held=False)  # UIA says muted
        assert ms.is_muted is True

        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._run_detection_cycle(apply_held=False)  # toolbar hidden

        assert ms.is_muted is True  # held, not flapped to registry
        ms._detect_via_any_pid.assert_not_called()

    def test_held_state_does_not_undo_hotkey_toggle(self):
        """The poller must not re-apply held state over a hotkey toggle."""
        ms = MuteSync(
            app_key="zoom", target_pids={100}, start_muted=False,
            privacy_first=False,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is True

        # Hotkey blind-toggle (no manual override)
        ms._is_meeting_app_focused = mock.MagicMock(return_value=True)
        ms._on_mute_shortcut_pressed()
        assert ms.is_muted is False

        # Inconclusive UIA poll holds, does not re-apply the stale state
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False


class TestPrivacyFirstMute:
    """Privacy-first: never record the mic without positive unmute proof."""

    def _ms(self, **kw):
        clock = kw.pop("clock")
        return MuteSync(
            app_key="zoom", target_pids={100}, start_muted=True,
            privacy_first=True, remute_grace_seconds=10.0, clock=clock, **kw,
        )

    def test_unmutes_on_conclusive_uia(self):
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms._detect_via_uia = mock.MagicMock(return_value=False)  # unmuted
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False

    def test_remutes_after_grace_when_blind(self):
        """Lost sight of the mute button while unmuted -> re-mute after grace."""
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms._detect_via_any_pid = mock.MagicMock(return_value=False)  # registry "unmuted"
        # Become unmuted via a conclusive UIA read at t=0
        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False

        # Toolbar hidden from now on (UIA blind)
        ms._detect_via_uia = mock.MagicMock(return_value=None)

        # Within grace: still unmuted
        t[0] = 5.0
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False

        # Past grace: re-muted (never keeps recording the room)
        t[0] = 11.0
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is True

    def test_registry_never_unmutes(self):
        """The flawed registry 'mic in use' signal must not unmute."""
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms._detect_via_uia = mock.MagicMock(return_value=None)  # blind
        ms._detect_via_any_pid = mock.MagicMock(return_value=False)  # "unmuted"
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is True  # stayed muted despite registry

    def test_registry_may_mute(self):
        """Registry 'muted' (app released mic) is allowed to mute."""
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms._run_detection_cycle(apply_held=False)  # unmuted via UIA
        assert ms.is_muted is False

        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._detect_via_any_pid = mock.MagicMock(return_value=True)  # muted
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is True

    def test_blind_then_uia_confirms_resets_grace(self):
        """A fresh conclusive unmuted read resets the re-mute timer."""
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms._run_detection_cycle(apply_held=False)  # unmuted at t=0

        t[0] = 8.0  # blind, within grace
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False

        t[0] = 9.0  # toolbar visible again, confirms unmuted -> resets timer
        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms._run_detection_cycle(apply_held=False)

        t[0] = 18.0  # 9s since last confirm < 10s grace -> still unmuted
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False

    def test_manual_override_not_remuted(self):
        """A manual unmute is never auto-re-muted by privacy-first."""
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms.toggle()  # manual unmute (was start_muted=True), override sticky
        assert ms.is_muted is False
        ms._detect_via_uia = mock.MagicMock(return_value=None)  # blind
        t[0] = 100.0  # well past grace
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False  # manual wins, no re-mute

    def test_hotkey_unmute_resets_remute_grace(self):
        """Unmuting via the meeting hotkey (Alt+A) gives the full grace window.

        Regression: _on_mute_shortcut_pressed toggled _muted but left
        _last_uia_ts stale, so if the toolbar was hidden (UIA blind) the very
        next poll re-muted the user within ~1s of them unmuting. The hotkey is
        fresh user-driven evidence of the unmute and must reset the timer.
        """
        t = [0.0]
        ms = self._ms(clock=lambda: t[0])
        ms._detect_via_uia = mock.MagicMock(return_value=True)  # conclusive MUTED
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is True  # _last_uia_ts pinned at t=0

        # Much later, user presses Alt+A to unmute; toolbar then hidden.
        t[0] = 30.0
        ms._is_meeting_app_focused = mock.MagicMock(return_value=True)
        ms._on_mute_shortcut_pressed()
        assert ms.is_muted is False

        # Immediate blind poll must NOT re-mute (we're within the fresh grace).
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        t[0] = 30.5
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False  # the bug re-muted here (30.5 - 0 >= grace)

        # Still re-mutes if we stay blind past a FULL grace window from unmute.
        t[0] = 41.0  # 11s after the hotkey unmute, grace = 10s
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is True

    def test_uia_disabled_uses_registry_only(self):
        ms = MuteSync(
            app_key="zoom", target_pids={100},
            start_muted=False, use_uia_detection=False,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms._detect_via_any_pid = mock.MagicMock(return_value=True)

        ms._run_detection_cycle(apply_held=False)

        assert ms.is_muted is True
        ms._detect_via_uia.assert_not_called()

    def test_registry_fallback_disabled(self):
        ms = MuteSync(
            app_key="zoom", target_pids={100},
            start_muted=False, use_registry_fallback=False,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._detect_via_any_pid = mock.MagicMock(return_value=True)

        ms._run_detection_cycle(apply_held=False)

        assert ms.is_muted is False  # nothing detected, state unchanged
        ms._detect_via_any_pid.assert_not_called()

    def test_detection_fires_callback(self):
        events = []
        ms = MuteSync(
            app_key="zoom", target_pids={100},
            start_muted=False, on_mute_changed=events.append,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._run_detection_cycle(apply_held=False)
        assert events == [True]

    def test_no_callback_when_state_unchanged(self):
        events = []
        ms = MuteSync(
            app_key="zoom", target_pids={100},
            start_muted=True, on_mute_changed=events.append,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._run_detection_cycle(apply_held=False)
        assert events == []

    def test_manual_override_blocks_detection(self):
        """Manual toggle wins over any subsequent auto-detection."""
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms.toggle()  # manual: muted, override sticky
        assert ms.is_muted is True

        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms._run_detection_cycle(apply_held=False)

        assert ms.is_muted is True  # detection did not win

    def test_detect_via_uia_returns_none_on_import_failure(self):
        ms = MuteSync(app_key="zoom", target_pids={100})
        with mock.patch.dict(
            "sys.modules", {"meeting_recorder.audio.uia_mute_detector": None},
        ):
            assert ms._detect_via_uia() is None


# ---------------------------------------------------------------------------
# resume_auto_sync
# ---------------------------------------------------------------------------

class TestResumeAutoSync:
    """Right-click affordance: clear override and re-detect once."""

    def test_clears_override_and_applies_detection(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms.toggle()  # manual: muted, override on
        assert ms._manual_override is True

        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms.resume_auto_sync()

        assert ms._manual_override is False
        assert ms.is_muted is False  # re-detected immediately

    def test_applies_held_uia_state_when_inconclusive(self):
        ms = MuteSync(
            app_key="zoom", target_pids={100}, start_muted=False,
            privacy_first=False,
        )
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._run_detection_cycle(apply_held=False)  # held state: muted

        ms.toggle()  # manual: unmuted, override on
        assert ms.is_muted is False

        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._detect_via_any_pid = mock.MagicMock(return_value=False)
        ms.resume_auto_sync()
        ms._resume_thread.join(timeout=2)  # detection runs off-thread now

        assert ms.is_muted is True  # held UIA state re-applied
        ms._detect_via_any_pid.assert_not_called()

    def test_falls_back_to_registry_when_nothing_held(self):
        ms = MuteSync(
            app_key="zoom", target_pids={100}, start_muted=False,
            privacy_first=False,
        )
        ms.toggle()  # manual: muted, override on

        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._detect_via_any_pid = mock.MagicMock(return_value=False)
        ms.resume_auto_sync()
        ms._resume_thread.join(timeout=2)

        assert ms._manual_override is False
        assert ms.is_muted is False

    def test_safe_when_detection_raises(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms.toggle()
        ms._detect_via_uia = mock.MagicMock(side_effect=RuntimeError("boom"))
        ms.resume_auto_sync()  # must not raise
        ms._resume_thread.join(timeout=2)
        assert ms._manual_override is False

    def test_resume_resets_remute_timer(self):
        """Resuming auto-sync must restart the privacy grace clock.

        Regression: a stale _last_uia_ts frozen during a long manual-override
        period would otherwise re-mute the user instantly on resume.
        """
        t = [0.0]
        ms = MuteSync(
            app_key="zoom", target_pids={100}, start_muted=True,
            privacy_first=True, remute_grace_seconds=10.0, clock=lambda: t[0],
        )
        ms.toggle()  # manual unmute at t=0, override on
        assert ms.is_muted is False
        t[0] = 100.0  # long override period; _last_uia_ts stays frozen at 0
        ms._detect_via_uia = mock.MagicMock(return_value=None)  # blind on resume
        ms.resume_auto_sync()
        ms._resume_thread.join(timeout=2)
        # Without the reset, blind_for would be 100s >> 10s grace -> instant
        # re-mute. With the reset it is ~0 -> stays unmuted.
        assert ms.is_muted is False

    def test_manual_toggle_after_resume_is_sticky_again(self):
        ms = MuteSync(app_key="zoom", target_pids={100}, start_muted=False)
        ms.toggle()
        ms._detect_via_uia = mock.MagicMock(return_value=None)
        ms._detect_via_any_pid = mock.MagicMock(return_value=None)
        ms.resume_auto_sync()
        ms._resume_thread.join(timeout=2)
        assert ms._manual_override is False

        ms.toggle()  # user takes manual control again
        assert ms._manual_override is True
        ms._detect_via_uia = mock.MagicMock(return_value=True)
        ms._run_detection_cycle(apply_held=False)
        assert ms.is_muted is False  # manual still wins

    def test_fires_callback_on_state_change(self):
        events = []
        ms = MuteSync(
            app_key="zoom", target_pids={100},
            start_muted=False, on_mute_changed=events.append,
        )
        ms.toggle()  # muted (manual)
        events.clear()
        ms._detect_via_uia = mock.MagicMock(return_value=False)
        ms.resume_auto_sync()
        assert events == [False]
