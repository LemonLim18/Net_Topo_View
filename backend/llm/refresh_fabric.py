from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Iterable, Optional, Sequence

from llm.context_state import ContextStateManager, get_context_state_manager
from llm.refresh_state import RefreshStateManager, get_refresh_state_manager


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_names(values: Iterable[str]) -> tuple[str, ...]:
    seen = set()
    normalized = []
    for value in values:
        item = (value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def _normalize_downstream_targets(
    refresh_targets: Sequence[str],
    listener_results: Sequence[Dict[str, Any]],
) -> list[Dict[str, object]]:
    targets: list[Dict[str, object]] = [
        {"kind": "refresh_target", "name": target}
        for target in refresh_targets
    ]
    for item in listener_results:
        result = item.get("result")
        if isinstance(result, dict):
            targets.append(result)
        elif isinstance(result, list):
            for entry in result:
                if isinstance(entry, dict):
                    targets.append(entry)
    return targets


@dataclass(frozen=True)
class RefreshFabricEvent:
    source: str
    action: str
    summary: str
    domains: Sequence[str]
    metadata: Dict[str, object] = field(default_factory=dict)
    refresh_targets: Sequence[str] = field(default_factory=tuple)
    event_type: str = "invalidate"
    refresh_mode: str = "async"


class RefreshFabric:
    def __init__(
        self,
        context_state: Optional[ContextStateManager] = None,
        refresh_state: Optional[RefreshStateManager] = None,
    ) -> None:
        self._context_state = context_state or get_context_state_manager()
        self._refresh_state = refresh_state or get_refresh_state_manager()
        self._lock = threading.Lock()
        self._recent_publications: Deque[Dict[str, object]] = deque(maxlen=200)
        self._listeners: Dict[str, Callable[[Dict[str, object]], None]] = {}
        self._sinks: Dict[str, Callable[[Dict[str, object]], None]] = {}

    def register_listener(self, name: str, callback: Callable[[Dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners[name] = callback

    def unregister_listener(self, name: str) -> None:
        with self._lock:
            self._listeners.pop(name, None)

    def register_sink(self, name: str, callback: Callable[[Dict[str, object]], None]) -> None:
        with self._lock:
            self._sinks[name] = callback

    def unregister_sink(self, name: str) -> None:
        with self._lock:
            self._sinks.pop(name, None)

    def publish(self, event: RefreshFabricEvent) -> Dict[str, object]:
        event_id = f"evt-{uuid.uuid4().hex[:10]}"
        timestamp = _utc_now()
        domains = _normalize_names(event.domains)
        refresh_targets = _normalize_names(event.refresh_targets)
        metadata = dict(event.metadata)
        metadata.setdefault("action", event.action)
        metadata.setdefault("event_source", event.source)
        metadata.setdefault("event_id", event_id)

        versions: Dict[str, int] = {}
        for domain in domains:
            versions[domain] = self._context_state.invalidate(
                domain=domain,
                source=event.source,
                summary=event.summary,
                event_type=event.event_type,
                metadata=metadata,
            )

        requested_by = f"fabric:{event.source}:{event.action}"
        refresh_jobs = []
        if event.refresh_mode != "none":
            for target in refresh_targets:
                if event.refresh_mode == "sync":
                    result = self._refresh_state.run_refresh(target, requested_by=requested_by)
                    refresh_jobs.append({"target": target, "mode": "sync", "result": result})
                else:
                    job = self._refresh_state.start_refresh_job(target, requested_by=requested_by)
                    refresh_jobs.append({"target": target, "mode": "async", "job": job})

        listener_results = []
        with self._lock:
            listeners = list(self._listeners.items())
        for name, listener in listeners:
            try:
                result = listener({
                    "event_id": event_id,
                    "timestamp": timestamp,
                    "source": event.source,
                    "action": event.action,
                    "summary": event.summary,
                    "domains": list(domains),
                    "versions": versions,
                    "event_type": event.event_type,
                    "refresh_mode": event.refresh_mode,
                    "refresh_targets": list(refresh_targets),
                    "refresh_jobs": refresh_jobs,
                    "metadata": metadata,
                })
                listener_results.append({"listener": name, "status": "ok", "result": result})
            except Exception as exc:
                listener_results.append({"listener": name, "status": "error", "error": str(exc)})

        downstream_targets = _normalize_downstream_targets(refresh_targets, listener_results)

        publication = {
            "event_id": event_id,
            "timestamp": timestamp,
            "source": event.source,
            "action": event.action,
            "summary": event.summary,
            "domains": list(domains),
            "versions": versions,
            "event_type": event.event_type,
            "refresh_mode": event.refresh_mode,
            "refresh_targets": list(refresh_targets),
            "refresh_jobs": refresh_jobs,
            "listener_results": listener_results,
            "downstream_targets": downstream_targets,
            "metadata": metadata,
        }
        with self._lock:
            self._recent_publications.appendleft(publication)
            sinks = list(self._sinks.values())

        for sink in sinks:
            try:
                sink(publication)
            except Exception:
                continue
        return publication

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            recent_events = list(self._recent_publications)[:20]
        return {"recent_publications": recent_events}


_REFRESH_FABRIC = RefreshFabric()


def get_refresh_fabric() -> RefreshFabric:
    return _REFRESH_FABRIC


def register_refresh_listener(name: str, callback: Callable[[Dict[str, object]], None]) -> None:
    get_refresh_fabric().register_listener(name, callback)


def unregister_refresh_listener(name: str) -> None:
    get_refresh_fabric().unregister_listener(name)


def register_refresh_sink(name: str, callback: Callable[[Dict[str, object]], None]) -> None:
    get_refresh_fabric().register_sink(name, callback)


def unregister_refresh_sink(name: str) -> None:
    get_refresh_fabric().unregister_sink(name)


def publish_context_refresh_event(
    *,
    source: str,
    action: str,
    summary: str,
    domains: Sequence[str],
    metadata: Optional[Dict[str, object]] = None,
    refresh_targets: Sequence[str] = (),
    event_type: str = "invalidate",
    refresh_mode: str = "async",
) -> Dict[str, object]:
    return get_refresh_fabric().publish(
        RefreshFabricEvent(
            source=source,
            action=action,
            summary=summary,
            domains=tuple(domains),
            metadata=dict(metadata or {}),
            refresh_targets=tuple(refresh_targets),
            event_type=event_type,
            refresh_mode=refresh_mode,
        )
    )