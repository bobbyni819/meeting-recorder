"""Find running meeting application processes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)

# Process name patterns for meeting apps
MEETING_APPS = {
    "zoom": {
        "process_names": ["zoom.exe", "zoom"],
        "display_name": "Zoom",
    },
    "teams": {
        "process_names": ["ms-teams.exe", "teams.exe", "teams"],
        "display_name": "Microsoft Teams",
    },
    "webex": {
        "process_names": ["webexmta.exe", "ciscowebexstart.exe", "atmgr.exe"],
        "display_name": "Webex",
    },
    "meet": {
        "process_names": ["chrome.exe", "msedge.exe", "firefox.exe"],
        "display_name": "Browser (Google Meet)",
    },
}


@dataclass
class MeetingProcess:
    """Represents a detected meeting application process."""
    pid: int
    name: str
    app_key: str
    display_name: str


def find_meeting_processes() -> list[MeetingProcess]:
    """Scan running processes for known meeting applications.

    Returns a list of MeetingProcess objects for detected meeting apps.
    Browser processes are only included if they match known patterns.
    """
    found = []
    seen_pids = set()

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = proc.info["pid"]
            pname = proc.info["name"].lower()

            if pid in seen_pids:
                continue

            for app_key, app_info in MEETING_APPS.items():
                # Skip browser-based apps in auto-detection (too many false positives)
                if app_key == "meet":
                    continue

                if pname in app_info["process_names"]:
                    found.append(MeetingProcess(
                        pid=pid,
                        name=pname,
                        app_key=app_key,
                        display_name=app_info["display_name"],
                    ))
                    seen_pids.add(pid)
                    logger.info("Found %s (PID %d)", app_info["display_name"], pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return found


def find_primary_meeting_process() -> MeetingProcess | None:
    """Find the most likely active meeting process.

    Prefers Zoom > Teams > Webex. Returns None if no meeting app found.
    """
    processes = find_meeting_processes()
    if not processes:
        return None

    # Priority order
    priority = {"zoom": 0, "teams": 1, "webex": 2}
    processes.sort(key=lambda p: priority.get(p.app_key, 99))
    return processes[0]


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
