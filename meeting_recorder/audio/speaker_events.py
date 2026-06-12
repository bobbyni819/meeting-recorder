"""Capture active-speaker names from the Zoom/Teams UI during a meeting.

Diarization separates voices but cannot name them; the meeting app already
shows who is speaking. This polls the meeting window's UIAutomation tree
for the highlighted "active speaker" and logs (timestamp, name) events to
``speaker_events.jsonl`` in the recording directory. Post-processing aligns
those events with transcript segments to assign real names.

EXPERIMENTAL / opt-in (recording.capture_speaker_events, default off): the
active-speaker accessibility names exposed by Zoom and Teams vary by
version, view (gallery vs speaker), and could not be validated against a
live meeting at build time. The capture degrades to writing nothing when
it finds no usable indicators, so enabling it never harms a recording —
but verify it against a real call before relying on the labels.

The sidecar file is additive: it never alters transcript.json or any
existing output. Alignment only RENAMES generic "Speaker N" labels, never
labels already resolved to real names.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Accessibility-name fragments that tend to mark the active speaker tile.
# Intentionally broad; refine after a live-meeting validation pass.
_ACTIVE_SPEAKER_HINTS = (
    "is speaking",
    "speaking,",
    "active speaker",
    "current speaker",
)


class SpeakerEventCapture:
    """Background poller that logs active-speaker names during a recording."""

    def __init__(
        self,
        pids: set[int],
        output_path: Path,
        poll_interval: float = 1.0,
    ):
        self._pids = set(pids)
        self._output_path = Path(output_path)
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._start_monotonic = 0.0
        self._last_name: Optional[str] = None
        self._events_written = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._start_monotonic = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, name="speaker-events", daemon=True,
        )
        self._thread.start()
        logger.info("Speaker-event capture started (experimental).")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._events_written:
            logger.info(
                "Speaker-event capture: %d events logged.", self._events_written,
            )

    def _loop(self) -> None:
        try:
            import comtypes
        except Exception:
            logger.debug("comtypes unavailable; speaker capture disabled")
            return
        try:
            comtypes.CoInitializeEx(getattr(comtypes, "COINIT_MULTITHREADED", 0x0))
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                name = self._detect_active_speaker()
                if name and name != self._last_name:
                    self._write_event(name)
                    self._last_name = name
            except Exception:
                logger.debug("Speaker poll failed", exc_info=True)
            self._stop.wait(self._poll_interval)

    def _detect_active_speaker(self) -> Optional[str]:
        """Return the current active-speaker name from the meeting UI, or None."""
        try:
            import uiautomation as auto
        except Exception:
            return None
        try:
            root = auto.GetRootControl()
        except Exception:
            return None
        deadline = time.monotonic() + 0.2  # keep each poll cheap
        for win in root.GetChildren():
            try:
                if win.ProcessId not in self._pids or not win.Name:
                    continue
            except Exception:
                continue
            name = self._search_tree(win, deadline)
            if name:
                return name
        return None

    def _search_tree(self, ctrl, deadline: float, depth: int = 0) -> Optional[str]:
        if depth > 14 or time.monotonic() > deadline:
            return None
        try:
            children = ctrl.GetChildren()
        except Exception:
            return None
        for c in children:
            try:
                nm = (c.Name or "")
            except Exception:
                nm = ""
            low = nm.lower()
            for hint in _ACTIVE_SPEAKER_HINTS:
                if hint in low:
                    speaker = _extract_name(nm, hint)
                    if speaker:
                        return speaker
            found = self._search_tree(c, deadline, depth + 1)
            if found:
                return found
        return None

    def _write_event(self, name: str) -> None:
        ts = time.monotonic() - self._start_monotonic
        try:
            with open(self._output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(ts, 2), "speaker": name}) + "\n")
            self._events_written += 1
        except OSError:
            logger.debug("Could not write speaker event", exc_info=True)


def _extract_name(label: str, hint: str) -> Optional[str]:
    """Pull a person's name out of an accessibility label like 'Alice is speaking'."""
    idx = label.lower().find(hint)
    candidate = label[:idx].strip(" ,")
    # Reject empty / overly long / obviously-not-a-name strings.
    if 1 <= len(candidate) <= 60 and not candidate.isdigit():
        return candidate
    return None


def load_speaker_events(recording_dir: Path) -> list[tuple[float, str]]:
    """Load (timestamp, name) events from speaker_events.jsonl, sorted by time."""
    path = Path(recording_dir) / "speaker_events.jsonl"
    events: list[tuple[float, str]] = []
    if not path.exists():
        return events
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            events.append((float(obj["t"]), str(obj["speaker"])))
    except (OSError, ValueError, KeyError):
        logger.debug("Could not parse speaker events", exc_info=True)
    return sorted(events, key=lambda e: e[0])


def build_speaker_name_map(
    segments,
    events: list[tuple[float, str]],
    min_confidence: float = 0.6,
) -> dict[str, str]:
    """Map generic speaker labels to real names using active-speaker events.

    For each generic label ("Speaker N"), find which captured name was
    active during most of that label's segments. A label is mapped only
    when one name dominates (>= ``min_confidence`` of its segment time) so
    a noisy event stream can't mislabel a speaker.

    Returns a {generic_label: real_name} map (only confident mappings).
    """
    if not events or not segments:
        return {}

    def name_at(t: float) -> Optional[str]:
        # The active speaker is the last event at or before time t.
        chosen = None
        for ev_t, name in events:
            if ev_t <= t + 0.5:
                chosen = name
            else:
                break
        return chosen

    # Per generic label, accumulate weighted votes by segment duration.
    votes: dict[str, dict[str, float]] = {}
    totals: dict[str, float] = {}
    for seg in segments:
        label = getattr(seg, "speaker", None)
        if not label or not _is_generic_label(label):
            continue
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        mid = (start + end) / 2
        dur = max(end - start, 0.1)
        name = name_at(mid)
        totals[label] = totals.get(label, 0.0) + dur
        if name:
            votes.setdefault(label, {})[name] = (
                votes.get(label, {}).get(name, 0.0) + dur
            )

    mapping: dict[str, str] = {}
    for label, name_votes in votes.items():
        best_name, best_dur = max(name_votes.items(), key=lambda kv: kv[1])
        if totals.get(label, 0.0) > 0 and best_dur / totals[label] >= min_confidence:
            mapping[label] = best_name
    return mapping


def _is_generic_label(label: str) -> bool:
    """True for unresolved labels like 'Speaker 1' (not already a real name)."""
    low = label.lower().strip()
    return low.startswith("speaker ") or low in ("speaker", "unknown")
