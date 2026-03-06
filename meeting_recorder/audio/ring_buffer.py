"""Thread-safe ring buffer for audio data exchange between threads."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


class RingBuffer:
    """Thread-safe ring buffer backed by a deque.

    Stores raw audio chunks (bytes) with a maximum capacity.
    When full, oldest chunks are silently dropped.
    """

    def __init__(self, max_chunks: int = 1000):
        self._buffer: deque[bytes] = deque(maxlen=max_chunks)
        self._max_chunks = max_chunks
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._overflow_count = 0
        self._overflow_logged = 0

    def put(self, chunk: bytes) -> None:
        """Add a chunk to the buffer."""
        with self._lock:
            if len(self._buffer) >= self._max_chunks:
                self._overflow_count += 1
                if self._overflow_count - self._overflow_logged >= 100:
                    logger.warning(
                        "Ring buffer overflow: %d chunks dropped total",
                        self._overflow_count,
                    )
                    self._overflow_logged = self._overflow_count
            self._buffer.append(chunk)
        self._event.set()

    @property
    def overflow_count(self) -> int:
        """Total number of chunks dropped due to overflow."""
        with self._lock:
            return self._overflow_count

    def get(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Get the oldest chunk, blocking until one is available."""
        while True:
            with self._lock:
                if self._buffer:
                    return self._buffer.popleft()
            if not self._event.wait(timeout=timeout):
                return None
            self._event.clear()

    def get_all(self) -> list[bytes]:
        """Drain all available chunks without blocking."""
        with self._lock:
            chunks = list(self._buffer)
            self._buffer.clear()
            self._event.clear()
            return chunks

    def clear(self) -> None:
        """Remove all chunks."""
        with self._lock:
            self._buffer.clear()
            self._event.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._buffer) == 0
