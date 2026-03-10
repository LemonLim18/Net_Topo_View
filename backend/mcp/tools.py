from __future__ import annotations

import json
from typing import Any, Dict

try:
    from .server import mcp, db
except ImportError:  # pragma: no cover - direct script/test fallback
    from mcp.server import mcp, db

try:
    from llm import network_tools
except ImportError:  # pragma: no cover - package import path
    from ..llm import network_tools


_DEPRECATED_UNSAFE_MESSAGE = (
    "Direct free-form command execution is not exposed through the MCP tool surface because it "
    "would bypass the canonical authority and safety contracts. Use the authoritative inventory, "
    "planned-vs-live comparison, config snapshot, and proposal workflows instead."
)


def _ensure_bindings() -> None:
    network_tools.set_db_manager(db)


def _tool_payload(tool_func, *args, **kwargs) -> Dict[str, Any]:
    _ensure_bindings()
    raw = tool_func(*args, **kwargs)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"message": raw}
    if isinstance(raw, dict):
        return raw
    return {"value": raw}


@mcp.tool()
def list_devices(scope: str = "both") -> str:
    """List devices using canonical live, inventory, or merged semantics."""
    return json.dumps(_tool_payload(network_tools.list_all_devices, scope=scope), ensure_ascii=False)


@mcp.tool()
def get_device_details(hostname: str) -> str:
    """Get authoritative device details with inventory, live, and canonical planned-vs-live semantics."""
    inventory = _tool_payload(network_tools.get_inventory_device, hostname)
    live = _tool_payload(network_tools.get_device_details, hostname)
    canonical = _tool_payload(network_tools.compare_planned_vs_live, hostname=hostname)
    device_state = None
    for item in canonical.get("devices") or []:
        if str(item.get("hostname") or "").lower() == hostname.lower():
            device_state = item
            break
    return json.dumps(
        {
            "hostname": hostname,
            "inventory": inventory,
            "live": live,
            "canonical_state": device_state,
            "authority_map_version": canonical.get("authority_map_version") or inventory.get("authority_map_version"),
            "semantics_map": canonical.get("semantics_map") or inventory.get("semantics_map"),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def get_canonical_semantics() -> str:
    """Return the canonical authority map and semantics contract shared across MCP, REST, prompt context, and grounding."""
    return json.dumps(_tool_payload(network_tools.get_canonical_semantics_map), ensure_ascii=False)


@mcp.tool()
def compare_device_state(hostname: str = "") -> str:
    """Compare planned/source-of-truth and live device state using the canonical authority map."""
    return json.dumps(_tool_payload(network_tools.compare_planned_vs_live, hostname=hostname), ensure_ascii=False)


@mcp.tool()
def run_command(hostname: str, command: str) -> str:
    """Fail closed for free-form operational commands to avoid fabricated or unsafe command execution."""
    return json.dumps(
        {
            "error": _DEPRECATED_UNSAFE_MESSAGE,
            "hostname": hostname,
            "command": command,
            "allowed_alternatives": [
                "get_device_details",
                "compare_device_state",
                "get_canonical_semantics",
            ],
            "status": "rejected",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def plan_deployment(description: str, devices: list[str]) -> str:
    """Fail closed for free-form deployment planning requests that lack a validated structured intent."""
    return json.dumps(
        {
            "error": (
                "Free-form deployment planning through MCP is disabled until a validated structured intent "
                "is supplied through the approval-gated change-planning path."
            ),
            "description": description,
            "devices": devices,
            "status": "rejected",
            "allowed_alternatives": [
                "Use the REST or chat proposal workflow backed by parse_intent, validate_intent, and plan_change",
                "Use get_canonical_semantics to inspect the authority map before planning",
            ],
        },
        ensure_ascii=False,
    )
