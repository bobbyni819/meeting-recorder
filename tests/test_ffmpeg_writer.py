from __future__ import annotations

import importlib
import sys


def test_ffmpeg_writer_import_does_not_load_windows_wintypes() -> None:
    sys.modules.pop("meeting_recorder.video.ffmpeg_writer", None)
    sys.modules.pop("ctypes.wintypes", None)

    module = importlib.import_module("meeting_recorder.video.ffmpeg_writer")

    assert module.FFmpegVideoWriter is not None
    assert "ctypes.wintypes" not in sys.modules
