# tags: [valravn, recon, intel, database, persistence, memory, core, TTLCache, _MISS, thread-safety, ttl-expiration, lock-synchronization, in-memory-cache, monotonic-clock, stale-cleanup]
"""Small thread-safe TTL cache for Action-lifetime reuse."""

from __future__ import annotations

import threading
import time
from typing import Any

_MISS = object()


class TTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return default
            expires, value = item
            if expires <= now:
                self._values.pop(key, None)
                return default
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        if ttl <= 0:
            return
        now = time.monotonic()
        with self._lock:
            for stale in [k for k, (exp, _) in self._values.items() if exp <= now]:
                self._values.pop(stale, None)
            self._values[key] = (now + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
