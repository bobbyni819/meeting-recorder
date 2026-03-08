"""macOS notification backend.

Uses osascript to show native macOS notifications.
No external dependencies required.

For richer notifications, consider:
- pync: pip install pync (wraps terminal-notifier)
- rumps: pip install rumps (macOS menu bar apps with notifications)
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def _show(title: str, message: str, sound: bool = False) -> None:
    """Show a macOS notification via osascript."""
    try:
        sound_part = 'sound name "default"' if sound else ""
        script = (
            f'display notification "{message}" '
            f'with title "{title}" {sound_part}'
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5,
        )
    except FileNotFoundError:
        logger.warning("osascript not found. Notification: %s - %s", title, message)
    except Exception:
        logger.exception("Failed to show notification: %s", title)


def notify_recording_started(app_name: str) -> None:
    _show("Recording Started", f"Now recording audio from {app_name}.")


def notify_recording_stopped(duration_str: str, output_dir: str = "") -> None:
    _show("Recording Stopped", f"Recording saved. Duration: {duration_str}")


def notify_transcription_started() -> None:
    _show("Processing", "Transcribing recording...")


def notify_transcription_complete(output_dir: str) -> None:
    _show("Transcription Complete", f"Done: {output_dir}")


def notify_error(message: str) -> None:
    _show("Error", message, sound=True)


def notify_no_meeting_found() -> None:
    _show(
        "No Meeting Found",
        "No Zoom, Teams, or Webex detected. Use the menu bar to record any window.",
    )


def notify_info(message: str) -> None:
    _show("Meeting Recorder", message)
