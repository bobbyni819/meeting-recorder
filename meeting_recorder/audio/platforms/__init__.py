"""Platform-specific audio backend selection.

On Windows: uses ProcTap, PyAudioWPatch (WASAPI), pycaw, keyboard module.
On macOS:   stubs — to be implemented with CoreAudio / BlackHole / ScreenCaptureKit.

Consumers should import from this module instead of directly from
platform-specific files. Example::

    from meeting_recorder.audio.platforms import (
        AppAudioCapture, DesktopAudioCapture, MicAudioCapture,
        MuteSync, get_all_pids_for_process, detect_initial_mute_state,
        find_meeting_processes, find_primary_meeting_process, is_process_running,
        check_system_volume,
    )
"""

from __future__ import annotations

import sys

PLATFORM = sys.platform  # 'win32', 'darwin', 'linux'

if PLATFORM == "win32":
    from meeting_recorder.audio.app_audio import AppAudioCapture
    from meeting_recorder.audio.desktop_audio import DesktopAudioCapture
    from meeting_recorder.audio.mic_audio import MicAudioCapture
    from meeting_recorder.audio.mute_sync import (
        MuteSync,
        get_all_pids_for_process,
        detect_initial_mute_state,
    )
    from meeting_recorder.audio.process_finder import (
        MeetingProcess,
        find_meeting_processes,
        find_primary_meeting_process,
        is_process_running,
    )

    def check_system_volume():
        """Return the system master volume (0.0-1.0), or None if unavailable."""
        try:
            from meeting_recorder.audio._pyaudio_lock import pyaudio_init_lock
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            with pyaudio_init_lock:
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = interface.QueryInterface(IAudioEndpointVolume)
                if volume.GetMute():
                    return 0.0
                return volume.GetMasterVolumeLevelScalar()
        except Exception:
            return None

elif PLATFORM == "darwin":
    from meeting_recorder.audio.platforms.macos import (  # noqa: F401
        AppAudioCapture,
        DesktopAudioCapture,
        MicAudioCapture,
        MuteSync,
        MeetingProcess,
        get_all_pids_for_process,
        detect_initial_mute_state,
        find_meeting_processes,
        find_primary_meeting_process,
        is_process_running,
        check_system_volume,
    )

else:
    raise ImportError(
        f"Platform '{PLATFORM}' is not supported. "
        "Meeting Recorder requires Windows or macOS."
    )

__all__ = [
    "AppAudioCapture",
    "DesktopAudioCapture",
    "MicAudioCapture",
    "MuteSync",
    "MeetingProcess",
    "get_all_pids_for_process",
    "detect_initial_mute_state",
    "find_meeting_processes",
    "find_primary_meeting_process",
    "is_process_running",
    "check_system_volume",
]
