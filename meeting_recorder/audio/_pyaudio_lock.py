"""Shared lock for PyAudio/COM initialization.

PortAudio (used by PyAudioWPatch) and COM (used by pycaw) are not safe to
initialize concurrently from multiple threads.  When mic capture, desktop
audio capture, and system-volume checks all create PyAudio() instances at
the same time, the concurrent COM/PortAudio init causes a segfault.

All code that calls ``pyaudio.PyAudio()`` or COM-based audio APIs (pycaw)
must acquire this lock first.
"""

import threading

pyaudio_init_lock = threading.Lock()
