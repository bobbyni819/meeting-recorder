# Design: Record Anything Mode + Teams Reliability + Bug Sweep

**Date:** 2026-03-05
**Status:** Approved

---

## Overview

Three priorities, executed in order:

1. **Record Anything Mode** — Allow recording any window, not just Zoom/Teams/Webex
2. **Teams Reliability** — Silence health checks, volume pre-checks, auto-stop for desktop audio
3. **Bug Sweep** — Fix race conditions, thread safety, and silent failure modes

---

## Priority 1: Record Anything Mode

### Problem

The app requires Zoom, Teams, or Webex to be running before recording can start.
`start_recording()` calls `find_primary_meeting_process()` and aborts if it returns None.
The window picker exists but only works mid-recording.

### Solution

When no meeting app is detected, fall through to a window picker instead of aborting.
Add a "Record Window..." tray menu item that always opens the picker directly.

### Detailed Design

#### 1.1 Modified `start_recording()` flow (app.py)

```
Ctrl+Shift+R or tray "Start"
  → find_primary_meeting_process()
  → if found: start as today (auto-detect, CaptureManager with PID)
  → if not found:
      → open window picker dialog (blocking, on main thread)
      → user selects window → get (hwnd, pid, title)
      → create CaptureManager with pid, app_key="manual", hwnd override
      → start recording
```

#### 1.2 New tray menu item

Add "Record Window..." to the system tray right-click menu. This always opens the
window picker regardless of whether a meeting app is running. Useful for recording
Discord calls, browser tabs, presentations, etc.

#### 1.3 Audio strategy for arbitrary windows

- Start with ProcTap per-process capture on the selected window's PID
- Run silence detection: monitor app audio RMS in the first 3 seconds
- If silence detected (RMS below noise floor), auto-switch to desktop audio
- Dashboard shows audio mode indicator ("App Audio" / "Desktop Audio")
- User can still manually toggle via dashboard button

This reuses the same proven path as the Teams auto-switch.

#### 1.4 New app_key: "manual"

| Behavior | manual | zoom | teams | webex |
|----------|--------|------|-------|-------|
| Mute sync | disabled | Alt+A | Ctrl+Shift+M | Ctrl+M |
| Process monitor | active (watches PID) | active | skip in desktop mode | active |
| Outlook calendar | skipped | active | active | active |
| Audio mode | ProcTap → auto desktop fallback | ProcTap | desktop (auto) | ProcTap |
| Post-processing | full | full | full | full |

#### 1.5 Window picker improvements

- Filter out tiny/system windows (< 200×150 px)
- Show process name next to title: "Discord — discord.exe"
- Sort: meeting apps first, then alphabetical
- Remember last-picked window for quick re-selection

#### 1.6 Files to modify

| File | Changes |
|------|---------|
| `app.py` | `start_recording()` fallback to picker, new tray menu item, `_pick_window_and_record()` |
| `capture_manager.py` | Silence detection auto-switch (3s), accept `app_key="manual"` |
| `window_finder.py` | `list_visible_windows()` adds size filter + process name |
| `dashboard.py` | Window picker shows process names |
| `mute_sync.py` | No-op when `app_key="manual"` |
| `notifications.py` | Update "no meeting" notification text |

---

## Priority 2: Teams Reliability

### Problem

Teams desktop audio path is well-designed in code but may fail silently in practice
due to environmental issues (system volume at 0, wrong window detected, etc.).

### Solution

Add runtime health checks that detect and surface audio problems.

### Detailed Design

#### 2.1 Silence health check (capture_manager.py)

- Background monitor checks app audio RMS every 2 seconds
- If app audio is silent for 10 consecutive seconds after recording starts:
  - Fire `on_health_warning` callback
  - Dashboard shows amber: "⚠ No audio detected — check system volume"
- Resets when audio is detected
- Works for ALL audio modes (ProcTap, desktop, manual)

#### 2.2 System volume pre-check (capture_manager.py)

- At recording start, if using desktop audio mode:
  - Query system master volume via pycaw `AudioUtilities.GetSpeakers()`
  - If master volume is 0 or muted, log warning + show notification
  - Don't block recording (user may unmute during the call)

#### 2.3 Desktop audio mode auto-stop (capture_manager.py)

Currently `_monitor_process()` skips PID monitoring in desktop mode (recording runs forever).

Fix:
- Still monitor the original Teams PID even in desktop audio mode
- When the PID exits, start a 5-second grace period (audio drain)
- After grace period, trigger auto-stop via `_on_stopped` callback
- If user switched windows mid-recording, monitor the new PID instead

#### 2.4 Files to modify

| File | Changes |
|------|---------|
| `capture_manager.py` | Silence health check, volume pre-check, desktop auto-stop |
| `dashboard.py` | Health warning display |
| `app.py` | Wire `on_health_warning` callback |

---

## Priority 3: Bug Sweep

### Critical

#### 3.1 `_live_transcriber` race condition (capture_manager.py)

**Bug:** `_live_transcriber` is read in `_writer_loop()`, written in `start()`, and
nullified in `stop()` without synchronization. TOCTOU bug causes AttributeError.

**Fix:** Add `_transcriber_lock` (threading.Lock). Acquire in `_writer_loop` before
feeding audio, and in `stop()` before nullifying.

#### 3.2 `_capture_manager` nullification race (app.py)

**Bug:** Callbacks like `_on_audio_levels()` read `self._capture_manager` without the
stop lock. Between the `if cm:` check and attribute access, another thread can nullify it.

**Fix:** All callbacks already use `cm = self._capture_manager` local var pattern.
Audit all callbacks and ensure they use this pattern consistently. The local reference
is safe because Python's GIL guarantees atomic reference reads for simple attributes.

### High

#### 3.3 Thread join timeout without cleanup (mic_audio.py, app_audio.py)

**Bug:** `join(timeout=5.0)` doesn't enforce cleanup. Zombie threads can hold audio devices.

**Fix:** After `join(5.0)`, if `thread.is_alive()`, log error with thread name.
Set a `_zombie_threads` list on the CaptureManager so the next recording start
can warn about leaked resources.

#### 3.4 Writer thread silent exceptions (capture_manager.py)

**Bug:** `_writer_loop` catches exceptions and logs them but doesn't notify the user.
Recording appears to succeed but WAV file may be corrupt.

**Fix:** On write failure, set `self._write_error = True` and fire a health warning
callback: "Audio write error — recording may be incomplete."

#### 3.5 Ring buffer overflow logging (ring_buffer.py)

**Bug:** When deque is full, oldest chunks silently drop. No notification.

**Fix:** Add `overflow_count` counter. When a put() drops a chunk, increment counter.
Log a warning every 100 drops: "Ring buffer overflow: N chunks dropped (M seconds of audio)."

### Medium

#### 3.6 Metadata save race (app.py)

**Bug:** metadata.json written from multiple threads (mark processing, final save,
summary callback, Drive upload callback) without synchronization.

**Fix:** Add `_metadata_lock` in app.py. Acquire before every `metadata.save()` call.

#### 3.7 Window PID resolution failure (capture_manager.py)

**Bug:** `switch_screen_window()` silently returns if `get_hwnd_pid()` returns None.

**Fix:** Log warning and fire health callback: "Window no longer available."

#### 3.8 WAV header validation (app.py post-processing)

**Bug:** If WAV file is corrupt (aborted write), transcription fails with unclear error.

**Fix:** Before running transcription, validate WAV file: check header, verify
non-zero duration. If corrupt, skip transcription and warn user.

### Files to modify

| File | Changes |
|------|---------|
| `capture_manager.py` | _transcriber_lock, write error flag, health warnings |
| `app.py` | _metadata_lock, WAV validation, consistent cm local var pattern |
| `ring_buffer.py` | overflow_count, periodic warning log |
| `mic_audio.py` | Thread cleanup logging |
| `app_audio.py` | Thread cleanup logging |

---

## What stays the same

- Post-processing pipeline (transcription, diarization, summary, Drive upload)
- Config file format (no new config keys)
- Hotkeys (Ctrl+Shift+R, Ctrl+Shift+D, Ctrl+Shift+U)
- Screen capture engine (PrintWindow + mss fallback)
- Mic capture path (PyAudioWPatch + Silero VAD)
- Resampling pipeline

---

## Testing Strategy

- Unit tests for silence detection, volume check, window picker filtering
- Unit tests for ring buffer overflow counter
- Integration test: manual recording start with mocked window picker
- Thread safety tests for _live_transcriber lock, _metadata_lock
- Existing test suite must continue to pass (568 tests)
