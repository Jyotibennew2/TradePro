"""
TradePro Backend - Cache Layer
TTL-based in-memory cache for quotes and option chain.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import threading
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Thread-safe TTL cache.
    Automatically expires entries after ttl seconds.
    """

    def __init__(self, ttl: int = 5) -> None:
        self._ttl   : int            = ttl
        self._store : dict           = {}
        self._lock  : threading.Lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                logger.debug(f"Cache EXPIRED: {key}")
                return None
            logger.debug(f"Cache HIT: {key}")
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            expires_at = time.monotonic() + (ttl or self._ttl)
            self._store[key] = (value, expires_at)
            logger.debug(f"Cache SET: {key} ttl={ttl or self._ttl}s")

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            logger.info("Cache cleared")

    def cleanup(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now     = time.monotonic()
        removed = 0
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
                removed += 1
        if removed:
            logger.debug(f"Cache cleanup: removed {removed} expired entries")
        return removed

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------

quote_cache  : TTLCache = TTLCache(ttl=3)   # quotes refresh every 3s
chain_cache  : TTLCache = TTLCache(ttl=10)  # option chain every 10s
general_cache: TTLCache = TTLCache(ttl=60)  # general purpose 60s
