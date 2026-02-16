"""Per-process audio capture using ProcTap (WASAPI process loopback)."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np
from scipy.signal import resample_poly
from math import gcd

from meeting_recorder.audio.ring_buffer import RingBuffer

logger = logging.getLogger(__name__)

# ProcTap outputs 48kHz stereo float32
PROCTAP_SAMPLE_RATE = 48000
PROCTAP_CHANNELS = 2


class AppAudioCapture:
    """Captures audio output from a specific process using ProcTap.

    ProcTap uses Windows WASAPI process loopback to capture audio from
    a single application without capturing other system audio.
    Output is 48kHz stereo float32 — we resample to 16kHz mono int16.
    """

    def __init__(
        self,
        pid: int,
        ring_buffer: RingBuffer,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
    ):
        self.pid = pid
        self.ring_buffer = ring_buffer
        self.target_sample_rate = sample_rate
        self.target_channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start capturing audio in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("App audio capture already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="app-audio-capture",
            daemon=True,
        )
        self._thread.start()
        logger.info("App audio capture started for PID %d", self.pid)

    def stop(self) -> None:
        """Stop capturing audio."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("App audio capture stopped.")

    def _capture_loop(self) -> None:
        """Main capture loop running in a background thread."""
        cap = None
        try:
            import proctap

            cap = proctap.ProcessAudioCapture(self.pid)
            cap.start()
            fmt = cap.get_format()
            logger.info(
                "ProcTap stream opened for PID %d: %s", self.pid, fmt
            )

            # Resample ratio
            g = gcd(PROCTAP_SAMPLE_RATE, self.target_sample_rate)
            up = self.target_sample_rate // g
            down = PROCTAP_SAMPLE_RATE // g

            while not self._stop_event.is_set():
                data = cap.read(timeout=0.5)
                if data is None or len(data) == 0:
                    continue

                # ProcTap gives float32 bytes: convert to numpy
                audio_f32 = np.frombuffer(data, dtype=np.float32)

                # Stereo to mono: reshape and average channels
                if PROCTAP_CHANNELS == 2 and len(audio_f32) >= 2:
                    audio_f32 = audio_f32.reshape(-1, PROCTAP_CHANNELS).mean(axis=1)

                # Resample 48kHz -> 16kHz
                if PROCTAP_SAMPLE_RATE != self.target_sample_rate:
                    audio_f32 = resample_poly(audio_f32, up, down).astype(np.float32)

                # Convert float32 [-1, 1] to int16
                audio_int16 = np.clip(audio_f32 * 32767, -32768, 32767).astype(np.int16)
                self.ring_buffer.put(audio_int16.tobytes())

        except ImportError:
            logger.error(
                "proc-tap is not installed. Install with: pip install proc-tap"
            )
        except Exception:
            logger.exception("App audio capture error")
        finally:
            if cap is not None:
                try:
                    cap.stop()
                    cap.close()
                except Exception:
                    pass
            logger.info("App audio capture thread exiting.")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
