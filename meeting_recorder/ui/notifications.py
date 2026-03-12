"""Windows toast notifications for recording events."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify_recording_started(app_name: str) -> None:
    """Show notification when recording starts."""
    _show("Recording Started", f"Now recording audio from {app_name}.")


def notify_recording_stopped(duration_str: str, output_dir: str = "") -> None:
    """Show notification when recording stops. Clicking opens the folder."""
    _show(
        "Recording Stopped",
        f"Recording saved. Duration: {duration_str}",
        launch=output_dir,
    )


def notify_transcription_started() -> None:
    """Show notification when transcription begins."""
    _show("Processing", "Transcribing recording...")


def notify_transcription_complete(output_dir: str, summary: str = "") -> None:
    """Show notification when transcription is done. Clicking opens the folder."""
    body = summary + "\n" if summary else ""
    body += f"Click to open: {output_dir}"
    _show(
        "Transcription Complete",
        body,
        launch=output_dir,
    )


def notify_error(message: str) -> None:
    """Show error notification."""
    _show("Error", message)


def notify_no_meeting_found() -> None:
    """Show notification when no meeting app is detected."""
    _show(
        "No Meeting Found",
        "No Zoom, Teams, or Webex detected. Use the tray menu to record any window.",
    )


def notify_info(message: str) -> None:
    """Show an informational notification."""
    _show("Meeting Recorder", message)


def _show(title: str, message: str, launch: str = "") -> None:
    """Show a Windows toast notification.

    Args:
        title: Notification title.
        message: Notification body.
        launch: Optional path or URL to open when the notification is clicked.
    """
    try:
        from winotify import Notification

        toast = Notification(
            app_id="Meeting Recorder",
            title=title,
            msg=message,
            duration="short",
            launch=launch,
        )
        toast.show()
    except ImportError:
        logger.warning("winotify not installed. Notification: %s - %s", title, message)
    except Exception:
        logger.exception("Failed to show notification: %s", title)
