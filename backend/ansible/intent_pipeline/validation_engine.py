"""
Generic schema validation engine (catalog-driven).

Used by ``validator.py`` (stable import path) and orchestration. Applies:
- required_parameters / implicit_requires_targets
- optional_parameters (documented; not used as hard missing checks)
- forbidden_fields (non-empty presence → violation)
- dependencies (surfaced as notes for pipeline / future rule engine)

Operation lookup uses :mod:`schema_recovery` when catalog has no row (adapter remap or synthetic).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ansible.intent_pipeline.schema_loader import (
    find_operation_spec_by_operation_key,
    find_operation_spec_in_catalog,
    get_catalog,
)
from ansible.intent_pipeline.schema_recovery import find_operation_spec_with_recovery

logger = logging.getLogger(__name__)


def _scope_has_devices(data: Dict[str, Any]) -> bool:
    scope = data.get("scope") or {}
    if not isinstance(scope, dict):
        return False
    hosts = scope.get("device_hostnames") or []
    if isinstance(hosts, list) and any(str(h).strip() for h in hosts):
        return True
    if scope.get("all_devices"):
        return True
    if scope.get("device_role"):
        return True
    return False


def _vlan_entries_from_params(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    vlans = params.get("vlans")
    if isinstance(vlans, list):
        for item in vlans:
            if isinstance(item, dict):
                out.append(dict(item))
            elif item is not None:
                try:
                    out.append({"id": int(item)})
                except (TypeError, ValueError):
                    out.append({"id": item})
    if not out and (params.get("vlan_id") is not None or params.get("vlan_name")):
        row: Dict[str, Any] = {}
        if params.get("vlan_id") is not None:
            row["id"] = params["vlan_id"]
        if params.get("vlan_name") is not None:
            row["name"] = params["vlan_name"]
        if row:
            out.append(row)
    return out


def _vlan_id_satisfied(params: Dict[str, Any]) -> bool:
    for e in _vlan_entries_from_params(params):
        if not isinstance(e, dict):
            continue
        vid = e.get("id")
        if vid is None or vid == "":
            continue
        try:
            int(str(vid).strip())
            return True
        except (TypeError, ValueError):
            if str(vid).strip():
                return True
    return False


def _vlan_name_satisfied(params: Dict[str, Any]) -> bool:
    for e in _vlan_entries_from_params(params):
        if not isinstance(e, dict):
            continue
        n = e.get("name")
        if n is not None and str(n).strip():
            return True
    vn = params.get("vlan_name")
    return bool(vn is not None and str(vn).strip())


def _targets_resolved(data: Dict[str, Any], params: Dict[str, Any]) -> bool:
    if _scope_has_devices(data):
        return True
    for key in ("target_devices", "target_hosts"):
        v = params.get(key)
        if isinstance(v, list) and any(str(x).strip() for x in v):
            return True
        if v is not None and str(v).strip():
            return True
    rd = data.get("resolved_devices")
    if isinstance(rd, list) and any(str(x).strip() for x in rd):
        return True
    # Do not treat device_deduction.deduced as resolved targets (avoid heuristic / stale lists).
    return False


def _expand_catalog_field(field: str, _action: str, _feature: str, params: Dict[str, Any]) -> List[str]:
    if field == "vlans":
        missing: List[str] = []
        if not _vlan_id_satisfied(params):
            missing.append("vlan_id")
        if not _vlan_name_satisfied(params):
            missing.append("vlan_name")
        return missing
    return [field]


def _pending_target_device_ui_selection(params: Dict[str, Any]) -> bool:
    """True when parser left a shortlist for admin to confirm (not yet resolved_devices)."""
    c = params.get("_target_device_candidates")
    if not isinstance(c, list):
        return False
    return any(str(x).strip() for x in c)


def _param_nonempty(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, list):
        return any(_param_nonempty(x) for x in val)
    if isinstance(val, dict):
        return bool(val)
    return str(val).strip() != ""


def _collect_forbidden_violations(params: Dict[str, Any], forbidden: List[Any]) -> List[str]:
    bad: List[str] = []
    for fname in forbidden or []:
        key = str(fname or "").strip()
        if not key:
            continue
        if _param_nonempty(params.get(key)):
            bad.append(key)
    return bad


def _rules_satisfied(params: Dict[str, Any]) -> bool:
    val = params.get("rules")
    if val is None:
        return False
    if isinstance(val, list):
        return any(_param_nonempty(x) for x in val)
    if isinstance(val, dict):
        return bool(val)
    return _param_nonempty(val)


def _iface_entry_meaningful(x: Any) -> bool:
    if isinstance(x, str):
        t = x.strip()
        return bool(t) and t.lower() not in ("",)
    if isinstance(x, dict):
        if x.get("all") is True:
            return True
        n = x.get("name") or x.get("interface") or x.get("interface_name")
        return bool(str(n or "").strip())
    return False


def _interfaces_list_satisfied(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, list):
        return any(_iface_entry_meaningful(x) for x in val)
    return _iface_entry_meaningful(val)


def _shutdown_targets_satisfied(val: Any) -> bool:
    """Explicit interfaces and/or admin-down all / wildcard."""
    if val is None:
        return False
    if isinstance(val, list):
        for x in val:
            if isinstance(x, str):
                t = x.strip().lower()
                if t in ("all", "*", "any", "every"):
                    return True
                if t:
                    return True
            if isinstance(x, dict):
                if x.get("all") is True:
                    return True
                n = str(x.get("name") or "").strip().lower()
                if n in ("all", "*"):
                    return True
                if x.get("name") or x.get("interface"):
                    return True
        return False
    if isinstance(val, str):
        return bool(val.strip())
    return False


def _destination_satisfied(params: Dict[str, Any]) -> bool:
    if _param_nonempty(params.get("destination")):
        return True
    if params.get("default_route") is True:
        return True
    pfx = params.get("prefix") or params.get("route_prefix")
    return _param_nonempty(pfx)


def _next_hop_satisfied(params: Dict[str, Any]) -> bool:
    if _param_nonempty(params.get("next_hop")):
        return True
    if _param_nonempty(params.get("gateway")):
        return True
    return False


def _egress_interface_satisfied(params: Dict[str, Any]) -> bool:
    for k in ("egress_interface", "outgoing_interface", "exit_interface", "interface"):
        if _param_nonempty(params.get(k)):
            return True
    return False


def _ospf_networks_satisfied(params: Dict[str, Any]) -> bool:
    v = params.get("ospf_networks") or params.get("networks") or params.get("ospf_network")
    if isinstance(v, list):
        return any(_param_nonempty(x) for x in v)
    return _param_nonempty(v)


def _passive_interfaces_satisfied(params: Dict[str, Any]) -> bool:
    v = params.get("passive_interfaces") or params.get("passive_interface")
    if isinstance(v, list):
        return any(_param_nonempty(x) for x in v)
    return _param_nonempty(v)


def _redistribute_satisfied(params: Dict[str, Any]) -> bool:
    v = params.get("redistribute") or params.get("redistribution")
    if isinstance(v, list):
        return any(_param_nonempty(x) for x in v)
    return _param_nonempty(v)


def _router_id_satisfied(params: Dict[str, Any]) -> bool:
    return _param_nonempty(params.get("router_id"))


def _missing_for_catalog_field(
    field: str,
    data: Dict[str, Any],
    params: Dict[str, Any],
    action: str,
    feature: str,
) -> List[str]:
    if field == "target_devices":
        if _pending_target_device_ui_selection(params):
            return []
        return [] if _targets_resolved(data, params) else ["target_devices"]
    if field == "vlans":
        return _expand_catalog_field("vlans", action, feature, params)
    if field == "rules":
        return [] if _rules_satisfied(params) else ["rules"]
    if field == "interfaces":
        return [] if _interfaces_list_satisfied(params.get("interfaces")) else ["interfaces"]
    if field == "interface_apply":
        ia = params.get("interface_apply")
        if _interfaces_list_satisfied(ia):
            return []
        ato = params.get("apply_to")
        if _interfaces_list_satisfied(ato):
            return []
        if _interfaces_list_satisfied(params.get("interfaces")):
            return []
        return ["interface_apply"]
    if field == "interfaces_to_shutdown":
        v = params.get("interfaces_to_shutdown")
        return [] if _shutdown_targets_satisfied(v) else ["interfaces_to_shutdown"]
    if field == "policy_name":
        pn = params.get("policy_name") or params.get("qos_policy") or params.get("policy")
        return [] if _param_nonempty(pn) else ["policy_name"]
    if field == "marking_dscp":
        mv = params.get("marking_dscp")
        if _param_nonempty(mv):
            return []
        for k in ("dscp", "dscp_value", "set_dscp"):
            if _param_nonempty(params.get(k)):
                return []
        return ["marking_dscp"]
    if field == "traffic_type":
        tv = params.get("traffic_type")
        if _param_nonempty(tv):
            return []
        for k in ("qos_class", "class_name", "class"):
            if _param_nonempty(params.get(k)):
                return []
        return ["traffic_type"]
    if field == "police_rate":
        rv = params.get("police_rate")
        if _param_nonempty(rv):
            return []
        for k in ("policer_rate", "rate_limit", "bandwidth_limit"):
            if _param_nonempty(params.get(k)):
                return []
        return ["police_rate"]
    if field == "acl_name":
        an = (
            params.get("acl_name")
            or params.get("acl")
            or params.get("acl_number")
            or params.get("acl_id")
        )
        return [] if _param_nonempty(an) else ["acl_name"]
    if field == "destination":
        return [] if _destination_satisfied(params) else ["destination"]
    if field == "next_hop":
        return [] if _next_hop_satisfied(params) else ["next_hop"]
    if field == "egress_interface":
        return [] if _egress_interface_satisfied(params) else ["egress_interface"]
    if field == "process_id":
        pid = params.get("process_id") or params.get("ospf_process") or params.get("process")
        return [] if _param_nonempty(pid) else ["process_id"]
    if field == "ospf_networks":
        return [] if _ospf_networks_satisfied(params) else ["ospf_networks"]
    if field == "passive_interfaces":
        return [] if _passive_interfaces_satisfied(params) else ["passive_interfaces"]
    if field == "redistribute":
        return [] if _redistribute_satisfied(params) else ["redistribute"]
    if field == "router_id":
        return [] if _router_id_satisfied(params) else ["router_id"]
    val = params.get(field)
    if val is None or val == "" or val == []:
        return [field]
    return []


def _dedupe_preserve(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _missing_for_required_fields(
    fields: List[Any],
    data: Dict[str, Any],
    params: Dict[str, Any],
    action: str,
    feature: str,
) -> List[str]:
    acc: List[str] = []
    for f in fields or []:
        acc.extend(_missing_for_catalog_field(str(f), data, params, action, feature))
    return _dedupe_preserve(acc)


def resolve_operation_spec_for_intent(data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any], str]]:
    """
    Prefer ``parameters._canonical_operation`` / ``canonical_operation`` (fine-grained op key)
    so validation matches the adapter-mapped legacy action.
    """
    cat = get_catalog()
    params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    params = params or {}
    action = str(data.get("action") or "")
    feature = str(data.get("feature") or "")
    co = str(params.get("_canonical_operation") or params.get("canonical_operation") or "").strip()
    if co:
        hk = find_operation_spec_by_operation_key(cat, co)
        if hk:
            return hk[0], hk[1], "catalog"
        hit = find_operation_spec_with_recovery(cat, co, feature)
        if hit:
            return hit
    return find_operation_spec_with_recovery(cat, action, feature)


def resolve_operation_spec(
    action: str,
    feature: str,
    *,
    catalog: Optional[Any] = None,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    cat = catalog or get_catalog()
    return find_operation_spec_with_recovery(cat, action, feature)


def validate_llm_intent_dict(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Backward-compatible: (ok, missing_required_field_names)."""
    result = validate_intent_full(data)
    return result["ok"], result["missing"]


def validate_intent_full(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full validation payload for orchestration / asks / pipeline trace.

    Keys: ok, missing, forbidden_violations, dependency_notes, schema_source, operation_key

    Supports ``required_parameter_groups.any_of`` — satisfied if any inner group is complete.
    """
    action = str(data.get("action") or "")
    feature = str(data.get("feature") or "")
    params = data.get("parameters")
    if not isinstance(params, dict):
        params = {}

    hit = resolve_operation_spec_for_intent(data)
    if not hit:
        return {
            "ok": True,
            "missing": [],
            "forbidden_violations": [],
            "dependency_notes": [],
            "schema_source": None,
            "operation_key": None,
        }

    op_key, spec, source = hit[0], hit[1], hit[2]
    forbidden = list(spec.get("forbidden_fields") or [])
    deps = [str(d) for d in (spec.get("dependencies") or []) if str(d).strip()]

    groups_block = spec.get("required_parameter_groups") or {}
    any_of = groups_block.get("any_of") if isinstance(groups_block, dict) else None

    missing: List[str] = []
    if isinstance(any_of, list) and any_of:
        best_missing: Optional[List[str]] = None
        group_ok = False
        for grp in any_of:
            if not isinstance(grp, list):
                continue
            m = _missing_for_required_fields(grp, data, params, action, feature)
            if not m:
                group_ok = True
                break
            if best_missing is None or len(m) < len(best_missing):
                best_missing = m
        missing = [] if group_ok else (best_missing or [])
    else:
        required = list(spec.get("required_parameters") or [])
        missing = _missing_for_required_fields(required, data, params, action, feature)

    if (
        spec.get("implicit_requires_targets")
        and not _targets_resolved(data, params)
        and not _pending_target_device_ui_selection(params)
    ):
        if "target_devices" not in missing:
            missing.append("target_devices")

    forb = _collect_forbidden_violations(params, forbidden)
    ok = not missing and not forb

    return {
        "ok": ok,
        "missing": missing,
        "forbidden_violations": forb,
        "dependency_notes": deps,
        "schema_source": source,
        "operation_key": op_key,
    }


def annotate_missing_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Attach _catalog_missing_fields, _catalog_forbidden_violations, _catalog_dependency_notes."""
    out = dict(data)
    params = dict(out.get("parameters") or {})
    vr = validate_intent_full(out)

    if vr["missing"]:
        params["_catalog_missing_fields"] = vr["missing"]
        out["requires_resolution"] = True
    else:
        params.pop("_catalog_missing_fields", None)

    if vr["forbidden_violations"]:
        params["_catalog_forbidden_violations"] = vr["forbidden_violations"]
        out["requires_resolution"] = True
    else:
        params.pop("_catalog_forbidden_violations", None)

    if vr["dependency_notes"]:
        params["_catalog_dependency_notes"] = vr["dependency_notes"]
    else:
        params.pop("_catalog_dependency_notes", None)

    if vr.get("schema_source"):
        params["_catalog_schema_source"] = vr["schema_source"]
    if vr.get("operation_key"):
        params["_catalog_operation_key"] = vr["operation_key"]

    out["parameters"] = params
    return out
