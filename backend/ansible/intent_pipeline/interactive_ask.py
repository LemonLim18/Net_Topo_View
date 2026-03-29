"""
Build user-facing ask sequences for missing / invalid / ambiguous fields (REFACTOR_INTENT_PARSER.md §5).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_interactive_asks(
    *,
    catalog_missing: List[str],
    cross_check: Dict[str, Any],
    requires_resolution: bool = False,
    catalog_forbidden_violations: Optional[List[str]] = None,
    dependency_notes: Optional[List[str]] = None,
    target_device_candidates: Optional[List[str]] = None,
    target_device_candidate_reason: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return ordered prompts for chat / workbench UI.

    authoritative_source_order reminder:
    user_selected_answer > planned_network > schema_defaults > llm_inference
    """
    asks: List[Dict[str, Any]] = []

    skip_missing_target = bool(target_device_candidates) and "target_devices" in (catalog_missing or [])

    for field in catalog_missing:
        if skip_missing_target and field == "target_devices":
            continue
        asks.append(
            {
                "kind": "missing_required",
                "field": field,
                "question": _default_question_for_field(field),
                "source": "catalog_validation",
            }
        )

    if target_device_candidates:
        reason = (target_device_candidate_reason or "").strip()
        asks.append(
            {
                "kind": "select_planned_devices",
                "field": "target_devices",
                "question": (
                    "The prompt used access/distribution/core switch numbers (not exact planned hostnames). "
                    "Select one or more devices below. You can also pick from the full planned network list."
                    + (f" ({reason})" if reason else "")
                ),
                "suggestions": list(target_device_candidates),
                "multi_select": True,
                "planned_network_context": True,
                "candidate_shortlist": True,
                "source": "planned_network_context",
            }
        )

    for fname in catalog_forbidden_violations or []:
        asks.append(
            {
                "kind": "forbidden_field",
                "field": fname,
                "question": (
                    f"Parameter `{fname}` is not allowed for this operation per the intent catalog. "
                    "Remove it or confirm a different operation."
                ),
                "source": "catalog_validation",
            }
        )

    for note in dependency_notes or []:
        n = str(note).strip()
        if not n:
            continue
        asks.append(
            {
                "kind": "schema_dependency_note",
                "field": "dependencies",
                "question": n,
                "source": "catalog_schema",
            }
        )

    for inv in cross_check.get("invalid_devices") or []:
        tok = str(inv.get("token") or "").strip() or "(unknown)"
        asks.append(
            {
                "kind": "invalid_inventory",
                "field": "target_devices",
                "unknown_device_name": tok,
                "question": (
                    f"Device '{tok}' was not found in the planned network context. "
                    f"Did you mean one of the following? Select one or more devices from your planned inventory."
                ),
                "suggestions": inv.get("suggestions") or [],
                "multi_select": bool(inv.get("multi_select", True)),
                "planned_network_context": bool(inv.get("planned_network_context", True)),
                "source": "planned_network_context",
            }
        )

    for inv in cross_check.get("invalid_interfaces") or []:
        asks.append(
            {
                "kind": "invalid_interface",
                "field": inv.get("field") or "interfaces",
                "hostname": inv.get("hostname"),
                "value": inv.get("value"),
                "question": inv.get("message")
                or (
                    f"Interface '{inv.get('value')}' was not found on '{inv.get('hostname')}' in the planned network context. "
                    "Select one or more interfaces from the list below."
                ),
                "suggestions": inv.get("suggestions") or [],
                "multi_select": bool(inv.get("multi_select", True)),
                "planned_network_context": bool(inv.get("planned_network_context", True)),
                "source": "planned_network_context",
            }
        )

    if requires_resolution and not asks:
        asks.append(
            {
                "kind": "resolution",
                "field": "general",
                "question": "Additional details are required to run this change safely.",
                "source": "schema",
            }
        )

    return asks


def _default_question_for_field(field: str) -> str:
    return {
        "vlan_id": "Which VLAN ID (1–4094)?",
        "vlan_name": "What name should this VLAN have?",
        "target_devices": "Which device(s) from the planned network should this apply to? (Select from inventory — do not guess names.)",
        "interfaces": "Which interfaces (per device) should be configured?",
        "interfaces_to_shutdown": "Which interfaces should this apply to (admin up or down follows your prompt)?",
        "next_hop": "What is the next-hop IP address?",
        "destination": "What is the destination prefix (e.g. 0.0.0.0/0)?",
        "acl_name": "What ACL name or number should be used?",
        "policy_name": "What QoS policy name should be used?",
        "process_id": "Which OSPF process ID?",
    }.get(field, f"Please provide a value for `{field}`.")
