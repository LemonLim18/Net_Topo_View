"""
Declarative Intent Tree engine.

Scope (Phase 1): enforce root-first device scope and handle "all interfaces" expansion,
including conditional interface leaf checks (best-effort; the controller may also rely
on existing JSON schema + UI overlays).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ansible.context_entity_validation import (
    intent_requests_all_interfaces,
)

logger = logging.getLogger("ansible.intent_tree_engine")

_INTENT_TREE_PATH = Path(__file__).parent / "intent_tree.json"


@dataclass
class IntentTreeResult:
    needs_target_devices: bool = False
    all_interfaces_requested: bool = False
    interface_action: Optional[str] = None  # shutdown_interface|configure_interface|None
    # Additional execution flags that api.py/templates can consume
    execution_flags: Dict[str, Any] = None  # type: ignore[assignment]
    # Best-effort conditional leaf questions
    missing_interface_fields: List[Dict[str, str]] = None  # type: ignore[assignment]
    # UI disambiguation fields (flat keys expected by existing wizard)
    params_disambiguation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "needs_target_devices": self.needs_target_devices,
            "all_interfaces_requested": self.all_interfaces_requested,
            "interface_action": self.interface_action,
            "execution_flags": self.execution_flags or {},
            "missing_interface_fields": self.missing_interface_fields or [],
            "params_disambiguation": self.params_disambiguation,
        }


def _intent_explicitly_requests_broad_device_scope(intent_description: str) -> bool:
    """
    Mirror the "do not clear targets when user explicitly asked for whole scope" heuristic.
    This is intentionally conservative.
    """
    t = re.sub(r"\s+", " ", (intent_description or "").strip().lower())
    if not t:
        return False
    if re.search(r"\ball\b", t) and any(w in t for w in ("switch", "switches", "device", "devices")):
        return True
    for phrase in (
        "all access",
        "all core",
        "all distribution",
        "every switch",
        "every device",
        "entire network",
        "whole network",
    ):
        if phrase in t:
            return True
    if re.search(r"\b(access|distribution|dist|core)\s+(switch|switches)\b", t):
        return True
    return False


def _deduce_needs_target_devices(
    intent_description: str,
    resolved_devices: List[str],
    device_deduction: Optional[Dict[str, Any]],
    db_manager: Any,
) -> bool:
    """
    True when we should not accept a bulk-resolved device set as "user intent" and should ask.

    We use the same signal as api.py: if resolved_devices is large and most hosts were chosen
    only by "inventory similarity", treat as unclear.
    """
    if len(resolved_devices) < 2:
        return False
    if _intent_explicitly_requests_broad_device_scope(intent_description):
        return False

    try:
        n_inv = len([d for d in db_manager.get_all_devices() if getattr(d, "hostname", None)]) if db_manager else 0
    except Exception:
        n_inv = 0

    if n_inv and len(resolved_devices) >= max(4, int(0.5 * n_inv)):
        return True

    mapping = (device_deduction or {}).get("mapping") or []
    if not mapping:
        return len(resolved_devices) >= 3

    sim = 0
    for m in mapping:
        if str((m.get("reason") or "").strip()) == "matched by inventory similarity":
            sim += 1
    return (sim / max(len(mapping), 1)) >= 0.75


def _extract_interface_action(parsed_intent: Dict[str, Any]) -> Optional[str]:
    action = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feature = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    # In this system, interface templates are driven by action for interface changes.
    if action in ("shutdown_interface", "configure_interface"):
        return action
    if feature == "interface" and action:
        # Leave unknown action as-is; controller will still schema-validate later.
        return action
    return action or (feature if feature in ("shutdown_interface", "configure_interface") else None)


def _infer_all_interface_mode_defaults(
    params: Dict[str, Any],
    interface_action: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[List[int]]]:
    """
    Extract best-effort shared defaults for configure_interface when we need to
    expand interfaces=='all' without an explicit interface list.

    Returns: (default_mode, access_vlan_str, trunk_vlans_list)
    """
    default_mode = params.get("default_mode") or params.get("mode")
    if default_mode is not None:
        default_mode = str(default_mode).strip().lower()

    access_vlan = params.get("access_vlan")
    if access_vlan is not None:
        try:
            access_vlan = int(access_vlan)
        except Exception:
            access_vlan = None

    trunk_vlans = params.get("trunk_vlans")
    if trunk_vlans is not None and isinstance(trunk_vlans, list):
        out: List[int] = []
        for x in trunk_vlans:
            try:
                out.append(int(x))
            except Exception:
                pass
        trunk_vlans = out

    # For shutdown_interface, these are not required.
    if interface_action == "shutdown_interface":
        default_mode = None
        access_vlan = None  # type: ignore[assignment]
        trunk_vlans = None  # type: ignore[assignment]

    return default_mode, access_vlan, trunk_vlans  # type: ignore[return-value]


def evaluate_intent_tree(
    *,
    intent_description: str,
    parsed_intent: Dict[str, Any],
    resolved_hosts: List[str],
    db_manager: Any,
) -> IntentTreeResult:
    """
    Evaluate Phase 1 rules and return execution flags.

    Note: this intentionally does not replace the existing JSON schema validation.
    It only provides early root-first enforcement and deterministic execution flags
    that templates can consume.
    """
    res = IntentTreeResult(
        needs_target_devices=False,
        all_interfaces_requested=False,
        interface_action=None,
        execution_flags={},
        missing_interface_fields=[],
        params_disambiguation=None,
    )

    interface_action = _extract_interface_action(parsed_intent)
    res.interface_action = interface_action

    params = parsed_intent.get("parameters") or parsed_intent.get("params") or {}
    device_deduction = parsed_intent.get("device_deduction") or {}

    # Phase 2/3 (partial): category required leaf questions (before controller/schema).
    # This is not a full schema replacement; we just surface missing required leaves
    # as UI questions early so we can fail fast before any LLM playbook generation.
    action = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feature = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")

    def _is_missing(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, str):
            return v.strip() == ""
        if isinstance(v, list):
            return len(v) == 0
        return False

    # ACL
    if not res.params_disambiguation and (action == "configure_acl" or feature == "acl"):
        missing_fields: List[Dict[str, str]] = []
        if _is_missing(params.get("acl_name")):
            missing_fields.append({"field": "acl_name", "question": "ACL name or number?"})
        if _is_missing(params.get("acl_type")):
            missing_fields.append({"field": "acl_type", "question": "ACL type (ipv4, ipv6, mac, or l2)?"})
        if _is_missing(params.get("rules")):
            missing_fields.append(
                {"field": "rules", "question": "ACL rules (permit/deny, protocol, src/dst) or template name?"}
            )
        if missing_fields:
            res.params_disambiguation = {"question": "Please provide the following.", "fields": missing_fields}

    # QoS
    if not res.params_disambiguation and (action == "configure_qos" or feature == "qos"):
        missing_fields = []
        if _is_missing(params.get("policy_name")):
            missing_fields.append({"field": "policy_name", "question": "QoS policy name?"})
        if _is_missing(params.get("qos_type")):
            missing_fields.append({"field": "qos_type", "question": "QoS construct type (class-map, policy-map, or service-policy)?"})
        if _is_missing(params.get("classification_rules")):
            missing_fields.append(
                {"field": "classification_rules", "question": "Classification (match DSCP/ACL/COS/IP) and actions?"}
            )
        if missing_fields:
            res.params_disambiguation = {"question": "Please provide the following.", "fields": missing_fields}

    # OSPF
    if not res.params_disambiguation and (action == "configure_ospf" or feature == "ospf"):
        missing_fields = []
        if _is_missing(params.get("process_id")):
            missing_fields.append({"field": "process_id", "question": "OSPF process ID?"})
        if _is_missing(params.get("networks")):
            missing_fields.append(
                {"field": "networks", "question": "Which networks (CIDR) to advertise and in which area?"}
            )
        if missing_fields:
            res.params_disambiguation = {"question": "Please provide the following.", "fields": missing_fields}

    # static_route / routing
    if not res.params_disambiguation and (action == "configure_static_route" or feature in ("static_route", "routing")):
        destination = params.get("destination")
        next_hop = params.get("next_hop")
        missing_fields = []
        if _is_missing(destination):
            missing_fields.append({"field": "destination", "question": "Destination prefix (CIDR, e.g. 0.0.0.0/0)?"})
        if _is_missing(next_hop):
            missing_fields.append({"field": "next_hop", "question": "Next hop IP or egress interface?"})
        if missing_fields:
            res.params_disambiguation = {"question": "Please provide the following.", "fields": missing_fields}

    # VLAN
    if not res.params_disambiguation and (action == "create_vlan" or feature == "vlan"):
        vlan_id = params.get("vlan_id")
        missing_fields = []
        if _is_missing(vlan_id):
            missing_fields.append({"field": "vlan_id", "question": "Which VLAN ID (1-4094)?"})
        if missing_fields:
            res.params_disambiguation = {"question": "Please provide the following.", "fields": missing_fields}

    # 1) Root-first: ensure target devices are clear enough to proceed.
    res.needs_target_devices = _deduce_needs_target_devices(
        intent_description=intent_description,
        resolved_devices=list(resolved_hosts or []),
        device_deduction=device_deduction if isinstance(device_deduction, dict) else None,
        db_manager=db_manager,
    )

    # 2) "All interfaces" expansion
    wants_all = intent_requests_all_interfaces(intent_description)
    res.all_interfaces_requested = wants_all

    # Only produce interface execution flags when:
    # - it's an interface-changing intent
    # - we have at least one target host
    if (
        wants_all
        and resolved_hosts
        and interface_action in ("shutdown_interface", "configure_interface")
    ):
        # For shutdown_interface we already have a known safe execution path.
        # For configure_interface we set a separate flag so templates can compute
        # per-host interfaces_config from nettopo_interfaces.
        if interface_action == "shutdown_interface":
            res.execution_flags["interfaces_shutdown_use_nettopo"] = True
        else:
            res.execution_flags["interfaces_config_use_nettopo"] = True

        # Controller may decide whether to clear explicit interface lists after schema validation.
        res.execution_flags["tree_all_interfaces_requested"] = True

        # Best-effort conditional questions only when configure_interface has no defaults.
        if interface_action == "configure_interface":
            default_mode, access_vlan, trunk_vlans = _infer_all_interface_mode_defaults(params, interface_action)
            if default_mode is None:
                res.missing_interface_fields.append({"field": "default_mode", "question": "Default interface mode for all interfaces (access or trunk)?"})
            elif default_mode == "access" and access_vlan is None:
                res.missing_interface_fields.append({"field": "access_vlan", "question": "Access VLAN for all interfaces?"})
            elif default_mode == "trunk" and not trunk_vlans:
                res.missing_interface_fields.append({"field": "trunk_vlans", "question": "Allowed trunk VLANs for all interfaces (comma-separated)?"})

    return res


def load_intent_tree() -> Dict[str, Any]:
    """Load the intent_tree JSON from disk (for debugging/inspection)."""
    if not _INTENT_TREE_PATH.exists():
        return {}
    try:
        return json.loads(_INTENT_TREE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

