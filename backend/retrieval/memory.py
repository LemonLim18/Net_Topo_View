from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("retrieval.memory")


@dataclass
class MemoryRecord:
    value: Any
    expires_at: float


class OperationalMemoryStore:
    """
    Short-lived operational memory for current-state snapshots and query caches.

    Uses Redis when available, otherwise falls back to an in-memory TTL store.
    """

    def __init__(self, redis_client: Any = None, namespace: str = "nettopoview"):
        self._redis = redis_client
        self._namespace = namespace.strip() or "nettopoview"
        self._lock = threading.Lock()
        self._memory: dict[str, MemoryRecord] = {}

    @classmethod
    def from_env(cls) -> "OperationalMemoryStore":
        redis_url = (os.getenv("REDIS_URL") or os.getenv("REDIS_URI") or "").strip()
        if not redis_url:
            return cls()

        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            logger.info("Operational memory connected to Redis (%s)", redis_url)
            return cls(redis_client=client)
        except Exception as exc:
            logger.info("Redis unavailable for operational memory, using in-memory fallback: %s", exc)
            return cls()

    def _key(self, suffix: str) -> str:
        return f"{self._namespace}:{suffix.lstrip(':')}"

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, (dict, list, int, float, bool)):
            return raw
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def set(self, suffix: str, value: Any, ttl_seconds: int = 30) -> None:
        key = self._key(suffix)
        ttl_seconds = max(1, int(ttl_seconds))
        if self._redis is not None:
            try:
                self._redis.set(key, self._encode(value), ex=ttl_seconds)
                return
            except Exception as exc:
                logger.debug("Redis write failed for %s: %s", key, exc)

        with self._lock:
            self._memory[key] = MemoryRecord(value=value, expires_at=time.time() + ttl_seconds)

    def get(self, suffix: str) -> Any:
        key = self._key(suffix)
        if self._redis is not None:
            try:
                return self._decode(self._redis.get(key))
            except Exception as exc:
                logger.debug("Redis read failed for %s: %s", key, exc)

        with self._lock:
            record = self._memory.get(key)
            if record is None:
                return None
            if record.expires_at <= time.time():
                self._memory.pop(key, None)
                return None
            return record.value

    def delete(self, suffix: str) -> None:
        key = self._key(suffix)
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception as exc:
                logger.debug("Redis delete failed for %s: %s", key, exc)
        with self._lock:
            self._memory.pop(key, None)

    def remember_query_context(self, query_hash: str, payload: Any, ttl_seconds: int = 30) -> None:
        self.set(f"ctx:query:{query_hash}", payload, ttl_seconds=ttl_seconds)

    def load_query_context(self, query_hash: str) -> Any:
        return self.get(f"ctx:query:{query_hash}")

    def remember_device_snapshot(self, hostname: str, payload: Any, ttl_seconds: int = 30) -> None:
        self.set(f"ctx:device:{hostname}:live", payload, ttl_seconds=ttl_seconds)

    def load_device_snapshot(self, hostname: str) -> Any:
        return self.get(f"ctx:device:{hostname}:live")

    def remember_topology_snapshot(self, payload: Any, ttl_seconds: int = 30) -> None:
        self.set("ctx:topology:live", payload, ttl_seconds=ttl_seconds)

    def load_topology_snapshot(self) -> Any:
        return self.get("ctx:topology:live")

    def remember_incident(self, incident_id: str, payload: Any, ttl_seconds: int = 300) -> None:
        self.set(f"ctx:incident:{incident_id}", payload, ttl_seconds=ttl_seconds)

    def load_incident(self, incident_id: str) -> Any:
        return self.get(f"ctx:incident:{incident_id}")

    def remember_inventory_summary(self, scope: str, payload: Any, ttl_seconds: int = 120) -> None:
        self.set(f"ctx:inventory:{scope}", payload, ttl_seconds=ttl_seconds)

    def load_inventory_summary(self, scope: str) -> Any:
        return self.get(f"ctx:inventory:{scope}")
