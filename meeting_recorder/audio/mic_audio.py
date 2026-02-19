"""Microphone audio capture with Voice Activity Detection."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.audio.resampling import resample_to_16khz_mono
from meeting_recorder.audio.vad import VoiceActivityDetector
from meeting_recorder.audio.mute_sync import MuteSync

logger = logging.getLogger(__name__)

# Silero VAD requires 512 samples at 16kHz (32ms)
VAD_CHUNK_SAMPLES = 512


class MicAudioCapture:
    """Captures microphone audio with VAD filtering.

    Records at the mic's native sample rate, resamples to 16kHz mono,
    then runs VAD. When no speech is detected, silence bytes are written
    to the ring buffer to preserve time alignment with the app audio track.
    """

    def __init__(
        self,
        ring_buffer: RingBuffer,
        vad: VoiceActivityDetector,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        device_index: Optional[int] = None,
        mute_sync: Optional[MuteSync] = None,
    ):
        self.ring_buffer = ring_buffer
        self.vad = vad
        self.target_sample_rate = sample_rate
        self.target_channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self.device_index = device_index
        self.mute_sync = mute_sync
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start capturing microphone audio in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Mic capture already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="mic-audio-capture",
            daemon=True,
        )
        self._thread.start()
        logger.info("Mic audio capture started.")

    def stop(self) -> None:
        """Stop capturing."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Mic capture thread did not terminate (zombie).")
            self._thread = None
        logger.info("Mic audio capture stopped.")

    def _capture_loop(self) -> None:
        """Main capture loop with VAD filtering."""
        stream = None
        p = None
        try:
            import pyaudiowpatch as pyaudio

            p = pyaudio.PyAudio()

            # Find mic device and its native sample rate
            if self.device_index is not None:
                device_info = p.get_device_info_by_index(self.device_index)
            else:
                device_info = p.get_default_input_device_info()

            native_rate = int(device_info["defaultSampleRate"])
            native_channels = min(int(device_info["maxInputChannels"]), 2)

            logger.info(
                "Using mic device: %s (index %d, native %dHz %dch)",
                device_info["name"],
                device_info["index"],
                native_rate,
                native_channels,
            )

            # Calculate native chunk size to get ~VAD_CHUNK_SAMPLES after resample
            native_chunk = int(VAD_CHUNK_SAMPLES * native_rate / self.target_sample_rate)

            stream = p.open(
                format=pyaudio.paInt16,
                channels=native_channels,
                rate=native_rate,
                input=True,
                input_device_index=int(device_info["index"]),
                frames_per_buffer=native_chunk,
            )

            logger.info("Mic stream opened (native chunk: %d samples).", native_chunk)

            # Silence for VAD_CHUNK_SAMPLES at 16kHz mono int16
            silence = b"\x00" * (VAD_CHUNK_SAMPLES * 2)

            while not self._stop_event.is_set():
                try:
                    audio_data = stream.read(native_chunk, exception_on_overflow=False)
                except IOError as e:
                    logger.warning("Mic read error: %s", e)
                    continue

                # Convert to numpy int16
                audio_raw = np.frombuffer(audio_data, dtype=np.int16)

                # Resample to 16kHz mono int16 using shared utility
                audio_int16 = resample_to_16khz_mono(
                    audio_raw,
                    source_rate=native_rate,
                    target_rate=self.target_sample_rate,
                    source_channels=native_channels,
                )

                # Ensure exactly VAD_CHUNK_SAMPLES for Silero
                chunk_bytes = audio_int16.tobytes()

                # Check meeting app mute state first
                if self.mute_sync is not None and self.mute_sync.is_muted:
                    self.ring_buffer.put(silence)
                    continue

                # Apply VAD
                try:
                    if self.vad.is_speech(chunk_bytes):
                        self.ring_buffer.put(chunk_bytes)
                    else:
                        self.ring_buffer.put(silence)
                except Exception as e:
                    # If VAD fails (wrong size etc), just pass audio through
                    self.ring_buffer.put(chunk_bytes)

        except ImportError:
            logger.error(
                "PyAudioWPatch is not installed. Install with: pip install PyAudioWPatch"
            )
        except Exception:
            logger.exception("Mic capture error")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except OSError as e:
                    logger.debug("Device release during mic stream cleanup: %s", e)
                except Exception:
                    logger.warning("Unexpected error during mic stream cleanup", exc_info=True)
            if p is not None:
                try:
                    p.terminate()
                except OSError as e:
                    logger.debug("Device release during PyAudio cleanup: %s", e)
                except Exception:
                    logger.warning("Unexpected error during PyAudio cleanup", exc_info=True)
            logger.info("Mic capture thread exiting.")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
