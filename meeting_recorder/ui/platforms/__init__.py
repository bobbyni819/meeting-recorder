"""Platform-specific UI backend selection.

On Windows: uses winotify for toast notifications.
On macOS:   uses osascript for native notifications.

All Tkinter UI (dashboard, main window, settings, search) is already
cross-platform and does NOT go through this module.
"""

from __future__ import annotations

import sys

PLATFORM = sys.platform

if PLATFORM == "win32":
    from meeting_recorder.ui.notifications import (
        notify_recording_started,
        notify_recording_stopped,
        notify_transcription_started,
        notify_transcription_complete,
        notify_error,
        notify_no_meeting_found,
        notify_info,
    )

elif PLATFORM == "darwin":
    from meeting_recorder.ui.platforms.macos import (  # noqa: F401
        notify_recording_started,
        notify_recording_stopped,
        notify_transcription_started,
        notify_transcription_complete,
        notify_error,
        notify_no_meeting_found,
        notify_info,
    )

else:
    # Fallback: log-only notifications
    import logging
    _logger = logging.getLogger(__name__)

    def notify_recording_started(app_name: str) -> None:
        _logger.info("Recording started: %s", app_name)

    def notify_recording_stopped(duration_str: str, output_dir: str = "") -> None:
        _logger.info("Recording stopped. Duration: %s", duration_str)

    def notify_transcription_started() -> None:
        _logger.info("Transcription started.")

    def notify_transcription_complete(output_dir: str) -> None:
        _logger.info("Transcription complete: %s", output_dir)

    def notify_error(message: str) -> None:
        _logger.error("Error: %s", message)

    def notify_no_meeting_found() -> None:
        _logger.info("No meeting app found.")

    def notify_info(message: str) -> None:
        _logger.info("%s", message)

__all__ = [
    "notify_recording_started",
    "notify_recording_stopped",
    "notify_transcription_started",
    "notify_transcription_complete",
    "notify_error",
    "notify_no_meeting_found",
    "notify_info",
]
