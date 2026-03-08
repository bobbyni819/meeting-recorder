"""macOS audio backend stubs.

These classes mirror the Windows audio backends but are not yet implemented.
Each raises NotImplementedError with guidance on what macOS API to use.

To implement macOS audio capture:
- Per-process audio: ScreenCaptureKit (macOS 12.3+) or BlackHole virtual device
- Desktop/system audio: BlackHole loopback + PyAudio, or ScreenCaptureKit
- Mic audio: Standard PyAudio (PortAudio) — already cross-platform
- Process finding: NSWorkspace + CGWindowListCopyWindowInfo
- Mute sync: keyboard module (requires Accessibility permissions)
- System volume: osascript or CoreAudio HAL
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


_NOT_IMPL = "macOS audio backend not yet implemented. See audio/platforms/macos.py"


@dataclass
class MeetingProcess:
    """Detected meeting process info (mirrors Windows MeetingProcess)."""
    pid: int
    name: str
    app_key: str  # "zoom", "teams", "webex", "manual"
    window_title: str = ""
    window_hwnd: Optional[int] = None
    window_score: int = 0


class AppAudioCapture:
    """Per-process audio capture stub for macOS.

    macOS options:
    - ScreenCaptureKit (macOS 12.3+): SCStream can capture audio from a
      specific app via SCContentFilter with .includeApplications.
    - BlackHole virtual audio device: route app audio through BlackHole,
      capture with standard PyAudio. Requires user to install BlackHole.
    """

    def __init__(self, ring_buffer, pid: int, sample_rate: int = 16000,
                 channels: int = 1, chunk_duration_ms: int = 30):
        raise NotImplementedError(_NOT_IMPL)

    def start(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def stop(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    @property
    def is_process_specific(self) -> Optional[bool]:
        return True

    @property
    def is_running(self) -> bool:
        return False


class DesktopAudioCapture:
    """System-wide desktop audio capture stub for macOS.

    macOS options:
    - BlackHole (2ch): install BlackHole, create a Multi-Output Device in
      Audio MIDI Setup combining speakers + BlackHole. Capture BlackHole
      input with standard PyAudio.
    - ScreenCaptureKit (macOS 12.3+): SCStream with SCStreamConfiguration
      capturesAudio=True on the entire display.
    """

    def __init__(self, ring_buffer, sample_rate: int = 16000,
                 channels: int = 1, chunk_duration_ms: int = 30):
        raise NotImplementedError(_NOT_IMPL)

    def start(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def stop(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    @property
    def is_process_specific(self) -> Optional[bool]:
        return False

    @property
    def is_running(self) -> bool:
        return False


class MicAudioCapture:
    """Microphone capture stub for macOS.

    This is the easiest to implement — standard PyAudio (not PyAudioWPatch)
    works natively on macOS via PortAudio. Just replace the pyaudiowpatch
    import with standard pyaudio.
    """

    def __init__(self, ring_buffer, vad, sample_rate: int = 16000,
                 channels: int = 1, chunk_duration_ms: int = 30,
                 mute_sync=None, device_index: Optional[int] = None):
        raise NotImplementedError(_NOT_IMPL)

    def start(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def stop(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def set_paused(self, paused: bool) -> None:
        raise NotImplementedError(_NOT_IMPL)

    @property
    def is_running(self) -> bool:
        return False


class MuteSync:
    """Mute synchronization stub for macOS.

    macOS options:
    - keyboard module: works on macOS but requires Accessibility permissions
      in System Preferences > Privacy & Security > Accessibility.
    - Alternative: use NSEvent global monitor for key events.
    """

    def __init__(self, app_key: str, pids: set):
        raise NotImplementedError(_NOT_IMPL)

    def start(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def stop(self) -> None:
        raise NotImplementedError(_NOT_IMPL)


def get_all_pids_for_process(process_name: str) -> set[int]:
    """Find all PIDs for a process name on macOS.

    Use: subprocess.check_output(["pgrep", "-f", process_name])
    """
    raise NotImplementedError(_NOT_IMPL)


def detect_initial_mute_state(pid: int) -> Optional[bool]:
    """Detect mute state on macOS — not easily possible without UI automation."""
    return None  # Unknown — safe default


def find_meeting_processes() -> list[MeetingProcess]:
    """Find running meeting apps on macOS.

    Use: NSWorkspace.sharedWorkspace().runningApplications() to enumerate,
    or subprocess.check_output(["pgrep", "-fl", "zoom|teams|webex"]).
    """
    raise NotImplementedError(_NOT_IMPL)


def find_primary_meeting_process() -> Optional[MeetingProcess]:
    """Find the primary meeting process on macOS."""
    raise NotImplementedError(_NOT_IMPL)


def is_process_running(pid: int) -> bool:
    """Check if a process is still running (cross-platform via psutil)."""
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except Exception:
        return False


def check_system_volume() -> Optional[float]:
    """Get system volume on macOS.

    Use: osascript -e 'output volume of (get volume settings)'
    Returns 0-100, divide by 100 for 0.0-1.0.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip()) / 100.0
    except Exception:
        pass
    return None
