from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from llm.context_contract import (
    AUTHORITY_MATRIX,
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DEPLOYMENT,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_TELEMETRY,
    build_source_matrix,
)


SEMANTICS_VERSION = "phase6_step22_v1"
AUTHORITY_MAP_VERSION = "phase4_step13_v1"

DEVICE_PRESENCE_STATES = {
    "planned_and_live": "Device exists in source-of-truth inventory/planned topology and is also present in live discovery.",
    "planned_only": "Device exists in source-of-truth inventory/planned topology but is not present in live discovery.",
    "live_only": "Device is present in live discovery but is absent from source-of-truth inventory/planned topology.",
    "unknown": "Device presence cannot be determined from the currently available sources.",
}

INTERFACE_PRESENCE_STATES = {
    "planned_and_live": "Interface should be interpreted as present in both planned/source-of-truth and live-observed state.",
    "planned_only": "Interface should be interpreted as planned/source-of-truth only and not observed in live topology.",
    "live_only": "Interface should be interpreted as live-observed only and absent from the planned/source-of-truth model.",
    "unknown": "Interface presence cannot be determined from the currently available sources.",
}

MANAGEMENT_IP_ALIGNMENT_STATES = {
    "match": "Planned and live management IP values match.",
    "mismatch": "Planned and live management IP values disagree.",
    "planned_only": "Only planned/source-of-truth management IP is available.",
    "live_only": "Only live-discovered management IP is available.",
    "unknown": "Management-IP alignment cannot be determined from the currently available sources.",
}


def build_canonical_semantics_map() -> Dict[str, Any]:
    return {
        "version": SEMANTICS_VERSION,
        "device_presence_states": DEVICE_PRESENCE_STATES,
        "interface_presence_states": INTERFACE_PRESENCE_STATES,
        "management_ip_alignment_states": MANAGEMENT_IP_ALIGNMENT_STATES,
        "live_status_values": {
            "up": "Device is present in live discovery and not marked down.",
            "down": "Device is present in live discovery and marked down/unreachable.",
            "missing": "Device is not present in live discovery.",
        },
        "source_scopes": {
            "inventory": "planned/source-of-truth inventory semantics",
            "planned_topology": "planned/source-of-truth topology semantics",
            "live": "live discovered topology semantics",
        },
        "interpretation_rules": [
            "Use presence_state as the canonical cross-surface meaning for planned-vs-live device presence.",
            "Use interface_presence_state as the canonical cross-surface meaning for interface visibility when comparing planned and live state.",
            "Treat raw inventory status and raw live status as source-native fields; do not conflate them with presence_state.",
        ],
    }


def _serialize_domain_contract(domain: str) -> Dict[str, Any]:
    contract = AUTHORITY_MATRIX[domain]
    return {
        "domain": contract.domain,
        "canonical_sources": list(contract.canonical_sources),
        "fallback_sources": list(contract.fallback_sources),
        "description": contract.description,
        "live_max_age_minutes": contract.live_max_age_minutes,
        "warm_max_age_minutes": contract.warm_max_age_minutes,
    }


def build_canonical_authority_map() -> Dict[str, Any]:
    """Return the single authority map shared by prompt, REST, MCP, and grounding paths."""
    ordered_domains = (
        CONTEXT_DOMAIN_INVENTORY,
        CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
        CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        CONTEXT_DOMAIN_TELEMETRY,
        CONTEXT_DOMAIN_CONFIG,
        CONTEXT_DOMAIN_INCIDENTS,
        CONTEXT_DOMAIN_DEPLOYMENT,
    )
    return {
        "version": AUTHORITY_MAP_VERSION,
        "semantics_version": SEMANTICS_VERSION,
        "context_domains": {
            domain: _serialize_domain_contract(domain)
            for domain in ordered_domains
        },
        "query_source_matrix": build_source_matrix(),
        "canonical_semantics": build_canonical_semantics_map(),
    }


def build_canonical_semantics_prompt_text() -> str:
    semantics = build_canonical_semantics_map()
    authority_map = build_canonical_authority_map()
    lines = ["## Canonical Semantics"]
    lines.append(f"Authority map version: {authority_map['version']}")
    lines.append(f"Semantics version: {semantics['version']}")
    lines.append("- presence_state: planned_and_live | planned_only | live_only | unknown")
    lines.append("- interface_presence_state: planned_and_live | planned_only | live_only | unknown")
    lines.append("- management_ip_alignment: match | mismatch | planned_only | live_only | unknown")
    lines.append("- inventory authority: planned inventory uses NetBox first, then local DB fallback")
    lines.append("- live authority: live topology uses crawler cache first, with SDN as fallback where defined")
    lines.append("- operational freshness: telemetry, incidents, and deployment state remain source-native and must not be flattened into planned-state labels")
    lines.append("- Rule: inventory/planned-only objects must be labeled planned_only everywhere until live discovery confirms them.")
    return "\n".join(lines)


def _presence_state(inventory_present: bool, live_present: bool) -> str:
    if inventory_present and live_present:
        return "planned_and_live"
    if inventory_present:
        return "planned_only"
    if live_present:
        return "live_only"
    return "unknown"


def _live_status(live_record: Optional[Dict[str, Any]]) -> str:
    if not isinstance(live_record, dict):
        return "missing"
    return "down" if live_record.get("down") else "up"


def _inventory_status(inventory_record: Optional[Dict[str, Any]]) -> str:
    if not isinstance(inventory_record, dict):
        return "missing"
    return str(inventory_record.get("status") or "unknown").lower()


def _management_ip_alignment(planned_ip: Optional[str], live_ip: Optional[str], presence_state: str) -> str:
    if planned_ip and live_ip:
        return "match" if planned_ip == live_ip else "mismatch"
    if presence_state == "planned_only" and planned_ip:
        return "planned_only"
    if presence_state == "live_only" and live_ip:
        return "live_only"
    return "unknown"


def canonical_device_record(
    hostname: str,
    inventory_record: Optional[Dict[str, Any]] = None,
    live_record: Optional[Dict[str, Any]] = None,
    inventory_source: str = "local_db",
    live_source: str = "crawler_cache",
) -> Dict[str, Any]:
    inventory_present = isinstance(inventory_record, dict)
    live_present = isinstance(live_record, dict)
    presence_state = _presence_state(inventory_present, live_present)
    planned_ip = inventory_record.get("management_ip") if inventory_present else None
    live_ip = live_record.get("mgmt_ip") if live_present else None

    source_scopes: List[str] = []
    source_labels: List[str] = []
    if inventory_present:
        source_scopes.append("inventory")
        source_labels.append(inventory_source)
    if live_present:
        source_scopes.append("live")
        source_labels.append(live_source)

    return {
        "hostname": hostname,
        "presence_state": presence_state,
        "inventory_status": _inventory_status(inventory_record),
        "live_status": _live_status(live_record),
        "planned_management_ip": planned_ip,
        "live_management_ip": live_ip,
        "management_ip": planned_ip or live_ip,
        "management_ip_alignment": _management_ip_alignment(planned_ip, live_ip, presence_state),
        "inventory_present": inventory_present,
        "live_present": live_present,
        "source_scopes": source_scopes,
        "source_labels": source_labels,
        "semantics_version": SEMANTICS_VERSION,
    }


def build_canonical_device_matrix(
    inventory_records: Iterable[Dict[str, Any]],
    live_nodes: Dict[str, Any],
    inventory_source: str = "local_db",
    hostnames: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    inventory_map = {
        str(record.get("hostname")): record
        for record in inventory_records
        if isinstance(record, dict) and record.get("hostname")
    }
    live_map = {
        str(name): data
        for name, data in (live_nodes or {}).items()
        if isinstance(data, dict)
    }
    allowed = {name.lower() for name in hostnames} if hostnames else None

    matrix = []
    for hostname in sorted(set(inventory_map) | set(live_map)):
        if allowed and hostname.lower() not in allowed:
            continue
        matrix.append(
            canonical_device_record(
                hostname,
                inventory_record=inventory_map.get(hostname),
                live_record=live_map.get(hostname),
                inventory_source=inventory_source,
            )
        )
    return matrix


def device_state_index(device_matrix: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        str(device.get("hostname")): device
        for device in device_matrix
        if isinstance(device, dict) and device.get("hostname")
    }


def annotate_inventory_records(
    inventory_records: Iterable[Dict[str, Any]],
    live_nodes: Dict[str, Any],
    inventory_source: str,
) -> List[Dict[str, Any]]:
    matrix = device_state_index(build_canonical_device_matrix(inventory_records, live_nodes, inventory_source))
    annotated = []
    for record in inventory_records:
        if not isinstance(record, dict):
            continue
        hostname = str(record.get("hostname") or "")
        semantics = matrix.get(hostname, canonical_device_record(hostname, inventory_record=record, inventory_source=inventory_source))
        annotated.append(
            {
                **record,
                **{
                    k: semantics[k]
                    for k in (
                        "presence_state",
                        "live_status",
                        "management_ip_alignment",
                        "source_scopes",
                        "source_labels",
                        "semantics_version",
                    )
                },
            }
        )
    return annotated


def annotate_inventory_interfaces(
    interfaces: Iterable[Dict[str, Any]],
    semantics_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    annotated = []
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        hostname = str(interface.get("device") or interface.get("hostname") or "")
        device_semantics = semantics_index.get(hostname, {})
        presence_state = device_semantics.get("presence_state", "unknown")
        annotated.append(
            {
                **interface,
                "interface_presence_state": presence_state if presence_state in INTERFACE_PRESENCE_STATES else "unknown",
                "device_presence_state": presence_state,
                "semantics_version": SEMANTICS_VERSION,
            }
        )
    return annotated


def annotate_planned_links(
    links: Iterable[Dict[str, Any]],
    semantics_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    annotated = []
    for link in links:
        if not isinstance(link, dict):
            continue
        src_hostname = str(link.get("src_hostname") or "")
        dst_hostname = str(link.get("dst_hostname") or "")
        src_presence = semantics_index.get(src_hostname, {}).get("presence_state", "unknown")
        dst_presence = semantics_index.get(dst_hostname, {}).get("presence_state", "unknown")
        if src_presence == "planned_and_live" and dst_presence == "planned_and_live":
            interface_presence = "planned_and_live"
        elif src_presence == "planned_only" or dst_presence == "planned_only":
            interface_presence = "planned_only"
        elif src_presence == "live_only" or dst_presence == "live_only":
            interface_presence = "live_only"
        else:
            interface_presence = "unknown"
        annotated.append(
            {
                **link,
                "interface_presence_state": interface_presence,
                "semantics_version": SEMANTICS_VERSION,
            }
        )
    return annotated
