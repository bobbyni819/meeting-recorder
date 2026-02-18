"""Tests for ScreenCapture frame cache and CaptureManager.get_screen_frame()."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from meeting_recorder.video.screen_capture import ScreenCapture


class TestScreenCaptureLatestFrame:
    """Test the latest_frame cache on ScreenCapture."""

    def test_latest_frame_none_before_capture(self):
        sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"))
        assert sc.latest_frame is None

    def test_latest_frame_returns_assigned_value(self):
        sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sc._latest_frame = frame
        assert sc.latest_frame is frame

    def test_latest_frame_none_after_stop(self):
        sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"))
        sc._latest_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sc.stop()
        assert sc.latest_frame is None


class TestCaptureManagerGetScreenFrame:
    """Test CaptureManager.get_screen_frame() delegation."""

    def test_returns_none_when_screen_capture_disabled(self):
        from meeting_recorder.audio.capture_manager import CaptureManager

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"):
            cm = CaptureManager(
                pid=1234,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )
            assert cm.get_screen_frame() is None

    def test_returns_frame_from_screen_capture(self):
        from meeting_recorder.audio.capture_manager import CaptureManager

        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"):
            cm = CaptureManager(
                pid=1234,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )
            # Simulate a screen capture with a frame
            mock_sc = mock.MagicMock()
            mock_sc.latest_frame = frame
            cm._screen_capture = mock_sc
            assert cm.get_screen_frame() is frame
