from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import mcp.types as mcp_types
except Exception:  # pragma: no cover - test fallback without external MCP package
    mcp_types = None

try:
    from llm.context_contract import (
        CONTEXT_DOMAIN_DEPLOYMENT,
        CONTEXT_DOMAIN_INCIDENTS,
        CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
        CONTEXT_DOMAIN_TELEMETRY,
    )
    from llm import network_tools
    from rag.context_bundle import ContextBundleBuilder
except ImportError:  # pragma: no cover - package import path
    from ..llm.context_contract import (
        CONTEXT_DOMAIN_DEPLOYMENT,
        CONTEXT_DOMAIN_INCIDENTS,
        CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
        CONTEXT_DOMAIN_TELEMETRY,
    )
    from ..llm import network_tools
    from ..rag.context_bundle import ContextBundleBuilder

try:
    from .server import db, mcp
except ImportError:  # pragma: no cover - direct test/runtime fallback
    from mcp.server import db, mcp


logger = logging.getLogger("mcp.resources")

_DEFAULT_EVENT_LIMIT = 20
_MAX_EVENT_LIMIT = 100
_SUBSCRIPTION_POLL_SECONDS = 1.0
_EVENT_STREAM_URIS = {
    "topology-change": "network://events/topology-change",
    "device-state": "network://events/device-state",
    "incident-state": "network://events/incident-state",
    "deployment-state": "network://events/deployment-state",
    "telemetry-freshness": "network://events/telemetry-freshness",
}
_EVENT_STREAM_DOMAINS = {
    "topology-change": {CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY},
    "device-state": {CONTEXT_DOMAIN_LIVE_TOPOLOGY},
    "incident-state": {CONTEXT_DOMAIN_INCIDENTS},
    "deployment-state": {CONTEXT_DOMAIN_DEPLOYMENT},
    "telemetry-freshness": {CONTEXT_DOMAIN_TELEMETRY},
}
_SUBSCRIPTIONS: Dict[str, Dict[int, Dict[str, Any]]] = {}
_SUBSCRIPTION_LOCK = threading.Lock()
_WATCHER_STARTED = False
_LAST_STREAM_EVENT_IDS: Dict[str, Optional[str]] = {
    stream: None for stream in _EVENT_STREAM_URIS
}
_LAST_TELEMETRY_WARNING_FINGERPRINT: Optional[str] = None


def _ensure_mcp_bindings() -> None:
    network_tools.set_db_manager(db)


def _json_resource(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tool_payload(tool_func, *args, **kwargs) -> Dict[str, Any]:
    _ensure_mcp_bindings()
    try:
        raw = tool_func(*args, **kwargs)
    except Exception as exc:
        return {"error": str(exc)}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"message": raw}
    if isinstance(raw, dict):
        return raw
    return {"value": raw}


def _bundle_builder() -> ContextBundleBuilder:
    _ensure_mcp_bindings()
    return ContextBundleBuilder(db_manager=db)


def _build_live_topology_payload() -> Dict[str, Any]:
    _ensure_mcp_bindings()
    topo = db.get_topology()
    nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}
    return {
        "topology": topo,
        "total_devices": len(nodes),
        "source_used": "crawler_cache",
        "source_metadata": network_tools._topology_source_metadata(topo or {}),
    }


def _build_inventory_payload() -> Dict[str, Any]:
    return _tool_payload(network_tools.list_inventory_devices, limit=200)


def _build_planned_topology_payload() -> Dict[str, Any]:
    return _tool_payload(network_tools.get_planned_topology)


def _build_telemetry_payload() -> Dict[str, Any]:
    try:
        builder = _bundle_builder()
        inventory = builder._fetch_inventory()
        device_ips = builder._resolve_device_ips(inventory.devices)
        telemetry = builder._fetch_telemetry(device_ips)
    except Exception as exc:
        return {"metrics": [], "total": 0, "error": str(exc)}
    return {
        "metrics": telemetry.metrics,
        "total": len(telemetry.metrics),
        "source_used": telemetry.source,
        "query_range": telemetry.query_range,
        "policy_name": telemetry.policy_name,
        "retrieval_reason": telemetry.retrieval_reason,
        "sample_strategy": telemetry.sample_strategy,
        "aggregate_every": telemetry.aggregate_every,
        "anomaly_baselines": telemetry.anomaly_baselines,
        "change_markers": telemetry.change_markers,
        "source_metadata": telemetry.freshness.to_dict(),
    }


def _build_config_snapshots_payload() -> Dict[str, Any]:
    try:
        builder = _bundle_builder()
        config_history = builder._fetch_config_history()
    except Exception as exc:
        return {"snapshots": [], "devices_with_snapshots": 0, "devices_missing_snapshots": [], "error": str(exc)}
    return {
        "snapshots": config_history.snapshots,
        "latest_snapshot_at": config_history.latest_snapshot_at,
        "devices_with_snapshots": config_history.devices_with_snapshots,
        "devices_missing_snapshots": config_history.devices_missing_snapshots,
        "source_used": config_history.source,
        "source_metadata": config_history.freshness.to_dict(),
    }


def _build_device_config_payload(hostname: str) -> Dict[str, Any]:
    return _tool_payload(network_tools.get_device_running_config, hostname)


def _build_incidents_payload() -> Dict[str, Any]:
    return _tool_payload(network_tools.get_recent_incidents, limit=20)


def _build_drift_payload() -> Dict[str, Any]:
    return _tool_payload(network_tools.get_recent_drift_events, limit=20)


def _build_change_jobs_payload() -> Dict[str, Any]:
    try:
        builder = _bundle_builder()
        deployment = builder._fetch_deployment_status()
    except Exception as exc:
        return {"intents": [], "change_requests": [], "jobs": [], "error": str(exc)}
    return {
        "intents": deployment.intents,
        "change_requests": deployment.change_requests,
        "jobs": deployment.jobs,
        "source_used": deployment.source,
        "source_metadata": deployment.freshness.to_dict(),
    }


def _build_context_health_payload() -> Dict[str, Any]:
    health = _tool_payload(network_tools.get_context_health)
    sync = _tool_payload(network_tools.get_last_sync_status)
    return {
        "context_health": health,
        "last_sync_status": sync,
    }


def _build_semantics_payload() -> Dict[str, Any]:
    return _tool_payload(network_tools.get_canonical_semantics_map)


def _build_canonical_device_state_payload() -> Dict[str, Any]:
    return _tool_payload(network_tools.compare_planned_vs_live)


def _build_digital_twin_payload(hostname: Optional[str] = None) -> Dict[str, Any]:
    if hostname:
        return _tool_payload(network_tools.get_digital_twin, hostname=hostname)
    return _tool_payload(network_tools.get_digital_twin)


def _normalize_event_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = _DEFAULT_EVENT_LIMIT
    return max(1, min(value, _MAX_EVENT_LIMIT))


def _fetch_context_events(limit: int = _MAX_EVENT_LIMIT) -> list[Dict[str, Any]]:
    _ensure_mcp_bindings()
    if hasattr(db, "get_context_event_log"):
        try:
            return db.get_context_event_log(limit=_normalize_event_limit(limit))
        except Exception as exc:
            logger.debug("Direct context event log lookup failed: %s", exc)
    payload = _tool_payload(network_tools.get_context_event_log, limit=_normalize_event_limit(limit))
    events = payload.get("events") if isinstance(payload, dict) else None
    return events if isinstance(events, list) else []


def _stream_name_for_uri(uri: str) -> Optional[str]:
    for stream, resource_uri in _EVENT_STREAM_URIS.items():
        if uri == resource_uri:
            return stream
    return None


def _build_event_base(stream: str, publication: Dict[str, Any]) -> Dict[str, Any]:
    metadata = publication.get("metadata") or {}
    return {
        "stream": stream,
        "event_id": publication.get("event_id"),
        "timestamp": publication.get("created_at") or publication.get("timestamp"),
        "source": publication.get("source"),
        "action": publication.get("action"),
        "summary": publication.get("summary"),
        "event_type": publication.get("event_type"),
        "domains": publication.get("domains", []),
        "versions": publication.get("versions", {}),
        "metadata": metadata,
    }


def _publication_matches_stream(stream: str, publication: Dict[str, Any]) -> bool:
    domains = set(publication.get("domains") or [])
    metadata = publication.get("metadata") or {}
    source = str(publication.get("source") or "")

    if stream == "topology-change":
        return bool(domains & _EVENT_STREAM_DOMAINS[stream])

    if stream == "device-state":
        if metadata.get("device_state_changes"):
            return True
        if source == "onboarding_poller":
            return True
        return CONTEXT_DOMAIN_LIVE_TOPOLOGY in domains and any(
            key in metadata for key in ("hostname", "device_id", "mgmt_ip", "status")
        )

    if stream == "incident-state":
        return CONTEXT_DOMAIN_INCIDENTS in domains

    if stream == "deployment-state":
        return CONTEXT_DOMAIN_DEPLOYMENT in domains

    if stream == "telemetry-freshness":
        return CONTEXT_DOMAIN_TELEMETRY in domains

    return False


def _publication_to_stream_events(stream: str, publication: Dict[str, Any]) -> list[Dict[str, Any]]:
    metadata = publication.get("metadata") or {}
    base_event = _build_event_base(stream, publication)

    if stream == "device-state":
        state_changes = metadata.get("device_state_changes")
        if isinstance(state_changes, list) and state_changes:
            expanded = []
            for index, change in enumerate(state_changes):
                if not isinstance(change, dict):
                    continue
                event = dict(base_event)
                event["event_id"] = f"{base_event['event_id']}::{index}"
                event.update(change)
                expanded.append(event)
            if expanded:
                return expanded
        event = dict(base_event)
        event.update(
            {
                "hostname": metadata.get("hostname"),
                "device_id": metadata.get("device_id"),
                "management_ip": metadata.get("mgmt_ip") or metadata.get("management_ip"),
                "status": metadata.get("status"),
            }
        )
        return [event]

    if stream == "topology-change":
        event = dict(base_event)
        event["topology_change"] = metadata.get("topology_change", {})
        return [event]

    if stream == "incident-state":
        event = dict(base_event)
        event.update(
            {
                "incident_id": metadata.get("incident_id"),
                "severity": metadata.get("severity"),
                "status": metadata.get("status"),
                "affected_devices": metadata.get("affected_devices", []),
            }
        )
        return [event]

    if stream == "deployment-state":
        event = dict(base_event)
        event.update(
            {
                "job_id": metadata.get("job_id"),
                "request_id": metadata.get("request_id"),
                "hostname": metadata.get("hostname"),
                "status": metadata.get("status"),
                "stage": metadata.get("stage"),
                "error": metadata.get("error"),
            }
        )
        return [event]

    if stream == "telemetry-freshness":
        event = dict(base_event)
        event.update(
            {
                "metric": metadata.get("metric"),
                "device": metadata.get("device"),
                "severity": metadata.get("severity"),
                "threshold": metadata.get("threshold"),
                "value": metadata.get("value"),
            }
        )
        return [event]

    return [base_event]


def _build_current_telemetry_warning() -> tuple[list[Dict[str, Any]], Optional[str]]:
    telemetry = _build_telemetry_payload()
    freshness = telemetry.get("source_metadata", {}) if isinstance(telemetry, dict) else {}
    freshness_class = freshness.get("freshness_class")
    if freshness_class not in {"stale", "missing"}:
        return [], None

    captured_at = freshness.get("captured_at") or freshness.get("ingested_at") or _utc_now()
    fingerprint = "|".join(
        [
            str(freshness_class),
            str(captured_at),
            str(freshness.get("source") or "unknown"),
        ]
    )
    return (
        [
            {
                "stream": "telemetry-freshness",
                "event_id": f"telemetry-freshness::{fingerprint}",
                "timestamp": captured_at,
                "source": freshness.get("source") or "unknown",
                "action": "telemetry_freshness_warning",
                "summary": (
                    f"Telemetry is {freshness_class}; source={freshness.get('source') or 'unknown'}; "
                    f"captured_at={captured_at}"
                ),
                "event_type": "warning",
                "domains": [CONTEXT_DOMAIN_TELEMETRY],
                "versions": {},
                "severity": "warning" if freshness_class == "stale" else "critical",
                "freshness": freshness,
                "metadata": {
                    "warning_type": "telemetry_freshness",
                    "freshness_class": freshness_class,
                },
            }
        ],
        fingerprint,
    )


def _apply_cursor(events: list[Dict[str, Any]], cursor: Optional[str]) -> list[Dict[str, Any]]:
    if not cursor:
        return events
    for index, event in enumerate(events):
        if str(event.get("event_id")) == cursor:
            return events[:index]
    return events


def _build_event_stream_payload(stream: str, cursor: Optional[str] = None, limit: int = _DEFAULT_EVENT_LIMIT) -> Dict[str, Any]:
    if stream not in _EVENT_STREAM_URIS:
        return {
            "error": f"Unknown event stream '{stream}'",
            "supported_streams": sorted(_EVENT_STREAM_URIS),
        }

    normalized_limit = _normalize_event_limit(limit)
    publications = _fetch_context_events(limit=max(normalized_limit * 5, 50))
    events: list[Dict[str, Any]] = []
    for publication in publications:
        if _publication_matches_stream(stream, publication):
            events.extend(_publication_to_stream_events(stream, publication))

    current_warnings: list[Dict[str, Any]] = []
    warning_fingerprint: Optional[str] = None
    if stream == "telemetry-freshness":
        current_warnings, warning_fingerprint = _build_current_telemetry_warning()
        existing_ids = {str(event.get("event_id")) for event in events}
        for warning_event in current_warnings:
            if str(warning_event.get("event_id")) not in existing_ids:
                events.append(warning_event)

    events.sort(
        key=lambda event: (
            str(event.get("timestamp") or ""),
            str(event.get("event_id") or ""),
        ),
        reverse=True,
    )
    latest_event_id = str(events[0].get("event_id")) if events else None
    events = _apply_cursor(events, cursor)
    returned_events = events[:normalized_limit]

    return {
        "stream": stream,
        "resource_uri": _EVENT_STREAM_URIS[stream],
        "events": returned_events,
        "returned": len(returned_events),
        "latest_event_id": latest_event_id,
        "next_cursor": returned_events[-1].get("event_id") if returned_events else cursor,
        "cursor_requested": cursor,
        "subscribe_supported": True,
        "current_warnings": current_warnings if stream == "telemetry-freshness" else [],
        "current_warning_fingerprint": warning_fingerprint if stream == "telemetry-freshness" else None,
    }


def _prime_stream_state(stream: str) -> None:
    payload = _build_event_stream_payload(stream, limit=1)
    _LAST_STREAM_EVENT_IDS[stream] = payload.get("latest_event_id")
    if stream == "telemetry-freshness":
        global _LAST_TELEMETRY_WARNING_FINGERPRINT
        _LAST_TELEMETRY_WARNING_FINGERPRINT = payload.get("current_warning_fingerprint")


def _remove_subscription(uri: str, session_id: int) -> None:
    with _SUBSCRIPTION_LOCK:
        subscribers = _SUBSCRIPTIONS.get(uri)
        if not subscribers:
            return
        subscribers.pop(session_id, None)
        if not subscribers:
            _SUBSCRIPTIONS.pop(uri, None)


def _notify_uri_subscribers(uri: str) -> None:
    with _SUBSCRIPTION_LOCK:
        subscribers = list(_SUBSCRIPTIONS.get(uri, {}).items())

    stale_session_ids = []
    for session_id, subscriber in subscribers:
        session = subscriber.get("session")
        loop = subscriber.get("loop")
        if session is None or loop is None:
            stale_session_ids.append(session_id)
            continue
        try:
            future = asyncio.run_coroutine_threadsafe(session.send_resource_updated(uri), loop)
            future.result(timeout=5)
        except Exception:
            stale_session_ids.append(session_id)

    for session_id in stale_session_ids:
        _remove_subscription(uri, session_id)


def _subscription_watch_loop() -> None:
    global _LAST_TELEMETRY_WARNING_FINGERPRINT

    while True:
        try:
            with _SUBSCRIPTION_LOCK:
                subscribed_uris = list(_SUBSCRIPTIONS)

            for uri in subscribed_uris:
                stream = _stream_name_for_uri(uri)
                if not stream:
                    continue
                payload = _build_event_stream_payload(stream, limit=1)
                latest_event_id = payload.get("latest_event_id")
                should_notify = False

                with _SUBSCRIPTION_LOCK:
                    previous_event_id = _LAST_STREAM_EVENT_IDS.get(stream)
                    if latest_event_id and latest_event_id != previous_event_id:
                        _LAST_STREAM_EVENT_IDS[stream] = latest_event_id
                        should_notify = True

                    if stream == "telemetry-freshness":
                        warning_fingerprint = payload.get("current_warning_fingerprint")
                        if warning_fingerprint != _LAST_TELEMETRY_WARNING_FINGERPRINT:
                            _LAST_TELEMETRY_WARNING_FINGERPRINT = warning_fingerprint
                            should_notify = True

                if should_notify:
                    _notify_uri_subscribers(uri)
        except Exception as exc:
            logger.debug("MCP subscription watcher iteration failed: %s", exc)

        time.sleep(_SUBSCRIPTION_POLL_SECONDS)


def _ensure_subscription_watcher() -> None:
    global _WATCHER_STARTED

    with _SUBSCRIPTION_LOCK:
        if _WATCHER_STARTED:
            return
        watcher = threading.Thread(
            target=_subscription_watch_loop,
            name="mcp-resource-subscription-watcher",
            daemon=True,
        )
        watcher.start()
        _WATCHER_STARTED = True


def _register_subscription_handlers() -> None:
    if mcp_types is None or not hasattr(mcp, "_mcp_server"):
        return

    lowlevel_server = mcp._mcp_server
    if getattr(lowlevel_server, "_nettopoview_subscriptions_registered", False):
        return

    async def _handle_subscribe(req: Any):
        uri = str(req.params.uri)
        stream = _stream_name_for_uri(uri)
        if not stream:
            raise ValueError(f"Unsupported subscription resource: {uri}")

        context = lowlevel_server.request_context
        session = context.session
        loop = asyncio.get_running_loop()

        with _SUBSCRIPTION_LOCK:
            _SUBSCRIPTIONS.setdefault(uri, {})[id(session)] = {
                "session": session,
                "loop": loop,
            }

        _prime_stream_state(stream)
        _ensure_subscription_watcher()
        return mcp_types.ServerResult(mcp_types.EmptyResult())

    async def _handle_unsubscribe(req: Any):
        uri = str(req.params.uri)
        context = lowlevel_server.request_context
        _remove_subscription(uri, id(context.session))
        return mcp_types.ServerResult(mcp_types.EmptyResult())

    lowlevel_server.request_handlers[mcp_types.SubscribeRequest] = _handle_subscribe
    lowlevel_server.request_handlers[mcp_types.UnsubscribeRequest] = _handle_unsubscribe
    lowlevel_server._nettopoview_subscriptions_registered = True


@mcp.resource("network://topology")
def get_topology() -> str:
    """Returns the current live network topology with freshness metadata."""
    return _json_resource(_build_live_topology_payload())


@mcp.resource("network://live-topology")
def get_live_topology() -> str:
    """Returns the current live network topology with freshness metadata."""
    return _json_resource(_build_live_topology_payload())


@mcp.resource("network://devices")
def get_device_list() -> str:
    """Returns planned inventory devices from NetBox-preferred source-of-truth data."""
    return _json_resource(_build_inventory_payload())


@mcp.resource("network://inventory")
def get_inventory() -> str:
    """Returns planned inventory devices from NetBox-preferred source-of-truth data."""
    return _json_resource(_build_inventory_payload())


@mcp.resource("network://planned-topology")
def get_planned_topology() -> str:
    """Returns the planned topology from NetBox with local DB fallback and freshness metadata."""
    return _json_resource(_build_planned_topology_payload())


@mcp.resource("network://telemetry")
def get_telemetry_snapshot() -> str:
    """Returns recent telemetry snapshots and retrieval policy metadata."""
    return _json_resource(_build_telemetry_payload())


@mcp.resource("network://config-snapshots")
def get_config_snapshots() -> str:
    """Returns the latest configuration snapshot summary across devices."""
    return _json_resource(_build_config_snapshots_payload())


@mcp.resource("network://device/{hostname}/config")
def get_device_config(hostname: str) -> str:
    """Returns the latest captured running configuration snapshot for a specific device."""
    return _json_resource(_build_device_config_payload(hostname))


@mcp.resource("network://incidents")
def get_incidents() -> str:
    """Returns recent incidents with freshness metadata."""
    return _json_resource(_build_incidents_payload())


@mcp.resource("network://drift")
def get_drift() -> str:
    """Returns recent drift events with freshness metadata."""
    return _json_resource(_build_drift_payload())


@mcp.resource("network://change-jobs")
def get_change_jobs() -> str:
    """Returns deployment intents, change requests, and execution jobs."""
    return _json_resource(_build_change_jobs_payload())


@mcp.resource("network://context-health")
def get_context_health_resource() -> str:
    """Returns context versioning health and latest sync status."""
    return _json_resource(_build_context_health_payload())


@mcp.resource("network://semantics")
def get_canonical_semantics_resource() -> str:
    """Returns the canonical planned-vs-live semantics contract shared by tools, prompt bundles, and REST surfaces."""
    return _json_resource(_build_semantics_payload())


@mcp.resource("network://canonical-device-state")
def get_canonical_device_state_resource() -> str:
    """Returns the canonical device-state matrix for planned-vs-live comparisons."""
    return _json_resource(_build_canonical_device_state_payload())


@mcp.resource("network://digital-twin")
def get_digital_twin_resource() -> str:
    """Returns the formal digital-twin graph snapshot across planned/live/config/telemetry/incident/drift state."""
    return _json_resource(_build_digital_twin_payload())


@mcp.resource("network://digital-twin/device/{hostname}")
def get_device_digital_twin_resource(hostname: str) -> str:
    """Returns the formal digital-twin graph snapshot scoped to a single device."""
    return _json_resource(_build_digital_twin_payload(hostname))


@mcp.resource("network://events/topology-change")
def get_topology_change_events() -> str:
    """Returns recent topology-change events for continuous MCP consumption."""
    return _json_resource(_build_event_stream_payload("topology-change"))


@mcp.resource("network://events/device-state")
def get_device_state_events() -> str:
    """Returns recent device-state events derived from authoritative live-topology changes."""
    return _json_resource(_build_event_stream_payload("device-state"))


@mcp.resource("network://events/incident-state")
def get_incident_state_events() -> str:
    """Returns recent incident-state events for higher-level incident consumers."""
    return _json_resource(_build_event_stream_payload("incident-state"))


@mcp.resource("network://events/deployment-state")
def get_deployment_state_events() -> str:
    """Returns recent deployment-state events for higher-level orchestration consumers."""
    return _json_resource(_build_event_stream_payload("deployment-state"))


@mcp.resource("network://events/telemetry-freshness")
def get_telemetry_freshness_events() -> str:
    """Returns telemetry freshness warnings and recent telemetry-triggered events."""
    return _json_resource(_build_event_stream_payload("telemetry-freshness"))


@mcp.resource("network://events/{stream}/cursor/{cursor}")
def get_event_stream_from_cursor(stream: str, cursor: str) -> str:
    """Returns only events newer than the provided cursor for resumable event consumption."""
    return _json_resource(_build_event_stream_payload(stream, cursor=cursor))


_register_subscription_handlers()