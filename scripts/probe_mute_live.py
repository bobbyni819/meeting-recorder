"""Live mute-detection probe.

Reads the Zoom/Teams mute-button state via UIAutomation once a second and
prints it, so you can toggle mute in a real call and watch the detector
follow. This is the SAME detector the recorder uses to gate the mic track,
so if it tracks your toggles here, the recording will follow it too.

Run during an active Teams/Zoom call:
    python scripts/probe_mute_live.py
Ctrl+C to stop (auto-stops after ~3 minutes).
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

try:
    import psutil
except ImportError:
    print("psutil not installed."); sys.exit(2)

from meeting_recorder.audio.uia_mute_detector import detect_mute_state


def meeting_pids() -> set[int]:
    """Teams/Zoom PIDs plus their child processes (the call window may be a child)."""
    pids: set[int] = set()
    for p in psutil.process_iter(["pid", "name"]):
        name = (p.info["name"] or "").lower()
        if "teams" in name or "zoom" in name:
            pids.add(p.info["pid"])
            try:
                for c in psutil.Process(p.info["pid"]).children(recursive=True):
                    pids.add(c.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return pids


def label(state) -> str:
    if state is True:
        return "MUTED      (mic would NOT be recorded)"
    if state is False:
        return "UNMUTED    (mic WOULD be recorded)"
    return "can't read (call not started, or toolbar hidden)"


def main() -> int:
    pids = meeting_pids()
    if not pids:
        print("No Teams/Zoom process found. Start your call first.")
        return 1
    print(f"Watching {len(pids)} Teams/Zoom PIDs. Toggle your mute and watch below.")
    print("(Ctrl+C to stop; auto-stops after 3 minutes)\n")

    last = "__init__"
    last_beat = 0.0
    deadline = time.monotonic() + 50
    try:
        while time.monotonic() < deadline:
            pids = meeting_pids()  # refresh in case the call window spawned
            t0 = time.monotonic()
            state = detect_mute_state(pids)
            ms = (time.monotonic() - t0) * 1000
            now = datetime.now().strftime("%H:%M:%S")
            cur = label(state)
            if cur != last:
                print(f"[{now}] -> {cur}   ({ms:.0f} ms read)")
                last = cur
            elif time.monotonic() - last_beat > 10:
                print(f"[{now}]    still: {cur.split('(')[0].strip()}")
                last_beat = time.monotonic()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
