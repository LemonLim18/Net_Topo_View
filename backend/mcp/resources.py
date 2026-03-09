from __future__ import annotations

import json
from typing import Any, Dict, Optional

try:
    from llm import network_tools
    from rag.context_bundle import ContextBundleBuilder
except ImportError:  # pragma: no cover - package import path
    from ..llm import network_tools
    from ..rag.context_bundle import ContextBundleBuilder

try:
    from .server import db, mcp
except ImportError:  # pragma: no cover - direct test/runtime fallback
    from mcp.server import db, mcp


def _ensure_mcp_bindings() -> None:
    network_tools.set_db_manager(db)


def _json_resource(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


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