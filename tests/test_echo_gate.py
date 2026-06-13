"""Tests for acoustic echo detection on the mic track (echo_gate)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from meeting_recorder.audio.echo_gate import (
    EchoGate,
    FarEndReference,
    echo_explained_variance,
    streaming_echo_report,
)

SR = 16000
N = 512  # mic chunk size used across tests


def _chirp(n: int, f0: float = 200.0, f1: float = 6000.0, sr: int = SR, amp: float = 8000.0,
           seed: int | None = None) -> np.ndarray:
    """Broadband linear chirp as int16 — good autocorrelation (low side-lobes)."""
    t = np.arange(n) / sr
    k = (f1 - f0) / (n / sr)
    sig = np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t))
    if seed is not None:
        rng = np.random.default_rng(seed)
        sig = sig + 0.05 * rng.standard_normal(n)
    return (sig * amp).astype(np.int16)


def _noise(n: int, seed: int, amp: float = 8000.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) * amp).clip(-32768, 32767).astype(np.int16)


# --------------------------------------------------------------------------
# FarEndReference
# --------------------------------------------------------------------------
class TestFarEndReference:
    def test_push_then_snapshot_roundtrips(self):
        ref = FarEndReference(sample_rate=SR, ref_seconds=1.0)
        data = _noise(1000, seed=1)
        ref.push(data)
        snap = ref.snapshot()
        assert len(snap) == 1000
        np.testing.assert_array_equal(snap, data)

    def test_rolling_eviction_keeps_most_recent(self):
        ref = FarEndReference(sample_rate=10, ref_seconds=1.0)  # capacity 10
        ref.push(np.arange(6, dtype=np.int16))
        ref.push(np.arange(100, 108, dtype=np.int16))  # pushes total past capacity
        snap = ref.snapshot()
        assert len(snap) == 10
        # Oldest of the first batch evicted; newest 8 from second batch retained.
        np.testing.assert_array_equal(snap[-8:], np.arange(100, 108))

    def test_push_larger_than_capacity_keeps_tail(self):
        ref = FarEndReference(sample_rate=10, ref_seconds=1.0)  # capacity 10
        ref.push(np.arange(50, dtype=np.int16))
        snap = ref.snapshot()
        assert len(snap) == 10
        np.testing.assert_array_equal(snap, np.arange(40, 50))

    def test_empty_snapshot(self):
        ref = FarEndReference(sample_rate=SR)
        assert len(ref.snapshot()) == 0

    def test_clear(self):
        ref = FarEndReference(sample_rate=SR)
        ref.push(_noise(500, seed=2))
        ref.clear()
        assert len(ref.snapshot()) == 0

    def test_push_empty_is_noop(self):
        ref = FarEndReference(sample_rate=SR)
        ref.push(np.zeros(0, dtype=np.int16))
        assert len(ref.snapshot()) == 0

    def test_concurrent_push_does_not_crash(self):
        ref = FarEndReference(sample_rate=SR, ref_seconds=2.0)

        def worker(seed):
            for _ in range(50):
                ref.push(_noise(160, seed=seed))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Buffer stays bounded at capacity regardless of contention.
        assert len(ref.snapshot()) <= int(SR * 2.0)


# --------------------------------------------------------------------------
# echo_explained_variance
# --------------------------------------------------------------------------
class TestExplainedVariance:
    def test_pure_echo_is_near_one(self):
        far = _chirp(4000, seed=10)
        delay = 1000
        mic = (far[delay:delay + N] * 0.4).astype(np.int16)  # scaled delayed copy
        r2 = echo_explained_variance(mic, far, sample_rate=SR)
        assert r2 > 0.9

    def test_gain_invariant(self):
        far = _chirp(4000, seed=11)
        delay = 800
        seg = far[delay:delay + N]
        r2_quiet = echo_explained_variance((seg * 0.1).astype(np.int16), far)
        r2_loud = echo_explained_variance((seg * 0.9).astype(np.int16), far)
        assert abs(r2_quiet - r2_loud) < 0.05

    def test_phase_inverted_echo_still_detected(self):
        far = _chirp(4000, seed=12)
        delay = 500
        mic = (-0.5 * far[delay:delay + N]).astype(np.int16)  # inverted
        r2 = echo_explained_variance(mic, far)
        assert r2 > 0.9

    def test_independent_signals_low(self):
        far = _chirp(4000, seed=13)
        mic = _noise(N, seed=99)
        r2 = echo_explained_variance(mic, far)
        assert r2 < 0.3

    def test_silent_mic_returns_zero(self):
        far = _chirp(4000, seed=14)
        mic = np.zeros(N, dtype=np.int16)
        assert echo_explained_variance(mic, far) == 0.0

    def test_far_shorter_than_mic_returns_zero(self):
        far = _noise(100, seed=15)
        mic = _noise(N, seed=16)
        assert echo_explained_variance(mic, far) == 0.0

    def test_silent_far_returns_zero(self):
        far = np.zeros(4000, dtype=np.int16)
        mic = _noise(N, seed=17)
        assert echo_explained_variance(mic, far) == 0.0


# --------------------------------------------------------------------------
# EchoGate.is_echo
# --------------------------------------------------------------------------
class TestEchoGate:
    def test_pure_echo_dropped(self):
        gate = EchoGate(sample_rate=SR)
        far = _chirp(4000, seed=20)
        delay = 1200
        mic = (far[delay:delay + N] * 0.5).astype(np.int16)
        assert gate.is_echo(mic, far) is True
        assert gate.frames_dropped == 1

    def test_near_end_speech_kept(self):
        gate = EchoGate(sample_rate=SR)
        far = _chirp(4000, seed=21)
        mic = _chirp(N, f0=120, f1=3000, seed=77)  # user's own speech, unrelated
        assert gate.is_echo(mic, far) is False
        assert gate.frames_dropped == 0

    def test_double_talk_kept(self):
        # Mic = quiet echo (30% energy) + loud near-end speech -> explained
        # variance below threshold -> kept (we must never drop the user).
        gate = EchoGate(sample_rate=SR)
        far = _chirp(4000, seed=22)
        delay = 900
        echo = far[delay:delay + N].astype(np.float64) * 0.4
        near = _noise(N, seed=88).astype(np.float64) * 1.2
        mic = (echo + near).clip(-32768, 32767).astype(np.int16)
        assert gate.is_echo(mic, far) is False

    def test_silent_mic_kept(self):
        gate = EchoGate(sample_rate=SR)
        far = _chirp(4000, seed=23)
        mic = np.zeros(N, dtype=np.int16)
        assert gate.is_echo(mic, far) is False

    def test_missing_far_end_kept(self):
        gate = EchoGate(sample_rate=SR)
        mic = _chirp(N, seed=24)
        assert gate.is_echo(mic, None) is False
        assert gate.is_echo(mic, np.zeros(0, dtype=np.int16)) is False

    def test_far_from_reference_buffer(self):
        # End-to-end: push far-end into the rolling reference, then classify a
        # mic frame that is a delayed copy of recent far-end audio.
        gate = EchoGate(sample_rate=SR)
        ref = FarEndReference(sample_rate=SR, ref_seconds=1.0)
        far_block = _chirp(8000, seed=25)
        ref.push(far_block)
        snap = ref.snapshot()
        # mic echoes the most recent ~100ms of far-end
        delay_from_end = 1600
        seg = snap[len(snap) - delay_from_end: len(snap) - delay_from_end + N]
        mic = (seg * 0.5).astype(np.int16)
        assert gate.is_echo(mic, snap) is True

    def test_counters_track_frames(self):
        gate = EchoGate(sample_rate=SR)
        far = _chirp(4000, seed=26)
        gate.is_echo(_noise(N, seed=1), far)   # kept
        gate.is_echo(_noise(N, seed=2), far)   # kept
        assert gate.frames_seen == 2

    def test_threshold_is_configurable(self):
        far = _chirp(4000, seed=27)
        delay = 700
        echo = far[delay:delay + N].astype(np.float64) * 0.5
        near = _noise(N, seed=66).astype(np.float64) * 0.9
        mic = (echo + near).clip(-32768, 32767).astype(np.int16)
        r2 = echo_explained_variance(mic, far)
        strict = EchoGate(sample_rate=SR, echo_r2=max(0.01, r2 - 0.1))
        lax = EchoGate(sample_rate=SR, echo_r2=min(0.99, r2 + 0.1))
        assert strict.is_echo(mic, far) is True
        assert lax.is_echo(mic, far) is False


# --------------------------------------------------------------------------
# CaptureManager wiring (the echo gate plumbed into the recording pipeline)
# --------------------------------------------------------------------------
class TestCaptureManagerWiring:
    def _make_manager(self, echo_gate_enabled: bool):
        from meeting_recorder.audio.capture_manager import CaptureManager
        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            return CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test_echo"),
                screen_recording_enabled=False,
                echo_gate_enabled=echo_gate_enabled,
            )

    def test_disabled_by_default_no_gate(self):
        mgr = self._make_manager(echo_gate_enabled=False)
        assert mgr._echo_gate is None
        assert mgr._far_end_ref is None
        # _is_echo_chunk is a no-op (keep) when the gate is off.
        assert mgr._is_echo_chunk(_noise(N, seed=1).tobytes()) is False

    def test_enabled_creates_gate_and_drops_echo(self):
        mgr = self._make_manager(echo_gate_enabled=True)
        assert mgr._echo_gate is not None
        assert mgr._far_end_ref is not None

        # Feed far-end (loopback) audio, as the app writer would.
        far_block = _chirp(8000, seed=30)
        mgr._far_end_ref.push(far_block)
        snap = mgr._far_end_ref.snapshot()

        # An echo mic chunk (delayed copy of recent far-end) is detected...
        seg = snap[len(snap) - 1600: len(snap) - 1600 + N]
        echo_chunk = (seg * 0.5).astype(np.int16).tobytes()
        assert mgr._is_echo_chunk(echo_chunk) is True

        # ...while the user's own (independent) speech is kept.
        near_chunk = _chirp(N, f0=110, f1=2500, seed=44).tobytes()
        assert mgr._is_echo_chunk(near_chunk) is False

    def test_is_echo_chunk_never_raises(self):
        mgr = self._make_manager(echo_gate_enabled=True)
        # Odd-length / garbage bytes must not crash the writer thread.
        assert mgr._is_echo_chunk(b"\x01\x02\x03") in (True, False)


# --------------------------------------------------------------------------
# streaming_echo_report (the probe-echo CLI core)
# --------------------------------------------------------------------------
class TestStreamingReport:
    def test_all_echo_high_drop_rate(self):
        # mic is a delayed copy of app for its whole length -> high drop rate.
        app = _chirp(16000 * 3, seed=40)
        delay = 1280
        mic = np.concatenate([
            np.zeros(delay, dtype=np.int16),
            (app[:len(app) - delay] * 0.4).astype(np.int16),
        ])
        rep = streaming_echo_report(app, mic, sample_rate=SR)
        assert rep["frames"] > 0
        assert rep["drop_pct_of_nonsilent"] > 70
        assert len(rep["example_drop_times_sec"]) > 0

    def test_independent_low_drop_rate(self):
        app = _chirp(16000 * 3, seed=41)
        mic = _noise(16000 * 3, seed=123)  # unrelated near-end
        rep = streaming_echo_report(app, mic, sample_rate=SR)
        assert rep["drop_pct_of_nonsilent"] < 10

    def test_report_shape(self):
        app = _noise(16000, seed=1)
        mic = _noise(16000, seed=2)
        rep = streaming_echo_report(app, mic, sample_rate=SR)
        assert set(rep) == {
            "frames", "nonsilent", "dropped",
            "drop_pct_of_nonsilent", "example_drop_times_sec",
        }
