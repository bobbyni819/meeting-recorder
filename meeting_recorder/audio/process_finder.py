"""Find running meeting application processes."""

from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)

# Audio session bonus: PIDs actively rendering audio get a big score boost.
# This directly identifies the meeting PID even when it has no visible window.
_AUDIO_SESSION_BONUS = 200


def _get_audio_rendering_pids(target_pids: set[int]) -> set[int]:
    """Find which target PIDs have active audio render sessions.

    Uses Windows Core Audio (pycaw) to enumerate audio sessions. A PID that
    is actively rendering audio is almost certainly the meeting/call process.

    Returns:
        Set of PIDs from target_pids that have active audio sessions.
    """
    try:
        from pycaw.pycaw import AudioUtilities

        sessions = AudioUtilities.GetAllSessions()
        active = set()
        for session in sessions:
            if session.Process and session.Process.pid in target_pids:
                active.add(session.Process.pid)
                logger.debug(
                    "Audio session found: PID %d (%s)",
                    session.Process.pid,
                    session.Process.name(),
                )
        return active
    except ImportError:
        logger.debug("pycaw not installed; audio session detection unavailable.")
        return set()
    except Exception:
        logger.debug("Audio session detection failed", exc_info=True)
        return set()


def _get_descendant_pids(parent_pids: set[int]) -> set[int]:
    """Get all descendant PIDs of the given parent processes.

    Teams (ms-teams.exe) renders meeting audio through msedgewebview2.exe
    child processes. This walks the process tree so audio session detection
    can find those child renderers.
    """
    descendants: set[int] = set()
    for ppid in parent_pids:
        try:
            parent = psutil.Process(ppid)
            for child in parent.children(recursive=True):
                descendants.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return descendants


if sys.platform == "win32":
    import ctypes.wintypes

    # Callback type for EnumWindows
    _WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
else:
    _WNDENUMPROC = None

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
            raw_name = proc.info["name"]
            if not raw_name:
                continue
            pname = raw_name.lower()

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


def _score_meeting_window(title: str, app_key: str) -> int:
    """Score how likely a window title belongs to an active meeting.

    Higher score = more likely to be the actual meeting window.
    Scores:
      100 = Definite meeting (e.g. "Zoom Meeting")
       80 = Strong meeting signal (e.g. "Meeting with Bobby Ni | Microsoft Teams")
       50 = Likely meeting (e.g. Teams pop-out with meeting subject)
       30 = Possible meeting (e.g. "Sprint Planning | Microsoft Teams")
       10 = Generic app window (e.g. "Microsoft Teams", "Zoom Workplace")
        0 = Not a match
    """
    t = title.strip().lower()
    if not t:
        return 0

    if app_key == "zoom":
        if "zoom meeting" in t:
            return 100  # Active Zoom meeting window
        if "zoom" in t:
            return 10   # Generic Zoom window (lobby, workplace, settings)
        return 0

    if app_key == "teams":
        # Exact "Microsoft Teams" is the main chat/activity window
        if t == "microsoft teams":
            return 10
        # Utility/navigation windows — never a meeting
        if t.startswith("chat |") or "| chat |" in t:
            return 5
        if "calendar" in t:
            return 5   # "Calendar | Calendar | Microsoft Teams" etc.
        if "activity" in t and "microsoft teams" in t:
            return 5   # Activity feed
        if "notifications" in t:
            return 2
        # "Meeting with ..." or "Teams Meeting" — almost certainly the meeting window
        if "meeting" in t and "microsoft teams" in t:
            return 80
        # "{subject} | Microsoft Teams" — likely a meeting or open channel; score higher
        # than calendar/chat so a real meeting subject beats utility windows
        if "microsoft teams" in t:
            return 35
        # A Teams-process window WITHOUT "Microsoft Teams" in the title
        # is typically the meeting pop-out window (just shows the subject)
        return 50

    if app_key == "webex":
        if "meeting" in t:
            return 100
        if "webex" in t:
            return 30
        return 10

    return 10


def _find_meeting_window_pid(app_key: str) -> tuple[int | None, int]:
    """Find the PID owning the most likely meeting window for an app.

    Enumerates all visible windows for the app's processes, scores each by
    meeting-likeness, and returns the best match.

    Returns:
        (pid, score) tuple. pid is None if no window found.
    """
    app_info = MEETING_APPS.get(app_key)
    if not app_info:
        return (None, 0)

    user32 = ctypes.windll.user32
    process_names = [n.lower() for n in app_info["process_names"]]

    # Collect all PIDs for this app
    target_pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() in process_names:
                target_pids.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not target_pids:
        return (None, 0)

    # Include child processes in audio detection — Teams renders audio
    # through msedgewebview2.exe children, not through ms-teams.exe itself.
    child_pids = _get_descendant_pids(target_pids)
    audio_candidate_pids = target_pids | child_pids
    if child_pids:
        logger.debug(
            "Including %d child PIDs for %s audio detection", len(child_pids), app_key,
        )

    # Check which PIDs are actively rendering audio (strong meeting signal)
    audio_pids = _get_audio_rendering_pids(audio_candidate_pids)
    if audio_pids:
        logger.info(
            "Audio-active PIDs for %s: %s", app_key, sorted(audio_pids),
        )

    # (score, area, pid, title) — collected during enumeration
    candidates: list[tuple[int, int, int, str]] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in target_pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value
        if not title:
            return True
        score = _score_meeting_window(title, app_key)
        if score > 0:
            # Boost PIDs that are actively rendering audio
            if pid.value in audio_pids:
                score += _AUDIO_SESSION_BONUS
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            candidates.append((score, area, pid.value, title))
            logger.debug(
                "  candidate: '%s' (score=%d, PID=%d, area=%d)",
                title, score, pid.value, area,
            )
        return True

    user32.EnumWindows(_WNDENUMPROC(_callback), 0)

    # If an audio-active PID has no visible window, add it as a synthetic
    # candidate so it can still be selected for audio capture.
    pids_with_windows = {c[2] for c in candidates}
    for apid in audio_pids:
        if apid not in pids_with_windows:
            logger.info(
                "Audio-active PID %d has no window; adding as candidate.", apid,
            )
            candidates.append((_AUDIO_SESSION_BONUS, 0, apid, "(audio-only)"))

    if not candidates:
        return (None, 0)

    # Best: highest score, then largest area as tiebreaker
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    best_score, _, best_pid, best_title = candidates[0]
    logger.info(
        "Best window for %s: '%s' (score=%d, PID=%d)",
        app_key, best_title, best_score, best_pid,
    )
    return (best_pid, best_score)


def find_primary_meeting_process() -> MeetingProcess | None:
    """Find the most likely active meeting process.

    Scans all detected meeting apps, scores their windows by meeting-likeness,
    and picks the app with the highest-scoring window. An active Teams meeting
    (score 50) beats an idle Zoom lobby (score 10). When scores tie, the static
    priority order (Zoom > Teams > Webex) is used as a tiebreaker.

    Falls back to first-found by priority if no windows are found.
    """
    processes = find_meeting_processes()
    if not processes:
        return None

    # Priority order (lower = preferred in tiebreaks)
    priority = {"zoom": 0, "teams": 1, "webex": 2}
    processes.sort(key=lambda p: priority.get(p.app_key, 99))

    # Score all app types and pick the best
    # (score, priority_rank, pid, proc_template)
    results: list[tuple[int, int, int, MeetingProcess]] = []
    checked_apps: set[str] = set()

    for proc in processes:
        if proc.app_key in checked_apps:
            continue
        checked_apps.add(proc.app_key)

        pid, score = _find_meeting_window_pid(proc.app_key)
        if pid is not None and score > 0:
            results.append((score, priority.get(proc.app_key, 99), pid, proc))

    if results:
        # Sort: highest score first, then lowest priority number (tiebreaker)
        results.sort(key=lambda r: (-r[0], r[1]))
        best_score, _, best_pid, best_proc = results[0]

        # Return existing MeetingProcess if PID matches, otherwise create new
        for p in processes:
            if p.pid == best_pid:
                logger.info(
                    "Selected %s PID %d (score=%d)",
                    p.display_name, best_pid, best_score,
                )
                return p
        logger.info(
            "Selected %s PID %d (score=%d, new entry)",
            best_proc.display_name, best_pid, best_score,
        )
        return MeetingProcess(
            pid=best_pid,
            name=best_proc.name,
            app_key=best_proc.app_key,
            display_name=best_proc.display_name,
        )

    # No window found for any app — fall back to first by priority
    logger.warning(
        "No meeting window found for any app; falling back to %s PID %d",
        processes[0].display_name, processes[0].pid,
    )
    return processes[0]


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
