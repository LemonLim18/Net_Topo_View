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

        return json.dumps({"events": events, "total": len(events)})
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

    logger.info("Registered %d network tools", len(registry.list_tools()))

