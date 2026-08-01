"""Small thread-safe TTL cache for Action-lifetime reuse."""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= now:
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._values[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
