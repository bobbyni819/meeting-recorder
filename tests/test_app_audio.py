"""Tests for ProcTap capture mode detection in AppAudioCapture."""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from meeting_recorder.audio.app_audio import AppAudioCapture
from meeting_recorder.audio.ring_buffer import RingBuffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_capture(pid: int = 1234) -> AppAudioCapture:
    """Create an AppAudioCapture with a dummy ring buffer."""
    return AppAudioCapture(pid=pid, ring_buffer=RingBuffer(max_chunks=100))


def _make_mock_proctap_module(is_process_specific=True, has_detection=True):
    """Create a mock proctap module with configurable capture mode behavior.

    Returns (mock_module, mock_cap) tuple so the test can inspect the cap.
    """
    mock_cap = MagicMock()
    mock_cap.get_format.return_value = "48000Hz stereo float32"
    mock_cap.read.return_value = None  # no data -> loop skips, hits stop_event

    if has_detection:
        mock_cap._backend._native.is_process_specific.return_value = is_process_specific
    else:
        # Simulate missing private API — raise AttributeError when called
        mock_cap._backend._native.is_process_specific.side_effect = AttributeError(
            "no such attribute"
        )

    mock_module = MagicMock()
    mock_module.ProcessAudioCapture.return_value = mock_cap
    return mock_module, mock_cap


# ---------------------------------------------------------------------------
# Tests: capture mode detection
# ---------------------------------------------------------------------------

class TestCaptureMode:
    """Tests for ProcTap capture mode detection."""

    def test_is_process_specific_initially_none(self):
        """Property should be None before capture loop runs."""
        capture = _make_capture()
        assert capture.is_process_specific is None

    def test_detects_process_specific_mode(self):
        """When ProcTap reports process-specific, property should be True."""
        capture = _make_capture()
        mock_module, _ = _make_mock_proctap_module(is_process_specific=True)

        with patch.dict(sys.modules, {"proctap": mock_module}):
            capture._stop_event.set()
            capture._capture_loop()

        assert capture.is_process_specific is True

    def test_detects_system_wide_fallback(self):
        """When ProcTap reports system-wide, property should be False."""
        capture = _make_capture()
        mock_module, _ = _make_mock_proctap_module(is_process_specific=False)

        with patch.dict(sys.modules, {"proctap": mock_module}):
            capture._stop_event.set()
            capture._capture_loop()

        assert capture.is_process_specific is False

    def test_detection_failure_returns_none(self):
        """When the private API is unavailable, property should remain None."""
        capture = _make_capture()
        mock_module, _ = _make_mock_proctap_module(has_detection=False)

        with patch.dict(sys.modules, {"proctap": mock_module}):
            capture._stop_event.set()
            capture._capture_loop()

        assert capture.is_process_specific is None

    def test_logs_info_for_process_specific(self, caplog):
        """Should log at INFO level when per-process capture is detected."""
        capture = _make_capture()
        mock_module, _ = _make_mock_proctap_module(is_process_specific=True)

        with patch.dict(sys.modules, {"proctap": mock_module}):
            with caplog.at_level(logging.INFO, logger="meeting_recorder.audio.app_audio"):
                capture._stop_event.set()
                capture._capture_loop()

        assert any("process-specific" in msg for msg in caplog.messages)

    def test_logs_warning_for_system_wide(self, caplog):
        """Should log at WARNING level when system-wide fallback is detected."""
        capture = _make_capture()
        mock_module, _ = _make_mock_proctap_module(is_process_specific=False)

        with patch.dict(sys.modules, {"proctap": mock_module}):
            with caplog.at_level(logging.WARNING, logger="meeting_recorder.audio.app_audio"):
                capture._stop_event.set()
                capture._capture_loop()

        assert any("system-wide fallback" in msg for msg in caplog.messages)

    def test_logs_debug_on_detection_failure(self, caplog):
        """Should log at DEBUG level when detection API is unavailable."""
        capture = _make_capture()
        mock_module, _ = _make_mock_proctap_module(has_detection=False)

        with patch.dict(sys.modules, {"proctap": mock_module}):
            with caplog.at_level(logging.DEBUG, logger="meeting_recorder.audio.app_audio"):
                capture._stop_event.set()
                capture._capture_loop()

        assert any("Could not detect" in msg for msg in caplog.messages)
