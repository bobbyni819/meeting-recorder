"""Deep diagnostic: dump what Zoom/Teams expose via UIAutomation.

Walks the full accessibility tree of every Teams/Zoom top-level window
(generous time budget, no early bail) and reports any control whose name
looks mute/mic/camera/speaker related — with its control type and depth.
This reveals whether the mute button is reachable at all, under what name,
and how deep, so the live detector's depth/timeout/patterns can be tuned.

Run during an ACTIVE Teams/Zoom call:
    python scripts/probe_teams_tree.py
"""

from __future__ import annotations

import sys
import time

try:
    import psutil
    import uiautomation as auto
except ImportError as e:
    print(f"Missing dependency: {e}"); sys.exit(2)

KEYWORDS = ("mute", "unmute", "mic", "microphone", "camera", "speaking",
            "audio", "leave", "hang up", "raise")


def meeting_pids() -> set[int]:
    pids: set[int] = set()
    names: dict[int, str] = {}
    for p in psutil.process_iter(["pid", "name"]):
        nm = (p.info["name"] or "").lower()
        if "teams" in nm or "zoom" in nm:
            pids.add(p.info["pid"]); names[p.info["pid"]] = nm
            try:
                for c in psutil.Process(p.info["pid"]).children(recursive=True):
                    pids.add(c.pid); names[c.pid] = (c.name() or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return pids, names


def main() -> int:
    pids, names = meeting_pids()
    if not pids:
        print("No Teams/Zoom process found. Start the call first."); return 1
    print(f"{len(pids)} candidate PIDs. Enumerating top-level windows...\n")
    auto.SetGlobalSearchTimeout(2.0)
    root = auto.GetRootControl()

    matches = []
    windows_owned = 0
    for win in root.GetChildren():
        try:
            pid = win.ProcessId
            if pid not in pids or not win.Name:
                continue
        except Exception:
            continue
        windows_owned += 1
        proc = names.get(pid, "?")
        print(f"WINDOW: '{win.Name}'  [{win.ControlTypeName}, {win.ClassName}, "
              f"PID {pid} {proc}]")

        # Deep walk with a generous per-window budget.
        deadline = time.monotonic() + 4.0
        controls_seen = [0]

        def walk(ctrl, depth):
            if depth > 30 or time.monotonic() > deadline:
                return
            try:
                children = ctrl.GetChildren()
            except Exception:
                return
            for c in children:
                controls_seen[0] += 1
                try:
                    nm = c.Name or ""
                    ct = c.ControlTypeName
                    aid = c.AutomationId
                except Exception:
                    nm, ct, aid = "", "", ""
                low = nm.lower()
                if any(k in low for k in KEYWORDS):
                    toggle = ""
                    try:
                        tp = c.GetTogglePattern()
                        if tp:
                            toggle = f" ToggleState={tp.ToggleState}"
                    except Exception:
                        pass
                    matches.append(
                        f"    d{depth} [{ct}] name='{nm[:80]}' id='{aid[:30]}'{toggle}"
                    )
                walk(c, depth + 1)

        walk(win, 0)
        print(f"    (scanned {controls_seen[0]} controls, "
              f"{'budget hit' if time.monotonic() > deadline else 'complete'})")

    print(f"\nTop-level Teams/Zoom windows: {windows_owned}")
    print(f"Mute/mic/camera-related controls found: {len(matches)}")
    for m in matches[:60]:
        print(m)
    if not matches:
        print("  (NONE — controls not exposed via standard UIA, or call not active)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
