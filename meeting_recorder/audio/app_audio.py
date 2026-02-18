"""Per-process audio capture using ProcTap (WASAPI process loopback)."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.audio.resampling import resample_to_16khz_mono, NoiseGate

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
        self._is_process_specific: Optional[bool] = None
        self._noise_gate = NoiseGate()  # Reduces background hiss during silence

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
            if self._thread.is_alive():
                logger.warning("App audio capture thread did not terminate (zombie).")
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

            # Detect whether ProcTap achieved per-process capture or fell back
            # to system-wide loopback. This uses a private API that may change.
            try:
                is_ps = cap._backend._native.is_process_specific()
                self._is_process_specific = is_ps
                if is_ps:
                    logger.info(
                        "ProcTap capture mode: process-specific "
                        "(system volume won't affect recording)"
                    )
                else:
                    logger.warning(
                        "ProcTap capture mode: system-wide fallback "
                        "(system volume affects recording!)"
                    )
            except (AttributeError, Exception):
                logger.debug(
                    "Could not detect ProcTap capture mode (private API unavailable)"
                )

            while not self._stop_event.is_set():
                data = cap.read(timeout=0.5)
                if data is None or len(data) == 0:
                    continue

                # ProcTap gives float32 bytes: convert to numpy
                audio_f32 = np.frombuffer(data, dtype=np.float32)

                # Resample to 16kHz mono int16 using shared utility
                audio_int16 = resample_to_16khz_mono(
                    audio_f32,
                    source_rate=PROCTAP_SAMPLE_RATE,
                    target_rate=self.target_sample_rate,
                    source_channels=PROCTAP_CHANNELS,
                )

                # Apply noise gate to reduce background hiss during silence
                audio_int16 = self._noise_gate.process(audio_int16)

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
                except OSError:
                    pass  # Expected device-release errors
                except Exception:
                    logger.warning("Unexpected error during app audio cleanup", exc_info=True)
            logger.info("App audio capture thread exiting.")

    @property
    def is_process_specific(self) -> Optional[bool]:
        """Whether ProcTap is using per-process capture (True) or system-wide fallback (False).

        Returns None if detection hasn't run yet or the private API was unavailable.
        """
        return self._is_process_specific

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
