from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from llm.context_contract import FreshnessClass
from rag.context_bundle import ContextBundleBuilder

logger = logging.getLogger("ansible.planning_context")

_ALLOW_STALE_INVENTORY_FEATURES = {
    "vlan",
    "interface",
    "ospf",
    "static_route",
    "acl",
    "qos",
}

_ROLE_ALIASES = {
    "access": "access",
    "access_switch": "access",
    "access-switch": "access",
    "switch": "access",
    "switches": "access",
    "distribution": "distribution",
    "distribution_switch": "distribution",
    "distribution-switch": "distribution",
    "dist": "distribution",
    "core": "core",
    "core_router": "core",
    "core-router": "core",
    "router": "core",
    "routers": "core",
    "firewall": "firewall",
    "network-device": "network-device",
}

_VENDOR_ALIASES = {
    "cisco": "cisco_ios",
    "cisco-ios": "cisco_ios",
    "cisco_ios": "cisco_ios",
    "cisco-xe": "cisco_ios",
    "cisco_xe": "cisco_ios",
    "juniper": "juniper_junos",
    "juniper-junos": "juniper_junos",
    "juniper_junos": "juniper_junos",
    "junos": "juniper_junos",
    "huawei": "huawei_vrp",
    "huawei-vrp": "huawei_vrp",
    "huawei_vrp": "huawei_vrp",
    "vrp": "huawei_vrp",
}


def normalize_vendor(value: Any) -> str:
    lowered = str(value or "").strip().lower().replace(" ", "_")
    if not lowered:
        return ""
    return _VENDOR_ALIASES.get(lowered, lowered)


def normalize_role(value: Any) -> str:
    lowered = str(value or "").strip().lower().replace(" ", "_")
    if not lowered:
        return ""
    return _ROLE_ALIASES.get(lowered, lowered)


def normalize_site(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def infer_vendor_hint(hostnames: List[str], db_manager) -> str:
    vendors = set()
    if db_manager is None:
        return ""
    for hostname in hostnames:
        device = db_manager.get_device_by_hostname(hostname)
        vendor = normalize_vendor(getattr(device, "vendor", ""))
        if vendor:
            vendors.add(vendor)
    return next(iter(vendors)) if len(vendors) == 1 else ""


def _deduplicate_devices_by_ip(
    devices: List[Dict[str, Any]],
    db_manager,
    inventory_devices: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep one device per management IP; prefer hostname registered for that IP in DB (NetBox)."""
    by_ip: Dict[str, List[Dict[str, Any]]] = {}
    for d in devices:
        ip = str(d.get("management_ip") or "").strip() or str(d.get("hostname") or "").strip()
        if not ip:
            continue
        by_ip.setdefault(ip, []).append(d)
    result: List[Dict[str, Any]] = []
    for ip, group in by_ip.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        # Prefer the hostname that the DB has for this management IP (canonical/NetBox name)
        canonical_host: str | None = None
        if db_manager:
            try:
                dev = db_manager.get_device_by_management_ip(ip)
                if dev and dev.hostname:
                    canonical_host = dev.hostname.strip()
            except Exception:
                pass
        chosen: Dict[str, Any] | None = None
        if canonical_host:
            for d in group:
                if str(d.get("hostname") or "").strip() == canonical_host:
                    chosen = d
                    break
        if not chosen:
            def _rank(dev: Dict[str, Any]) -> int:
                host = str(dev.get("hostname") or "").strip()
                if host and host in inventory_devices:
                    return 0
                return 1
            chosen = min(group, key=_rank)
        result.append(chosen)
        dropped = [str(d.get("hostname")) for d in group if d != chosen]
        if dropped:
            logger.info(
                "Deduplicated by IP %s: keeping %s, dropping %s",
                ip,
                chosen.get("hostname"),
                dropped,
            )
    return result


def build_scoped_planning_context(
    db_manager,
    hostnames: List[str],
    *,
    vendor_filter: Optional[str] = None,
    query: Optional[str] = None,
    feature: Optional[str] = None,
) -> Dict[str, Any]:
    requested_hosts = _dedupe_hosts(hostnames)
    bundle = ContextBundleBuilder(db_manager=db_manager).build_bundle(
        device_hostnames=requested_hosts or None,
        vendor_filter=normalize_vendor(vendor_filter) or None,
        include_inventory=True,
        include_planned_topology=True,
        include_telemetry=False,
        include_sdn=False,
        include_config=False,
        include_deployment=False,
        include_incidents=False,
        include_threats=False,
        include_drift=False,
        include_knowledge=False,
        include_freshness_warnings=True,
        query=query,
    )

    inventory_devices = {
        str(device.get("hostname") or ""): dict(device)
        for device in bundle.inventory.devices
        if str(device.get("hostname") or "")
    }
    interfaces_by_device: Dict[str, List[Dict[str, Any]]] = {}
    for iface in bundle.inventory.interfaces:
        host = str(iface.get("device") or "")
        if not host:
            continue
        interfaces_by_device.setdefault(host, []).append(dict(iface))

    planned_nodes = (
        (bundle.planned_topology.topology or {}).get("nodes", {})
        if isinstance(bundle.planned_topology.topology, dict)
        else {}
    )
    planned_links = list(bundle.planned_topology.links or [])
    live_nodes = ((db_manager.get_topology() or {}).get("nodes", {}) if db_manager is not None else {}) or {}

    effective_hosts = requested_hosts or sorted(
        set(inventory_devices.keys()) | set(planned_nodes.keys()) | set(live_nodes.keys())
    )

    devices: List[Dict[str, Any]] = []
    for host in effective_hosts:
        inventory_device = inventory_devices.get(host, {})
        planned_node = planned_nodes.get(host, {}) if isinstance(planned_nodes, dict) else {}
        live_node = live_nodes.get(host, {}) if isinstance(live_nodes, dict) else {}
        db_device = db_manager.get_device_by_hostname(host) if db_manager is not None else None

        interfaces = list(interfaces_by_device.get(host) or [])
        if not interfaces and db_device is not None:
            interfaces = [
                {
                    "device": host,
                    "name": iface.name,
                    "ip_address": iface.ip_address,
                    "subnet_mask": iface.subnet_mask,
                    "mac_address": iface.mac_address,
                    "interface_type": getattr(iface, "interface_type", "l2"),
                    "is_management": bool(getattr(iface, "is_management", False)),
                    "status": iface.status,
                }
                for iface in db_manager.get_interfaces_for_device(db_device.id)
            ]

        neighbors = _build_neighbor_records(host, planned_links, live_node, planned_node)
        vendor = normalize_vendor(
            inventory_device.get("vendor")
            or getattr(db_device, "vendor", None)
            or live_node.get("vendor")
        )
        role = normalize_role(
            inventory_device.get("role")
            or getattr(db_device, "role", None)
            or planned_node.get("netbox_role")
        )
        site = inventory_device.get("site") or planned_node.get("netbox_site")
        management_ip = (
            inventory_device.get("management_ip")
            or getattr(db_device, "management_ip", None)
            or planned_node.get("mgmt_ip")
            or live_node.get("mgmt_ip")
            or live_node.get("ip")
        )

        devices.append(
            {
                "hostname": host,
                "management_ip": management_ip,
                "vendor": vendor,
                "role": role,
                "site": site,
                "model": inventory_device.get("model") or getattr(db_device, "model", None),
                "status": inventory_device.get("status") or getattr(db_device, "status", None),
                "serial": inventory_device.get("serial") or getattr(db_device, "serial_number", None),
                "interfaces": interfaces,
                "neighbors": neighbors,
                "inventory_source": bundle.inventory.source,
                "planned_topology_source": bundle.planned_topology.source,
                "live_neighbor_source": "crawler_cache" if live_node else None,
            }
        )

    # One device per management IP: prefer NetBox/DB hostname over crawler-discovered name (e.g. Acc_Huawei_3 over Acc_Huawei_1)
    devices = _deduplicate_devices_by_ip(devices, db_manager, inventory_devices)
    effective_hosts = [str(d.get("hostname") or "").strip() for d in devices if str(d.get("hostname") or "").strip()]

    return {
        "target_hosts": effective_hosts,
        "feature": str(feature or ""),
        "query": str(query or ""),
        "inventory": {
            "source": bundle.inventory.source,
            "source_detail": bundle.inventory.source_detail,
            "freshness": bundle.inventory.freshness.to_dict(),
            "total_devices": bundle.inventory.total_devices,
            "interfaces_included": len(bundle.inventory.interfaces or []),
        },
        "planned_topology": {
            "source": bundle.planned_topology.source,
            "source_detail": bundle.planned_topology.source_detail,
            "freshness": bundle.planned_topology.freshness.to_dict(),
            "link_count": len(planned_links),
        },
        "freshness_warnings": list(bundle.freshness_warnings.warnings or []),
        "devices": devices,
        "fallback_evidence": _fallback_evidence(bundle),
    }


def planning_context_to_prompt_text(context: Dict[str, Any]) -> str:
    return json.dumps(context or {}, indent=2, sort_keys=True)


def planning_context_readiness(context: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    devices = list(context.get("devices") or [])
    requested_hosts = [str(host).strip() for host in (context.get("target_hosts") or []) if str(host).strip()]
    found_hosts = {str(device.get("hostname") or "") for device in devices if str(device.get("hostname") or "")}
    missing_hosts = [host for host in requested_hosts if host not in found_hosts]
    if missing_hosts:
        issues.append(f"Missing target inventory records: {', '.join(missing_hosts)}")

    inventory_meta = dict(context.get("inventory") or {})
    planned_meta = dict(context.get("planned_topology") or {})
    inventory_freshness = str((inventory_meta.get("freshness") or {}).get("freshness_class") or "")
    planned_freshness = str((planned_meta.get("freshness") or {}).get("freshness_class") or "")
    inventory_source = str(inventory_meta.get("source") or "")
    feature = str(context.get("feature") or "").strip().lower()

    # Prefer NetBox; allow local_db if all target devices have required fields (e.g. after sync with name mismatch)
    if inventory_source != "netbox" and (not devices or missing_hosts):
        issues.append(f"Planned inventory source is '{inventory_source or 'unknown'}' instead of NetBox")
    # For feature sets that only require host + vendor + management IP, we can proceed
    # even when freshness marks are stale (common immediately after reconciliation).
    # We still enforce missing-host and per-device required field checks below.
    if feature not in _ALLOW_STALE_INVENTORY_FEATURES and inventory_freshness in {
        FreshnessClass.MISSING.value,
        FreshnessClass.STALE.value,
    }:
        issues.append(f"Planned inventory freshness is {inventory_freshness or 'unknown'}")
    if feature == "routing" and planned_freshness in {
        FreshnessClass.MISSING.value,
        FreshnessClass.STALE.value,
    }:
        issues.append(f"Planned topology freshness is {planned_freshness or 'unknown'}")
    if feature == "interface" and planned_freshness in {
        FreshnessClass.MISSING.value,
        FreshnessClass.STALE.value,
    }:
        # Allow interface changes (e.g. shutdown) with stale topology; interface inventory from NetBox is enough
        pass

    for device in devices:
        host = str(device.get("hostname") or "unknown")
        if not str(device.get("management_ip") or "").strip():
            issues.append(f"{host} is missing a management IP")
        if not str(device.get("vendor") or "").strip():
            issues.append(f"{host} is missing a normalized vendor")
        if feature in {"routing", "interface"} and not list(device.get("interfaces") or []):
            issues.append(f"{host} is missing interface inventory for feature '{feature}'")

    return {"ready": len(issues) == 0, "issues": issues}


def reconcile_inventory_state(db_manager) -> Dict[str, Any]:
    from netbox.sync_devices import DeviceSyncer
    from netbox.sync_topology import TopologySyncer

    inventory_result = DeviceSyncer(db_manager).sync_all_devices()
    topology_result = TopologySyncer(db_manager).sync_connections()
    return {
        "inventory_sync": inventory_result,
        "topology_sync": topology_result,
    }


def topology_override_from_planning_context(context: Dict[str, Any]) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    for device in context.get("devices") or []:
        hostname = str(device.get("hostname") or "").strip()
        if not hostname:
            continue
        mgmt_ip = str(device.get("management_ip") or "").strip() or hostname
        edge_links = {}
        for neighbor in device.get("neighbors") or []:
            local_interface = str(neighbor.get("local_interface") or "").strip()
            if not local_interface:
                continue
            edge_links[local_interface] = {
                "remote_node": neighbor.get("remote_hostname"),
                "remote_interface": neighbor.get("remote_interface"),
                "remote_ip": neighbor.get("remote_ip"),
            }
        nodes[hostname] = {
            "id": hostname,
            "hostname": hostname,
            "name": hostname,
            "mgmt_ip": mgmt_ip,
            "ip": mgmt_ip,
            "vendor": normalize_vendor(device.get("vendor")),
            "role": normalize_role(device.get("role")),
            "site": device.get("site"),
            "model": device.get("model"),
            "status": device.get("status"),
            "interfaces": list(device.get("interfaces") or []),
            "connectedNode": [n.get("remote_hostname") for n in device.get("neighbors") or [] if n.get("remote_hostname")],
            "edgeLink": edge_links,
            "hop_chain": [],
        }
    return {"nodes": nodes}


def _build_neighbor_records(
    hostname: str,
    planned_links: List[Dict[str, Any]],
    live_node: Dict[str, Any],
    planned_node: Dict[str, Any],
) -> List[Dict[str, Any]]:
    neighbors: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for link in planned_links:
        src = str(link.get("src_hostname") or "")
        dst = str(link.get("dst_hostname") or "")
        if hostname not in {src, dst}:
            continue
        is_src = hostname == src
        remote_hostname = dst if is_src else src
        local_interface = str(link.get("src_interface") if is_src else link.get("dst_interface") or "")
        remote_interface = str(link.get("dst_interface") if is_src else link.get("src_interface") or "")
        key = (remote_hostname, local_interface, remote_interface)
        if key in seen:
            continue
        seen.add(key)
        neighbors.append(
            {
                "remote_hostname": remote_hostname,
                "local_interface": local_interface,
                "remote_interface": remote_interface,
                "remote_ip": None,
                "source": "planned_topology",
            }
        )

    for local_interface, edge in (live_node.get("edgeLink") or {}).items():
        if not isinstance(edge, dict):
            continue
        remote_hostname = str(edge.get("remote_node") or "").strip()
        remote_interface = str(edge.get("remote_interface") or "").strip()
        key = (remote_hostname, str(local_interface), remote_interface)
        if not remote_hostname or key in seen:
            continue
        seen.add(key)
        neighbors.append(
            {
                "remote_hostname": remote_hostname,
                "local_interface": str(local_interface),
                "remote_interface": remote_interface,
                "remote_ip": edge.get("remote_ip"),
                "source": "live_topology",
            }
        )

    for local_interface, edge in (planned_node.get("edgeLink") or {}).items():
        if not isinstance(edge, dict):
            continue
        remote_hostname = str(edge.get("remote_node") or "").strip()
        remote_interface = str(edge.get("remote_interface") or "").strip()
        key = (remote_hostname, str(local_interface), remote_interface)
        if not remote_hostname or key in seen:
            continue
        seen.add(key)
        neighbors.append(
            {
                "remote_hostname": remote_hostname,
                "local_interface": str(local_interface),
                "remote_interface": remote_interface,
                "remote_ip": edge.get("remote_ip"),
                "source": "planned_topology",
            }
        )

    return neighbors


def _dedupe_hosts(hostnames: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for host in hostnames or []:
        value = str(host or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _fallback_evidence(bundle) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    if getattr(bundle.inventory, "source", "") != "netbox":
        evidence.append(
            {
                "domain": "planned_inventory",
                "source": bundle.inventory.source,
                "detail": bundle.inventory.source_detail,
            }
        )
    if getattr(bundle.planned_topology, "source", "") != "netbox":
        evidence.append(
            {
                "domain": "planned_topology",
                "source": bundle.planned_topology.source,
                "detail": bundle.planned_topology.source_detail,
            }
        )
    return evidence
