"""
Network tools — topology and config tools for the LLM agent.

These are the functions that Gemini can call to interact with the
network controller.  They are registered with the ``ToolRegistry``.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_KNOWLEDGE,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    compute_freshness,
)
from llm.context_state import get_context_state_manager


logger = logging.getLogger("llm.network_tools")


# -----------------------------------------------------------------------
# Global reference to the DB manager (set by the agent at startup)
# -----------------------------------------------------------------------
_db_manager = None


def set_db_manager(db) -> None:
    """Called once at startup to inject the DatabaseManager."""
    global _db_manager
    _db_manager = db


def _topology_source_metadata(topo: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = topo.get("timestamp") if isinstance(topo, dict) else None
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        captured_at=timestamp,
        ingested_at=timestamp,
        source="crawler_cache",
        scope="live_topology",
        confidence=0.9 if topo.get("nodes") else 0.0,
    )
    return metadata.to_dict()


def _inventory_source_metadata(source: str, captured_at: Optional[str], confidence: float = 0.85) -> Dict[str, Any]:
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_INVENTORY,
        captured_at=captured_at,
        ingested_at=captured_at,
        source=source,
        scope="planned_inventory",
        confidence=confidence,
    )
    return metadata.to_dict()


def _planned_topology_source_metadata(source: str, captured_at: Optional[str], confidence: float = 0.75) -> Dict[str, Any]:
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
        captured_at=captured_at,
        ingested_at=captured_at,
        source=source,
        scope="planned_topology",
        confidence=confidence,
    )
    return metadata.to_dict()


def _config_source_metadata(source: str, captured_at: Optional[str], confidence: float = 0.9) -> Dict[str, Any]:
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_CONFIG,
        captured_at=captured_at,
        ingested_at=captured_at,
        source=source,
        scope="device_configs",
        confidence=confidence,
    )
    return metadata.to_dict()


def _incident_source_metadata(source: str, captured_at: Optional[str], confidence: float = 0.9, scope: str = "incident_state") -> Dict[str, Any]:
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_INCIDENTS,
        captured_at=captured_at,
        ingested_at=captured_at,
        source=source,
        scope=scope,
        confidence=confidence,
    )
    return metadata.to_dict()


def _drift_source_metadata(source: str, captured_at: Optional[str], confidence: float = 0.9) -> Dict[str, Any]:
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_DRIFT,
        captured_at=captured_at,
        ingested_at=captured_at,
        source=source,
        scope="drift_state",
        confidence=confidence,
    )
    return metadata.to_dict()


def _knowledge_source_metadata(source: str, captured_at: Optional[str], confidence: float = 0.95) -> Dict[str, Any]:
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_KNOWLEDGE,
        captured_at=captured_at,
        ingested_at=captured_at,
        source=source,
        scope="knowledge_documents",
        confidence=confidence,
    )
    return metadata.to_dict()


def _normalize_inventory_device(record: Any, source: str) -> Dict[str, Any]:
    if isinstance(record, dict):
        hostname = record.get("hostname") or record.get("name") or "unknown"
        management_ip = record.get("management_ip") or record.get("mgmt_ip")
        vendor = record.get("vendor") or record.get("platform") or "unknown"
        model = record.get("model")
        status = record.get("status") or "unknown"
        role = record.get("role")
        updated_at = record.get("updated_at") or record.get("last_updated")
    else:
        hostname = str(getattr(record, "name", "unknown"))
        primary_ip = getattr(record, "primary_ip4", None) or getattr(record, "primary_ip", None)
        management_ip = str(primary_ip).split("/")[0] if primary_ip else None
        platform = getattr(record, "platform", None)
        vendor = str(getattr(platform, "slug", platform or "unknown"))
        device_type = getattr(record, "device_type", None)
        model = str(getattr(device_type, "model", device_type or "")) or None
        raw_status = getattr(record, "status", None)
        status = str(getattr(raw_status, "value", raw_status or "unknown"))
        raw_role = getattr(record, "role", None)
        role = str(getattr(raw_role, "slug", raw_role or "")) or None
        updated_at = str(getattr(record, "last_updated", "") or "") or None

    return {
        "hostname": hostname,
        "management_ip": management_ip,
        "vendor": vendor,
        "model": model,
        "status": status,
        "role": role,
        "source": source,
        "updated_at": updated_at,
    }


def _inventory_records_from_netbox() -> List[Dict[str, Any]]:
    try:
        from netbox.client import NetBoxClient

        client = NetBoxClient.get_client()
        if not client or not getattr(client, "api", None):
            return []

        return [_normalize_inventory_device(device, "netbox") for device in client.get_devices()]
    except Exception:
        return []


def _inventory_records_from_local_db() -> List[Dict[str, Any]]:
    if not _db_manager:
        return []

    devices = []
    for device in _db_manager.get_all_devices():
        devices.append(
            {
                "hostname": device.hostname,
                "management_ip": device.management_ip,
                "vendor": device.vendor,
                "model": device.model,
                "status": device.status,
                "role": device.role,
                "source": device.source or "local_db",
                "updated_at": device.updated_at or device.created_at or device.last_polled,
            }
        )
    return devices


def _find_inventory_record(records: List[Dict[str, Any]], hostname: str) -> Optional[Dict[str, Any]]:
    for record in records:
        if str(record.get("hostname", "")).lower() == hostname.lower():
            return record
    return None


def _search_inventory_records(records: List[Dict[str, Any]], query: str, limit: int) -> List[Dict[str, Any]]:
    query_lower = query.lower().strip()
    matches = []
    for record in records:
        haystack = " ".join(
            str(record.get(field) or "")
            for field in ("hostname", "management_ip", "vendor", "model", "role")
        ).lower()
        if query_lower in haystack:
            matches.append(record)

    return sorted(matches, key=lambda item: str(item.get("hostname") or ""))[: max(limit, 1)]


def _get_inventory_records() -> tuple[List[Dict[str, Any]], str]:
    netbox_records = _inventory_records_from_netbox()
    if netbox_records:
        return netbox_records, "netbox"
    return _inventory_records_from_local_db(), "local_db"


def _planned_topology_links_from_nodes(nodes: Dict[str, Any]) -> List[Dict[str, Any]]:
    links: List[Dict[str, Any]] = []
    seen = set()
    for src_hostname, node in nodes.items():
        if not isinstance(node, dict):
            continue
        for src_interface, edge in (node.get("edgeLink") or {}).items():
            if not isinstance(edge, dict):
                continue
            dst_hostname = edge.get("remote_node")
            dst_interface = edge.get("remote_interface")
            if not dst_hostname:
                continue
            key = tuple(sorted((str(src_hostname), str(dst_hostname)))) + tuple(sorted((str(src_interface), str(dst_interface or "?"))))
            if key in seen:
                continue
            seen.add(key)
            links.append(
                {
                    "src_hostname": src_hostname,
                    "src_interface": src_interface,
                    "dst_hostname": dst_hostname,
                    "dst_interface": dst_interface,
                    "discovery_protocol": "netbox",
                    "updated_at": None,
                }
            )
    return sorted(links, key=lambda link: (str(link.get("src_hostname") or ""), str(link.get("dst_hostname") or ""), str(link.get("src_interface") or "")))


def _get_planned_topology_data() -> Dict[str, Any]:
    netbox_timestamp = None
    if _db_manager:
        try:
            from netbox.sync_topology import TopologySyncer

            planned = TopologySyncer(_db_manager).get_planned_topology()
            nodes = planned.get("nodes", {}) if isinstance(planned, dict) else {}
            if nodes and not planned.get("error") and planned.get("status") != "no_connection":
                netbox_timestamp = planned.get("timestamp")
                return {
                    "topology": planned,
                    "links": _planned_topology_links_from_nodes(nodes),
                    "source": "netbox",
                    "captured_at": netbox_timestamp,
                }
        except Exception:
            pass

    if not _db_manager:
        return {
            "topology": {"nodes": {}},
            "links": [],
            "source": "local_db",
            "captured_at": None,
        }

    with _db_manager._connect() as conn:
        links = conn.execute(
            "SELECT sd.hostname AS src_hostname, si.name AS src_interface, dd.hostname AS dst_hostname, di.name AS dst_interface, l.discovery_protocol, l.updated_at "
            "FROM links l "
            "LEFT JOIN devices sd ON sd.id = l.src_device_id "
            "LEFT JOIN devices dd ON dd.id = l.dst_device_id "
            "LEFT JOIN interfaces si ON si.id = l.src_interface_id "
            "LEFT JOIN interfaces di ON di.id = l.dst_interface_id "
            "ORDER BY sd.hostname, dd.hostname"
        ).fetchall()

    planned_links = [dict(link) for link in links]
    captured_at = max((link.get("updated_at") for link in planned_links if link.get("updated_at")), default=None)
    return {
        "topology": {"nodes": {}},
        "links": planned_links,
        "source": "local_db",
        "captured_at": captured_at,
    }


# -----------------------------------------------------------------------
# Topology Tools
# -----------------------------------------------------------------------

def get_topology_summary() -> str:
    """
    Get a summary of the current network topology including all devices,
    their status, management IPs, and connections.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    topo = _db_manager.get_topology()
    if not topo or not topo.get("nodes"):
        return json.dumps({"message": "No topology data. Run a crawl first."})

    nodes = topo.get("nodes", {})
    summary = {
        "timestamp": topo.get("timestamp", "unknown"),
        "source_metadata": _topology_source_metadata(topo),
        "total_devices": len(nodes),
        "devices": [],
    }

    for hostname, data in nodes.items():
        if not isinstance(data, dict):
            continue
        device = {
            "hostname": hostname,
            "status": "down" if data.get("down") else "up",
            "mgmt_ip": data.get("mgmt_ip", "N/A"),
            "neighbors": data.get("connectedNode", []),
            "neighbor_count": len(data.get("connectedNode", [])),
        }
        summary["devices"].append(device)

    return json.dumps(summary, ensure_ascii=False)


def get_device_details(hostname: str) -> str:
    """
    Get detailed information about a specific network device including
    its interfaces, IP addresses, LLDP neighbors, and edge links.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    topo = _db_manager.get_topology()
    if not topo or not topo.get("nodes"):
        return json.dumps({"error": "No topology data available"})

    node = topo["nodes"].get(hostname)
    if not node:
        # Try case-insensitive search
        for name, data in topo["nodes"].items():
            if name.lower() == hostname.lower():
                node = data
                hostname = name
                break

    if not node:
        available = list(topo["nodes"].keys())
        return json.dumps({
            "error": f"Device '{hostname}' not found",
            "available_devices": available,
        })

    ifaces = node.get("interfaceList", [{}])
    iface_dict = ifaces[0] if ifaces else {}

    details = {
        "hostname": hostname,
        "source_metadata": _topology_source_metadata(topo),
        "status": "down" if node.get("down") else "up",
        "mgmt_ip": node.get("mgmt_ip", "N/A"),
        "interfaces": iface_dict if isinstance(iface_dict, dict) else {},
        "neighbors": node.get("connectedNode", []),
        "edge_links": node.get("edgeLink", {}),
    }

    return json.dumps(details, ensure_ascii=False)


def list_all_devices() -> str:
    """
    List all discovered devices with their hostnames, status,
    and management IPs.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    topo = _db_manager.get_topology()
    if not topo or not topo.get("nodes"):
        return json.dumps({"devices": [], "message": "No devices discovered"})

    devices = []
    for hostname, data in topo.get("nodes", {}).items():
        if not isinstance(data, dict):
            continue
        devices.append({
            "hostname": hostname,
            "status": "down" if data.get("down") else "up",
            "mgmt_ip": data.get("mgmt_ip", "N/A"),
        })

    return json.dumps({
        "devices": devices,
        "total": len(devices),
        "source_metadata": _topology_source_metadata(topo),
    })


def get_context_health() -> str:
    """
    Get current context versions and the latest invalidation/refresh events.
    """
    return json.dumps(get_context_state_manager().snapshot(), ensure_ascii=False)


def get_recent_incidents(limit: int = 10, status: str = "") -> str:
    """
    Get recent incidents and alerts with freshness metadata from the incident store.
    """
    try:
        from incidents.store import IncidentStore

        rows = IncidentStore().list_all(status=status or None, limit=limit)
        latest_updated = max(
            ((row.get("updated_at") or row.get("created_at")) for row in rows if row.get("updated_at") or row.get("created_at")),
            default=None,
        )
        return json.dumps(
            {
                "incidents": rows,
                "total": len(rows),
                "source_metadata": _incident_source_metadata("incident_store", latest_updated),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_recent_drift_events(limit: int = 10, hostname: str = "") -> str:
    """
    Get recent drift events with freshness metadata.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        rows = _db_manager.get_drift_events(limit=limit)
        events = []
        for row in rows:
            if hostname and str(getattr(row, "hostname", "")).lower() != hostname.lower():
                continue
            events.append(
                {
                    "device_id": row.device_id,
                    "hostname": row.hostname,
                    "has_drift": row.has_drift,
                    "severity": row.severity,
                    "timestamp": row.timestamp,
                }
            )

        latest_timestamp = max((event.get("timestamp") for event in events if event.get("timestamp")), default=None)
        return json.dumps(
            {
                "events": events,
                "total": len(events),
                "source_metadata": _drift_source_metadata("drift_store", latest_timestamp, confidence=0.9 if events else 0.0),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_knowledge_base_status(limit: int = 10) -> str:
    """
    Get knowledge base document summaries and freshness metadata.
    """
    try:
        from llm.vector_store import KnowledgeVectorStore

        store = KnowledgeVectorStore(embedder=None)
        if not store.available:
            return json.dumps(
                {
                    "documents": [],
                    "total_documents": 0,
                    "total_chunks": 0,
                    "source_metadata": _knowledge_source_metadata("vector_store", None, confidence=0.0),
                },
                ensure_ascii=False,
            )

        documents = store.list_documents()[: max(limit, 1)]
        latest_ingested = max((doc.get("ingested_at") for doc in documents if doc.get("ingested_at")), default=None)
        return json.dumps(
            {
                "documents": documents,
                "total_documents": len(documents),
                "total_chunks": store.count(),
                "source_metadata": _knowledge_source_metadata("vector_store", latest_ingested, confidence=0.95 if documents else 0.0),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_inventory_devices(vendor: str = "", status: str = "", limit: int = 50) -> str:
    """
    List devices from source-of-truth inventory with NetBox preferred and local DB fallback.
    """
    records, source = _get_inventory_records()
    filtered = []
    vendor_filter = vendor.lower().strip()
    status_filter = status.lower().strip()

    for record in records:
        record_vendor = str(record.get("vendor") or "").lower()
        record_status = str(record.get("status") or "").lower()
        if vendor_filter and vendor_filter not in record_vendor:
            continue
        if status_filter and status_filter != record_status:
            continue
        filtered.append(record)

    filtered = sorted(filtered, key=lambda item: str(item.get("hostname") or ""))[: max(limit, 1)]
    captured_at = max((record.get("updated_at") for record in filtered if record.get("updated_at")), default=None)
    return json.dumps(
        {
            "devices": filtered,
            "total": len(filtered),
            "source_used": source,
            "captured_at": captured_at,
            "source_metadata": _inventory_source_metadata(source, captured_at),
        },
        ensure_ascii=False,
    )


def get_inventory_device(hostname: str) -> str:
    """
    Retrieve a specific device from source-of-truth inventory.
    """
    netbox_records = _inventory_records_from_netbox()
    record = _find_inventory_record(netbox_records, hostname)
    if record is not None:
        return json.dumps(
            {
                "device": record,
                "source_used": "netbox",
                "captured_at": record.get("updated_at"),
                "source_metadata": _inventory_source_metadata("netbox", record.get("updated_at")),
            },
            ensure_ascii=False,
        )

    local_records = _inventory_records_from_local_db()
    record = _find_inventory_record(local_records, hostname)
    if record is not None:
        return json.dumps(
            {
                "device": record,
                "source_used": "local_db",
                "captured_at": record.get("updated_at"),
                "source_metadata": _inventory_source_metadata("local_db", record.get("updated_at")),
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "error": f"Inventory device '{hostname}' not found",
            "source_used": "local_db" if local_records else "netbox",
            "captured_at": None,
            "source_metadata": _inventory_source_metadata("netbox" if netbox_records else "local_db", None, confidence=0.0),
        },
        ensure_ascii=False,
    )


def search_inventory_devices(query: str, limit: int = 20) -> str:
    """
    Search source-of-truth inventory by hostname, management IP, vendor, model, or role.
    """
    netbox_matches = _search_inventory_records(_inventory_records_from_netbox(), query, limit)
    if netbox_matches:
        source = "netbox"
        matches = netbox_matches
    else:
        source = "local_db"
        matches = _search_inventory_records(_inventory_records_from_local_db(), query, limit)

    captured_at = max((record.get("updated_at") for record in matches if record.get("updated_at")), default=None)
    return json.dumps(
        {
            "matches": matches,
            "total": len(matches),
            "source_used": source,
            "captured_at": captured_at,
            "source_metadata": _inventory_source_metadata(source, captured_at),
        },
        ensure_ascii=False,
    )


def get_planned_topology() -> str:
    """
    Get planned topology from NetBox when available, with local DB fallback.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    planned = _get_planned_topology_data()
    planned_links = planned["links"]
    source = planned["source"]
    captured_at = planned["captured_at"]
    return json.dumps(
        {
            "topology": planned["topology"],
            "links": planned_links,
            "total": len(planned_links),
            "source_used": source,
            "captured_at": captured_at,
            "source_metadata": _planned_topology_source_metadata(source, captured_at, confidence=0.85 if source == "netbox" else 0.75),
        },
        ensure_ascii=False,
    )


def compare_planned_vs_live(hostname: str = "") -> str:
    """
    Compare planned inventory devices with live discovered topology, optionally scoped to a hostname.
    """
    inventory_records, inventory_source = _get_inventory_records()
    topo = _db_manager.get_topology() if _db_manager else {}
    live_nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}

    inventory_names = {str(record.get("hostname")): record for record in inventory_records}
    live_names = {str(name): data for name, data in live_nodes.items() if isinstance(data, dict)}

    if hostname:
        inventory_names = {name: record for name, record in inventory_names.items() if name.lower() == hostname.lower()}
        live_names = {name: record for name, record in live_names.items() if name.lower() == hostname.lower()}

    planned_not_live = sorted(name for name in inventory_names if name not in live_names)
    live_not_planned = sorted(name for name in live_names if name not in inventory_names)
    matching = []
    for name in sorted(set(inventory_names) & set(live_names)):
        planned = inventory_names[name]
        live = live_names[name]
        matching.append(
            {
                "hostname": name,
                "planned_management_ip": planned.get("management_ip"),
                "live_management_ip": live.get("mgmt_ip"),
                "status_match": str(planned.get("status") or "").lower() == ("down" if live.get("down") else "active"),
            }
        )

    inventory_captured_at = max((record.get("updated_at") for record in inventory_records if record.get("updated_at")), default=None)
    return json.dumps(
        {
            "matching_devices": matching,
            "planned_only": planned_not_live,
            "live_only": live_not_planned,
            "source_used": inventory_source,
            "captured_at": inventory_captured_at,
            "inventory_source_metadata": _inventory_source_metadata(inventory_source, inventory_captured_at),
            "live_source_metadata": _topology_source_metadata(topo),
        },
        ensure_ascii=False,
    )


def find_path(source: str, destination: str) -> str:
    """
    Find the network path between two devices by tracing
    neighbor adjacencies in the topology.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    topo = _db_manager.get_topology()
    if not topo or not topo.get("nodes"):
        return json.dumps({"error": "No topology data"})

    nodes = topo["nodes"]

    # Normalize hostnames (case-insensitive lookup)
    src = dst = None
    for name in nodes:
        if name.lower() == source.lower():
            src = name
        if name.lower() == destination.lower():
            dst = name

    if not src:
        return json.dumps({"error": f"Source '{source}' not found"})
    if not dst:
        return json.dumps({"error": f"Destination '{destination}' not found"})

    # BFS to find shortest path
    from collections import deque
    visited = {src}
    queue = deque([(src, [src])])

    while queue:
        current, path = queue.popleft()
        if current == dst:
            return json.dumps({
                "path": path,
                "hops": len(path) - 1,
                "source": src,
                "destination": dst,
            })

        node_data = nodes.get(current, {})
        for neighbor in node_data.get("connectedNode", []):
            if neighbor not in visited and neighbor in nodes:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return json.dumps({
        "error": f"No path found between {src} and {dst}",
        "source": src,
        "destination": dst,
    })


# -----------------------------------------------------------------------
# Configuration Tools (M3 integration)
# -----------------------------------------------------------------------

def generate_config(vendor: str, feature: str, params: str) -> str:
    """
    Generate device configuration commands using the M3 template engine.

    Args:
        vendor:  Device vendor (e.g. 'cisco_ios', 'huawei_vrp', 'juniper_junos')
        feature: Configuration feature (e.g. 'vlan', 'ospf', 'interface', 'acl')
        params:  JSON string of template parameters
    """
    try:
        from config_engine.renderer import TemplateRenderer

        renderer = TemplateRenderer()

        # Parse params from JSON string (LLM sends strings)
        if isinstance(params, str):
            parsed_params = json.loads(params)
        else:
            parsed_params = params

        config = renderer.render(vendor, feature, parsed_params)
        return json.dumps({
            "vendor": vendor,
            "feature": feature,
            "config": config,
            "line_count": len(config.strip().splitlines()),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def generate_rollback(vendor: str, feature: str, params: str) -> str:
    """
    Generate rollback/undo commands for a configuration change.

    Args:
        vendor:  Device vendor
        feature: Configuration feature
        params:  JSON string of template parameters
    """
    try:
        from config_engine.renderer import TemplateRenderer

        renderer = TemplateRenderer()

        if isinstance(params, str):
            parsed_params = json.loads(params)
        else:
            parsed_params = params

        config = renderer.render_undo(vendor, feature, parsed_params)
        return json.dumps({
            "vendor": vendor,
            "feature": feature,
            "rollback_config": config,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_templates(vendor: str = "") -> str:
    """
    List all available configuration templates, optionally filtered
    by vendor (e.g. 'cisco_ios').
    """
    try:
        from config_engine.renderer import TemplateRenderer

        renderer = TemplateRenderer()
        templates = renderer.list_templates(vendor=vendor if vendor else None)

        return json.dumps({
            "templates": templates,
            "total": len(templates),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_template_schema(feature: str) -> str:
    """
    Get the JSON schema (expected parameters) for a configuration template.
    Helps understand what parameters are needed for a feature.

    Args:
        feature: Template name (e.g. 'vlan', 'ospf', 'interface')
    """
    try:
        from config_engine.renderer import TemplateRenderer

        renderer = TemplateRenderer()
        schema = renderer.get_schema(feature)

        if schema:
            return json.dumps(schema, ensure_ascii=False)
        else:
            return json.dumps({"message": f"No schema found for '{feature}'"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def diff_configs(old_config: str, new_config: str) -> str:
    """
    Compare two configuration texts and show the differences
    in unified diff format.

    Args:
        old_config: The original/current configuration text
        new_config: The new/proposed configuration text
    """
    try:
        from config_engine.differ import ConfigDiffer

        differ = ConfigDiffer()
        diff_result = differ.unified_diff(old_config, new_config)

        return json.dumps({
            "diff": diff_result,
            "has_changes": bool(diff_result.strip()),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# -----------------------------------------------------------------------
# Audit / History Tools
# -----------------------------------------------------------------------

def get_audit_log(limit: int = 20) -> str:
    """
    Get the recent audit log entries showing what actions have been
    performed on the network.
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        entries = _db_manager.get_audit_log(limit=limit)
        log = []
        for entry in entries:
            log.append({
                "timestamp": entry.get("timestamp", ""),
                "user": entry.get("user_id", ""),
                "action": entry.get("action", ""),
                "target": entry.get("target", ""),
                "details": entry.get("details", ""),
            })
        return json.dumps({"audit_log": log, "total": len(log)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# -----------------------------------------------------------------------
# Security Tools (M13 integration)
# -----------------------------------------------------------------------

def run_security_audit(hostname: str) -> str:
    """
    Run a CIS benchmark security audit on a device and return the
    security score and findings (pass/fail for each check).

    Args:
        hostname: Device hostname to audit
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        from security.engine import SecurityAuditEngine

        # Find device by hostname
        device = _db_manager.get_device_by_hostname(hostname)
        if not device:
            # Try topology
            topo = _db_manager.get_topology()
            if topo and topo.get("nodes"):
                for name in topo["nodes"]:
                    if name.lower() == hostname.lower():
                        return json.dumps({"error": f"Device '{hostname}' found in topology but not in inventory database. Run a crawl first."})
            return json.dumps({"error": f"Device '{hostname}' not found"})

        # Get latest config
        history = _db_manager.get_config_history(device.id, limit=1)
        if not history:
            return json.dumps({"error": f"No config snapshot for '{hostname}'. Crawl the device first."})

        engine = SecurityAuditEngine()
        audit = engine.run_audit(device.id, history[0].config_text, device.vendor or "cisco_ios")

        findings_summary = []
        for r in audit.results:
            findings_summary.append({
                "rule": r.rule.id,
                "category": r.rule.category,
                "severity": r.rule.severity,
                "status": r.status,
                "description": r.rule.description,
                "evidence": r.evidence[:100] if r.evidence else "",
                "remediation": r.remediation[:100] if r.remediation else "",
            })

        return json.dumps({
            "hostname": hostname,
            "score": audit.score,
            "total_checks": audit.total_checks,
            "passed": audit.passed,
            "failed": audit.failed,
            "critical_failures": audit.critical,
            "high_failures": audit.high,
            "findings": findings_summary,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


def get_security_events(limit: int = 10) -> str:
    """
    Get recent security events and threats detected on the network,
    including Suricata alerts, brute force attempts, and rogue devices.

    Args:
        limit: Maximum number of events to return (default 10)
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        with _db_manager._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()

        events = []
        for r in rows:
            events.append({
                "id": r["id"],
                "type": r["event_type"],
                "severity": r["severity"],
                "source_ip": r["source_ip"] or "",
                "dest_ip": r["dest_ip"] or "",
                "description": r["description"] or "",
                "status": r["status"],
                "time": r["created_at"],
            })

        latest_created = max((event.get("time") for event in events if event.get("time")), default=None)
        return json.dumps({
            "events": events,
            "total": len(events),
            "source_metadata": _incident_source_metadata("security_events", latest_created, confidence=0.85 if events else 0.0, scope="security_alerts"),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def analyze_security_alert(event_id: int) -> str:
    """
    Analyze a specific security alert and recommend response actions
    including remediation config commands and playbook execution.

    Args:
        event_id: ID of the security event to analyze
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        from security.response.responder import IncidentResponder
        from security.response.playbooks import PlaybookEngine
        from security.models import SecurityEvent

        with _db_manager._connect() as conn:
            row = conn.execute("SELECT * FROM security_events WHERE id = ?", (event_id,)).fetchone()

        if not row:
            return json.dumps({"error": f"Event {event_id} not found"})

        event = SecurityEvent(
            event_type=row["event_type"], severity=row["severity"],
            source_ip=row["source_ip"] or "", dest_ip=row["dest_ip"] or "",
            device_id=row["device_id"], description=row["description"] or "",
            raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
            status=row["status"], id=row["id"],
        )

        responder = IncidentResponder()
        action = responder.respond(event)

        playbook_engine = PlaybookEngine()
        playbook = playbook_engine.get_playbook(event.event_type)

        result = {
            "event_id": event_id,
            "event_type": event.event_type,
            "severity": event.severity,
            "response": {
                "action_type": action.action_type,
                "description": action.description,
                "config_snippet": action.config_snippet,
                "requires_approval": action.requires_approval,
            },
        }

        if playbook:
            result["playbook"] = {
                "name": playbook.name,
                "mode": playbook.mode,
                "steps": [{"action": s.action, "description": s.description, "auto": s.auto_execute} for s in playbook.steps],
            }

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


def generate_security_remediation(hostname: str, finding_id: str) -> str:
    """
    Generate remediation configuration commands for a specific security
    audit finding on a device.

    Args:
        hostname: Device hostname
        finding_id: The CIS rule ID to remediate (e.g. 'CIS-1.1.1')
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        from security.rules import SECURITY_RULES
        from security.checks.cisco_ios import CHECK_REGISTRY

        device = _db_manager.get_device_by_hostname(hostname)
        if not device:
            return json.dumps({"error": f"Device '{hostname}' not found"})

        # Find the rule
        rule = None
        for r in SECURITY_RULES:
            if r.id == finding_id:
                rule = r
                break

        if not rule:
            available = [r.id for r in SECURITY_RULES]
            return json.dumps({"error": f"Rule '{finding_id}' not found", "available_rules": available})

        # Get device config and run the specific check
        history = _db_manager.get_config_history(device.id, limit=1)
        if not history:
            return json.dumps({"error": f"No config snapshot for '{hostname}'"})

        check_fn = CHECK_REGISTRY.get(rule.check_fn)
        if not check_fn:
            return json.dumps({"error": f"Check function not implemented for {finding_id}"})

        result = check_fn(history[0].config_text, device.vendor or "cisco_ios")

        return json.dumps({
            "hostname": hostname,
            "rule_id": finding_id,
            "description": rule.description,
            "status": result.status,
            "evidence": result.evidence,
            "remediation_config": result.remediation,
            "severity": rule.severity,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


# -----------------------------------------------------------------------
# QoS Tools (M18 integration)
# -----------------------------------------------------------------------

def apply_qos_policy(vendor: str, interface: str, direction: str, rules_json: str) -> str:
    """
    Generate a 5-step QoS policy (Classify, Mark, Police/Shape, Queue, Schedule)
    for a specific device interface to manage bandwidth allocation.

    The 5 QoS steps are:
      1. Classification — identify traffic type (VoIP, Video, Web, Downloads)
      2. Marking — tag with DSCP value (EF, AF41, BE, CS1)
      3. Policing/Shaping — rate-limit or smooth bursty traffic
      4. Queuing — place packets in priority queues
      5. Scheduling — determine which queue is served first (LLQ, WFQ)

    Args:
        vendor:     Device vendor: cisco_ios, huawei_vrp, or juniper_junos
        interface:  Interface to apply QoS (e.g. GigabitEthernet0/1)
        direction:  input or output
        rules_json: JSON string matching the qos.json schema with class_maps and policy_classes
    """
    try:
        from config_engine.renderer import TemplateRenderer

        renderer = TemplateRenderer()

        # Parse rules from JSON string
        if isinstance(rules_json, str):
            params = json.loads(rules_json)
        else:
            params = rules_json

        # Ensure interface and direction are set
        params["interface"] = interface
        params["direction"] = direction
        if "policy_name" not in params:
            params["policy_name"] = "AI_QOS_POLICY"

        config = renderer.render(vendor, "qos", params)
        return json.dumps({
            "vendor": vendor,
            "interface": interface,
            "direction": direction,
            "qos_config": config,
            "line_count": len(config.strip().splitlines()),
            "note": "QoS policy generated. Submit via deployment pipeline for approval.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# -----------------------------------------------------------------------
# Intent Pipeline Tools  (Stage 1–3)
# -----------------------------------------------------------------------

# --- Verification mapping: feature → show commands + expected patterns ---
FEATURE_VERIFICATION_MAP: Dict[str, List[Dict[str, str]]] = {
    "vlan": [
        {"name": "VLAN exists", "check_type": "command_output",
         "command": "show vlan brief", "expected": ""},  # filled at runtime with vlan_id
    ],
    "ospf": [
        {"name": "OSPF neighbor up", "check_type": "command_output",
         "command": "show ip ospf neighbor", "expected": "FULL"},
    ],
    "bgp": [
        {"name": "BGP peer established", "check_type": "command_output",
         "command": "show bgp summary", "expected": "Estab"},
    ],
    "interface": [
        {"name": "Interface up/up", "check_type": "interface_up",
         "command": "", "expected": "up"},
    ],
    "acl": [
        {"name": "ACL present", "check_type": "command_output",
         "command": "show access-lists", "expected": ""},
    ],
    "static_route": [
        {"name": "Route in table", "check_type": "command_output",
         "command": "show ip route static", "expected": ""},
    ],
    "ntp": [
        {"name": "NTP synchronized", "check_type": "command_output",
         "command": "show ntp status", "expected": "synchronized|Clock is synchronized"},
    ],
    "ssh_harden": [
        {"name": "SSH v2 enabled", "check_type": "command_output",
         "command": "show ip ssh", "expected": "version 2"},
    ],
    "logging": [
        {"name": "Logging active", "check_type": "command_output",
         "command": "show logging", "expected": "Logging.*enabled"},
    ],
}


def _build_verification_checks(feature: str, params: dict) -> List[Dict]:
    """
    Auto-generate verification checks for a given feature and parameters.
    Returns a list of dicts compatible with ``VerificationCheck``.
    """
    templates = FEATURE_VERIFICATION_MAP.get(feature, [])
    checks: List[Dict] = []

    for tmpl in templates:
        check = {
            "name": tmpl["name"],
            "check_type": tmpl["check_type"],
            "target": tmpl.get("command", ""),
            "expected": tmpl.get("expected", ""),
        }
        # Customize expected pattern based on parameters
        if feature == "vlan":
            vlans = params.get("vlans", [])
            if vlans:
                ids = [str(v.get("id", "")) for v in vlans if v.get("id")]
                check["expected"] = "|".join(ids) if ids else "."
        elif feature == "interface":
            iface = params.get("name", params.get("interface", ""))
            if iface:
                check["target"] = iface  # interface_up uses target as the interface name
        elif feature == "acl":
            acl_name = params.get("name", "")
            if acl_name:
                check["expected"] = acl_name
        elif feature == "static_route":
            routes = params.get("routes", [])
            if routes:
                dests = [r.get("destination", "") for r in routes if r.get("destination")]
                check["expected"] = "|".join(dests) if dests else "."

        checks.append(check)
    return checks


def parse_intent(user_message: str) -> str:
    """
    Parse a natural-language network request into a structured intent object.

    This is Stage 1 of the intent pipeline.  The LLM extracts:
    - intent_type (configure/troubleshoot/query/audit/rollback)
    - action, feature, vendor, parameters
    - target device scope
    - risk level

    Returns a JSON-encoded ParsedIntent.

    Args:
        user_message: The user's natural language request
    """
    try:
        from llm.intent import IntentParser
        parser = IntentParser(provider=_intent_provider, db_manager=_db_manager)
        # Build topology context for device resolution
        topo_ctx = ""
        if _db_manager:
            topo = _db_manager.get_topology()
            if topo and topo.get("nodes"):
                hosts = list(topo["nodes"].keys())
                topo_ctx = f"Discovered devices: {', '.join(hosts)}"
        return parser.parse(user_message, topology_context=topo_ctx)
    except Exception as e:
        logger.error("parse_intent failed: %s", e)
        return json.dumps({"error": str(e)})


def validate_intent(intent_json: str, user_role: str = "operator") -> str:
    """
    Validate a structured intent against policy rules.

    This is Stage 2 of the intent pipeline.  Checks:
    - RBAC permissions
    - Change window
    - Naming conventions
    - Blast radius
    - VLAN/IP conflicts
    - Device reachability

    Returns a JSON-encoded PolicyResult with approved/violations/warnings.

    Args:
        intent_json: JSON string of the ParsedIntent from parse_intent
        user_role: The authenticated user's role (admin/operator/auditor)
    """
    try:
        from llm.policy import PolicyEngine
        engine = PolicyEngine(db_manager=_db_manager)
        return engine.validate(intent_json, user_role=user_role)
    except Exception as e:
        logger.error("validate_intent failed: %s", e)
        return json.dumps({"error": str(e)})


def plan_change(intent_json: str) -> str:
    """
    Generate a complete change plan from a validated intent.

    This is Stage 3 of the intent pipeline.  For each target device:
    1. Generates CLI config via Jinja2 templates
    2. Generates rollback/undo commands
    3. Validates syntax (per-vendor structural checks)
    4. Computes unified diff
    5. Auto-generates post-deploy verification checks
    6. Determines execution engine (Ansible/SSH)

    Returns a JSON-encoded ChangePlan with all artifacts.

    Args:
        intent_json: JSON string of the ParsedIntent (must have resolved_devices)
    """
    try:
        from config_engine.renderer import TemplateRenderer
        from config_engine.validator import SyntaxValidator
        from config_engine.differ import ConfigDiffer
        from llm.intent import ParsedIntent

        intent = ParsedIntent.model_validate_json(intent_json)
        renderer = TemplateRenderer()
        validator = SyntaxValidator()
        differ = ConfigDiffer()

        if not intent.feature:
            return json.dumps({"error": "No feature specified in intent. Cannot generate config."})

        if not intent.resolved_devices:
            return json.dumps({"error": "No target devices resolved. Specify device hostnames."})

        # Determine vendor — from intent or from device inventory
        vendor = intent.vendor
        devices_plan: List[Dict[str, Any]] = []

        for hostname in intent.resolved_devices:
            dev_vendor = vendor
            if not dev_vendor and _db_manager:
                device = _db_manager.get_device_by_hostname(hostname)
                if device and device.vendor:
                    dev_vendor = device.vendor
            if not dev_vendor:
                dev_vendor = "cisco_ios"  # safe default

            # --- Generate config ---
            try:
                config = renderer.render(dev_vendor, intent.feature, intent.parameters)
            except Exception as e:
                devices_plan.append({
                    "hostname": hostname,
                    "vendor": dev_vendor,
                    "error": f"Config generation failed: {e}",
                })
                continue

            # --- Generate rollback ---
            try:
                rollback = renderer.render_undo(dev_vendor, intent.feature, intent.parameters)
            except Exception:
                rollback = ""

            # --- Syntax validation ---
            syntax_warnings = validator.validate(dev_vendor, config)

            # --- Diff against empty (new config) ---
            diff_text = differ.unified_diff("", config)

            # --- Verification checks ---
            ver_checks = _build_verification_checks(intent.feature, intent.parameters)

            # --- Execution engine routing ---
            exec_engine = "ssh_fallback"
            try:
                from ansible.router import route_execution
                plan = route_execution(
                    intent={"vendor": dev_vendor, "feature": intent.feature,
                            "rendered_commands": config.strip().splitlines()},
                    device_vars={"nettopo_vendor": dev_vendor},
                    rag_state_matches=False,
                )
                exec_engine = plan.action.value
            except Exception:
                pass

            devices_plan.append({
                "hostname": hostname,
                "vendor": dev_vendor,
                "generated_config": config,
                "rollback_config": rollback,
                "diff_text": diff_text,
                "syntax_warnings": syntax_warnings,
                "execution_engine": exec_engine,
                "verification_checks": ver_checks,
                "line_count": len(config.strip().splitlines()),
            })

        total_blast = len(devices_plan)
        return json.dumps({
            "intent_id": intent.id,
            "action": intent.action,
            "feature": intent.feature,
            "risk_level": intent.risk_level,
            "total_devices": total_blast,
            "devices": devices_plan,
            "verification_plan": {
                "auto_checks": sum(len(d.get("verification_checks", [])) for d in devices_plan),
                "description": f"Auto-generated {intent.feature} verification for {total_blast} device(s).",
            },
        }, default=str)

    except Exception as e:
        logger.error("plan_change failed: %s", e)
        return json.dumps({"error": str(e)})


def get_device_running_config(hostname: str) -> str:
    """
    Retrieve the last known running configuration snapshot for a device.

    This provides context for the LLM to understand what is already
    configured before proposing changes.

    Args:
        hostname: Device hostname to look up
    """
    if not _db_manager:
        return json.dumps({"error": "Database not available"})

    try:
        device = _db_manager.get_device_by_hostname(hostname)
        if not device:
            return json.dumps({"error": f"Device '{hostname}' not found"})

        history = _db_manager.get_config_history(device.id, limit=1)
        if not history:
            return json.dumps({
                "hostname": hostname,
                "config": "(no config snapshot available — device has not been polled yet)",
                "note": "Run a discovery crawl to capture the running configuration.",
                "source_metadata": _config_source_metadata("config_snapshots", None, confidence=0.0),
            })

        config_text = history[0].config_text or ""
        captured_at = str(history[0].captured_at) if hasattr(history[0], 'captured_at') else None
        return json.dumps({
            "hostname": hostname,
            "config_length": len(config_text),
            "config": config_text[:8000],  # cap at 8k chars to avoid token overflow
            "snapshot_time": captured_at or "unknown",
            "truncated": len(config_text) > 8000,
            "source_metadata": _config_source_metadata("config_snapshots", captured_at, confidence=0.9),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# -----------------------------------------------------------------------
# LLM Provider for intent parsing (set at startup)
# -----------------------------------------------------------------------
_intent_provider = None


def set_intent_provider(provider) -> None:
    """Called once at startup to inject the LLM provider for intent parsing."""
    global _intent_provider
    _intent_provider = provider


# -----------------------------------------------------------------------
# Tool registration helper
# -----------------------------------------------------------------------

def register_all_tools(registry) -> None:
    """Register all network tools with the given ToolRegistry."""

    # Topology tools
    registry.register(
        get_topology_summary,
        description="Get a summary of the current network topology including all devices, their status, and connections.",
    )
    registry.register(
        get_device_details,
        description="Get detailed information about a specific device by hostname.",
        param_descriptions={"hostname": "The device hostname to look up"},
    )
    registry.register(
        list_all_devices,
        description="List all discovered network devices with status and management IPs.",
    )
    registry.register(
        list_inventory_devices,
        description="List devices from source-of-truth inventory with NetBox preferred and local DB fallback.",
        param_descriptions={
            "vendor": "Optional vendor filter",
            "status": "Optional status filter",
            "limit": "Maximum number of devices to return",
        },
    )
    registry.register(
        get_inventory_device,
        description="Get a specific source-of-truth inventory device by hostname.",
        param_descriptions={"hostname": "The inventory hostname to look up"},
    )
    registry.register(
        search_inventory_devices,
        description="Search source-of-truth inventory by hostname, management IP, vendor, model, or role.",
        param_descriptions={
            "query": "Search text",
            "limit": "Maximum number of matches to return",
        },
    )
    registry.register(
        get_planned_topology,
        description="Get planned topology links from the persistent inventory/link database.",
    )
    registry.register(
        compare_planned_vs_live,
        description="Compare source-of-truth inventory against live topology, optionally scoped to one hostname.",
        param_descriptions={"hostname": "Optional device hostname to scope the comparison"},
    )
    registry.register(
        get_context_health,
        description="Get context versions, last-updated timestamps, and recent invalidation events across live and planned network state.",
    )
    registry.register(
        get_recent_incidents,
        description="Get recent incidents and alerts with freshness metadata from the incident store.",
        param_descriptions={
            "limit": "Maximum number of incidents to return",
            "status": "Optional lifecycle status filter",
        },
    )
    registry.register(
        get_recent_drift_events,
        description="Get recent drift events with freshness metadata.",
        param_descriptions={
            "limit": "Maximum number of drift events to return",
            "hostname": "Optional hostname filter",
        },
    )
    registry.register(
        get_knowledge_base_status,
        description="Get knowledge base document summaries and freshness metadata.",
        param_descriptions={"limit": "Maximum number of documents to return"},
    )
    registry.register(
        find_path,
        description="Find the shortest network path between two devices.",
        param_descriptions={
            "source": "Source device hostname",
            "destination": "Destination device hostname",
        },
    )

    # Config tools
    registry.register(
        generate_config,
        description="Generate CLI configuration commands for a device using Jinja2 templates.",
        param_descriptions={
            "vendor": "Device vendor: cisco_ios, huawei_vrp, or juniper_junos",
            "feature": "Config feature: vlan, ospf, bgp, interface, acl, static_route, ntp, logging, banner, ssh_harden",
            "params": "JSON string of template parameters (use get_template_schema to see expected format)",
        },
    )
    registry.register(
        generate_rollback,
        description="Generate rollback/undo commands that reverse a configuration change.",
        param_descriptions={
            "vendor": "Device vendor",
            "feature": "Config feature name",
            "params": "JSON string of the same parameters used in generate_config",
        },
    )
    registry.register(
        list_templates,
        description="List all available configuration templates, optionally filtered by vendor.",
        param_descriptions={"vendor": "Optional vendor filter (e.g. 'cisco_ios')"},
    )
    registry.register(
        get_template_schema,
        description="Get the JSON schema showing required parameters for a configuration template.",
        param_descriptions={"feature": "Template name (e.g. 'vlan', 'ospf')"},
    )
    registry.register(
        diff_configs,
        description="Compare two configuration texts and show differences.",
        param_descriptions={
            "old_config": "Original configuration text",
            "new_config": "New/proposed configuration text",
        },
    )

    # Audit tools
    registry.register(
        get_audit_log,
        description="Get recent audit log entries showing performed actions.",
        param_descriptions={"limit": "Maximum number of entries to return (default 20)"},
    )

    # Security tools (M13)
    registry.register(
        run_security_audit,
        description="Run a CIS benchmark security audit on a device, returning score and pass/fail findings.",
        param_descriptions={"hostname": "Device hostname to audit"},
    )
    registry.register(
        get_security_events,
        description="Get recent security events/threats (brute force, port scans, Suricata alerts, rogue devices).",
        param_descriptions={"limit": "Max events to return (default 10)"},
    )
    registry.register(
        analyze_security_alert,
        description="Analyze a security alert by ID and get recommended response actions and playbook steps.",
        param_descriptions={"event_id": "Security event ID to analyze"},
    )
    registry.register(
        generate_security_remediation,
        description="Generate remediation config commands for a specific CIS finding on a device.",
        param_descriptions={
            "hostname": "Device hostname",
            "finding_id": "CIS rule ID (e.g. 'CIS-1.1.1', 'CIS-7.1.1')",
        },
    )

    # QoS tools (M18)
    registry.register(
        apply_qos_policy,
        description=(
            "Generate a 5-step QoS policy (Classify, Mark, Police/Shape, Queue, Schedule) "
            "for a device interface. Use this to prioritize VoIP, throttle downloads, "
            "or remediate bandwidth congestion."
        ),
        param_descriptions={
            "vendor": "Device vendor: cisco_ios, huawei_vrp, or juniper_junos",
            "interface": "Interface to apply QoS (e.g. GigabitEthernet0/1)",
            "direction": "Direction: input or output",
            "rules_json": (
                'JSON string with policy_name, class_maps (name + matches), '
                'and policy_classes (class_name, dscp, priority_percent, bandwidth_percent, '
                'police_rate, shape_average). Use get_template_schema(\"qos\") to see full schema.'
            ),
        },
    )

    # Intent Pipeline tools (Stage 1-3)
    registry.register(
        parse_intent,
        description=(
            "Parse a natural-language network request into a structured intent object (JSON). "
            "This is STAGE 1 of the intent pipeline. Call this FIRST for any configuration request. "
            "Returns: intent_type, action, feature, vendor, parameters, scope, risk_level, resolved_devices."
        ),
        param_descriptions={
            "user_message": "The user's natural language request to parse into a structured intent",
        },
    )
    registry.register(
        validate_intent,
        description=(
            "Validate a structured intent against policy rules (RBAC, change window, naming, "
            "blast radius, conflicts, device reachability). This is STAGE 2 — call AFTER parse_intent. "
            "Returns: approved (bool), violations, warnings, blast_radius."
        ),
        param_descriptions={
            "intent_json": "JSON string of the ParsedIntent from parse_intent",
            "user_role": "The user's role: admin, operator, or auditor (default: operator)",
        },
    )
    registry.register(
        plan_change,
        description=(
            "Generate a complete change plan from a validated intent. This is STAGE 3 — "
            "call AFTER validate_intent returns approved=true. Generates CLI config, rollback, "
            "syntax validation, diff preview, and auto verification checks for each target device."
        ),
        param_descriptions={
            "intent_json": "JSON string of the ParsedIntent (must have resolved_devices)",
        },
    )
    registry.register(
        get_device_running_config,
        description=(
            "Retrieve the last known running-config snapshot for a device. "
            "Use this to understand what is already configured before proposing changes."
        ),
        param_descriptions={
            "hostname": "Device hostname to look up",
        },
    )

    logger.info("Registered %d network tools", len(registry.list_tools()))

