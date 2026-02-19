"""Real-time audio level monitoring for VU meter display."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Reference level for dB calculation (full-scale int16)
REFERENCE_AMPLITUDE = 32768.0
# Minimum dB value (silence floor)
MIN_DB = -60.0


def compute_levels_db(audio_bytes: bytes) -> tuple[float, float]:
    """Compute RMS and peak levels in dB from raw int16 PCM audio bytes.

    Parses the audio bytes once and computes both metrics in a single pass
    to avoid redundant np.frombuffer allocations on the hot path.

    Args:
        audio_bytes: Raw 16-bit PCM audio bytes.

    Returns:
        (rms_db, peak_db) tuple. Values range from MIN_DB (silence) to 0.0 (full scale).
    """
    if not audio_bytes or len(audio_bytes) < 2:
        return MIN_DB, MIN_DB

    samples = np.frombuffer(audio_bytes, dtype=np.int16)
    if len(samples) == 0:
        return MIN_DB, MIN_DB

    # Compute both RMS and peak from the same parsed samples
    float_samples = samples.astype(np.float64)
    rms = np.sqrt(np.mean(float_samples ** 2))
    peak = float(np.max(np.abs(samples)))

    rms_db = MIN_DB
    if rms >= 1.0:
        rms_db = max(20.0 * math.log10(rms / REFERENCE_AMPLITUDE), MIN_DB)

    peak_db = MIN_DB
    if peak >= 1.0:
        peak_db = max(20.0 * math.log10(peak / REFERENCE_AMPLITUDE), MIN_DB)

    return rms_db, peak_db


def compute_rms_db(audio_bytes: bytes) -> float:
    """Compute RMS level in dB from raw int16 PCM audio bytes.

    Args:
        audio_bytes: Raw 16-bit PCM audio bytes.

    Returns:
        RMS level in dB (0.0 = full scale, MIN_DB = silence).
    """
    return compute_levels_db(audio_bytes)[0]


def compute_peak_db(audio_bytes: bytes) -> float:
    """Compute peak level in dB from raw int16 PCM audio bytes.

    Args:
        audio_bytes: Raw 16-bit PCM audio bytes.

    Returns:
        Peak level in dB (0.0 = full scale, MIN_DB = silence).
    """
    return compute_levels_db(audio_bytes)[1]


class AudioLevelMonitor:
    """Monitors audio levels from ring buffers and reports via callback.

    Computes RMS and peak dB levels for app and mic audio streams,
    calling a callback at a configurable update rate.
    """

    def __init__(
        self,
        on_levels: Optional[Callable[[float, float, float, float], None]] = None,
        update_interval: float = 0.1,
        history_size: int = 50,
    ):
        """
        Args:
            on_levels: Callback receiving (app_rms_db, app_peak_db, mic_rms_db, mic_peak_db).
            update_interval: Seconds between level updates.
            history_size: Number of recent level readings to keep.
        """
        self._on_levels = on_levels
        self._update_interval = update_interval
        self._app_rms_db = MIN_DB
        self._app_peak_db = MIN_DB
        self._mic_rms_db = MIN_DB
        self._mic_peak_db = MIN_DB
        self._app_history: deque[float] = deque(maxlen=history_size)
        self._mic_history: deque[float] = deque(maxlen=history_size)
        self._lock = threading.Lock()

    def update_app_level(self, audio_bytes: bytes) -> None:
        """Update app audio level from a new chunk of audio data."""
        rms, peak = compute_levels_db(audio_bytes)
        with self._lock:
            self._app_rms_db = rms
            self._app_peak_db = peak
            self._app_history.append(rms)

    def update_mic_level(self, audio_bytes: bytes) -> None:
        """Update mic audio level from a new chunk of audio data."""
        rms, peak = compute_levels_db(audio_bytes)
        with self._lock:
            self._mic_rms_db = rms
            self._mic_peak_db = peak
            self._mic_history.append(rms)

    @property
    def app_level(self) -> tuple[float, float]:
        """Current app audio (rms_db, peak_db)."""
        with self._lock:
            return self._app_rms_db, self._app_peak_db

    @property
    def mic_level(self) -> tuple[float, float]:
        """Current mic audio (rms_db, peak_db)."""
        with self._lock:
            return self._mic_rms_db, self._mic_peak_db

    @property
    def app_avg_db(self) -> float:
        """Average app RMS dB over recent history."""
        with self._lock:
            if not self._app_history:
                return MIN_DB
            return sum(self._app_history) / len(self._app_history)

    @property
    def mic_avg_db(self) -> float:
        """Average mic RMS dB over recent history."""
        with self._lock:
            if not self._mic_history:
                return MIN_DB
            return sum(self._mic_history) / len(self._mic_history)

    def notify(self) -> None:
        """Trigger the on_levels callback with current values."""
        if self._on_levels:
            with self._lock:
                app_rms = self._app_rms_db
                app_peak = self._app_peak_db
                mic_rms = self._mic_rms_db
                mic_peak = self._mic_peak_db
            # Call outside the lock so the callback (e.g. Tk dashboard update)
            # doesn't block writer threads from updating levels.
            self._on_levels(app_rms, app_peak, mic_rms, mic_peak)

    def reset(self) -> None:
        """Reset all levels to silence."""
        with self._lock:
            self._app_rms_db = MIN_DB
            self._app_peak_db = MIN_DB
            self._mic_rms_db = MIN_DB
            self._mic_peak_db = MIN_DB
            self._app_history.clear()
            self._mic_history.clear()
