"""Tests for meeting process detection (with mocked psutil)."""

from __future__ import annotations

from unittest import mock

import pytest

from meeting_recorder.audio.process_finder import (
    find_meeting_processes,
    find_primary_meeting_process,
    is_process_running,
    _find_meeting_window_pid,
    _score_meeting_window,
    _get_audio_rendering_pids,
    _AUDIO_SESSION_BONUS,
    MEETING_APPS,
    MeetingProcess,
)


# ---------------------------------------------------------------------------
# Helpers for creating mock processes
# ---------------------------------------------------------------------------

def _make_mock_process(pid: int, name: str):
    """Create a mock object compatible with psutil.process_iter()."""
    proc = mock.MagicMock()
    proc.info = {"pid": pid, "name": name}
    return proc


# ---------------------------------------------------------------------------
# find_meeting_processes
# ---------------------------------------------------------------------------

class TestFindMeetingProcesses:
    """Test find_meeting_processes() with mocked process lists."""

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_finds_zoom(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
            _make_mock_process(200, "notepad.exe"),
        ]

        result = find_meeting_processes()
        assert len(result) == 1
        assert result[0].app_key == "zoom"
        assert result[0].pid == 100
        assert result[0].display_name == "Zoom"

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_finds_teams(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(300, "ms-teams.exe"),
        ]

        result = find_meeting_processes()
        assert len(result) == 1
        assert result[0].app_key == "teams"
        assert result[0].display_name == "Microsoft Teams"

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_finds_webex(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(400, "atmgr.exe"),
        ]

        result = find_meeting_processes()
        assert len(result) == 1
        assert result[0].app_key == "webex"

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_no_meeting_processes(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(1, "explorer.exe"),
            _make_mock_process(2, "chrome.exe"),  # browsers skipped in auto-detect
        ]

        result = find_meeting_processes()
        assert len(result) == 0

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_skips_browsers_in_auto_detect(self, mock_psutil):
        """Browser processes should be skipped to avoid false positives."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(500, "chrome.exe"),
            _make_mock_process(501, "firefox.exe"),
            _make_mock_process(502, "msedge.exe"),
        ]

        result = find_meeting_processes()
        assert len(result) == 0

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_multiple_meeting_apps(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
            _make_mock_process(300, "ms-teams.exe"),
        ]

        result = find_meeting_processes()
        assert len(result) == 2
        app_keys = {p.app_key for p in result}
        assert app_keys == {"zoom", "teams"}

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_deduplicates_by_pid(self, mock_psutil):
        """Same PID should only be counted once."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
            _make_mock_process(100, "zoom.exe"),  # duplicate
        ]

        result = find_meeting_processes()
        assert len(result) == 1

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_handles_access_denied(self, mock_psutil):
        """AccessDenied processes should be silently skipped."""
        import psutil as real_psutil

        bad_proc = mock.MagicMock()
        bad_proc.info.__getitem__ = mock.Mock(
            side_effect=real_psutil.AccessDenied(pid=999)
        )

        mock_psutil.process_iter.return_value = [
            bad_proc,
            _make_mock_process(100, "zoom.exe"),
        ]
        # Re-assign the exception classes so the except clause can catch them
        mock_psutil.NoSuchProcess = real_psutil.NoSuchProcess
        mock_psutil.AccessDenied = real_psutil.AccessDenied

        result = find_meeting_processes()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# find_primary_meeting_process -- priority
# ---------------------------------------------------------------------------

class TestScoreMeetingWindow:
    """Test window title scoring for different apps."""

    def test_zoom_meeting_window(self):
        assert _score_meeting_window("Zoom Meeting", "zoom") == 100

    def test_zoom_generic_window(self):
        assert _score_meeting_window("Zoom Workplace", "zoom") == 10

    def test_zoom_irrelevant_window(self):
        assert _score_meeting_window("Notepad", "zoom") == 0

    def test_teams_generic_main_window(self):
        assert _score_meeting_window("Microsoft Teams", "teams") == 10

    def test_teams_chat_window_penalized(self):
        """Chat windows should score very low — they're never meetings."""
        assert _score_meeting_window("Chat | Faye Guo | Microsoft Teams", "teams") == 5
        assert _score_meeting_window("Chat | Microsoft Teams", "teams") == 5

    def test_teams_named_tab_window(self):
        """Calendar, channels, etc. — might be meeting, might not."""
        assert _score_meeting_window("Sprint Planning | Microsoft Teams", "teams") == 30

    def test_teams_meeting_popout(self):
        """Pop-out meeting windows don't have 'Microsoft Teams' in the title."""
        assert _score_meeting_window("Weekly Standup", "teams") == 50
        assert _score_meeting_window("1:1 with Bob", "teams") == 50

    def test_teams_popout_beats_generic(self):
        """Meeting pop-out (50) should outscore generic main window (10)."""
        popout = _score_meeting_window("Sprint Planning", "teams")
        generic = _score_meeting_window("Microsoft Teams", "teams")
        assert popout > generic

    def test_empty_title_scores_zero(self):
        assert _score_meeting_window("", "zoom") == 0
        assert _score_meeting_window("", "teams") == 0


class TestFindPrimaryMeetingProcess:
    """Test priority and scoring selection logic."""

    # _find_meeting_window_pid now returns (pid, score) tuples

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid", return_value=(None, 0))
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_prefers_zoom_over_teams_when_no_windows(self, mock_psutil, _mock_wnd):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(300, "ms-teams.exe"),
            _make_mock_process(100, "zoom.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.app_key == "zoom"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid", return_value=(None, 0))
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_prefers_teams_over_webex(self, mock_psutil, _mock_wnd):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(400, "atmgr.exe"),
            _make_mock_process(300, "teams.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.app_key == "teams"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid", return_value=(None, 0))
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_returns_none_when_nothing_found(self, mock_psutil, _mock_wnd):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(1, "notepad.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is None

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid")
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_window_match_selects_correct_zoom_pid(self, mock_psutil, mock_wnd):
        """When window matching finds the meeting PID, use it over the lobby PID."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),  # lobby
            _make_mock_process(200, "zoom.exe"),  # meeting
        ]
        mock_wnd.return_value = (200, 100)  # meeting PID, high score

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 200
        assert result.app_key == "zoom"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid")
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_window_match_creates_new_if_pid_not_in_list(self, mock_psutil, mock_wnd):
        """If the window-matched PID isn't in the process list, create a new entry."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
        ]
        mock_wnd.return_value = (999, 100)  # child process PID

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 999
        assert result.app_key == "zoom"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid", return_value=(None, 0))
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_falls_back_when_no_window_match(self, mock_psutil, _mock_wnd):
        """When no window match is found, fall back to first process."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 100

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid")
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_teams_meeting_beats_idle_zoom(self, mock_psutil, mock_wnd):
        """Active Teams meeting (score 50) beats idle Zoom lobby (score 10)."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),      # idle Zoom
            _make_mock_process(300, "ms-teams.exe"),   # active Teams meeting
        ]

        # Zoom: generic lobby window (score 10)
        # Teams: meeting pop-out (score 50)
        mock_wnd.side_effect = [(100, 10), (300, 50)]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 300
        assert result.app_key == "teams"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid")
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_zoom_meeting_beats_idle_teams(self, mock_psutil, mock_wnd):
        """Active Zoom meeting (score 100) beats idle Teams (score 10)."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
            _make_mock_process(300, "ms-teams.exe"),
        ]

        # Zoom: active meeting (score 100), Teams: generic window (score 10)
        mock_wnd.side_effect = [(100, 100), (300, 10)]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 100
        assert result.app_key == "zoom"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid")
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_both_in_meetings_uses_priority(self, mock_psutil, mock_wnd):
        """When both apps have high scores, Zoom wins by priority tiebreak."""
        mock_psutil.process_iter.return_value = [
            _make_mock_process(100, "zoom.exe"),
            _make_mock_process(300, "ms-teams.exe"),
        ]

        # Both have meeting windows with equal score
        mock_wnd.side_effect = [(100, 100), (300, 100)]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 100
        assert result.app_key == "zoom"

    @mock.patch("meeting_recorder.audio.process_finder._find_meeting_window_pid")
    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_teams_popout_beats_teams_chat(self, mock_psutil, mock_wnd):
        """Teams meeting pop-out window should be chosen over the chat window.

        This is the user's reported issue: multiple Teams windows open and the
        recorder picks the chat window instead of the meeting.
        """
        mock_psutil.process_iter.return_value = [
            _make_mock_process(300, "ms-teams.exe"),
        ]

        # _find_meeting_window_pid is called once for "teams" and should
        # internally pick the highest-scored window. We mock the result
        # as if it correctly picked the pop-out (pid=300, score=50).
        mock_wnd.return_value = (300, 50)

        result = find_primary_meeting_process()
        assert result is not None
        assert result.pid == 300


# ---------------------------------------------------------------------------
# Audio session detection
# ---------------------------------------------------------------------------

class TestAudioSessionDetection:
    """Test audio session PID detection."""

    def test_returns_empty_when_pycaw_missing(self):
        """Gracefully returns empty set if pycaw is not available."""
        with mock.patch.dict("sys.modules", {"pycaw": None, "pycaw.pycaw": None}):
            result = _get_audio_rendering_pids({100, 200})
            assert result == set()

    def test_audio_bonus_is_large(self):
        """Audio bonus should dominate window title scores."""
        assert _AUDIO_SESSION_BONUS > 100

    def test_chat_with_audio_bonus_beats_popout_without(self):
        """Even a chat window scores high when its PID has active audio."""
        chat_score = _score_meeting_window("Chat | Microsoft Teams", "teams")
        popout_score = _score_meeting_window("Weekly Standup", "teams")
        assert chat_score + _AUDIO_SESSION_BONUS > popout_score


# ---------------------------------------------------------------------------
# is_process_running
# ---------------------------------------------------------------------------

class TestIsProcessRunning:
    """Test is_process_running() with mocked psutil.Process."""

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_running_process(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.status.return_value = "running"
        mock_psutil.Process.return_value = mock_proc
        mock_psutil.STATUS_ZOMBIE = "zombie"

        assert is_process_running(123) is True

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_zombie_process(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.status.return_value = "zombie"
        mock_psutil.Process.return_value = mock_proc
        mock_psutil.STATUS_ZOMBIE = "zombie"

        assert is_process_running(123) is False

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_no_such_process(self, mock_psutil):
        import psutil as real_psutil
        mock_psutil.Process.side_effect = real_psutil.NoSuchProcess(pid=999)
        mock_psutil.NoSuchProcess = real_psutil.NoSuchProcess
        mock_psutil.AccessDenied = real_psutil.AccessDenied

        assert is_process_running(999) is False
