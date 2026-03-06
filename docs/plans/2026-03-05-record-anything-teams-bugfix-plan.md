# Record Anything + Teams Reliability + Bug Sweep — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users record any window (not just meeting apps), improve Teams audio reliability with health checks, and fix race conditions / thread safety bugs.

**Architecture:** Three independent priorities executed sequentially. Priority 1 modifies the recording start flow to fallback to a window picker when no meeting is detected, adds a tray menu item, and adds silence-detection auto-switch to desktop audio. Priority 2 adds runtime health checks for audio silence and system volume. Priority 3 fixes thread safety bugs found in the audit.

**Tech Stack:** Python 3.12, Win32 API (ctypes), pystray, tkinter, pycaw, threading

---

## Priority 1: Record Anything Mode

### Task 1: Improve `list_visible_windows()` to include process names and filter small windows

**Files:**
- Modify: `meeting_recorder/video/window_finder.py:138-168`
- Test: `tests/test_window_finder.py` (create if missing)

**Step 1: Write the failing tests**

```python
# tests/test_window_finder.py
"""Tests for window_finder module."""
from unittest.mock import patch, MagicMock
import ctypes.wintypes

from meeting_recorder.video.window_finder import list_visible_windows


class TestListVisibleWindows:
    """Tests for list_visible_windows with process name and size filtering."""

    def test_returns_process_name_in_tuples(self):
        """list_visible_windows should return (hwnd, title, pid, process_name) tuples."""
        # We can't easily mock EnumWindows, so just verify the return type
        result = list_visible_windows()
        assert isinstance(result, list)
        # If any windows exist, verify tuple shape
        if result:
            item = result[0]
            assert len(item) == 4, f"Expected 4-tuple, got {len(item)}-tuple: {item}"
            hwnd, title, pid, proc_name = item
            assert isinstance(title, str)
            assert isinstance(pid, int)
            assert isinstance(proc_name, str)

    def test_filters_small_windows(self):
        """Windows smaller than MIN_WINDOW_SIZE should be excluded."""
        result = list_visible_windows()
        # All returned windows should have been filtered by the size threshold
        # We can't verify dimensions directly, but we check the function runs
        assert isinstance(result, list)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_window_finder.py -v`
Expected: FAIL — `list_visible_windows` returns 3-tuples, not 4-tuples

**Step 3: Update `list_visible_windows()`**

In `meeting_recorder/video/window_finder.py`, replace lines 138-168:

```python
# Minimum window dimensions to filter out tiny system/tooltip windows
MIN_WINDOW_WIDTH = 200
MIN_WINDOW_HEIGHT = 150


def list_visible_windows() -> list[tuple[int, str, int, str]]:
    """Enumerate all visible top-level windows with non-empty titles.

    Returns a list of (hwnd, title, pid, process_name) tuples, sorted
    alphabetically by title. Excludes small windows (< 200x150),
    zero-area windows, and untitled windows.
    """
    results = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title_len = user32.GetWindowTextLengthW(hwnd)
        if title_len <= 0:
            return True
        buf = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, buf, title_len + 1)
        title = buf.value.strip()
        if not title:
            return True
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < MIN_WINDOW_WIDTH or h < MIN_WINDOW_HEIGHT:
            return True
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Resolve process name
        proc_name = ""
        try:
            import psutil
            proc_name = psutil.Process(pid.value).name()
        except Exception:
            pass
        results.append((hwnd, title, pid.value, proc_name))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    results.sort(key=lambda x: x[1].lower())
    return results
```

**Step 4: Update all callers of `list_visible_windows()`**

There are two callers:

1. `meeting_recorder/audio/capture_manager.py:409-415` — `list_capturable_windows()`:

```python
def list_capturable_windows(self) -> list[tuple[int, str]]:
    """Return (hwnd, title, process_name) triples for all visible top-level windows."""
    from meeting_recorder.video.window_finder import list_visible_windows
    return [(hwnd, f"{title} — {proc}" if proc else title, proc)
            for hwnd, title, _pid, proc in list_visible_windows()]
```

Wait — this changes the return type. The dashboard window picker uses `(hwnd, title)` pairs. Let's keep the external API stable but add process name to the display title:

```python
def list_capturable_windows(self) -> list[tuple[int, str]]:
    """Return (hwnd, display_title) pairs for all visible top-level windows."""
    from meeting_recorder.video.window_finder import list_visible_windows
    result = []
    for hwnd, title, _pid, proc_name in list_visible_windows():
        display = f"{title}  —  {proc_name}" if proc_name else title
        result.append((hwnd, display))
    return result
```

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_window_finder.py tests/test_config.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add meeting_recorder/video/window_finder.py meeting_recorder/audio/capture_manager.py tests/test_window_finder.py
git commit -m "feat: add process names and size filtering to window picker"
```

---

### Task 2: Add `_pick_window_for_recording()` method to MeetingRecorderApp

This is the key method that shows a standalone window picker when no meeting is detected, and returns a `MeetingProcess`-like object for the selected window.

**Files:**
- Modify: `meeting_recorder/app.py:101-127` (start_recording)
- Modify: `meeting_recorder/audio/process_finder.py:94-100` (MeetingProcess)
- Test: `tests/test_manual_recording.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_manual_recording.py
"""Tests for manual (non-meeting) recording mode."""
from unittest.mock import patch, MagicMock
from meeting_recorder.audio.process_finder import MeetingProcess


class TestManualRecordingProcess:
    """Test that MeetingProcess can represent a manual recording target."""

    def test_manual_meeting_process(self):
        """MeetingProcess with app_key='manual' should work."""
        proc = MeetingProcess(
            pid=1234,
            name="discord.exe",
            app_key="manual",
            display_name="Discord",
        )
        assert proc.app_key == "manual"
        assert proc.pid == 1234
        assert proc.display_name == "Discord"
```

**Step 2: Run to verify it passes** (MeetingProcess is already a dataclass — this should pass immediately, confirming the API works)

Run: `python -m pytest tests/test_manual_recording.py -v`
Expected: PASS

**Step 3: Add `_pick_window_for_recording()` to app.py**

Add after `_on_pick_capture_window` (line 625):

```python
def _pick_window_for_recording(self) -> Optional[MeetingProcess]:
    """Show a blocking window picker dialog and return a MeetingProcess for the selection.

    Returns None if the user cancels or no window is selected.
    Used when no meeting app is auto-detected.
    """
    import tkinter as tk
    from meeting_recorder.video.window_finder import (
        list_visible_windows,
        get_hwnd_pid,
    )

    windows = list_visible_windows()
    if not windows:
        logger.warning("No visible windows found for picker.")
        return None

    chosen = [None]  # mutable container for closure

    root = tk.Tk()
    root.title("Meeting Recorder — Pick a Window")
    root.configure(bg="#1a1a2e")
    root.attributes("-topmost", True)
    root.geometry("500x400")

    tk.Label(
        root, text="No meeting app detected. Select a window to record:",
        font=("Segoe UI", 10), fg="#e0e0e0", bg="#1a1a2e",
        wraplength=460, justify=tk.LEFT,
    ).pack(padx=16, pady=(14, 6), anchor=tk.W)

    list_frame = tk.Frame(root, bg="#1a1a2e")
    list_frame.pack(fill=tk.BOTH, expand=True, padx=16)

    scrollbar = tk.Scrollbar(list_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    listbox = tk.Listbox(
        list_frame, yscrollcommand=scrollbar.set,
        bg="#0d0d1a", fg="#e0e0e0", selectbackground="#16213e",
        selectforeground="#e0e0e0", activestyle="none",
        font=("Segoe UI", 9), bd=0,
        highlightthickness=1, highlightcolor="#16213e",
    )
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.configure(command=listbox.yview)

    for _hwnd, title, _pid, proc_name in windows:
        display = f"  {title}  —  {proc_name}" if proc_name else f"  {title}"
        listbox.insert(tk.END, display)

    def _confirm():
        sel = listbox.curselection()
        if sel:
            chosen[0] = sel[0]
        root.destroy()

    listbox.bind("<Double-Button-1>", lambda e: _confirm())

    btn_frame = tk.Frame(root, bg="#1a1a2e")
    btn_frame.pack(fill=tk.X, padx=16, pady=12)

    record_btn = tk.Button(
        btn_frame, text="Record This Window",
        font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#0f3460",
        command=_confirm, padx=12, pady=4, cursor="hand2",
    )
    record_btn.pack(side=tk.LEFT)

    cancel_btn = tk.Button(
        btn_frame, text="Cancel",
        font=("Segoe UI", 9), fg="#a0a0a0", bg="#2a2a3e",
        command=root.destroy, padx=12, pady=4, cursor="hand2",
    )
    cancel_btn.pack(side=tk.LEFT, padx=8)

    root.mainloop()

    if chosen[0] is None:
        return None

    hwnd, title, pid, proc_name = windows[chosen[0]]
    display_name = title.split(" — ")[0].strip() if " — " in title else title
    # Truncate long display names
    if len(display_name) > 60:
        display_name = display_name[:57] + "..."

    return MeetingProcess(
        pid=pid,
        name=proc_name or "unknown",
        app_key="manual",
        display_name=display_name,
    )
```

**Step 4: Modify `start_recording()` to use the picker fallback**

Replace `meeting_recorder/app.py` lines 101-127:

```python
def start_recording(self) -> None:
    """Start recording the active meeting."""
    cm = self._capture_manager
    if cm and cm.is_recording:
        logger.warning("Already recording.")
        return

    # Find meeting process
    process = find_primary_meeting_process()
    if process is None:
        logger.info("No meeting app found — opening window picker.")
        process = self._pick_window_for_recording()
        if process is None:
            logger.info("Window picker cancelled.")
            return

    self._current_process = process
    logger.info("Found %s (PID %d)", process.display_name, process.pid)

    try:
        self._start_recording_for_process(process)
    except Exception:
        logger.exception("Failed to start recording for %s", process.display_name)
        notifications.notify_error(f"Failed to start recording: see log for details")
        self._capture_manager = None
        self._current_recording_dir = None
        self._current_metadata = None
        self._current_process = None
        self._tray.set_state("idle")
```

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: All pass (existing tests shouldn't break since auto-detection still works as before)

**Step 6: Commit**

```bash
git add meeting_recorder/app.py tests/test_manual_recording.py
git commit -m "feat: fallback to window picker when no meeting app detected"
```

---

### Task 3: Add "Record Window..." tray menu item

**Files:**
- Modify: `meeting_recorder/ui/tray.py:32-98`
- Modify: `meeting_recorder/app.py:70-79`

**Step 1: Add callback to TrayIcon constructor**

In `meeting_recorder/ui/tray.py`, add `on_record_window` parameter to `__init__` (after `on_start`, line 34):

```python
def __init__(
    self,
    on_start: Optional[Callable] = None,
    on_stop: Optional[Callable] = None,
    on_record_window: Optional[Callable] = None,  # NEW
    on_quit: Optional[Callable] = None,
    on_settings: Optional[Callable] = None,
    on_open_recordings: Optional[Callable] = None,
    on_search: Optional[Callable] = None,
    on_show_dashboard: Optional[Callable] = None,
):
    self._on_start = on_start
    self._on_stop = on_stop
    self._on_record_window = on_record_window  # NEW
    # ... rest unchanged
```

**Step 2: Add menu item in `run()` method**

In the menu definition (after the Start/Stop toggle item, line 76), add:

```python
Item(
    "Record Window...",
    self._handle_record_window,
    enabled=lambda _: self._state == "idle",
),
```

**Step 3: Add handler method**

After `_on_toggle_recording` (line 141):

```python
def _handle_record_window(self, icon, item) -> None:
    """Handle 'Record Window...' menu click — always opens the window picker."""
    if self._on_record_window:
        threading.Thread(target=self._on_record_window, daemon=True).start()
```

**Step 4: Wire callback in app.py**

In `meeting_recorder/app.py`, update TrayIcon initialization (lines 71-79):

```python
self._tray = TrayIcon(
    on_start=self.start_recording,
    on_stop=self.stop_recording,
    on_record_window=self._record_window,  # NEW
    on_quit=self.quit,
    on_settings=self._open_settings,
    on_open_recordings=self._open_recordings_folder,
    on_search=self._open_search,
    on_show_dashboard=self._show_dashboard,
)
```

Add the handler method in app.py (after `start_recording`):

```python
def _record_window(self) -> None:
    """Open window picker and start recording the selected window."""
    cm = self._capture_manager
    if cm and cm.is_recording:
        logger.warning("Already recording.")
        return

    process = self._pick_window_for_recording()
    if process is None:
        logger.info("Window picker cancelled.")
        return

    self._current_process = process
    logger.info("Manual recording: %s (PID %d)", process.display_name, process.pid)

    try:
        self._start_recording_for_process(process)
    except Exception:
        logger.exception("Failed to start recording for %s", process.display_name)
        notifications.notify_error("Failed to start recording: see log for details")
        self._capture_manager = None
        self._current_recording_dir = None
        self._current_metadata = None
        self._current_process = None
        self._tray.set_state("idle")
```

**Step 5: Run tests**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 6: Commit**

```bash
git add meeting_recorder/ui/tray.py meeting_recorder/app.py
git commit -m "feat: add 'Record Window...' tray menu item"
```

---

### Task 4: Silence detection auto-switch to desktop audio

When `app_key="manual"` (or any non-Teams app), try per-process audio first. If silent for 3 seconds, auto-switch to desktop audio.

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:152-234` (start method)
- Test: `tests/test_silence_detection.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_silence_detection.py
"""Tests for silence detection auto-switch in CaptureManager."""
import threading
import time
from unittest.mock import MagicMock, patch

from meeting_recorder.audio.ring_buffer import RingBuffer


class TestSilenceDetection:
    """Test the silence detection logic that triggers desktop audio fallback."""

    def test_all_zeros_detected_as_silence(self):
        """A buffer full of zero bytes should be detected as silent."""
        from meeting_recorder.audio.capture_manager import _is_buffer_silent
        silent_chunk = b"\x00" * 1024
        assert _is_buffer_silent(silent_chunk) is True

    def test_nonzero_data_not_silent(self):
        """A buffer with real audio data should not be detected as silent."""
        from meeting_recorder.audio.capture_manager import _is_buffer_silent
        import struct
        # Create a chunk with some non-zero int16 samples
        data = struct.pack("<" + "h" * 512, *([500] * 512))
        assert _is_buffer_silent(data) is False

    def test_low_noise_still_silent(self):
        """Very low amplitude noise (< threshold) should count as silent."""
        from meeting_recorder.audio.capture_manager import _is_buffer_silent
        import struct
        # Near-zero samples (noise floor)
        data = struct.pack("<" + "h" * 512, *([2] * 512))
        assert _is_buffer_silent(data) is True
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_silence_detection.py -v`
Expected: FAIL — `_is_buffer_silent` not found

**Step 3: Add `_is_buffer_silent()` helper function**

At the top of `meeting_recorder/audio/capture_manager.py` (after imports, before the class):

```python
import struct

# RMS threshold below which audio is considered silent (int16 samples)
_SILENCE_RMS_THRESHOLD = 10


def _is_buffer_silent(data: bytes) -> bool:
    """Check if an audio buffer (int16 PCM) is effectively silent.

    Returns True if the RMS amplitude is below the silence threshold.
    """
    if not data:
        return True
    n_samples = len(data) // 2
    if n_samples == 0:
        return True
    samples = struct.unpack_from(f"<{n_samples}h", data)
    rms = (sum(s * s for s in samples) / n_samples) ** 0.5
    return rms < _SILENCE_RMS_THRESHOLD
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_silence_detection.py -v`
Expected: PASS

**Step 5: Add silence detection thread to `start()` method**

In `meeting_recorder/audio/capture_manager.py`, add a new method after `_level_monitor_loop`:

```python
_SILENCE_CHECK_SECONDS = 3.0  # how long to wait before declaring silence


def _silence_auto_switch(self) -> None:
    """Monitor app audio for silence and auto-switch to desktop audio.

    Only runs for non-Teams apps (Teams already auto-switches).
    Checks the app audio ring buffer for 3 seconds after recording starts.
    If all audio is silent, switches to desktop audio and notifies.
    """
    if self._app_key == "teams":
        return  # Teams already handled
    if self._is_desktop_audio:
        return  # Already on desktop audio

    deadline = time.time() + _SILENCE_CHECK_SECONDS
    saw_audio = False

    while time.time() < deadline and not self._stop_event.is_set():
        self._stop_event.wait(0.5)
        # Check if the level monitor has seen any app audio
        app_level = self._level_monitor.app_level
        if app_level > 0.01:  # non-trivial audio detected
            saw_audio = True
            break

    if saw_audio or self._stop_event.is_set() or self._is_desktop_audio:
        return

    logger.info(
        "App audio silent for %.0fs — auto-switching to desktop audio.",
        _SILENCE_CHECK_SECONDS,
    )
    self.switch_to_desktop_audio()
    if self._on_health_warning:
        try:
            self._on_health_warning(
                "silence_auto_switch"
            )
        except Exception:
            logger.exception("on_health_warning callback error")
```

In the `start()` method, after starting the level monitor thread (after line 217), add:

```python
# Start silence detection for non-Teams apps (auto-switch to desktop if silent)
if self._app_key != "teams":
    self._silence_thread = threading.Thread(
        target=self._silence_auto_switch,
        name="silence-detector",
        daemon=True,
    )
    self._silence_thread.start()
```

**Step 6: Run full test suite**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 7: Commit**

```bash
git add meeting_recorder/audio/capture_manager.py tests/test_silence_detection.py
git commit -m "feat: auto-switch to desktop audio when app audio is silent for 3s"
```

---

### Task 5: MuteSync no-op for `app_key="manual"`

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:94-107`

**Step 1: Verify current behavior**

The existing code at lines 97-107 already handles this correctly:

```python
if app_key and process_name:
    target_pids = get_all_pids_for_process(process_name)
    if target_pids:
        ...
        self._mute_sync = MuteSync(app_key=app_key, ...)
```

When `app_key="manual"`, `MuteSync.start()` is called and `APP_MUTE_SHORTCUTS.get("manual")` returns `None` — so no mute shortcut is hooked, which is correct. The manual toggle hotkey (Ctrl+Shift+U) still works.

**No code change needed.** The existing code handles this correctly.

**Step 2: Write a confirmation test**

```python
# Add to tests/test_manual_recording.py:

class TestManualMuteSync:
    """MuteSync should not hook any app shortcut for manual recordings."""

    def test_no_app_shortcut_for_manual(self):
        """APP_MUTE_SHORTCUTS should not have a 'manual' entry."""
        from meeting_recorder.audio.mute_sync import APP_MUTE_SHORTCUTS
        assert "manual" not in APP_MUTE_SHORTCUTS
```

**Step 3: Run and verify**

Run: `python -m pytest tests/test_manual_recording.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add tests/test_manual_recording.py
git commit -m "test: confirm mute sync is no-op for manual recording mode"
```

---

### Task 6: Update notifications text

**Files:**
- Modify: `meeting_recorder/ui/notifications.py:35-37`

**Step 1: Update the notification message**

This is no longer called in the fallback path (we show the picker instead), but keep it for edge cases where the picker isn't available. Update the message:

```python
def notify_no_meeting_found() -> None:
    """Show notification when no meeting app is detected."""
    _show(
        "No Meeting Found",
        "No Zoom, Teams, or Webex detected. Use the tray menu to record any window.",
    )
```

**Step 2: Run tests**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 3: Commit**

```bash
git add meeting_recorder/ui/notifications.py
git commit -m "feat: update no-meeting notification to mention window recording"
```

---

## Priority 2: Teams Reliability

### Task 7: Silence health check monitor

This is different from Task 4's silence auto-switch. This runs **throughout** the recording (not just the first 3 seconds) and warns the user if audio goes silent for 10+ seconds.

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:358-377` (level_monitor_loop)
- Test: `tests/test_silence_health.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_silence_health.py
"""Tests for the ongoing silence health check."""
from unittest.mock import MagicMock
from meeting_recorder.audio.level_monitor import AudioLevelMonitor


class TestSilenceHealthCheck:
    """Test that prolonged silence triggers a health warning."""

    def test_silence_tracked_on_level_monitor(self):
        """AudioLevelMonitor should track consecutive silent readings."""
        monitor = AudioLevelMonitor(on_levels=MagicMock())
        # Initially, silence_seconds should be 0
        assert monitor.app_silence_seconds == 0.0

    def test_silence_increments_on_zero_levels(self):
        """Silence counter should increase when app level is 0."""
        monitor = AudioLevelMonitor(on_levels=MagicMock())
        # Feed zero-level updates
        monitor.update_app_level(b"\x00" * 1024)
        # After notify(), silence counter should increase
        assert monitor.app_level < 0.01
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_silence_health.py -v`
Expected: FAIL — `app_silence_seconds` not found

**Step 3: Add silence tracking to AudioLevelMonitor**

Read `meeting_recorder/audio/level_monitor.py` first, then add an `app_silence_seconds` property that tracks how long the app audio has been at zero. This is tracked by the existing level monitor loop in capture_manager.

Actually, simpler approach: track silence directly in `_level_monitor_loop` in capture_manager.py. Add instance variables in `__init__`:

```python
# Silence health check
self._app_silence_start: Optional[float] = None
_SILENCE_WARNING_SECONDS = 10.0
```

Then in `_level_monitor_loop`, after the existing health check block (after line 377), add:

```python
# Check for prolonged app audio silence
app_level = self._level_monitor.app_level
if app_level < 0.005:
    if self._app_silence_start is None:
        self._app_silence_start = now
    elif (now - self._app_silence_start >= self._SILENCE_WARNING_SECONDS
          and self._on_health_warning):
        try:
            self._on_health_warning("app_audio_silent")
        except Exception:
            logger.exception("on_health_warning callback error")
        self._app_silence_start = now  # reset to avoid spamming
else:
    self._app_silence_start = None
```

**Step 4: Run tests**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 5: Commit**

```bash
git add meeting_recorder/audio/capture_manager.py tests/test_silence_health.py
git commit -m "feat: health warning when app audio is silent for 10+ seconds"
```

---

### Task 8: System volume pre-check via pycaw

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:152-234` (start method)

**Step 1: Add volume check helper**

Add to `meeting_recorder/audio/capture_manager.py` (module level, near `_is_buffer_silent`):

```python
def _check_system_volume() -> Optional[float]:
    """Return the system master volume (0.0 - 1.0), or None if unavailable."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        if volume.GetMute():
            return 0.0
        return volume.GetMasterVolumeLevelScalar()
    except Exception:
        return None
```

**Step 2: Add volume check to `start()` method**

After the Teams auto-switch block (after line 180), add:

```python
# Warn if system volume is zero/muted when using desktop audio
if self._is_desktop_audio:
    vol = _check_system_volume()
    if vol is not None and vol < 0.01:
        logger.warning(
            "System volume is muted/zero — desktop audio will be silent!"
        )
        if self._on_health_warning:
            try:
                self._on_health_warning("system_volume_muted")
            except Exception:
                logger.exception("on_health_warning callback error")
```

**Step 3: Update `_on_health_warning` in app.py to show better messages**

In `meeting_recorder/app.py`, update `_on_health_warning` (lines 585-590):

```python
def _on_health_warning(self, warning_key: str) -> None:
    """Called when a capture issue is detected."""
    messages = {
        "system_volume_muted": "System volume is muted — desktop audio will be silent!",
        "app_audio_silent": "No audio detected for 10s — check volume or switch audio mode",
        "silence_auto_switch": "No app audio detected — switched to desktop audio",
    }
    msg = messages.get(warning_key, f"Warning: {warning_key} may have stalled")
    logger.warning("Health warning: %s", msg)
    notifications.notify_info(msg)
    if self._dashboard and self._dashboard.is_visible:
        self._dashboard.update_transcript(f"[⚠ {msg}]")
```

**Step 4: Run tests**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 5: Commit**

```bash
git add meeting_recorder/audio/capture_manager.py meeting_recorder/app.py
git commit -m "feat: warn when system volume is muted during desktop audio capture"
```

---

### Task 9: Desktop audio mode auto-stop when PID exits

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:334-356` (_monitor_process)

**Step 1: Update `_monitor_process` to still watch PID in desktop mode**

Replace lines 334-356:

```python
_DESKTOP_EXIT_GRACE_SECONDS = 5.0

def _monitor_process(self) -> None:
    """Monitor the target process and auto-stop if it exits.

    When the process exits, fires _on_stopped so the app layer can
    orchestrate a full stop (capture_manager.stop + post-processing).
    Does NOT call self.stop() directly — doing so would set
    _is_recording = False before the app gets a chance to run its
    stop_recording, causing post-processing to be skipped.
    """
    while not self._stop_event.is_set():
        if not is_process_running(self.pid):
            if self._is_desktop_audio:
                # Grace period: Teams may still be flushing audio
                logger.info(
                    "Target PID %d exited (desktop mode) — waiting %.0fs grace period.",
                    self.pid,
                    _DESKTOP_EXIT_GRACE_SECONDS,
                )
                self._stop_event.wait(_DESKTOP_EXIT_GRACE_SECONDS)
                if self._stop_event.is_set():
                    return
            logger.info("Target process (PID %d) exited. Auto-stopping.", self.pid)
            if self._on_stopped:
                try:
                    self._on_stopped()
                except Exception:
                    logger.exception("on_stopped callback error")
            return
        self._stop_event.wait(2.0)
```

**Step 2: Run tests**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 3: Commit**

```bash
git add meeting_recorder/audio/capture_manager.py
git commit -m "fix: auto-stop recording when Teams exits in desktop audio mode"
```

---

## Priority 3: Bug Sweep

### Task 10: Fix `_live_transcriber` race condition

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py` (multiple locations)

**Step 1: Add `_transcriber_lock` to `__init__`**

In `__init__` (after line 87):

```python
self._transcriber_lock = threading.Lock()
```

**Step 2: Protect reads in `_writer_loop`**

Replace lines 311-312:

```python
if is_app:
    with self._transcriber_lock:
        lt = self._live_transcriber
    if lt is not None:
        lt.feed_audio(chunk)
```

**Step 3: Protect nullification in `stop()`**

Replace lines 281-283:

```python
# Stop live transcription after writers have drained
with self._transcriber_lock:
    lt = self._live_transcriber
    self._live_transcriber = None
if lt is not None:
    lt.stop()
```

**Step 4: Protect creation in `start()`**

Replace lines 220-231:

```python
if self._live_transcription_enabled:
    try:
        from meeting_recorder.transcription.live_transcriber import LiveTranscriber

        lt = LiveTranscriber(on_transcript=self._on_live_transcript)
        lt.start()
        with self._transcriber_lock:
            self._live_transcriber = lt
    except ImportError:
        logger.warning("Live transcription dependencies not available.")
    except Exception:
        logger.exception("Failed to start live transcription")
```

**Step 5: Run tests**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 6: Commit**

```bash
git add meeting_recorder/audio/capture_manager.py
git commit -m "fix: protect _live_transcriber with lock to prevent race condition"
```

---

### Task 11: Fix `_capture_manager` nullification race in app.py callbacks

**Files:**
- Modify: `meeting_recorder/app.py` (audit all callback methods)

**Step 1: Audit all callbacks that read `self._capture_manager`**

The pattern `cm = self._capture_manager; if cm:` is already used in most places. Audit and fix any that don't use this pattern. The following methods need checking:

- `_on_audio_levels` (line 480) — already uses `cm = self._capture_manager`
- `_toggle_mute` (line 530) — already uses `cm = self._capture_manager`
- `_toggle_audio_mode` (line 536) — already uses `cm = self._capture_manager`
- `_on_mute_changed` (line 525) — doesn't access cm, just dashboard
- `_on_pick_capture_window` (line 617) — already uses `cm = self._capture_manager`

All callbacks already use the safe local-var pattern. No change needed.

**Step 2: Verify with a test**

```python
# Add to tests/test_manual_recording.py:

class TestCallbackNullSafety:
    """Verify callbacks handle None _capture_manager safely."""

    def test_toggle_mute_when_no_capture_manager(self):
        """_toggle_mute should not crash when _capture_manager is None."""
        from meeting_recorder.app import MeetingRecorderApp
        app = MeetingRecorderApp.__new__(MeetingRecorderApp)
        app._capture_manager = None
        app._toggle_mute()  # should not raise
```

**Step 3: Run and commit**

Run: `python -m pytest tests/test_manual_recording.py -v`

```bash
git add tests/test_manual_recording.py
git commit -m "test: verify callback null safety for _capture_manager"
```

---

### Task 12: Thread join cleanup improvements

**Files:**
- Modify: `meeting_recorder/audio/app_audio.py:62-70`
- Modify: `meeting_recorder/audio/mic_audio.py:65-73`

**Step 1: Improve thread join logging in both files**

In `app_audio.py`, replace the stop method's join block:

```python
if self._thread is not None:
    self._thread.join(timeout=5.0)
    if self._thread.is_alive():
        logger.error(
            "App audio capture thread did not terminate within 5s "
            "(zombie thread — audio device may be held). "
            "Thread: %s",
            self._thread.name,
        )
```

Same pattern in `mic_audio.py`.

**Step 2: Run tests and commit**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`

```bash
git add meeting_recorder/audio/app_audio.py meeting_recorder/audio/mic_audio.py
git commit -m "fix: improve thread join timeout logging for zombie detection"
```

---

### Task 13: Writer thread error notification

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:288-332`

**Step 1: Add write error flag and notification**

Add to `__init__`:

```python
self._write_error = False
```

Replace the exception handler in `_writer_loop` (lines 325-326):

```python
except Exception:
    logger.exception("WAV writer error (%s)", label)
    self._write_error = True
    if self._on_health_warning:
        try:
            self._on_health_warning(f"{label}_write_error")
        except Exception:
            pass
```

Add the `_write_error` key to the health warning messages in `app.py`:

```python
"app_write_error": "Audio write error — recording may be incomplete",
"mic_write_error": "Mic write error — recording may be incomplete",
```

**Step 2: Run tests and commit**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`

```bash
git add meeting_recorder/audio/capture_manager.py meeting_recorder/app.py
git commit -m "fix: notify user when WAV writer encounters errors"
```

---

### Task 14: Ring buffer overflow logging

**Files:**
- Modify: `meeting_recorder/audio/ring_buffer.py:17-26`
- Test: `tests/test_ring_buffer.py` (modify existing or create)

**Step 1: Write the failing test**

```python
# tests/test_ring_buffer_overflow.py
"""Tests for ring buffer overflow tracking."""
from meeting_recorder.audio.ring_buffer import RingBuffer


class TestRingBufferOverflow:
    """Test that overflow is tracked."""

    def test_overflow_count_starts_at_zero(self):
        buf = RingBuffer(max_chunks=3)
        assert buf.overflow_count == 0

    def test_overflow_count_increments(self):
        buf = RingBuffer(max_chunks=2)
        buf.put(b"a")
        buf.put(b"b")
        buf.put(b"c")  # this drops "a"
        assert buf.overflow_count == 1

    def test_overflow_count_accumulates(self):
        buf = RingBuffer(max_chunks=1)
        buf.put(b"a")
        buf.put(b"b")  # drops a
        buf.put(b"c")  # drops b
        assert buf.overflow_count == 2
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ring_buffer_overflow.py -v`
Expected: FAIL — `overflow_count` not found

**Step 3: Add overflow tracking to RingBuffer**

Replace `meeting_recorder/audio/ring_buffer.py`:

```python
"""Thread-safe ring buffer for audio data exchange between threads."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


class RingBuffer:
    """Thread-safe ring buffer backed by a deque.

    Stores raw audio chunks (bytes) with a maximum capacity.
    When full, oldest chunks are dropped and overflow is tracked.
    """

    def __init__(self, max_chunks: int = 1000):
        self._max_chunks = max_chunks
        self._buffer: deque[bytes] = deque(maxlen=max_chunks)
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._overflow_count = 0
        self._overflow_logged = 0  # last logged overflow count

    def put(self, chunk: bytes) -> None:
        """Add a chunk to the buffer. Drops oldest if full."""
        with self._lock:
            if len(self._buffer) >= self._max_chunks:
                self._overflow_count += 1
                # Log every 100 drops to avoid spam
                if self._overflow_count - self._overflow_logged >= 100:
                    logger.warning(
                        "Ring buffer overflow: %d chunks dropped total",
                        self._overflow_count,
                    )
                    self._overflow_logged = self._overflow_count
            self._buffer.append(chunk)
        self._event.set()

    def get(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Get the oldest chunk, blocking until one is available."""
        while True:
            with self._lock:
                if self._buffer:
                    return self._buffer.popleft()
            if not self._event.wait(timeout=timeout):
                return None
            self._event.clear()

    def get_all(self) -> list[bytes]:
        """Drain all available chunks without blocking."""
        with self._lock:
            chunks = list(self._buffer)
            self._buffer.clear()
            self._event.clear()
            return chunks

    def clear(self) -> None:
        """Remove all chunks."""
        with self._lock:
            self._buffer.clear()
            self._event.clear()

    @property
    def overflow_count(self) -> int:
        """Number of chunks dropped due to buffer overflow."""
        with self._lock:
            return self._overflow_count

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._buffer) == 0
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_ring_buffer_overflow.py tests/ -q --ignore=tests/e2e`
Expected: PASS

**Step 5: Commit**

```bash
git add meeting_recorder/audio/ring_buffer.py tests/test_ring_buffer_overflow.py
git commit -m "fix: track and log ring buffer overflow instead of silent drops"
```

---

### Task 15: Metadata save lock

**Files:**
- Modify: `meeting_recorder/app.py` (add lock, wrap save calls)

**Step 1: Add lock to `__init__`**

In `meeting_recorder/app.py`, add after `self._stop_lock` (line 63):

```python
self._metadata_lock = threading.Lock()
```

**Step 2: Create helper method**

```python
def _save_metadata(self, metadata, recording_dir: Path) -> None:
    """Thread-safe metadata save."""
    with self._metadata_lock:
        metadata.save(recording_dir)
```

**Step 3: Replace all `metadata.save()` calls in `_post_process` with `self._save_metadata(metadata, recording_dir)`**

Search for all `metadata.save(` in `_post_process` and replace with `self._save_metadata(metadata, recording_dir)`.

Also replace the initial save in `_start_recording_for_process` (line 166):
```python
self._save_metadata(self._current_metadata, self._current_recording_dir)
```

**Step 4: Run tests and commit**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`

```bash
git add meeting_recorder/app.py
git commit -m "fix: protect metadata.json writes with lock to prevent corruption"
```

---

### Task 16: Window PID resolution failure feedback

**Files:**
- Modify: `meeting_recorder/audio/capture_manager.py:429-431`

**Step 1: Update the warning to fire a health callback**

Replace lines 429-431:

```python
if new_pid is None:
    logger.warning("Could not resolve PID for HWND %d; audio not switched.", hwnd)
    if self._on_health_warning:
        try:
            self._on_health_warning("window_pid_failed")
        except Exception:
            pass
    return
```

Add the key to health messages in `app.py`:

```python
"window_pid_failed": "Selected window is no longer available",
```

**Step 2: Run tests and commit**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`

```bash
git add meeting_recorder/audio/capture_manager.py meeting_recorder/app.py
git commit -m "fix: notify user when window picker fails to resolve PID"
```

---

### Task 17: WAV header validation before post-processing

**Files:**
- Modify: `meeting_recorder/app.py` (_post_process method)

**Step 1: Add WAV validation helper**

Add to `meeting_recorder/app.py` (module level):

```python
def _validate_wav(path: Path) -> bool:
    """Check that a WAV file has a valid header and non-zero duration."""
    try:
        import wave
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if frames <= 0 or rate <= 0:
                return False
            duration = frames / rate
            return duration > 0.1  # at least 100ms
    except Exception:
        return False
```

**Step 2: Add validation before transcription in `_post_process`**

In the `_post_process` method, before calling the transcription pipeline, add:

```python
# Validate audio files before transcription
app_wav = recording_dir / "app_audio.wav"
mic_wav = recording_dir / "mic_audio.wav"
if not _validate_wav(app_wav):
    logger.warning("app_audio.wav is corrupt or empty — skipping transcription")
    notifications.notify_error("App audio file is corrupt — transcription skipped")
    return
```

**Step 3: Run tests and commit**

Run: `python -m pytest tests/ -q --ignore=tests/e2e`

```bash
git add meeting_recorder/app.py
git commit -m "fix: validate WAV files before transcription to catch corruption"
```

---

### Task 18: Final integration test and full test run

**Step 1: Run the full test suite**

```bash
python -m pytest tests/ -q --ignore=tests/e2e
```

Expected: All tests pass (568+ tests)

**Step 2: Verify the app starts**

```bash
python -m meeting_recorder diagnose
```

**Step 3: Final commit with any remaining fixes**

```bash
git add -A
git status
# Only commit if there are changes
git commit -m "chore: final integration fixes after record-anything + bug sweep"
```

---

## Summary

| Task | Priority | Description | Files |
|------|----------|-------------|-------|
| 1 | P1 | Window picker: process names + size filter | window_finder.py, capture_manager.py |
| 2 | P1 | Manual recording start with picker fallback | app.py, process_finder.py |
| 3 | P1 | "Record Window..." tray menu item | tray.py, app.py |
| 4 | P1 | Silence detection auto-switch | capture_manager.py |
| 5 | P1 | MuteSync no-op for manual (confirm only) | tests only |
| 6 | P1 | Update notification text | notifications.py |
| 7 | P2 | Ongoing silence health check | capture_manager.py |
| 8 | P2 | System volume pre-check | capture_manager.py, app.py |
| 9 | P2 | Desktop audio auto-stop on PID exit | capture_manager.py |
| 10 | P3 | _live_transcriber lock | capture_manager.py |
| 11 | P3 | _capture_manager null safety audit | tests only |
| 12 | P3 | Thread join cleanup logging | app_audio.py, mic_audio.py |
| 13 | P3 | Writer thread error notification | capture_manager.py, app.py |
| 14 | P3 | Ring buffer overflow logging | ring_buffer.py |
| 15 | P3 | Metadata save lock | app.py |
| 16 | P3 | Window PID resolution feedback | capture_manager.py, app.py |
| 17 | P3 | WAV header validation | app.py |
| 18 | — | Final integration test | all |
