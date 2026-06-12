"""UI Automation based mute-state detection for meeting apps.

Reads the actual mute-button state from the Zoom/Teams window via the
Windows UI Automation tree. This is the primary mute signal: the
registry mic-usage poller (CapabilityAccessManager) cannot see
soft-mute because Zoom and Teams keep the mic device open while muted,
so it reports "unmuted" for the whole meeting.

``uiautomation`` is imported lazily inside functions so this module can
always be imported even when the optional dependency is missing.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# Per-poll search limits: the mute button lives in the meeting toolbar,
# which is shallow in Zoom (~depth 5) and deeper in the WebView-based
# new Teams. The time budget is the hard limit; the depth limit keeps
# the walk from drowning in chat/transcript subtrees.
DEFAULT_BUDGET_SECONDS = 0.15
DEFAULT_MAX_DEPTH = 16

# UIA control types worth inspecting for a mute toggle.
_BUTTON_CONTROL_TYPE_NAMES = frozenset(
    {"ButtonControl", "CheckBoxControl", "SplitButtonControl"}
)

# UIA ToggleState enum values (stable Windows constants).
_TOGGLE_STATE_OFF = 0
_TOGGLE_STATE_ON = 1

# Name -> mute-state decision table, evaluated in order. Action labels
# ("Mute my microphone") name the opposite of the current state; state
# labels ("currently unmuted", "Mic off") name the state directly.
# \bmute\b deliberately does not match inside "unmute"/"unmuted".
_NAME_RULES: tuple = (
    (re.compile(r"\bunmute\b"), True),    # "Unmute my microphone" -> muted
    (re.compile(r"\bmute\b"), False),     # "Mute my microphone" -> unmuted
    (re.compile(r"\bunmuted\b"), False),  # Zoom "currently unmuted"
    (re.compile(r"\bmuted\b"), True),     # Zoom "currently muted"
    (re.compile(r"\bmic(rophone)?\s+(is\s+)?off\b"), True),   # Teams aria
    (re.compile(r"\bmic(rophone)?\s+(is\s+)?on\b"), False),
)

# Require microphone/audio/state context so generic buttons ("Mute All",
# per-participant "Mute John") in side panels are not misread.
_CONTEXT_RE = re.compile(r"\b(mic|microphone|audio|currently)\b")
_BARE_ACTIONS = {"mute": False, "unmute": True}

# Bare "Mic" toggle buttons (new Teams) carry the state in TogglePattern.
_MIC_ONLY_RE = re.compile(r"^\s*mic(rophone)?\s*$")

_thread_state = threading.local()


def classify_mute_button_name(name: Optional[str]) -> Optional[bool]:
    """Classify a UIA button Name into a mute state.

    Returns:
        True if the name indicates the app is currently muted, False if
        currently unmuted, None if the name is not a mute control.
    """
    if not name:
        return None
    text = name.strip().lower()
    if text in _BARE_ACTIONS:
        return _BARE_ACTIONS[text]
    if not _CONTEXT_RE.search(text):
        return None
    for pattern, muted in _NAME_RULES:
        if pattern.search(text):
            return muted
    return None


def detect_mute_state(
    pids: set[int],
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Optional[bool]:
    """Detect the meeting app's mute state from its UI Automation tree.

    Finds top-level windows owned by ``pids`` and searches each for the
    mute toggle button (Zoom: "Mute/Unmute my microphone", "currently
    (un)muted"; Teams: "Mute/Unmute mic", "Mic off", or a bare "Mic"
    toggle whose TogglePattern carries the state).

    Args:
        pids: Candidate process IDs of the meeting app (incl. children).
        budget_seconds: Hard time budget for the whole search.
        max_depth: Maximum UIA tree depth to walk per window.

    Returns:
        True if muted, False if unmuted, None when no conclusive mute
        control was found (toolbar hidden, window minimized,
        uiautomation unavailable, or search budget exhausted). Never
        raises.
    """
    if not pids:
        return None
    deadline = time.monotonic() + budget_seconds
    try:
        import uiautomation as auto
    except Exception:
        logger.debug("uiautomation unavailable; UIA mute detection disabled")
        return None
    try:
        _ensure_com_initialized()
        for window in _candidate_windows(auto, pids, deadline):
            for control in _walk(window, max_depth, deadline):
                state = _control_mute_state(control)
                if state is not None:
                    return state
            if time.monotonic() > deadline:
                break
    except Exception:
        logger.debug("UIA mute detection failed", exc_info=True)
    return None


def _ensure_com_initialized() -> None:
    """Initialize COM once for the calling thread.

    UIA requires per-thread COM init. Deliberately never uninitialized:
    uiautomation caches COM interface pointers process-wide, and a
    CoUninitialize here would invalidate them for subsequent polls.
    """
    if getattr(_thread_state, "com_initialized", False):
        return
    import comtypes

    try:
        comtypes.CoInitializeEx()
    except OSError:
        # Already initialized with a different threading model (e.g. an
        # STA Tk thread) — existing init is usable for UIA reads.
        pass
    _thread_state.com_initialized = True


def _candidate_windows(auto, pids: set[int], deadline: float) -> list:
    """Top-level windows owned by ``pids``, meeting windows first."""
    try:
        top_level = auto.GetRootControl().GetChildren()
    except Exception:
        return []
    candidates = []
    for window in top_level:
        if time.monotonic() > deadline:
            break
        try:
            if window.ProcessId not in pids:
                continue
            name = (window.Name or "").lower()
            class_name = (window.ClassName or "").lower()
        except Exception:
            continue
        # Zoom's in-meeting window class, then "meeting"-titled windows.
        if "zpcontentview" in class_name:
            priority = 0
        elif "meeting" in name:
            priority = 1
        else:
            priority = 2
        candidates.append((priority, len(candidates), window))
    candidates.sort(key=lambda item: item[:2])
    return [window for _, _, window in candidates]


def _walk(control, max_depth: int, deadline: float) -> Iterator[object]:
    """Depth-first walk of a UIA subtree with depth and time limits."""
    stack = [(control, 0)]
    while stack:
        if time.monotonic() > deadline:
            return
        node, depth = stack.pop()
        yield node
        if depth >= max_depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            continue
        for child in reversed(children):
            stack.append((child, depth + 1))


def _control_mute_state(control) -> Optional[bool]:
    """Read the mute state from one control, if it is the mute button."""
    try:
        if control.ControlTypeName not in _BUTTON_CONTROL_TYPE_NAMES:
            return None
        name = control.Name
    except Exception:
        return None
    state = classify_mute_button_name(name)
    if state is not None:
        return state
    return _toggle_pattern_state(control, name)


def _toggle_pattern_state(control, name: Optional[str]) -> Optional[bool]:
    """Secondary signal: a bare "Mic" toggle (new Teams) — On = live mic."""
    if not name or not _MIC_ONLY_RE.match(name.strip().lower()):
        return None
    try:
        import uiautomation as auto

        pattern = control.GetPattern(auto.PatternId.TogglePattern)
        if pattern is None:
            return None
        toggle_state = pattern.ToggleState
    except Exception:
        return None
    if toggle_state == _TOGGLE_STATE_ON:
        return False
    if toggle_state == _TOGGLE_STATE_OFF:
        return True
    return None
