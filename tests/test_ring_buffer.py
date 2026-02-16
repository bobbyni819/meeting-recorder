"""Tests for the thread-safe RingBuffer."""

from __future__ import annotations

import threading
import time

import pytest

from meeting_recorder.audio.ring_buffer import RingBuffer


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------

class TestRingBufferBasic:
    """Basic put / get / len / clear operations."""

    def test_put_and_get_single_chunk(self):
        buf = RingBuffer(max_chunks=10)
        buf.put(b"hello")
        assert buf.get(timeout=0.1) == b"hello"

    def test_fifo_order(self):
        buf = RingBuffer(max_chunks=10)
        for i in range(5):
            buf.put(bytes([i]))
        for i in range(5):
            assert buf.get(timeout=0.1) == bytes([i])

    def test_len(self):
        buf = RingBuffer(max_chunks=10)
        assert len(buf) == 0
        buf.put(b"a")
        buf.put(b"b")
        assert len(buf) == 2

    def test_is_empty(self):
        buf = RingBuffer(max_chunks=10)
        assert buf.is_empty is True
        buf.put(b"x")
        assert buf.is_empty is False

    def test_clear(self):
        buf = RingBuffer(max_chunks=10)
        buf.put(b"a")
        buf.put(b"b")
        buf.clear()
        assert len(buf) == 0
        assert buf.is_empty is True

    def test_get_all(self):
        buf = RingBuffer(max_chunks=10)
        buf.put(b"a")
        buf.put(b"b")
        buf.put(b"c")
        chunks = buf.get_all()
        assert chunks == [b"a", b"b", b"c"]
        assert buf.is_empty is True

    def test_get_all_empty(self):
        buf = RingBuffer(max_chunks=10)
        assert buf.get_all() == []


# ---------------------------------------------------------------------------
# Capacity / overflow
# ---------------------------------------------------------------------------

class TestRingBufferCapacity:
    """Verify that old chunks are dropped when capacity is exceeded."""

    def test_overflow_drops_oldest(self):
        buf = RingBuffer(max_chunks=3)
        buf.put(b"a")
        buf.put(b"b")
        buf.put(b"c")
        buf.put(b"d")  # should evict b"a"
        assert len(buf) == 3
        chunks = buf.get_all()
        assert chunks == [b"b", b"c", b"d"]

    def test_overflow_multiple(self):
        buf = RingBuffer(max_chunks=2)
        for i in range(10):
            buf.put(bytes([i]))
        assert len(buf) == 2
        chunks = buf.get_all()
        assert chunks == [bytes([8]), bytes([9])]


# ---------------------------------------------------------------------------
# Timeout / blocking
# ---------------------------------------------------------------------------

class TestRingBufferBlocking:
    """Test blocking get() with timeout."""

    def test_get_returns_none_on_timeout(self):
        buf = RingBuffer(max_chunks=10)
        result = buf.get(timeout=0.05)
        assert result is None

    def test_get_blocks_until_data_available(self):
        buf = RingBuffer(max_chunks=10)
        result_holder = [None]

        def consumer():
            result_holder[0] = buf.get(timeout=2.0)

        t = threading.Thread(target=consumer)
        t.start()

        # Small delay then produce
        time.sleep(0.05)
        buf.put(b"delayed")
        t.join(timeout=3.0)

        assert result_holder[0] == b"delayed"


# ---------------------------------------------------------------------------
# Thread safety under contention
# ---------------------------------------------------------------------------

class TestRingBufferConcurrency:
    """Concurrent producers and consumers."""

    def test_concurrent_put_and_get(self):
        """Multiple producers, single consumer -- no data corruption."""
        buf = RingBuffer(max_chunks=5000)
        num_producers = 4
        items_per_producer = 200
        total_items = num_producers * items_per_producer
        collected: list[bytes] = []
        stop = threading.Event()

        def producer(producer_id: int):
            for i in range(items_per_producer):
                buf.put(f"{producer_id}:{i}".encode())

        def consumer():
            while not stop.is_set() or not buf.is_empty:
                chunk = buf.get(timeout=0.05)
                if chunk is not None:
                    collected.append(chunk)

        threads = [
            threading.Thread(target=producer, args=(pid,))
            for pid in range(num_producers)
        ]
        consumer_thread = threading.Thread(target=consumer)
        consumer_thread.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Signal consumer to drain remaining
        stop.set()
        consumer_thread.join(timeout=5.0)

        assert len(collected) == total_items

    def test_concurrent_put_with_overflow(self):
        """Producers overflow a small buffer -- no crash, length <= max."""
        buf = RingBuffer(max_chunks=10)

        def producer():
            for _ in range(100):
                buf.put(b"x")

        threads = [threading.Thread(target=producer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(buf) <= 10
