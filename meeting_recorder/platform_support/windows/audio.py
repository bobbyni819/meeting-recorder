"""Windows audio source adapters.

The adapters delegate to the existing capture classes and expose the new
platform-support ``AudioSource`` interface without changing capture behavior.
"""

from __future__ import annotations

from typing import Callable, Optional

from meeting_recorder.platform_support.base import AudioSource, RingBufferLike


class _RingBufferedAudioSource(AudioSource):
    """Common adapter behavior for existing ring-buffer audio captures."""

    def __init__(self, capture: object, ring_buffer: RingBufferLike):
        self._capture = capture
        self._ring_buffer = ring_buffer

    def start(self) -> None:
        self._capture.start()

    def stop(self) -> None:
        self._capture.stop()

    def close(self) -> None:
        self.stop()

    def get_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        return self._ring_buffer.get(timeout=timeout)

    @property
    def is_running(self) -> bool:
        return bool(getattr(self._capture, "is_running", False))

    @property
    def ring_buffer(self) -> RingBufferLike:
        return self._ring_buffer

    @property
    def wrapped(self) -> object:
        """Underlying existing Windows capture object."""
        return self._capture


class WindowsAppAudioSource(_RingBufferedAudioSource):
    """Adapter for per-process Windows WASAPI loopback capture."""

    def __init__(
        self,
        pid: int,
        ring_buffer: RingBufferLike,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
    ):
        from meeting_recorder.audio.app_audio import AppAudioCapture

        super().__init__(
            AppAudioCapture(
                pid=pid,
                ring_buffer=ring_buffer,
                sample_rate=sample_rate,
                channels=channels,
                chunk_duration_ms=chunk_duration_ms,
            ),
            ring_buffer,
        )

    @property
    def is_process_specific(self) -> Optional[bool]:
        return getattr(self._capture, "is_process_specific", None)


class WindowsDesktopAudioSource(_RingBufferedAudioSource):
    """Adapter for system-wide Windows WASAPI loopback capture."""

    def __init__(
        self,
        ring_buffer: RingBufferLike,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
    ):
        from meeting_recorder.audio.desktop_audio import DesktopAudioCapture

        super().__init__(
            DesktopAudioCapture(
                ring_buffer=ring_buffer,
                sample_rate=sample_rate,
                channels=channels,
                chunk_duration_ms=chunk_duration_ms,
            ),
            ring_buffer,
        )

    @property
    def is_process_specific(self) -> bool:
        return False


class WindowsMicAudioSource(_RingBufferedAudioSource):
    """Adapter for Windows microphone capture with VAD and mute sync."""

    def __init__(
        self,
        ring_buffer: RingBufferLike,
        vad: object,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        device_index: Optional[int] = None,
        mute_sync: Optional[object] = None,
        vad_hangover_ms: float = 300.0,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        from meeting_recorder.audio.mic_audio import MicAudioCapture

        super().__init__(
            MicAudioCapture(
                ring_buffer=ring_buffer,
                vad=vad,
                sample_rate=sample_rate,
                channels=channels,
                chunk_duration_ms=chunk_duration_ms,
                device_index=device_index,
                mute_sync=mute_sync,
                vad_hangover_ms=vad_hangover_ms,
                on_error=on_error,
            ),
            ring_buffer,
        )
