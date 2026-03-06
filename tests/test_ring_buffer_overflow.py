"""Tests for RingBuffer overflow tracking."""

from __future__ import annotations

from meeting_recorder.audio.ring_buffer import RingBuffer


class TestRingBufferOverflow:
    def test_overflow_count_starts_at_zero(self):
        buf = RingBuffer(max_chunks=3)
        assert buf.overflow_count == 0

    def test_overflow_count_increments(self):
        buf = RingBuffer(max_chunks=2)
        buf.put(b"a")
        buf.put(b"b")
        buf.put(b"c")  # drops "a"
        assert buf.overflow_count == 1

    def test_overflow_count_accumulates(self):
        buf = RingBuffer(max_chunks=1)
        buf.put(b"a")
        buf.put(b"b")  # drops a
        buf.put(b"c")  # drops b
        assert buf.overflow_count == 2

    def test_no_overflow_when_within_capacity(self):
        buf = RingBuffer(max_chunks=5)
        for i in range(5):
            buf.put(bytes([i]))
        assert buf.overflow_count == 0

    def test_overflow_logging_batched_at_100(self, caplog):
        """Overflow warnings should only log every 100 drops, not every drop."""
        import logging

        buf = RingBuffer(max_chunks=1)
        buf.put(b"seed")  # fill the buffer

        with caplog.at_level(logging.WARNING, logger="meeting_recorder.audio.ring_buffer"):
            for _ in range(99):
                buf.put(b"x")
            # 99 overflows: no log yet
            assert len(caplog.records) == 0

            buf.put(b"x")  # 100th overflow triggers log
            assert len(caplog.records) == 1
            assert "100 chunks dropped" in caplog.records[0].message
