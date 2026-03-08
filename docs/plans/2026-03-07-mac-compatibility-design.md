# macOS Compatibility Design

**Date:** 2026-03-07
**Status:** Groundwork complete. macOS stubs in place, ready for implementation.

## Architecture: Platform Backends

All platform-specific code is routed through factory modules:

```
meeting_recorder/
  audio/
    platforms/
      __init__.py       # Selects Windows or macOS backend at import time
      macos.py          # Stubs with implementation guidance
    app_audio.py        # Windows: ProcTap per-process capture
    desktop_audio.py    # Windows: WASAPI loopback
    mic_audio.py        # Windows: PyAudioWPatch mic
    process_finder.py   # Windows: ctypes + pycaw
    mute_sync.py        # Windows: ctypes + keyboard
    capture_manager.py  # Cross-platform orchestrator (imports from platforms/)
  video/
    platforms/
      __init__.py       # Selects Windows or macOS backend
      macos.py          # Stubs
    window_finder.py    # Windows: ctypes Win32 API
    screen_capture.py   # Windows: PrintWindow + mss fallback
  ui/
    platforms/
      __init__.py       # Selects Windows or macOS notifications
      macos.py          # Implemented: osascript notifications
    notifications.py    # Windows: winotify
```

**How it works:** `platforms/__init__.py` checks `sys.platform` and imports from the
appropriate backend. Consumers import from `platforms/` instead of platform-specific files.

## What's Already Cross-Platform

| Component | Status |
|-----------|--------|
| All Tkinter UI (dashboard, main window, settings, search) | Works |
| System tray (pystray) | Works |
| Google Drive integration | Works |
| Transcription (whisper, Gemini) | Works |
| Diarization (pyannote) | Works |
| Summary generation | Works |
| Config management | Works |
| Recording store/metadata | Works |
| Audio resampling, ring buffer, VAD, mixer | Works |
| macOS notifications (osascript) | Implemented |
| Config export/import for multi-machine setup | Works |

## What Needs macOS Implementation

### Priority 1: Audio Capture (HARD)
- **Mic capture:** Replace `pyaudiowpatch` with standard `pyaudio` (PortAudio)
- **Desktop audio:** BlackHole virtual device + PyAudio, or ScreenCaptureKit
- **Per-process audio:** ScreenCaptureKit app-specific capture

### Priority 2: Window/Process Management (MEDIUM)
- **Process finder:** NSWorkspace + pgrep (replaces ctypes/pycaw)
- **Window finder:** CGWindowListCopyWindowInfo (replaces EnumWindows)
- **Screen capture:** mss already works; upgrade to CGWindowListCreateImage

### Priority 3: Extras (LOW)
- **Mute sync:** keyboard module (needs Accessibility permissions)
- **Outlook calendar:** Disable on macOS or use CalDAV/Google Calendar
- **System volume:** osascript (already implemented in stub)

## Import Path Changes

Consumers now import through platform factories:

```python
# Before:
from meeting_recorder.audio.app_audio import AppAudioCapture
from meeting_recorder.video.window_finder import get_window_title
from meeting_recorder.ui import notifications

# After:
from meeting_recorder.audio.platforms import AppAudioCapture
from meeting_recorder.video.platforms import get_window_title
from meeting_recorder.ui import platforms as notifications
```

## macOS Dependencies (Future)

```
pip install pyaudio           # Standard PortAudio (mic capture)
pip install pyobjc-framework-Quartz  # CGWindowList, NSWorkspace
pip install pyobjc-framework-ScreenCaptureKit  # Audio + window capture (macOS 12.3+)
# BlackHole: installed via brew cask (not pip)
```
