"""Acoustic echo *detection* for the mic track.

When the user listens on open speakers (not headphones), the microphone picks
up the meeting audio playing out of those speakers. That echo is the other
participants' voices — which we already capture cleanly on the per-process
loopback (``app_audio``) — leaking into the mic track, where it gets
mis-attributed to the user ("[You]") and pollutes the live transcript.

Commercial recorders solve this with acoustic echo cancellation (AEC), which
needs the *far-end* reference signal (what is playing out the speakers). We
uniquely already capture that: the WASAPI per-process loopback. Rather than
subtract the echo (true AEC, which can distort genuine near-end speech when the
estimate is off), we take the safer *gate* approach: if a mic frame's energy is
almost entirely explained by a lagged copy of the far-end, it is pure echo and
gets dropped. When the user is actually talking (double-talk), their voice adds
energy uncorrelated with the far-end, the correlation drops, and the frame is
kept — so real speech is never discarded.

The core measurement is the normalized cross-correlation (NCC) between the mic
frame and a rolling window of recent far-end audio, maximized over a search
range of lags (the unknown speaker->mic acoustic + capture latency). NCC**2 is
the fraction of mic energy explained by the far-end (explained variance); above
a threshold the frame is echo-dominated.

This module is pure numpy/scipy and holds no global state beyond the rolling
reference, so it is cheap enough to run per mic chunk on the writer thread.
"""

from __future__ import annotations

import threading

import numpy as np
from scipy.signal import correlate

# Defaults tuned for 16 kHz mono int16 audio (the pipeline's canonical format).
_DEFAULT_SAMPLE_RATE = 16000
# How much far-end history to keep. Must comfortably exceed one mic chunk plus
# the maximum speaker->mic latency we search, so the true echo lag is always
# inside the window.
_DEFAULT_REF_SECONDS = 1.0
# Speaker->mic latency search range. The loopback and mic land in their ring
# buffers at slightly different times, and the acoustic path adds delay, so we
# search a generous window on both sides of zero.
_DEFAULT_MAX_LAG_MS = 400.0
# Explained-variance threshold: a mic frame with >= this fraction of its energy
# explained by the best-fit lagged far-end is treated as echo. 0.5 = "more than
# half the mic frame is just the speakers" — conservative, errs toward keeping.
_DEFAULT_ECHO_R2 = 0.5
# Below this RMS (int16 scale) a signal is considered silent; silent frames are
# never classified as echo (nothing meaningful to drop, and NCC is ill-defined).
_DEFAULT_SILENCE_RMS = 60.0


class FarEndReference:
    """Thread-safe rolling buffer of the most recent far-end (loopback) audio.

    The app-audio writer thread pushes loopback chunks here as it writes them;
    the mic writer thread reads a recent window to align against each mic frame.
    Stored as int16 mono at the pipeline sample rate.
    """

    def __init__(
        self,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        ref_seconds: float = _DEFAULT_REF_SECONDS,
    ):
        self._capacity = max(1, int(sample_rate * ref_seconds))
        self._buf = np.zeros(self._capacity, dtype=np.int16)
        self._filled = 0
        self._lock = threading.Lock()

    def push(self, samples: np.ndarray) -> None:
        """Append far-end samples (int16 mono), evicting the oldest."""
        if samples is None or len(samples) == 0:
            return
        if samples.dtype != np.int16:
            samples = samples.astype(np.int16)
        n = len(samples)
        with self._lock:
            if n >= self._capacity:
                self._buf[:] = samples[-self._capacity:]
                self._filled = self._capacity
                return
            # shift left by n, append at the end
            self._buf[:-n] = self._buf[n:]
            self._buf[-n:] = samples
            self._filled = min(self._capacity, self._filled + n)

    def snapshot(self) -> np.ndarray:
        """Return a copy of the currently-filled far-end history (oldest first)."""
        with self._lock:
            if self._filled == 0:
                return np.zeros(0, dtype=np.int16)
            return self._buf[self._capacity - self._filled:].copy()

    def clear(self) -> None:
        with self._lock:
            self._buf[:] = 0
            self._filled = 0


def _rms(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def echo_explained_variance(
    mic: np.ndarray,
    far: np.ndarray,
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    max_lag_ms: float = _DEFAULT_MAX_LAG_MS,
) -> float:
    """Fraction of *mic* frame energy explained by a lagged copy of *far*.

    Computes the peak normalized cross-correlation (over the lag search range)
    between ``mic`` and the far-end window ``far`` and returns NCC**2, i.e. the
    explained-variance fraction in [0, 1]. 1.0 means the mic frame is a pure
    scaled/delayed copy of the far-end (full echo); ~0 means uncorrelated.

    ``far`` should be at least as long as ``mic``; extra length is the lag
    search budget. Returns 0.0 when either signal is effectively silent.
    """
    if mic is None or far is None:
        return 0.0
    m = mic.astype(np.float64)
    f = far.astype(np.float64)
    if len(m) == 0 or len(f) < len(m):
        return 0.0

    m_norm = np.sqrt(np.sum(m * m))
    if m_norm <= 1e-9:
        return 0.0

    # Restrict the far-end search window to len(mic) + max_lag samples so we
    # don't pay for correlating against the whole history.
    max_lag = int(sample_rate * max_lag_ms / 1000.0)
    want = len(m) + max_lag
    if len(f) > want:
        f = f[-want:]
    if np.sqrt(np.sum(f * f)) <= 1e-9:
        return 0.0

    # Sliding cross-correlation of mic within far (valid positions only).
    cc = correlate(f, m, mode="valid", method="fft")  # length len(f)-len(m)+1

    # Per-position far-end window energy via a cumulative-sum sliding window,
    # so each correlation value is normalized by the norm of the exact window
    # it aligned against (true NCC, not a global normalization).
    win = len(m)
    f_sq = f * f
    csum = np.concatenate(([0.0], np.cumsum(f_sq)))
    win_energy = csum[win:] - csum[:-win]  # length len(f)-win+1 == len(cc)
    win_norm = np.sqrt(np.maximum(win_energy, 0.0))

    denom = m_norm * win_norm
    valid = denom > 1e-9
    if not np.any(valid):
        return 0.0
    ncc = np.zeros_like(cc)
    ncc[valid] = cc[valid] / denom[valid]

    peak = float(np.max(np.abs(ncc)))  # abs: echo may be phase-inverted
    peak = min(1.0, max(0.0, peak))
    return peak * peak


class EchoGate:
    """Per-frame echo classifier for the mic track.

    Call :meth:`is_echo` with a mic chunk and the current far-end reference; it
    returns True when the chunk is echo-dominated (and should be dropped). The
    gate is stateless across calls apart from the reference it is given, so it
    is safe to share across threads if each call passes its own arrays.
    """

    def __init__(
        self,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        echo_r2: float = _DEFAULT_ECHO_R2,
        max_lag_ms: float = _DEFAULT_MAX_LAG_MS,
        silence_rms: float = _DEFAULT_SILENCE_RMS,
    ):
        self.sample_rate = sample_rate
        self.echo_r2 = echo_r2
        self.max_lag_ms = max_lag_ms
        self.silence_rms = silence_rms
        # Lightweight counters for diagnostics / health reporting.
        self.frames_seen = 0
        self.frames_dropped = 0

    def is_echo(self, mic: np.ndarray, far: np.ndarray) -> bool:
        """True if *mic* is echo of *far* and should be dropped from the track.

        Returns False (keep) for silent mic frames, missing/short far-end, and
        double-talk (user speaking over the meeting), since those have low
        explained variance.
        """
        self.frames_seen += 1
        if mic is None or len(mic) == 0:
            return False
        # Never drop a frame that has real near-field energy unless it's almost
        # entirely echo — handled by the R2 threshold below. But cheap-out on
        # truly silent mic frames (no echo to remove, NCC unstable).
        if _rms(mic) < self.silence_rms:
            return False
        if far is None or len(far) < len(mic) or _rms(far) < self.silence_rms:
            return False

        r2 = echo_explained_variance(
            mic, far, sample_rate=self.sample_rate, max_lag_ms=self.max_lag_ms
        )
        if r2 >= self.echo_r2:
            self.frames_dropped += 1
            return True
        return False
