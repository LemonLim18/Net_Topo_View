from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Dict, List, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_KNOWLEDGE,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_SDN,
    CONTEXT_DOMAIN_TELEMETRY,
)


@dataclass(frozen=True)
class ContextEvent:
    event_type: str
    domain: str
    source: str
    summary: str
    version: int
    timestamp: str
    metadata: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "event_type": self.event_type,
            "domain": self.domain,
            "source": self.source,
            "summary": self.summary,
            "version": self.version,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class ContextStateManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._versions: Dict[str, int] = {
            CONTEXT_DOMAIN_INVENTORY: 0,
            CONTEXT_DOMAIN_PLANNED_TOPOLOGY: 0,
            CONTEXT_DOMAIN_LIVE_TOPOLOGY: 0,
            CONTEXT_DOMAIN_TELEMETRY: 0,
            CONTEXT_DOMAIN_CONFIG: 0,
            CONTEXT_DOMAIN_INCIDENTS: 0,
            CONTEXT_DOMAIN_DRIFT: 0,
            CONTEXT_DOMAIN_KNOWLEDGE: 0,
            CONTEXT_DOMAIN_SDN: 0,
        }
        self._last_updated: Dict[str, Optional[str]] = {domain: None for domain in self._versions}
        self._events: Deque[ContextEvent] = deque(maxlen=200)

    def invalidate(
        self,
        domain: str,
        source: str,
        summary: str,
        event_type: str = "invalidate",
        metadata: Optional[Dict[str, object]] = None,
    ) -> int:
        with self._lock:
            version = self._versions.get(domain, 0) + 1
            self._versions[domain] = version
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._last_updated[domain] = timestamp
            self._events.appendleft(
                ContextEvent(
                    event_type=event_type,
                    domain=domain,
                    source=source,
                    summary=summary,
                    version=version,
                    timestamp=timestamp,
                    metadata=metadata or {},
                )
            )
            return version

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            versions = {domain: version for domain, version in self._versions.items()}
            updated = {domain: value for domain, value in self._last_updated.items()}
            events = [event.to_dict() for event in list(self._events)[:20]]

        return {
            "versions": versions,
            "last_updated": updated,
            "recent_events": events,
        }

    def to_prompt_text(self) -> str:
        snapshot = self.snapshot()
        lines = ["## Context Versions"]
        for domain, version in snapshot["versions"].items():
            last_updated = snapshot["last_updated"].get(domain) or "never"
            lines.append(f"- {domain}: version={version}; last_updated={last_updated}")

        recent_events: List[Dict[str, object]] = snapshot["recent_events"]  # type: ignore[assignment]
        if recent_events:
            lines.append("")
            lines.append("## Recent Context Events")
            for event in recent_events[:5]:
                lines.append(
                    f"- {event['timestamp']}: {event['domain']} -> v{event['version']} "
                    f"via {event['source']} ({event['summary']})"
                )
        return "\n".join(lines)


_STATE_MANAGER = ContextStateManager()


def get_context_state_manager() -> ContextStateManager:
    return _STATE_MANAGER
