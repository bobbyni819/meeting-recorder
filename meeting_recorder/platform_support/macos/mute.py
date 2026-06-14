"""Best-effort macOS meeting mute-state detection through Accessibility.

The macOS Accessibility tree is not as stable across Zoom/Teams/Webex releases
as Windows UI Automation, so this detector is intentionally conservative:
permission failures, missing controls, and unexpected tree shapes return
``None`` rather than raising. Live macOS tuning should add app-specific control
identifiers as they are observed.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from meeting_recorder.platform_support.base import MuteDetector

logger = logging.getLogger(__name__)

_APP_NAME_HINTS = {
    "zoom": ("zoom", "us.zoom.xos"),
    "teams": ("teams", "microsoft teams", "com.microsoft.teams"),
    "webex": ("webex", "cisco webex", "com.cisco.webex"),
}

_TEXT_ATTRS = (
    "AXTitle",
    "AXDescription",
    "AXHelp",
    "AXIdentifier",
    "AXValue",
)


class MacMuteDetector(MuteDetector):
    """Read soft-mute state from Zoom/Teams/Webex with PyObjC AX APIs."""

    def read_mute_state(
        self,
        app_key: str,
        target_pids: Optional[set[int]] = None,
    ) -> Optional[bool]:
        try:
            ax = _load_ax()
            if not _accessibility_is_trusted(ax):
                logger.debug("Accessibility permission not granted; mute state unknown")
                return None

            pids = set(target_pids or _find_app_pids(app_key))
            if not pids:
                return None

            for pid in pids:
                state = self._read_pid(ax, pid)
                if state is not None:
                    return state
        except Exception:
            logger.debug("macOS mute detection failed", exc_info=True)
        return None

    def _read_pid(self, ax, pid: int) -> Optional[bool]:
        app = ax.AXUIElementCreateApplication(int(pid))
        roots = [app]
        for attr in ("AXWindows", "AXMenuBar"):
            value = _copy_attr(ax, app, attr)
            if isinstance(value, (list, tuple)):
                roots.extend(value)
            elif value is not None:
                roots.append(value)

        for root in roots:
            state = _walk_for_mute_control(ax, root)
            if state is not None:
                return state
        return None


def _load_ax():
    try:
        import ApplicationServices as ax
    except ImportError as exc:
        raise RuntimeError(
            "pyobjc-framework-ApplicationServices is required for macOS mute "
            "detection. Install with: pip install -e '.[macos]'"
        ) from exc
    return ax


def _accessibility_is_trusted(ax) -> bool:
    try:
        option = getattr(ax, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
        return bool(ax.AXIsProcessTrustedWithOptions({option: False}))
    except Exception:
        try:
            return bool(ax.AXIsProcessTrusted())
        except Exception:
            return False


def _find_app_pids(app_key: str) -> set[int]:
    try:
        from AppKit import NSWorkspace
    except ImportError:
        return set()

    hints = _APP_NAME_HINTS.get(app_key.lower(), (app_key.lower(),))
    pids: set[int] = set()
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        try:
            name = str(app.localizedName() or "").lower()
            bundle = str(app.bundleIdentifier() or "").lower()
            if any(hint in name or hint in bundle for hint in hints):
                pids.add(int(app.processIdentifier()))
        except Exception:
            continue
    return pids


def _copy_attr(ax, element, attr: str):
    try:
        result = ax.AXUIElementCopyAttributeValue(element, attr, None)
    except TypeError:
        result = ax.AXUIElementCopyAttributeValue(element, attr)
    except Exception:
        return None

    # PyObjC commonly bridges AX functions as either (error, value) tuples or
    # plain return values depending on framework version.
    if isinstance(result, tuple) and len(result) == 2:
        err, value = result
        if err != getattr(ax, "kAXErrorSuccess", 0):
            return None
        return value
    return result


def _walk_for_mute_control(ax, root) -> Optional[bool]:
    queue = deque([root])
    seen: set[int] = set()
    max_nodes = 700

    while queue and max_nodes > 0:
        max_nodes -= 1
        element = queue.popleft()
        ident = id(element)
        if ident in seen:
            continue
        seen.add(ident)

        state = _infer_mute_state(ax, element)
        if state is not None:
            return state

        # Most meeting mute controls are buttons nested under windows, but
        # menu bar items can expose the same command while the meeting window
        # is not focused. Walk both generic children and known AX containers.
        for attr in ("AXChildren", "AXVisibleChildren", "AXMenuItemMarkChar"):
            children = _copy_attr(ax, element, attr)
            if isinstance(children, (list, tuple)):
                queue.extend(children)
        for attr in ("AXWindows", "AXMenuBar"):
            child = _copy_attr(ax, element, attr)
            if isinstance(child, (list, tuple)):
                queue.extend(child)
            elif child is not None:
                queue.append(child)
    return None


def _infer_mute_state(ax, element) -> Optional[bool]:
    role = str(_copy_attr(ax, element, "AXRole") or "").lower()
    if role and not any(token in role for token in ("button", "menuitem", "checkbox")):
        return None

    parts = []
    value = None
    for attr in _TEXT_ATTRS:
        attr_value = _copy_attr(ax, element, attr)
        if attr == "AXValue":
            value = attr_value
        if attr_value is not None:
            parts.append(str(attr_value).lower())
    text = " ".join(parts)
    if "mute" not in text and "microphone" not in text and "mic" not in text:
        return None

    # Common meeting-app convention: a button titled "Unmute" is shown when
    # the current state is muted; a button titled "Mute" is shown while live.
    if "unmute" in text or "muted" in text or "mic off" in text:
        if "not muted" not in text:
            return True
    if "mute" in text or "mic on" in text or "microphone on" in text:
        return False

    # Some controls expose a boolean AXValue instead of changing their title.
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None

