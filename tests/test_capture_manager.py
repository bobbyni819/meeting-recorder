"""Focused tests for CaptureManager stop/auto-stop threading."""

from __future__ import annotations

import threading

from meeting_recorder.audio.capture_manager import CaptureManager


def test_window_closed_auto_stop_dispatches_off_capture_thread():
    called = threading.Event()
    callback_thread_names = []
    errors = []

    def on_stopped():
        callback_thread_names.append(threading.current_thread().name)
        called.set()

    cm = object.__new__(CaptureManager)
    cm._stop_event = threading.Event()
    cm._is_recording = True
    cm._on_stopped = on_stopped

    def invoke_handler():
        try:
            cm._on_screen_window_closed()
        except Exception as exc:
            errors.append(exc)

    capture_thread = threading.Thread(target=invoke_handler, name="screen-capture")
    capture_thread.start()
    capture_thread.join(timeout=2.0)

    assert not capture_thread.is_alive()
    assert errors == []
    assert called.wait(timeout=2.0)
    assert callback_thread_names == ["auto-stop"]
