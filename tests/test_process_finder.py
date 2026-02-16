"""Tests for meeting process detection (with mocked psutil)."""

from __future__ import annotations

from unittest import mock

import pytest

from meeting_recorder.audio.process_finder import (
    find_meeting_processes,
    find_primary_meeting_process,
    is_process_running,
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

class TestFindPrimaryMeetingProcess:
    """Test priority selection logic."""

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_prefers_zoom_over_teams(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(300, "ms-teams.exe"),
            _make_mock_process(100, "zoom.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.app_key == "zoom"

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_prefers_teams_over_webex(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(400, "atmgr.exe"),
            _make_mock_process(300, "teams.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is not None
        assert result.app_key == "teams"

    @mock.patch("meeting_recorder.audio.process_finder.psutil")
    def test_returns_none_when_nothing_found(self, mock_psutil):
        mock_psutil.process_iter.return_value = [
            _make_mock_process(1, "notepad.exe"),
        ]

        result = find_primary_meeting_process()
        assert result is None


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
