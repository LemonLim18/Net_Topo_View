"""
Network tools — topology and config tools for the LLM agent.

These are the functions that Gemini can call to interact with the
network controller.  They are registered with the ``ToolRegistry``.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("llm.network_tools")


# -----------------------------------------------------------------------
# Global reference to the DB manager (set by the agent at startup)
# -----------------------------------------------------------------------
_db_manager = None


def set_db_manager(db) -> None:
    """Called once at startup to inject the DatabaseManager."""
    global _db_manager
    _db_manager = db


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

    return json.dumps({"devices": devices, "total": len(devices)})


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
        from backend.config_engine.renderer import TemplateRenderer

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
        from backend.config_engine.renderer import TemplateRenderer

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
        from backend.config_engine.renderer import TemplateRenderer

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
        from backend.config_engine.renderer import TemplateRenderer

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
        from backend.config_engine.differ import ConfigDiffer

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

    logger.info("Registered %d network tools", len(registry.list_tools()))
