"""macOS audio source adapters.

Microphone capture uses PortAudio through ``sounddevice``. System audio capture
uses a user-configured loopback input device such as BlackHole. Native
per-process or system-audio capture through ScreenCaptureKit is a future
enhancement; today macOS does not expose Windows-style per-process loopback to
ordinary PortAudio clients.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from meeting_recorder.audio.resampling import NoiseGate, resample_to_16khz_mono
from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.platform_support.base import AudioSource, RingBufferLike

logger = logging.getLogger(__name__)

VAD_CHUNK_SAMPLES = 512


class _SoundDeviceAudioSource(AudioSource):
    """Common sounddevice capture loop.

    ``sounddevice`` invokes the callback on a PortAudio thread, so all work here
    is bounded and exception-safe: convert the callback buffer, resample it to
    16 kHz mono int16 PCM, then enqueue bytes in the existing ring buffer.
    """

    stream_name = "macos-audio"

    def __init__(
        self,
        ring_buffer: Optional[RingBufferLike] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        device: object = None,
        target_length: Optional[int] = None,
        apply_noise_gate: bool = False,
    ):
        self.ring_buffer = ring_buffer if ring_buffer is not None else RingBuffer(max_chunks=2000)
        self.target_sample_rate = sample_rate
        self.target_channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self.device = device
        self._target_length = target_length
        self._noise_gate = NoiseGate() if apply_noise_gate else None
        self._stream = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        if self.is_running:
            return

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "sounddevice is required for macOS audio capture. "
                "Install with: pip install -e '.[macos]'"
            ) from exc

        device = self._resolve_device(sd)
        device_info = sd.query_devices(device, "input")
        native_rate = int(device_info.get("default_samplerate") or 48000)
        native_channels = max(1, min(int(device_info.get("max_input_channels") or 1), 2))
        blocksize = self._native_blocksize(native_rate)

        logger.info(
            "%s input: %s (device=%s, native=%dHz %dch, block=%d)",
            self.stream_name,
            device_info.get("name", "unknown"),
            device,
            native_rate,
            native_channels,
            blocksize,
        )

        def callback(indata, frames, time_info, status) -> None:
            if status:
                logger.debug("%s callback status: %s", self.stream_name, status)
            try:
                self._handle_input(indata, native_rate, native_channels)
            except Exception:
                logger.debug("%s callback failed", self.stream_name, exc_info=True)

        stream = sd.InputStream(
            device=device,
            samplerate=native_rate,
            channels=native_channels,
            dtype="float32",
            blocksize=blocksize,
            callback=callback,
        )
        stream.start()
        with self._lock:
            self._stream = stream
            self._running = True

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._running = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.debug("%s stream cleanup failed", self.stream_name, exc_info=True)

    def close(self) -> None:
        self.stop()

    def get_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        return self.ring_buffer.get(timeout=timeout)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _resolve_device(self, sd):
        return self.device

    def _native_blocksize(self, native_rate: int) -> int:
        if self._target_length is not None:
            return max(1, int(round(self._target_length * native_rate / 16000)))
        return max(1, int(round(native_rate * self.chunk_duration_ms / 1000.0)))

    def _handle_input(self, indata: np.ndarray, native_rate: int, native_channels: int) -> None:
        data = np.asarray(indata, dtype=np.float32)
        if data.ndim == 2 and data.shape[1] > 2:
            mono = data.mean(axis=1).astype(np.float32)
            source_channels = 1
            raw = mono
        elif data.ndim == 2:
            source_channels = int(data.shape[1])
            raw = data.reshape(-1)
        else:
            source_channels = native_channels
            raw = data.reshape(-1)

        audio_int16 = resample_to_16khz_mono(
            raw,
            source_rate=native_rate,
            target_rate=self.target_sample_rate,
            source_channels=source_channels,
            target_length=self._target_length,
        )
        if self._noise_gate is not None:
            audio_int16 = self._noise_gate.process(audio_int16)
        self.ring_buffer.put(audio_int16.tobytes())


class MacMicAudioSource(_SoundDeviceAudioSource):
    """Capture the default macOS microphone via sounddevice."""

    stream_name = "macos-mic"

    def __init__(
        self,
        ring_buffer: Optional[RingBufferLike] = None,
        vad: object = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        device_index: Optional[int] = None,
        mute_sync: object = None,
        vad_hangover_ms: float = 300.0,
        on_error: object = None,
        **_: object,
    ):
        # ``vad`` and ``mute_sync`` are accepted for constructor compatibility
        # with the Windows adapter; the normalized raw mic stream is emitted
        # here and higher layers can apply policy-specific filtering.
        self.vad = vad
        self.mute_sync = mute_sync
        self.vad_hangover_ms = vad_hangover_ms
        self.on_error = on_error
        super().__init__(
            ring_buffer=ring_buffer,
            sample_rate=sample_rate,
            channels=channels,
            chunk_duration_ms=chunk_duration_ms,
            device=device_index,
            target_length=VAD_CHUNK_SAMPLES,
            apply_noise_gate=False,
        )


class MacSystemAudioSource(_SoundDeviceAudioSource):
    """Capture macOS system audio from a loopback input device.

    This is system-wide, not per-process. Use BlackHole, Loopback, or another
    virtual input device and route the meeting app/system output to it.
    """

    stream_name = "macos-system-audio"

    def __init__(
        self,
        ring_buffer: Optional[RingBufferLike] | int = None,
        maybe_ring_buffer: Optional[RingBufferLike] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        device_name_or_index: str | int | None = "BlackHole",
        device: str | int | None = None,
        pid: Optional[int] = None,
        **_: object,
    ):
        # Also tolerate the Windows app-source positional shape:
        # MacSystemAudioSource(pid, ring_buffer, ...). The PID is intentionally
        # ignored because PortAudio loopback devices are system-wide on macOS.
        if isinstance(ring_buffer, int) and maybe_ring_buffer is not None:
            pid = ring_buffer
            ring_buffer = maybe_ring_buffer
        self.pid = pid
        self.device_name_or_index = (
            device if device is not None else device_name_or_index
        )
        super().__init__(
            ring_buffer=ring_buffer if not isinstance(ring_buffer, int) else None,
            sample_rate=sample_rate,
            channels=channels,
            chunk_duration_ms=chunk_duration_ms,
            device=self.device_name_or_index,
            target_length=None,
            apply_noise_gate=True,
        )

    def _resolve_device(self, sd):
        requested = self.device_name_or_index
        if isinstance(requested, int):
            return requested

        query = (requested or "BlackHole").lower()
        matches: list[tuple[int, dict]] = []
        for index, info in enumerate(sd.query_devices()):
            try:
                if int(info.get("max_input_channels") or 0) <= 0:
                    continue
                if query in str(info.get("name", "")).lower():
                    matches.append((index, info))
            except Exception:
                continue
        if matches:
            return matches[0][0]

        available = [
            f"{i}: {info.get('name', 'unknown')}"
            for i, info in enumerate(sd.query_devices())
            if int(info.get("max_input_channels") or 0) > 0
        ]
        raise RuntimeError(
            "No macOS loopback input device matching "
            f"{requested!r} was found. Install BlackHole with "
            "`brew install blackhole-2ch`, create a Multi-Output or Aggregate "
            "Device that includes BlackHole, or pass a sounddevice input "
            "device index/name. Input devices: " + "; ".join(available)
        )

    @property
    def is_process_specific(self) -> bool:
        return False
