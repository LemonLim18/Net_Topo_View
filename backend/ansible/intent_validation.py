"""
Intent schema tree and validation engine.

Per-action parameter dependency: required/optional fields, resolution_order (from_intent ->
from_inventory -> from_topology -> from_policy -> ask_user), and questions for disambiguation.
Validates completeness and type; resolves missing fields with resolution_trace; respects safe_mode.
"""

import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ansible.context_entity_validation import intent_requests_all_interfaces

logger = logging.getLogger("ansible.intent_validation")

_INTENT_SCHEMAS_DIR = Path(__file__).parent / "intent_schemas"

# Map feature (and optionally action) to schema file name for load_schema fallback
_FEATURE_ACTION_TO_SCHEMA: Dict[str, str] = {
    # Default interface feature maps to general configure_interface; shutdown uses action shutdown_interface.
    "interface": "configure_interface",
    "vlan": "create_vlan",
    "ospf": "configure_ospf",
    "static_route": "configure_static_route",
    "routing": "configure_static_route",
    "acl": "configure_acl",
    "qos": "configure_qos",
}


def _load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """Load a YAML file; return None on error."""
    if not path.is_file():
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("Failed to load schema %s: %s", path, e)
        return None


def load_schema(action_or_feature: str) -> Optional[Dict[str, Any]]:
    """
    Resolve action or feature to intent schema file and return schema dict.

    Uses action first (e.g. create_vlan -> create_vlan.yml), then fallback to
    feature (e.g. vlan -> create_vlan.yml). Returns dict with required, optional,
    resolution_order, questions.
    """
    if not action_or_feature or not _INTENT_SCHEMAS_DIR.is_dir():
        return None
    key = str(action_or_feature).strip().lower().replace("-", "_")
    # Direct action match
    candidate = key + ".yml"
    path = _INTENT_SCHEMAS_DIR / candidate
    if path.is_file():
        return _load_yaml(path)
    # Fallback: feature -> schema name
    schema_name = _FEATURE_ACTION_TO_SCHEMA.get(key)
    if schema_name:
        path = _INTENT_SCHEMAS_DIR / (schema_name + ".yml")
        if path.is_file():
            return _load_yaml(path)
    return None


def _get_type_schema_path(action_or_feature: str) -> Optional[Path]:
    """Return path to intent_schemas/<action_or_feature>.schema.json if it exists."""
    if not action_or_feature or not _INTENT_SCHEMAS_DIR.is_dir():
        return None
    key = str(action_or_feature).strip().lower().replace("-", "_")
    candidates = [key + ".schema.json"]
    mapped = _FEATURE_ACTION_TO_SCHEMA.get(key)
    if mapped and mapped != key:
        candidates.append(mapped + ".schema.json")
    for name in candidates:
        path = _INTENT_SCHEMAS_DIR / name
        if path.is_file():
            return path
    return None


def _normalize_intent_for_type_schema(intent: Dict[str, Any], schema_key: str) -> Dict[str, Any]:
    """
    Build a payload matching the JSON-Schema shape for type validation.
    schema_key: e.g. 'create_vlan', 'shutdown_interface'.
    """
    flat = _intent_to_flat_fields(intent, {"required": [], "optional": []})
    out: Dict[str, Any] = {}
    if schema_key == "create_vlan":
        out["target_hosts"] = flat.get("target_devices") or []
        vlan_id_raw = flat.get("vlan_id")
        vlan_name = flat.get("vlan_name")
        vlan_id: Optional[int] = None
        if vlan_id_raw is not None and str(vlan_id_raw).strip() != "":
            try:
                vlan_id = int(vlan_id_raw)
            except (TypeError, ValueError):
                pass
        out["vlan"] = {"id": vlan_id, "name": str(vlan_name or "").strip() if vlan_name is not None else ""}
        ifaces = flat.get("interfaces") or []
        out["interfaces"] = []
        for item in ifaces:
            if isinstance(item, str):
                # When intent supplies only interface names for VLAN binding,
                # treat it as "bind VLAN as access VLAN" and default access_vlan=vlan_id.
                # This avoids type validation failures on create_vlan.schema.json.
                iface_obj: Dict[str, Any] = {"name": item, "mode": "access"}
                if vlan_id is not None:
                    iface_obj["access_vlan"] = vlan_id
                out["interfaces"].append(iface_obj)
            elif isinstance(item, dict):
                iface_obj = dict(item)
                # Default mode/access_vlan semantics when LLM/intent omitted them.
                # If mode is explicitly access and access_vlan missing, use vlan_id.
                mode = str(iface_obj.get("mode") or "access").strip().lower()
                if not iface_obj.get("mode"):
                    iface_obj["mode"] = mode
                if mode == "access" and vlan_id is not None:
                    if iface_obj.get("access_vlan") in (None, "", []):
                        iface_obj["access_vlan"] = vlan_id
                # Optional JSON-Schema fields must be omitted when null (not JSON null vs integer).
                for _null_opt in ("native_vlan", "access_vlan", "allowed_vlans"):
                    if _null_opt in iface_obj and iface_obj[_null_opt] is None:
                        del iface_obj[_null_opt]
                out["interfaces"].append(iface_obj)
            else:
                iface_obj = {"name": str(item), "mode": "access"}
                if vlan_id is not None:
                    iface_obj["access_vlan"] = vlan_id
                out["interfaces"].append(iface_obj)
    elif schema_key == "shutdown_interface":
        out["target_hosts"] = flat.get("target_devices") or []
        ifaces = flat.get("interfaces") or []
        out["interfaces_to_shutdown"] = (
            [str(x) for x in ifaces]
            if all(isinstance(x, str) for x in ifaces)
            else [str(x.get("name", x)) for x in ifaces]
        )
    elif schema_key == "configure_interface":
        out["target_hosts"] = flat.get("target_devices") or []
        params_raw = dict(intent.get("parameters") or intent.get("params") or {})
        def_mode = str(params_raw.get("default_mode") or "").strip().lower()
        if def_mode not in ("access", "trunk", "routed"):
            def_mode = "access"
        ifaces = flat.get("interfaces") or []
        norm: List[Dict[str, Any]] = []
        for item in ifaces:
            if isinstance(item, str):
                norm.append({"name": item.strip(), "mode": def_mode})
            elif isinstance(item, dict):
                d = dict(item)
                if not str(d.get("mode") or "").strip():
                    d["mode"] = def_mode
                for _null_opt in ("native_vlan", "access_vlan", "allowed_vlans", "trunk_vlans"):
                    if _null_opt in d and d[_null_opt] is None:
                        del d[_null_opt]
                norm.append(d)
            else:
                norm.append({"name": str(item), "mode": def_mode})
        out["interfaces"] = norm
    elif schema_key == "configure_acl":
        out["target_hosts"] = flat.get("target_devices") or []
        out["acl_name"] = str(flat.get("acl_name") or "").strip()
        out["acl_type"] = str(flat.get("acl_type") or "").strip().lower()
        rules = flat.get("rules")
        if isinstance(rules, str) and str(rules).strip():
            out["rules"] = [str(rules).strip()]
        elif isinstance(rules, list):
            out["rules"] = rules
        else:
            out["rules"] = []
        apply_to = flat.get("apply_to")
        if apply_to is not None:
            out["apply_to"] = apply_to if isinstance(apply_to, list) else [apply_to] if apply_to else []
    elif schema_key == "configure_ospf":
        out["target_hosts"] = flat.get("target_devices") or []
        pid = flat.get("process_id")
        try:
            out["process_id"] = int(pid) if pid is not None and str(pid).strip() != "" else None
        except (TypeError, ValueError):
            out["process_id"] = None
        nets = flat.get("networks")
        if isinstance(nets, str) and nets.strip():
            out["networks"] = [x.strip() for x in nets.replace(",", " ").split() if x.strip()]
        elif isinstance(nets, list):
            out["networks"] = nets
        else:
            out["networks"] = []
        if flat.get("router_id"):
            out["router_id"] = str(flat.get("router_id")).strip()
        for opt in ("area_definitions", "passive_interfaces", "redistribution"):
            if flat.get(opt) is not None:
                out[opt] = flat.get(opt)
    elif schema_key == "configure_ospf_redistribute":
        out["target_hosts"] = flat.get("target_devices") or []
        pid = flat.get("process_id")
        try:
            out["process_id"] = int(pid) if pid is not None and str(pid).strip() != "" else None
        except (TypeError, ValueError):
            out["process_id"] = None
        red = flat.get("redistribution")
        if isinstance(red, list):
            out["redistribution"] = [str(x).strip() for x in red if str(x).strip()]
        elif red is not None and str(red).strip():
            out["redistribution"] = [str(red).strip()]
        else:
            out["redistribution"] = []
        if flat.get("router_id"):
            out["router_id"] = str(flat.get("router_id")).strip()
    elif schema_key == "configure_ospf_passive_interface":
        out["target_hosts"] = flat.get("target_devices") or []
        pid = flat.get("process_id")
        try:
            out["process_id"] = int(pid) if pid is not None and str(pid).strip() != "" else None
        except (TypeError, ValueError):
            out["process_id"] = None
        passive = flat.get("passive_interfaces")
        if isinstance(passive, list):
            out["passive_interfaces"] = [str(x).strip() for x in passive if str(x).strip()]
        elif passive is not None and str(passive).strip():
            out["passive_interfaces"] = [str(passive).strip()]
        else:
            out["passive_interfaces"] = []
        if flat.get("router_id"):
            out["router_id"] = str(flat.get("router_id")).strip()
    elif schema_key == "configure_qos":
        out["target_hosts"] = flat.get("target_devices") or []
        out["policy_name"] = str(flat.get("policy_name") or "").strip()
        out["qos_type"] = str(flat.get("qos_type") or "").strip().lower()
        cr = flat.get("classification_rules")
        if isinstance(cr, str) and cr.strip():
            out["classification_rules"] = [cr.strip()]
        elif isinstance(cr, list):
            out["classification_rules"] = cr
        else:
            out["classification_rules"] = []
        if flat.get("interface_apply") is not None:
            out["interface_apply"] = flat.get("interface_apply")
    elif schema_key == "configure_static_route":
        out["target_hosts"] = flat.get("target_devices") or []
        out["destination"] = str(flat.get("destination") or "").strip()
        out["next_hop"] = str(flat.get("next_hop") or "").strip()
        ad = flat.get("admin_distance")
        if ad is not None and str(ad).strip() != "":
            try:
                out["admin_distance"] = int(ad)
            except (TypeError, ValueError):
                out["admin_distance"] = ad
        if flat.get("vrf_name"):
            out["vrf_name"] = str(flat.get("vrf_name")).strip()
    else:
        out["target_hosts"] = flat.get("target_devices") or []
        out.setdefault("params", intent.get("parameters") or intent.get("params") or {})
    return out


def validate_types(intent: Dict[str, Any], action_or_feature: str) -> Tuple[bool, List[str]]:
    """
    Validate intent against per-action JSON-Schema (type, range, format).
    Returns (True, []) if valid, or (False, list of error messages).
    """
    path = _get_type_schema_path(action_or_feature)
    if not path:
        return True, []
    try:
        schema_dict = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load type schema %s: %s", path, e)
        return True, []
    key = path.stem.replace(".schema", "")
    payload = _normalize_intent_for_type_schema(intent, key)
    try:
        import jsonschema  # type: ignore
    except ModuleNotFoundError:
        logger.warning("jsonschema not installed; skipping type validation for %s", action_or_feature)
        return True, []
    try:
        jsonschema.validate(instance=payload, schema=schema_dict)
        return True, []
    except jsonschema.ValidationError as e:
        err_path = getattr(e, "absolute_path", None) or getattr(e, "path", None) or ()
        msg = getattr(e, "message", str(e))
        if err_path:
            msg = f"{msg} at {'/'.join(str(p) for p in err_path)}"
        return False, [msg]
    except Exception as e:
        return False, [str(e)]


def _intent_to_flat_fields(intent: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize intent to a flat field map for validation.
    Maps: target_devices <- resolved_devices | parameters.target_hosts | target_hosts
          interfaces <- parameters.interfaces_to_shutdown | parameters.interfaces
          vlan_id, vlan_name, etc. <- parameters.*
    """
    params = dict(intent.get("parameters") or intent.get("params") or {})
    resolved = list(intent.get("resolved_devices") or [])
    target_hosts = list(intent.get("target_hosts") or [])
    flat: Dict[str, Any] = {}

    # target_devices (from resolved_devices, params.target_hosts, params.target_devices, or target_hosts)
    if resolved:
        flat["target_devices"] = resolved
    elif params.get("target_devices"):
        td = params["target_devices"]
        flat["target_devices"] = td if isinstance(td, list) else [td] if td else []
    elif params.get("target_hosts"):
        th = params["target_hosts"]
        flat["target_devices"] = th if isinstance(th, list) else [th] if th else []
    elif target_hosts:
        flat["target_devices"] = target_hosts
    else:
        flat["target_devices"] = []

    # interfaces / interfaces_to_shutdown
    ifaces = params.get("interfaces_to_shutdown") or params.get("interfaces")
    if ifaces is not None:
        flat["interfaces"] = ifaces if isinstance(ifaces, list) else [ifaces] if ifaces else []
    else:
        flat["interfaces"] = None

    # VLAN
    flat["vlan_id"] = params.get("vlan_id")
    flat["vlan_name"] = params.get("vlan_name")
    if params.get("vlan") and isinstance(params["vlan"], dict):
        v = params["vlan"]
        if flat["vlan_id"] is None:
            flat["vlan_id"] = v.get("id") or v.get("vlan_id")
        if flat["vlan_name"] is None:
            flat["vlan_name"] = v.get("name") or v.get("vlan_name")

    # interface_binding (presence = interfaces configured for vlan)
    flat["interface_binding"] = params.get("interface_binding")
    if flat["interface_binding"] is None and (params.get("interfaces") or params.get("interfaces_config")):
        flat["interface_binding"] = params.get("interfaces") or params.get("interfaces_config")

    # OSPF, static_route, ACL, QoS
    flat["process_id"] = params.get("process_id")
    flat["router_id"] = params.get("router_id")
    flat["networks"] = params.get("networks")
    flat["destination"] = params.get("destination")
    flat["next_hop"] = params.get("next_hop")
    flat["acl_name"] = params.get("acl_name")
    flat["rules"] = params.get("rules")
    flat["policy_name"] = params.get("policy_name")
    flat["acl_type"] = params.get("acl_type")
    flat["apply_to"] = params.get("apply_to")
    flat["acl_direction"] = params.get("acl_direction") or params.get("direction")
    flat["desired_state"] = params.get("desired_state")
    flat["area_definitions"] = params.get("area_definitions")
    flat["passive_interfaces"] = params.get("passive_interfaces")
    flat["redistribution"] = params.get("redistribution")
    flat["admin_distance"] = params.get("admin_distance")
    flat["vrf_name"] = params.get("vrf_name")
    flat["qos_type"] = params.get("qos_type") or params.get("qos_construct_type")
    flat["classification_rules"] = params.get("classification_rules")
    flat["interface_apply"] = params.get("interface_apply")

    # ACL apply: if direction wasn't promoted to acl_direction, infer it from apply_to.
    # apply_to can be:
    # - ["Acc_Cisco_1:Ethernet0/1 in", ...]
    # - [{"interface": "Ethernet0/1", "direction": "in"}]
    if not flat.get("acl_direction") and flat.get("apply_to") is not None:
        ato = flat.get("apply_to")
        first = None
        if isinstance(ato, list) and ato:
            first = ato[0]
        elif isinstance(ato, str) and ato.strip():
            first = ato
        if isinstance(first, dict):
            d = str(first.get("direction") or "").strip().lower()
            if d in ("in", "out", "inbound", "outbound"):
                flat["acl_direction"] = "in" if d.startswith("in") else "out"
        elif isinstance(first, str):
            m = re.search(r"\b(in|out)\b\s*$", first.strip(), re.I)
            if m:
                flat["acl_direction"] = m.group(1).lower()

    # Static route: allow alternate LLM keys so we don't ask the user for values
    # that already exist in the parsed intent under different names.
    schema_action = str(schema.get("action") or "").strip().lower().replace("-", "_")
    if schema_action == "configure_static_route":
        def _nonempty_str(v: Any) -> bool:
            return v is not None and str(v).strip() != ""

        def _first_static_route_from_nested(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            # Common shapes seen in regression logs:
            # - parameters.static_routes: [{destination, next_hop}]
            # - parameters.interfaces[i].static_routes: [{destination, next_hop}]
            sr = p.get("static_routes") or p.get("routes") or p.get("static_route")
            if isinstance(sr, list):
                for item in sr:
                    if isinstance(item, dict) and _nonempty_str(item.get("destination")):
                        return item
            if isinstance(p.get("interfaces"), list):
                for iface in p.get("interfaces") or []:
                    if not isinstance(iface, dict):
                        continue
                    nested = iface.get("static_routes") or iface.get("static_route") or []
                    if isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, dict) and _nonempty_str(item.get("destination")):
                                return item
            return None

        if not _nonempty_str(flat.get("destination")):
            # “default route” often appears as a boolean.
            if params.get("default_route") is True:
                flat["destination"] = "0.0.0.0/0"
            else:
                for k in ("destination_network", "network", "route_destination", "destination_prefix"):
                    if _nonempty_str(params.get(k)):
                        flat["destination"] = str(params.get(k)).strip()
                        break
                if not _nonempty_str(flat.get("destination")):
                    nested = _first_static_route_from_nested(params)
                    if nested and _nonempty_str(nested.get("destination")):
                        flat["destination"] = str(nested.get("destination")).strip()

        if not _nonempty_str(flat.get("next_hop")):
            for k in ("gateway", "next_hop_ip", "route_gateway"):
                if _nonempty_str(params.get(k)):
                    flat["next_hop"] = str(params.get(k)).strip()
                    break
            if not _nonempty_str(flat.get("next_hop")):
                nested = _first_static_route_from_nested(params)
                if nested and _nonempty_str(nested.get("next_hop")):
                    flat["next_hop"] = str(nested.get("next_hop")).strip()

    return flat


def _is_empty(value: Any) -> bool:
    """True if value is considered missing/empty for completeness."""
    if value is None:
        return True
    if isinstance(value, (list, str)):
        return len(value) == 0
    if isinstance(value, (int, float)):
        return False  # 0 is valid for e.g. process_id
    return False


def interface_intent_params_satisfied(params: Optional[Dict[str, Any]]) -> bool:
    """
    True when API/merged params already specify at least one interface for shutdown/configure flows.

    Used by preview-intent and plan-ansible so we never re-prompt or re-run topology resolution
    just because the LLM left requires_resolution=true after the user (or wizard) filled params.
    """
    if not isinstance(params, dict):
        return False
    # Shutdown-all via per-host inventory at playbook runtime (multi-target safe).
    if params.get("interfaces_shutdown_use_nettopo") is True:
        return True
    shutdown = params.get("interfaces_to_shutdown")
    if shutdown is not None:
        if isinstance(shutdown, list):
            return any(str(x).strip() for x in shutdown if x is not None)
        return bool(str(shutdown).strip())
    raw = params.get("interfaces")
    if raw is None:
        return False
    if isinstance(raw, list):
        if len(raw) == 0:
            return False
        for x in raw:
            if isinstance(x, str) and str(x).strip():
                return True
            if isinstance(x, dict) and str(x.get("name") or x.get("interface") or "").strip():
                return True
        return False
    return bool(str(raw).strip())


def validate_completeness(
    intent: Dict[str, Any],
    schema: Dict[str, Any],
    action_or_feature: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """
    Check required fields are present and non-empty; optionally run JSON-Schema type validation.

    Returns (missing_fields, validation_errors). validation_errors include type/range/format
    errors from per-action JSON-Schema when action_or_feature is provided and a .schema.json exists.
    """
    flat = _intent_to_flat_fields(intent, schema)
    required = list(schema.get("required") or [])
    missing: List[str] = []
    errors: List[str] = []

    for field in required:
        value = flat.get(field)
        if _is_empty(value):
            missing.append(field)

    if action_or_feature:
        ok, type_errors = validate_types(intent, action_or_feature)
        if not ok:
            errors.extend(type_errors)

    return missing, errors


def build_params_disambiguation_fields_from_intent(intent: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Build chat/preview wizard fields from the operation-specific intent schema only
    (required fields missing on ``intent``). Uses ``schema_field_to_param_key`` so
    API keys match Studio (e.g. target_devices → target_hosts).

    Used by ``intent_tree_engine`` for early fail-fast alignment with ``intent_schemas/*.yml``.
    """
    schema = get_schema_for_parsed_intent(intent)
    if not schema:
        return []
    missing, _ = validate_completeness(intent, schema)
    if not missing:
        return []
    questions = schema.get("questions") or {}
    out: List[Dict[str, str]] = []
    for sf in missing:
        param_key = schema_field_to_param_key(schema, sf)
        q = questions.get(sf) or f"Please provide: {param_key}"
        out.append({"field": param_key, "question": str(q)})
    return out


def resolve_missing_fields(
    intent: Dict[str, Any],
    schema: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    safe_mode: bool = True,
    strict_user_input: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    For each missing field, try resolution_order; build resolution_trace.

    context: { "planning_context": ..., "resolve_interfaces_fn": callable, "inventory_lookup_fn": callable } (optional).
    inventory_lookup_fn(field, intent, context) -> (value, detail_str) or (None, None) for from_inventory resolution.
    safe_mode: if True, do not infer values that affect live network; ask user or fail.

    Returns (updated_intent, ask_user_fields, resolution_trace).
    ask_user_fields = [ {"field": str, "question": str} ]
    resolution_trace = [ {"field": str, "source": str, "detail": str} ]
    """
    import copy
    missing, _ = validate_completeness(intent, schema)
    if not missing:
        return intent, [], []

    intent = copy.deepcopy(intent)
    params = dict(intent.get("parameters") or intent.get("params") or {})
    intent.setdefault("parameters", params)
    schema_action = (schema.get("action") or "").strip().lower().replace("-", "_")
    resolution_order = schema.get("resolution_order") or {}
    questions = schema.get("questions") or {}
    ask_user_fields: List[Dict[str, Any]] = []
    resolution_trace: List[Dict[str, Any]] = []
    planning_context = (context or {}).get("planning_context") if context else None
    resolve_interfaces_fn = (context or {}).get("resolve_interfaces_fn") if context else None
    inventory_lookup_fn = (context or {}).get("inventory_lookup_fn") if context else None

    for field in missing:
        order = resolution_order.get(field)
        if not order:
            ask_user_fields.append({
                "field": field,
                "question": questions.get(field) or f"Please provide: {field}",
            })
            continue

        resolved = False
        for strategy in order:
            strategy = str(strategy).strip().lower()
            if strategy == "from_intent":
                # Re-check: might have been set by previous merge
                flat = _intent_to_flat_fields(intent, schema)
                if not _is_empty(flat.get(field)):
                    resolved = True
                    break
            elif strategy == "from_inventory":
                if strict_user_input:
                    continue
                if inventory_lookup_fn:
                    try:
                        value, detail = inventory_lookup_fn(field, intent, context or {})
                        if value is not None and not _is_empty(value):
                            if field == "target_devices":
                                params["target_hosts"] = value if isinstance(value, list) else [value]
                                intent["resolved_devices"] = params["target_hosts"]
                            elif field == "interfaces":
                                if schema_action == "shutdown_interface":
                                    params["interfaces_to_shutdown"] = value if isinstance(value, list) else [value]
                                else:
                                    params["interfaces"] = value if isinstance(value, list) else [value]
                            else:
                                params[field] = value
                            intent["parameters"] = params
                            resolution_trace.append({
                                "field": field,
                                "source": "inventory",
                                "detail": detail or f"Resolved from inventory/RAG",
                            })
                            resolved = True
                            break
                    except Exception as e:
                        logger.debug("from_inventory lookup failed: %s", e)
            elif strategy == "from_topology":
                if strict_user_input:
                    continue
                if field == "interfaces" and planning_context and resolve_interfaces_fn:
                    try:
                        names = resolve_interfaces_fn(intent, planning_context)
                        if names:
                            if schema_action == "shutdown_interface":
                                params["interfaces_to_shutdown"] = names
                            else:
                                params["interfaces"] = names
                            intent["parameters"] = params
                            resolution_trace.append({
                                "field": field,
                                "source": "topology",
                                "detail": f"Resolved {len(names)} interface(s) from planning_context",
                            })
                            resolved = True
                            break
                    except Exception as e:
                        logger.debug("from_topology resolve_interfaces failed: %s", e)
            elif strategy == "from_policy":
                if safe_mode:
                    # Do not apply policy defaults that affect live network
                    pass
                # else: placeholder for policy defaults
            elif strategy == "ask_user":
                ask_user_fields.append({
                    "field": field,
                    "question": questions.get(field) or f"Please provide: {field}",
                })
                resolved = True  # we are asking, so stop trying
                break

        if not resolved and field not in [a["field"] for a in ask_user_fields]:
            ask_user_fields.append({
                "field": field,
                "question": questions.get(field) or f"Please provide: {field}",
            })

    return intent, ask_user_fields, resolution_trace


def extract_create_vlan_params_from_natural_language(text: str) -> Dict[str, Any]:
    """
    Deterministic extraction of ``vlan_id`` / ``vlan_name`` from user text when the LLM
    leaves ``parameters`` empty (common with prompts like “access switch 1, 2, and 3”).
    Only fills keys that are clearly present in the description.
    """
    out: Dict[str, Any] = {}
    if not text or not str(text).strip():
        return out
    raw = str(text).strip()
    tl = raw.lower()
    # Prefer "create vlan N" / "vlan N" (1–4094); avoid relying on trailing "switch 1, 2, 3" digits.
    m = re.search(r"\b(?:create\s+)?vlan\s+(\d{1,4})\b", tl, re.I)
    if not m:
        m = re.search(r"\bvlan\s*-?\s*id\s*[=:\s#]+\s*(\d{1,4})\b", tl, re.I)
    if not m:
        m = re.search(r"\bvlan\s*[=:#]+\s*(\d{1,4})\b", tl, re.I)
    if m:
        try:
            vid = int(m.group(1))
            if 1 <= vid <= 4094:
                out["vlan_id"] = vid
        except ValueError:
            pass
    # Named / called / name: (allow mixed-case and common punctuation; stop at comma/space)
    nm = re.search(r"\bnamed\s+[\"']?([^\s,;]+)", raw, re.I)
    if not nm:
        nm = re.search(r"\bcalled\s+[\"']?([^\s,;]+)", raw, re.I)
    if not nm:
        nm = re.search(r"\bname\s*[=:]\s*[\"']?([^\s,;]+)", raw, re.I)
    if nm:
        name = nm.group(1).strip().strip("\"'.,;")
        if name and not name.isdigit():
            out["vlan_name"] = name
    return out


def merge_extracted_vlan_params_into_parsed_intent(
    parsed_intent: Dict[str, Any],
    intent_description: str,
) -> None:
    """Merge :func:`extract_create_vlan_params_from_natural_language` into ``parsed_intent['parameters']``."""
    feat = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    if feat != "vlan":
        return
    if act not in ("create_vlan", ""):
        return
    ext = extract_create_vlan_params_from_natural_language(intent_description or "")
    if not ext:
        return
    p = dict(parsed_intent.get("parameters") or {})
    if "vlan_id" in ext and not _params_satisfies_create_vlan_field(p, "vlan_id"):
        p["vlan_id"] = ext["vlan_id"]
    if "vlan_name" in ext and not _params_satisfies_create_vlan_field(p, "vlan_name"):
        p["vlan_name"] = ext["vlan_name"]
    parsed_intent["parameters"] = p
    correct_vlan_interface_binding_from_trunk_prompt(intent_description, parsed_intent)


def merge_extracted_vlan_params_into_flat_params(
    params: Dict[str, Any],
    intent_description: str,
) -> None:
    """Same as merge into primary parameters dict (plan-ansible / create-and-plan ``req.params``)."""
    ext = extract_create_vlan_params_from_natural_language(intent_description or "")
    if not ext:
        return
    if "vlan_id" in ext and not _params_satisfies_create_vlan_field(params, "vlan_id"):
        params["vlan_id"] = ext["vlan_id"]
    if "vlan_name" in ext and not _params_satisfies_create_vlan_field(params, "vlan_name"):
        params["vlan_name"] = ext["vlan_name"]


def user_prompt_mentions_vlan_id(text: str) -> bool:
    """
    True if the user text clearly states a VLAN numeric ID (not inferred by the LLM).
    Used so preview can still show params_disambiguation when the model invented vlan_id.
    """
    if not text or not str(text).strip():
        return False
    t = str(text).strip()
    tl = t.lower()
    # vlan 100, vlan id 10, VLAN ID: 20, vlan=99, vlan #30
    if re.search(r"\bvlan\s*-?\s*id\s*[=:\s#]+\d{1,4}\b", tl, re.I):
        return True
    if re.search(r"\bvlan\s*[=:#]+\s*\d{1,4}\b", tl, re.I):
        return True
    if re.search(r"\bvlan\s+#\s*\d{1,4}\b", tl, re.I):
        return True
    # "vlan 100" / "vlans 100" but not "vlan on" (next token must be digits)
    if re.search(r"\bvlans?\s+\d{1,4}\b", tl, re.I):
        return True
    return False


def user_prompt_mentions_vlan_name(text: str) -> bool:
    """True if the user text clearly names the VLAN (not only 'VLAN' as a feature word)."""
    if not text or not str(text).strip():
        return False
    t = str(text).strip()
    if re.search(r"\bname\s*[=:]\s*[\"']?[\w][\w\-_.]*", t, re.I):
        return True
    if re.search(r"\bnamed\s+[\"']?[\w][\w\-_.]*", t, re.I):
        return True
    if re.search(r"\bvlan\s+name\s*[=:\s]+[\"']?[\w][\w\-_.]*", t, re.I):
        return True
    if re.search(r"\bcalled\s+[\"']?[\w][\w\-_.]*", t, re.I):
        return True
    return False


def vlan_schema_fields_missing_from_user_prompt(intent_description: str) -> List[str]:
    """
    create_vlan fields the user did not explicitly state in natural language.
    Caller should add these to params_disambiguation even when parameters are LLM-complete.
    """
    desc = (intent_description or "").strip()
    out: List[str] = []
    if not user_prompt_mentions_vlan_id(desc):
        out.append("vlan_id")
    if not user_prompt_mentions_vlan_name(desc):
        out.append("vlan_name")
    return out


def _params_satisfies_create_vlan_field(parameters: Optional[Dict[str, Any]], field: str) -> bool:
    """
    True when merged intent parameters already contain vlan_id / vlan_name (from LLM, wizard, or API).
    Used so we do not re-ask in params_disambiguation after the user filled the chat wizard.
    """
    if not isinstance(parameters, dict):
        return False
    if field == "vlan_id":
        v = parameters.get("vlan_id")
        if v is not None and str(v).strip() != "":
            return True
        vlan = parameters.get("vlan")
        if isinstance(vlan, dict):
            vid = vlan.get("id")
            if vid is None:
                vid = vlan.get("vlan_id")
            if vid is not None and str(vid).strip() != "":
                return True
        return False
    if field == "vlan_name":
        v = parameters.get("vlan_name")
        if v is not None and str(v).strip() != "":
            return True
        vlan = parameters.get("vlan")
        if isinstance(vlan, dict):
            n = vlan.get("name")
            if n is None:
                n = vlan.get("vlan_name")
            if n is not None and str(n).strip() != "":
                return True
        return False
    return False


def _vlan_field_trusted_by_request(
    field: str,
    trusted_param_keys: Set[str],
    parameters: Optional[Dict[str, Any]],
) -> bool:
    """
    True when the client explicitly sent this field via preview ``req.params`` (wizard / API),
    not when the LLM invented it inside ``parameters`` before merge.

    Supports flat ``vlan_id`` / ``vlan_name`` and nested ``vlan: {id, name}``.
    """
    if not trusted_param_keys:
        return False
    t = trusted_param_keys
    params = parameters if isinstance(parameters, dict) else {}
    if field == "vlan_id":
        if "vlan_id" in t:
            return True
        if "vlan" in t and isinstance(params.get("vlan"), dict):
            v = params["vlan"]
            if v.get("id") not in (None, "") or v.get("vlan_id") not in (None, ""):
                return True
    if field == "vlan_name":
        if "vlan_name" in t:
            return True
        if "vlan" in t and isinstance(params.get("vlan"), dict):
            v = params["vlan"]
            if v.get("name") not in (None, "") or v.get("vlan_name") not in (None, ""):
                return True
    return False


def vlan_schema_fields_missing_from_user_prompt_respecting_params(
    intent_description: str,
    parameters: Optional[Dict[str, Any]],
    trusted_param_keys: Optional[Set[str]] = None,
) -> List[str]:
    """
    Same as ``vlan_schema_fields_missing_from_user_prompt``, but drops vlan_id / vlan_name when
    the user confirmed them out-of-band (preview wizard sends keys in ``req.params`` —
    ``trusted_param_keys``), because the raw ``intent_description`` never changes.

    Values that exist only because the LLM filled ``parameters`` must **not** suppress the
    wizard; otherwise missing VLAN names reappear as "sudden" regressions.
    """
    missing = vlan_schema_fields_missing_from_user_prompt(intent_description)
    trusted = trusted_param_keys or set()
    result: List[str] = []
    for f in missing:
        if f in ("vlan_id", "vlan_name"):
            if _vlan_field_trusted_by_request(f, trusted, parameters) and _params_satisfies_create_vlan_field(
                parameters, f
            ):
                continue
            result.append(f)
        else:
            result.append(f)
    return result


# ---------------------------------------------------------------------------
# Multi-intent / single-prompt: conditional follow-up fields per schema action
# ---------------------------------------------------------------------------
# Extend by registering callables: ``parameters`` dict -> list of
# ``{"field", "question"}`` for the chat wizard (primary or secondary slices).

# Chat-only keys (preview / wizard). Not sent to device JSON-Schema payloads.
CHAT_INTERFACE_LAYER_FIELD = "_chat_interface_layer"
CHAT_INTERFACE_L2_MODE_FIELD = "_chat_interface_l2_mode"


def user_prompt_has_explicit_interface_layer_intent(description: str) -> bool:
    """
    True when the user already indicated L2 (access/trunk/switchport) or L3 (IP/routed)
    clearly enough that we should not ask the generic L2-vs-L3 question.
    """
    text = str(description or "").strip()
    if not text:
        return False
    tl = re.sub(r"\s+", " ", text.lower())
    if intent_requests_all_interfaces(text):
        return True
    if re.search(r"\b(?:shutdown|shut\s*down|admin[- ]?down|disable)\b", tl) and re.search(
        r"\binterface", tl
    ):
        return True
    # L3 / routed
    if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b", text):
        return True
    if re.search(
        r"\b(?:ip\s+address|ipv4|subnet|cidr|routed|layer\s*3|\bl3\b|no\s+switchport|svi\b)\b",
        tl,
    ):
        return True
    if re.search(r"\bwith\s+ip\s+address\b", tl):
        return True
    # L2 switchport
    if re.search(
        r"\b(?:trunk|access\s+vlan|access\s+port|as\s+access|switchport|tagged|untagged|"
        r"native\s+vlan|allowed\s+vlan|voice\s+vlan|port[- ]channel)\b",
        tl,
    ):
        return True
    return False


def parameters_imply_interface_layer(parameters: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Return ``\"l2\"``, ``\"l3\"``, or None if parameters do not yet imply a layer.
    """
    if not isinstance(parameters, dict):
        return None
    # Ignore ``_chat_*`` wizard keys: they are pending choices until merged into ``default_mode`` / rows.
    dm = str(parameters.get("default_mode") or "").strip().lower()
    if dm in ("access", "trunk"):
        return "l2"
    if dm == "routed":
        return "l3"
    interfaces = parameters.get("interfaces") or []
    if not isinstance(interfaces, list):
        return None
    saw_l3 = False
    saw_l2 = False
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        mode = str(iface.get("mode") or "").strip().lower()
        if mode == "routed" or iface.get("ip_address") not in (None, ""):
            saw_l3 = True
        elif mode in ("access", "trunk"):
            saw_l2 = True
    if saw_l3 and not saw_l2:
        return "l3"
    if saw_l2 and not saw_l3:
        return "l2"
    if saw_l3 and saw_l2:
        return "l2"  # mixed; avoid blocking — let validation ask
    return None


def configure_interface_prompt_is_layer_ambiguous(
    intent_description: str,
    parameters: Optional[Dict[str, Any]],
) -> bool:
    """
    Super-vague interface configuration (e.g. "Configure interfaces on devices") with no
    L2/L3 hint in text or parameters.
    """
    if user_prompt_has_explicit_interface_layer_intent(intent_description):
        return False
    if parameters_imply_interface_layer(parameters) is not None:
        return False
    t = re.sub(r"\s+", " ", str(intent_description or "").strip().lower())
    if not t:
        return False
    if not re.search(r"\b(?:configure|set\s*up|setup|change|modify|update)\b", t):
        return False
    if not (
        re.search(r"\b(?:interface|interfaces|port|ports|switchport)\b", t)
        or re.search(r"\b(?:device|devices|switch|switches|router|routers)\b", t)
    ):
        return False
    return True


def apply_chat_interface_wizard_to_parameters(params: Dict[str, Any]) -> None:
    """
    Map chat wizard answers into ``default_mode`` so downstream validation and templates
    see consistent L2/L3 intent. Wizard keys stay in ``params`` for trusted re-merge.
    """
    if not isinstance(params, dict):
        return
    layer = str(params.get(CHAT_INTERFACE_LAYER_FIELD) or "").strip().lower()
    l2mode = str(params.get(CHAT_INTERFACE_L2_MODE_FIELD) or "").strip().lower()
    if layer == "l2" and l2mode in ("access", "trunk"):
        params["default_mode"] = l2mode
    elif layer == "l3":
        params["default_mode"] = "routed"
    dm = str(params.get("default_mode") or "").strip().lower()
    if dm in ("access", "trunk", "routed") and isinstance(params.get("interfaces"), list) and params["interfaces"]:
        reconcile_configure_interface_rows_with_default_mode(params)


def reconcile_configure_interface_rows_with_default_mode(params: Dict[str, Any]) -> None:
    """
    Align each interface row with ``default_mode`` after the L2/L3 wizard so Studio chips
    (often access-shaped) do not contradict routed/L2 intent.
    """
    if not isinstance(params, dict):
        return
    dm = str(params.get("default_mode") or "").strip().lower()
    if dm not in ("access", "trunk", "routed"):
        return
    ifaces = params.get("interfaces")
    if not isinstance(ifaces, list) or not ifaces:
        return
    out: List[Any] = []
    for item in ifaces:
        if isinstance(item, str):
            name = str(item).strip()
            if name:
                out.append({"name": name, "mode": dm})
            continue
        if not isinstance(item, dict):
            name = str(item).strip() if item else ""
            if name:
                out.append({"name": name, "mode": dm})
            continue
        row = dict(item)
        row["mode"] = dm
        if dm == "routed":
            row.pop("access_vlan", None)
            row.pop("trunk_vlans", None)
            row.pop("allowed_vlans", None)
        elif dm in ("access", "trunk"):
            row.pop("ip_address", None)
            row.pop("subnet_mask", None)
        out.append(row)
    params["interfaces"] = out


def configure_interface_phased_wizard_fields(
    intent_description: str,
    parameters: Dict[str, Any],
    *,
    trusted_param_keys: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    One step at a time: (1) L2 vs L3, then (2) for L2 only, access vs trunk.
    When this returns a non-empty list for step (1), preview should prioritize this question.
    """
    trusted = {str(k) for k in (trusted_param_keys or set())}
    if CHAT_INTERFACE_LAYER_FIELD in trusted and CHAT_INTERFACE_L2_MODE_FIELD in trusted:
        return []
    if not configure_interface_prompt_is_layer_ambiguous(intent_description, parameters):
        return []
    if CHAT_INTERFACE_LAYER_FIELD not in trusted:
        return [
            {
                "field": CHAT_INTERFACE_LAYER_FIELD,
                "question": (
                    "Your message does not say whether this is **Layer 2** (switchport: access/trunk) "
                    "or **Layer 3** (routed interface with an IP). Which do you want to configure?"
                ),
                "control": "single_select_strings",
                "options": ["l2", "l3"],
            }
        ]
    layer = str(parameters.get(CHAT_INTERFACE_LAYER_FIELD) or "").strip().lower()
    if layer == "l2" and CHAT_INTERFACE_L2_MODE_FIELD not in trusted:
        return [
            {
                "field": CHAT_INTERFACE_L2_MODE_FIELD,
                "question": (
                    "For Layer 2, should these ports be **access** or **trunk**? "
                    "(You can supply VLAN details in the next steps.)"
                ),
                "control": "single_select_strings",
                "options": ["access", "trunk"],
            }
        ]
    return []


def configure_interface_conditional_disambiguation_fields(
    parameters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Shared access/trunk/subnet follow-up questions for ``configure_interface`` (primary or secondary).
    """
    shared_fields: List[Dict[str, Any]] = []
    p2 = dict(parameters or {})
    interfaces_raw = p2.get("interfaces") or []
    if not isinstance(interfaces_raw, list) or not interfaces_raw:
        return shared_fields
    default_mode = str(p2.get("default_mode") or "").strip().lower() or None
    interfaces: List[Dict[str, Any]] = []
    for item in interfaces_raw:
        if isinstance(item, dict):
            interfaces.append(dict(item))
        elif isinstance(item, str):
            name = str(item).strip()
            if name and default_mode in ("access", "trunk", "routed"):
                interfaces.append({"name": name, "mode": default_mode})
        elif item is not None:
            s = str(item).strip()
            if s and default_mode in ("access", "trunk", "routed"):
                interfaces.append({"name": s, "mode": default_mode})
    if not interfaces:
        return shared_fields
    modes: set[str] = set()
    missing_trunk_vlans = False
    missing_routed_ip = False
    missing_access_vlan = False
    missing_native_vlan = False
    ip_addrs: set[str] = set()
    missing_subnet_mask = False

    for iface in interfaces:
        mode = str(iface.get("mode") or default_mode or "").strip().lower()
        if mode:
            modes.add(mode)
        if mode == "routed":
            if not str(iface.get("ip_address") or "").strip():
                missing_routed_ip = True
        elif mode == "access":
            av = iface.get("access_vlan")
            if av in (None, "", []):
                missing_access_vlan = True
        elif mode == "trunk":
            tv = iface.get("trunk_vlans")
            if tv in (None, "", []):
                tv = iface.get("allowed_vlans")
            if tv in (None, "", []):
                missing_trunk_vlans = True
            nv = iface.get("native_vlan")
            if nv in (None, "", []):
                missing_native_vlan = True
        ip_address = iface.get("ip_address")
        if ip_address not in (None, ""):
            ip_addrs.add(str(ip_address))
            if iface.get("subnet_mask") in (None, ""):
                missing_subnet_mask = True

    if missing_routed_ip:
        shared_fields.append(
            {
                "field": "ip_address",
                "question": (
                    "Which IPv4 address should be configured on this routed interface "
                    "(e.g. 192.168.10.1)? Use CIDR (192.168.10.1/24) if you prefer."
                ),
            }
        )

    if len(modes) == 1:
        only_mode = next(iter(modes))
        if only_mode == "access" and missing_access_vlan:
            shared_fields.append(
                {
                    "field": "access_vlan",
                    "question": (
                        "Which **access VLAN** ID (1–4094) should untagged traffic use on these ports?"
                    ),
                }
            )
        if only_mode == "trunk" and missing_trunk_vlans:
            shared_fields.append(
                {
                    "field": "trunk_vlans",
                    "question": (
                        "Which VLAN IDs should be **allowed** on this trunk? "
                        "(Pick from the list — options come from your intent and planned network context.)"
                    ),
                }
            )
        if only_mode == "trunk" and missing_native_vlan:
            shared_fields.append(
                {
                    "field": "native_vlan",
                    "question": (
                        "Which **native / PVID** VLAN ID (1-4094) should be set on this trunk?"
                    ),
                }
            )

    if missing_subnet_mask and len(ip_addrs) == 1:
        shared_fields.append(
            {
                "field": "subnet_mask",
                "question": "Subnet mask for the provided interface IP address(es)?",
            }
        )
    return shared_fields


def configure_ospf_conditional_disambiguation_fields(
    parameters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    OSPF follow-up questions collected in one pass (ordered):
    1) network IPv4 address,
    2) prefix length (/0-/32),
    3) OSPF area.
    """
    p = dict(parameters or {})
    fields: List[Dict[str, Any]] = []
    passive = p.get("passive_interfaces")
    if isinstance(passive, list) and any(str(x).strip() for x in passive):
        return fields

    # If structured OSPF networks already exist, no extra asks needed.
    nets = p.get("networks")
    if isinstance(nets, list) and any(isinstance(x, dict) and str(x.get("network") or "").strip() for x in nets):
        return fields

    network_address = str(p.get("network_address") or "").strip()
    prefix_raw = p.get("prefix_length")
    area_raw = p.get("area")

    has_prefix = prefix_raw is not None and str(prefix_raw).strip() != ""
    has_area = area_raw is not None and str(area_raw).strip() != ""

    if not network_address:
        fields.append(
            {
                "field": "network_address",
                "question": "Which OSPF network **address** (IPv4) should be advertised?",
            }
        )

    if not has_prefix:
        fields.append(
            {
                "field": "prefix_length",
                "question": "What is the prefix length for that network? (Enter **/0** to **/32**.)",
            }
        )

    if not has_area:
        fields.append(
            {
                "field": "area",
                "question": "Which OSPF **area** should this network be advertised in?",
            }
        )
    return fields


SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION: Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = {
    "configure_interface": configure_interface_conditional_disambiguation_fields,
    "configure_ospf": configure_ospf_conditional_disambiguation_fields,
}


def merge_prompt_aware_disambiguation_for_schema(
    intent_description: str,
    schema: Optional[Dict[str, Any]],
    params_disambiguation: Optional[Dict[str, Any]],
    *,
    parameters: Optional[Dict[str, Any]] = None,
    trusted_param_keys: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Merge operation-specific wizard fields when the user did not state certain parameters in
    natural language, while respecting values supplied via ``req.params`` (trusted keys).

    **Extensibility:** add a branch per ``schema['action']`` (e.g. ``create_vlan`` today).
    Secondary slices use the same function with that slice's schema.
    """
    if not schema:
        return params_disambiguation
    sa = (schema.get("action") or "").strip().lower().replace("-", "_")
    def _extract_vlan_ids_from_prompt(text: str) -> List[int]:
        t = str(text or "").strip().lower()
        if not t:
            return []
        ids: List[int] = []
        seen: Set[int] = set()
        # VLAN 10, VLANs 10,20,67, allow vlan 10 20 67
        for m in re.finditer(r"\bvlans?\b([^.;\n]*)", t):
            seg = m.group(1) or ""
            for tok in re.findall(r"\b\d{1,4}\b", seg):
                try:
                    v = int(tok)
                except ValueError:
                    continue
                if 1 <= v <= 4094 and v not in seen:
                    seen.add(v)
                    ids.append(v)
        return ids
    if sa == "create_vlan":
        force_field_names = vlan_schema_fields_missing_from_user_prompt_respecting_params(
            intent_description or "", parameters, trusted_param_keys=trusted_param_keys
        )
        if not force_field_names:
            return params_disambiguation
        questions_vlan = schema.get("questions") or {}
        fallback_questions = {
            "vlan_id": "Which VLAN ID (1-4094)?",
            "vlan_name": "VLAN name?",
        }
        new_entries = [
            {
                "field": fname,
                "question": questions_vlan.get(fname)
                or fallback_questions.get(fname)
                or f"Please provide: {fname}",
            }
            for fname in force_field_names
        ]
        fields_list: List[Dict[str, Any]] = list((params_disambiguation or {}).get("fields") or [])
        seen_fields = {f["field"] for f in fields_list}
        for e in new_entries:
            if e["field"] not in seen_fields:
                fields_list.append(e)
                seen_fields.add(e["field"])
        return {
            "question": (params_disambiguation or {}).get("question") or "Please provide the following.",
            "fields": fields_list,
        }
    if sa == "configure_interface":
        # If user already stated allowed VLAN IDs in prompt, do not re-ask trunk_vlans.
        vlan_ids_from_prompt = _extract_vlan_ids_from_prompt(intent_description or "")
        if vlan_ids_from_prompt and params_disambiguation:
            fields_list = list((params_disambiguation or {}).get("fields") or [])
            fields_list = [
                f for f in fields_list
                if not (
                    isinstance(f, dict)
                    and str(f.get("field") or "").strip().lower() in ("trunk_vlans", "allowed_vlans")
                )
            ]
            return {
                "question": (params_disambiguation or {}).get("question") or "Please provide the following.",
                "fields": fields_list,
            }
        return params_disambiguation
    if sa == "configure_ospf":
        desc = str(intent_description or "")
        if re.search(r"\bpassive\b", desc, re.I) and re.search(r"\bospf\b", desc, re.I):
            fields_list = list((params_disambiguation or {}).get("fields") or [])
            fields_list = [
                f for f in fields_list
                if str((f or {}).get("field") or "").strip().lower()
                not in ("networks", "area_definitions", "network_address", "prefix_length", "area")
            ]
            if not fields_list:
                return None
            return {
                "question": (params_disambiguation or {}).get("question") or "Please provide the following.",
                "fields": fields_list,
            }
        return params_disambiguation
    return params_disambiguation


def user_prompt_indicates_trunk_l2_binding(description: str) -> bool:
    """
    True when the user clearly refers to trunk L2 (allowed VLANs on a trunk, trunk port, etc.).

    Used to correct LLM/schema drift that defaults port binding to **access** when the prompt
    says **trunk** (e.g. "Create VLAN 10 and allow it on trunk port Gi…" — VLAN id appears
    before the word "trunk", so naive regexes miss allowed-VLAN extraction).
    """
    if not description or not str(description).strip():
        return False
    tl = re.sub(r"\s+", " ", str(description).strip().lower())
    if re.search(r"\b(?:no|not|without)\s+trunk\b", tl):
        return False
    has_trunk = bool(re.search(r"\btrunk\b", tl))
    # Explicit access-only port wording and no trunk mention → do not force trunk.
    if (
        re.search(r"\b(?:as\s+access|access\s+port|access\s+ports|access\s+vlan)\b", tl)
        and not has_trunk
    ):
        return False
    if has_trunk:
        return True
    if re.search(r"\ballowed?\s+vlan", tl) and re.search(
        r"\b(?:on|over)\s+(?:the\s+)?trunk\b", tl
    ):
        return True
    if re.search(r"allow\s+.+\s+on\s+trunk", tl):
        return True
    # Generic "allow VLAN on port/interface" wording usually means trunk allowed-VLANs,
    # unless the prompt explicitly says access.
    if (
        re.search(r"\ballow(?:ed)?\b", tl)
        and re.search(r"\b(?:vlan|vlans)\b", tl)
        and re.search(r"\b(?:on|to)\s+(?:the\s+)?(?:port|ports|interface|interfaces)\b", tl)
    ):
        return True
    return False


def user_prompt_suggests_interface_l3(description: str) -> bool:
    """True when prompt clearly asks for an interface IPv4 / routed (L3) configuration."""
    text = str(description or "").strip()
    if not text:
        return False
    tl = re.sub(r"\s+", " ", text.lower())
    if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b", text):
        return True
    if re.search(r"\bip\s+address\b", tl):
        return True
    if re.search(r"\bwith\s+ip\s+address\b", tl):
        return True
    if "routed interface" in tl or re.search(r"\bas\s+routed\b", tl):
        return True
    if re.search(r"\blayer\s*3\b", tl) or re.search(r"\bl3\s+interface\b", tl):
        return True
    return False


def _extract_l3_interface_row_from_prompt(description: str) -> Optional[Dict[str, Any]]:
    """Parse interface name + IPv4 + mask from natural language (CIDR or address + mask)."""
    text = str(description or "").strip()
    if not text:
        return None
    tl = text.lower()

    ip_addr: Optional[str] = None
    mask: Optional[str] = None

    cidr_m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b", text)
    if cidr_m:
        ip_addr = cidr_m.group(1)
        try:
            pfx = int(cidr_m.group(2))
            iface_ip = ipaddress.IPv4Interface(f"{ip_addr}/{pfx}")
            mask = str(iface_ip.network.netmask)
        except (ValueError, OSError):
            return None
    else:
        imm = re.search(
            r"\b(?:address|ip)\s+(?:is\s+)?(\d{1,3}(?:\.\d{1,3}){3})\s+(\d{1,3}(?:\.\d{1,3}){3})\b",
            tl,
        )
        if not imm:
            imm = re.search(
                r"\b(\d{1,3}(?:\.\d{1,3}){3})\s+(255\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
                text,
            )
        if imm:
            ip_addr = imm.group(1)
            mask = imm.group(2)
        else:
            ip_only = re.search(
                r"\b(?:with\s+)?ip\s+address\s+(?:is\s+)?(\d{1,3}(?:\.\d{1,3}){3})\b",
                tl,
            )
            if ip_only:
                ip_addr = ip_only.group(1)

    if not ip_addr:
        return None

    iface: Optional[str] = None
    m = re.search(
        r"\b((?:GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet)\d[/\w.-]+)\b",
        text,
        re.I,
    )
    if m:
        iface = m.group(1)
    else:
        m2 = re.search(r"\b(Gi|Te|Fa|Eth|Et)(\d+(?:/\d+)+)\b", text, re.I)
        if m2:
            iface = m2.group(1) + m2.group(2)

    if not iface:
        return None

    return {
        "name": iface,
        "mode": "routed",
        "ip_address": ip_addr,
        "subnet_mask": mask,
    }


def _parameters_l3_satisfied_for_prompt(parameters: Dict[str, Any], description: str) -> bool:
    want = _extract_l3_interface_row_from_prompt(description)
    if not want:
        return False
    want_ip = str(want.get("ip_address") or "").strip()
    want_mask = str(want.get("subnet_mask") or "").strip()
    for row in parameters.get("interfaces") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("ip_address") or "").strip() != want_ip:
            continue
        have_mask = str(row.get("subnet_mask") or "").strip()
        if (
            str(row.get("mode") or "").strip().lower() == "routed"
            and ((not want_mask) or bool(have_mask))
        ):
            return True
    return False


def _merge_l3_patch_into_parameters(parameters: Dict[str, Any], patch: Dict[str, Any]) -> None:
    if not isinstance(parameters, dict) or not patch:
        return
    ifaces = parameters.get("interfaces")
    if not isinstance(ifaces, list):
        ifaces = []

    def _strip_l2_keys(row: Dict[str, Any]) -> None:
        for k in ("access_vlan", "trunk_vlans", "allowed_vlans", "native_vlan"):
            row.pop(k, None)

    if len(ifaces) == 0:
        parameters["interfaces"] = [dict(patch)]
        return

    if len(ifaces) == 1 and isinstance(ifaces[0], dict):
        row = dict(ifaces[0])
        # User / UI-selected interface names (inventory, hostname:ifname) win over NL tokens.
        row["name"] = str(row.get("name") or patch.get("name") or "").strip()
        row["mode"] = "routed"
        row["ip_address"] = patch["ip_address"]
        if patch.get("subnet_mask"):
            row["subnet_mask"] = patch["subnet_mask"]
        _strip_l2_keys(row)
        parameters["interfaces"] = [row]
        return

    for i, row in enumerate(ifaces):
        if not isinstance(row, dict):
            continue
        if row.get("ip_address"):
            continue
        mode = str(row.get("mode") or "").strip().lower()
        # Include routed rows missing IP so we do not append a duplicate row named from the prompt only.
        if mode in ("access", "trunk", "", "routed"):
            merged = dict(row)
            merged["name"] = str(merged.get("name") or patch.get("name") or "").strip()
            merged["mode"] = "routed"
            merged["ip_address"] = patch["ip_address"]
            if patch.get("subnet_mask"):
                merged["subnet_mask"] = patch["subnet_mask"]
            _strip_l2_keys(merged)
            ifaces[i] = merged
            parameters["interfaces"] = ifaces
            return

    parameters["interfaces"] = list(ifaces) + [dict(patch)]


def correct_interface_l3_params_from_natural_language(
    intent_description: str,
    parsed_intent: Dict[str, Any],
) -> None:
    """
    When the user clearly asks for L3 interface config, coerce ``parameters.interfaces``
    away from accidental L2 defaults (e.g. mode access) produced by the LLM.
    """
    if not user_prompt_suggests_interface_l3(intent_description):
        return
    if not isinstance(parsed_intent, dict):
        return

    patch = _extract_l3_interface_row_from_prompt(intent_description)
    if not patch:
        return

    def _apply(container: Dict[str, Any]) -> None:
        p = container.get("parameters")
        if not isinstance(p, dict):
            return
        if _parameters_l3_satisfied_for_prompt(p, intent_description):
            return
        _merge_l3_patch_into_parameters(p, patch)

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower()
    if act in ("configure_interface", "configure", "") or feat == "interface":
        _apply(parsed_intent)

    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower()
        if sa == "configure_interface" or sf == "interface":
            _apply(sec)


def _extract_ospf_patch_from_prompt(intent_description: str) -> Dict[str, Any]:
    text = str(intent_description or "").strip()
    if not text:
        return {}
    out: Dict[str, Any] = {}

    rid_m = re.search(r"\brouter[\s_-]*id\s+(\d{1,3}(?:\.\d{1,3}){3})\b", text, re.I)
    if rid_m:
        rid = rid_m.group(1)
        try:
            ipaddress.IPv4Address(rid)
            out["router_id"] = rid
        except Exception:
            pass

    if re.search(r"\bpassive\b", text, re.I) and re.search(r"\bospf\b", text, re.I):
        iface_tokens: List[str] = []
        for m in re.finditer(
            r"\b((?:GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet|Gi|Te|Fa|Eth)\s*\d+(?:/\d+){1,2})\b",
            text,
            re.I,
        ):
            iface = re.sub(r"\s+", "", m.group(1) or "")
            if iface:
                iface_tokens.append(iface)
        if iface_tokens:
            out["passive_interfaces"] = sorted(set(iface_tokens))

    # Explicit OSPF process ID only (do not infer from "router 2" device names).
    for _pat in (
        r"\brouter\s+ospf\s+(\d+)\b",
        r"\bospf\s+process(?:\s+id)?\s+(\d+)\b",
    ):
        _pm = re.search(_pat, text, re.I)
        if not _pm:
            continue
        try:
            _v = int(_pm.group(1))
            if 0 <= _v <= 65535:
                out["process_id"] = _v
                break
        except Exception:
            continue
    if "process_id" not in out and re.search(r"\bospf\b", text, re.I):
        _pm = re.search(r"\bprocess\s+id\s+(\d+)\b", text, re.I)
        if _pm:
            try:
                _v = int(_pm.group(1))
                if 0 <= _v <= 65535:
                    out["process_id"] = _v
            except Exception:
                pass

    cidr_m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b", text)
    area_m = re.search(r"\barea\s+(\d+)\b", text, re.I)
    if cidr_m:
        net = cidr_m.group(1)
        try:
            pfx = int(cidr_m.group(2))
        except Exception:
            pfx = -1
        if 0 <= pfx <= 32:
            try:
                ipaddress.IPv4Address(net)
                mask = ipaddress.IPv4Network(f"0.0.0.0/{pfx}").netmask
                inv = int(ipaddress.IPv4Address(mask)) ^ 0xFFFFFFFF
                wildcard = str(ipaddress.IPv4Address(inv))
                area = int(area_m.group(1)) if area_m else 0
                out["network_address"] = net
                out["prefix_length"] = pfx
                out["area"] = area
                out["networks"] = [{"network": net, "wildcard": wildcard, "area": area}]
            except Exception:
                pass
    return out


def correct_ospf_params_from_natural_language(
    intent_description: str,
    parsed_intent: Dict[str, Any],
) -> None:
    """
    Fill obvious OSPF fields from user prompt so wizard doesn't re-ask
    values already present in natural language.
    """
    if not isinstance(parsed_intent, dict):
        return
    patch = _extract_ospf_patch_from_prompt(intent_description)
    if not patch:
        return

    def _apply(container: Dict[str, Any]) -> None:
        p = container.get("parameters")
        if not isinstance(p, dict):
            return
        if patch.get("router_id") and not str(p.get("router_id") or "").strip():
            p["router_id"] = patch["router_id"]
        if patch.get("networks") and not p.get("networks"):
            p["networks"] = patch["networks"]
        if patch.get("network_address") and not str(p.get("network_address") or "").strip():
            p["network_address"] = patch["network_address"]
        if patch.get("prefix_length") is not None and str(p.get("prefix_length") or "").strip() == "":
            p["prefix_length"] = patch["prefix_length"]
        if patch.get("area") is not None and str(p.get("area") or "").strip() == "":
            p["area"] = patch["area"]
        if patch.get("passive_interfaces") and not p.get("passive_interfaces"):
            p["passive_interfaces"] = patch["passive_interfaces"]
        if patch.get("process_id") is not None and (
            p.get("process_id") is None or str(p.get("process_id") or "").strip() == ""
        ):
            p["process_id"] = patch["process_id"]
        container["parameters"] = p

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower()
    if act == "configure_ospf" or feat == "ospf":
        _apply(parsed_intent)

    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower()
        if sa == "configure_ospf" or sf == "ospf":
            _apply(sec)


def _extract_static_route_patch_from_prompt(intent_description: str) -> Dict[str, Any]:
    """
    Best-effort extraction for static route prompts so the wizard can be populated.

    Supports:
    - destination CIDR: 10.10.10.0/24
    - next hop IP: 192.168.1.1 after “via” / “next hop” / “gateway”
    - next hop egress interface: after “via” (e.g. GigabitEthernet0/1)
    - default route: “default route” → 0.0.0.0/0
    """
    text = str(intent_description or "").strip()
    if not text:
        return {}

    out: Dict[str, Any] = {}
    tl = text.lower()

    if re.search(r"\bdefault\b.*\broute\b", tl) or re.search(r"\b0\.0\.0\.0/0\b", tl):
        out["destination"] = "0.0.0.0/0"

    # Destination CIDR
    cidr_m = re.search(r"\b((?:\d{1,3}\.){3}\d{1,3})/(\d{1,2})\b", text)
    if cidr_m:
        ip_s = cidr_m.group(1)
        pfx_s = cidr_m.group(2)
        try:
            pfx = int(pfx_s)
            if 0 <= pfx <= 32:
                ipaddress.IPv4Address(ip_s)
                out["destination"] = f"{ip_s}/{pfx}"
        except Exception:
            pass

    # Next hop IP (prefer IP if present)
    nh_ip_m = re.search(
        r"\b(?:via|next\s*hop|gateway)\s+((?:\d{1,3}\.){3}\d{1,3})\b",
        text,
        re.I,
    )
    if nh_ip_m:
        ip_s = nh_ip_m.group(1)
        try:
            ipaddress.IPv4Address(ip_s)
            out["next_hop"] = ip_s
        except Exception:
            pass

    # Next hop egress interface (if we didn't find an IP)
    if not out.get("next_hop"):
        nh_iface_m = re.search(
            r"\bvia\s+((?:GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet|Gi|Te|Fa|Eth)\s*\d+(?:/\d+){1,2})\b",
            text,
            re.I,
        )
        if nh_iface_m:
            iface = nh_iface_m.group(1) or ""
            iface = re.sub(r"\s+", "", iface)
            if iface:
                out["next_hop"] = iface

    return out


def correct_static_route_params_from_natural_language(
    intent_description: str,
    parsed_intent: Dict[str, Any],
) -> None:
    """
    Patch parsed intent parameters from the user prompt for static routes.

    Never overrides client-provided / already-present values: only fills when empty.
    """
    if not isinstance(parsed_intent, dict):
        return

    patch = _extract_static_route_patch_from_prompt(intent_description)
    if not patch:
        return

    def _apply(container: Dict[str, Any]) -> None:
        # Accept both shapes: `parameters` (preview/create) and `params` (some internal callers).
        p = container.get("parameters")
        if not isinstance(p, dict):
            p = container.get("params")
        if not isinstance(p, dict):
            return
        p = dict(p)

        if not str(p.get("destination") or "").strip():
            if patch.get("destination"):
                p["destination"] = patch["destination"]
            else:
                # Alternate keys produced by the legacy/parser.
                for k in ("destination_network", "network", "route_destination", "destination_prefix"):
                    if str(p.get(k) or "").strip():
                        p["destination"] = str(p.get(k)).strip()
                        break
                if not str(p.get("destination") or "").strip() and p.get("default_route") is True:
                    p["destination"] = "0.0.0.0/0"

        if not str(p.get("next_hop") or "").strip():
            if patch.get("next_hop"):
                p["next_hop"] = patch["next_hop"]
            else:
                for k in ("gateway", "next_hop_ip", "route_gateway"):
                    if str(p.get(k) or "").strip():
                        p["next_hop"] = str(p.get(k)).strip()
                        break

        # Write back to the existing key(s) so downstream completeness checks see it.
        if isinstance(container.get("parameters"), dict):
            container["parameters"] = p
        if isinstance(container.get("params"), dict):
            container["params"] = p
        if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
            container["parameters"] = p

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower()
    # Some paths label the feature as "routing" even for static routes.
    if act == "configure_static_route" or feat in ("static_route", "routing"):
        _apply(parsed_intent)

    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower()
        if sa == "configure_static_route" or sf in ("static_route", "routing"):
            _apply(sec)


def _wildcard_from_prefix(prefix: int) -> str:
    mask = ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask
    octets = [int(p) for p in str(mask).split(".")]
    return ".".join(str(255 - x) for x in octets)


def _wildcard_from_netmask_str(netmask: str) -> Optional[str]:
    """
    Convert subnet mask (255.255.255.0) to wildcard mask (0.0.0.255).
    Returns None if invalid.
    """
    try:
        m = ipaddress.IPv4Address(str(netmask).strip())
        octets = [int(p) for p in str(m).split(".")]
        return ".".join(str(255 - x) for x in octets)
    except Exception:
        return None


def _prefix_from_wildcard_str(wildcard: str) -> Optional[int]:
    """Convert wildcard mask to prefix length when possible."""
    try:
        wc = ipaddress.IPv4Address(str(wildcard).strip())
        mask_int = int(wc) ^ 0xFFFFFFFF
        net = ipaddress.IPv4Network((0, mask_int), strict=False)
        return int(net.prefixlen)
    except Exception:
        return None


def correct_acl_params_from_natural_language(
    intent_description: str,
    parsed_intent: Dict[str, Any],
) -> None:
    """
    Fix ACL rule fields to match device syntax expectations.

    Key fix:
      - When the user writes CIDR (/24), ACLs require wildcard masks (0.0.0.255),
        not subnet masks (255.255.255.0). Convert any netmask-like values to wildcard.
    """
    if not isinstance(parsed_intent, dict):
        return
    text = str(intent_description or "").strip()
    if not text:
        return

    def _prompt_explicit_acl_family(t: str) -> Optional[str]:
        """
        Return 'ipv4' or 'ipv6' only when the prompt explicitly indicates it:
        - keywords: ipv4/v4, ipv6/v6
        - literal addresses/prefixes: 192.0.2.1, 192.0.2.0/24, 2001:db8::1, 2001:db8::/64
        Otherwise return None so UI can ask.
        """
        tl = (t or "").lower()
        if re.search(r"\bipv6\b|\bv6\b", tl):
            return "ipv6"
        if re.search(r"\bipv4\b|\bv4\b", tl):
            return "ipv4"
        # IPv6 literal (supports compressed forms like 2001:db8::/64)
        if re.search(r"\b[0-9a-f:]*:[0-9a-f:]+\b", tl, re.I):
            return "ipv6"
        # IPv4 literal
        if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b", tl):
            return "ipv4"
        # If the rules explicitly mention common IPv4-only semantics, assume ipv4.
        # (This avoids re-asking for simple "deny tcp port 80" cases that don't specify IP family yet.)
        if any(k in tl for k in ("tcp", "udp", "icmp", "ip ")):
            return "ipv4"
        return None

    _explicit_family = _prompt_explicit_acl_family(text)

    def _extract_basic_rule_from_prompt(t: str) -> Optional[Dict[str, Any]]:
        tl = (t or "").lower()
        action = "deny" if "deny" in tl or "block" in tl else ("permit" if "permit" in tl or "allow" in tl else "")
        protocol = ""
        for p in ("icmp", "tcp", "udp", "ip"):
            if re.search(rf"\b{p}\b", tl):
                protocol = p
                break
        if not action or not protocol:
            return None
        # Safe baseline for ACL rule synthesis from vague create/apply prompts.
        return {
            "action": action,
            "protocol": protocol,
            "source": "any",
            "destination": "any",
        }

    def _fix_rules(container: Dict[str, Any]) -> None:
        p = container.get("parameters")
        if not isinstance(p, dict):
            p = container.get("params")
        if not isinstance(p, dict):
            return
        # Prompt-aware family inference (only when explicit); otherwise leave missing so UI asks.
        if _explicit_family and not str(p.get("acl_type") or "").strip():
            p = dict(p)
            p["acl_type"] = _explicit_family
        rules = p.get("rules")
        if not isinstance(rules, list):
            # If the user clearly expressed ACL rule intent (e.g. deny icmp) but parser dropped rules,
            # synthesize one safe explicit rule so templates do not render empty ACL blocks.
            sr = _extract_basic_rule_from_prompt(text)
            if sr:
                p2 = dict(p)
                p2["rules"] = [sr]
                # Safe fallback ACL name only when missing and we had to synthesize rules.
                if not str(p2.get("acl_name") or "").strip():
                    p2["acl_name"] = "BLOCK_ICMP" if str(sr.get("protocol") or "") == "icmp" else "ACL_AUTO"
                if isinstance(container.get("parameters"), dict):
                    container["parameters"] = p2
                if isinstance(container.get("params"), dict):
                    container["params"] = p2
                if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
                    container["parameters"] = p2
            return
        fixed: List[Any] = []
        for r in rules:
            if not isinstance(r, dict):
                fixed.append(r)
                continue
            row = dict(r)
            # Normalize common "any" shapes: if address is 'any', wildcard must not be present.
            for addr_key, wc_key in (("source", "source_wildcard"), ("destination", "dest_wildcard")):
                addr_raw = str(row.get(addr_key) or "").strip().lower()
                if addr_raw in ("any", "all", "*"):
                    row[addr_key] = "any"
                    row.pop(wc_key, None)

            # Normalize port: accept "eq 80" and store just "80" so templates don't render "eq eq 80".
            if row.get("port") is not None:
                ps = str(row.get("port") or "").strip()
                m = re.match(r"^(?:eq\s+)?(\d+)\s*$", ps, re.I)
                if m:
                    row["port"] = m.group(1)
                else:
                    # Keep advanced expressions (e.g. "range 1000 2000") as-is.
                    row["port"] = ps

            # Support "source" or "destination" expressed as CIDR.
            for addr_key, wc_key in (("source", "source_wildcard"), ("destination", "dest_wildcard")):
                addr = str(row.get(addr_key) or "").strip()
                if addr.strip().lower() == "any":
                    row.pop(wc_key, None)
                    continue
                if "/" in addr:
                    try:
                        ip, pfx_s = addr.split("/", 1)
                        ipaddress.IPv4Address(ip.strip())
                        pfx = int(pfx_s.strip())
                        if 0 <= pfx <= 32:
                            row[addr_key] = ip.strip()
                            row[wc_key] = _wildcard_from_prefix(pfx)
                    except Exception:
                        pass
                # If the wildcard field looks like a netmask, convert it.
                wc_val = row.get(wc_key)
                if wc_val is not None and str(wc_val).strip():
                    # Drop CIDR-like wildcard artifacts (e.g. "0.0.0.0/0") when addr isn't CIDR.
                    if "/" in str(wc_val):
                        row.pop(wc_key, None)
                        wc_val = None
                    converted = _wildcard_from_netmask_str(str(wc_val))
                    if converted:
                        row[wc_key] = converted
                # If the wildcard is still a netmask-ish value (e.g. 255.255.255.0),
                # force invert; ACL wildcard must be 0.x not 255.x.
                wc_after = str(row.get(wc_key) or "").strip()
                if wc_after.startswith("255."):
                    forced = _wildcard_from_netmask_str(wc_after)
                    if forced:
                        row[wc_key] = forced
                # If wildcard missing but prompt had CIDR for this address, try to infer from prompt.
                if (row.get(wc_key) in (None, "", [])) and addr and re.search(re.escape(addr) + r"/(\d{1,2})\b", text):
                    m = re.search(re.escape(addr) + r"/(\d{1,2})\b", text)
                    if m:
                        try:
                            pfx = int(m.group(1))
                            if 0 <= pfx <= 32:
                                row[wc_key] = _wildcard_from_prefix(pfx)
                        except Exception:
                            pass
            fixed.append(row)
        p2 = dict(p)
        p2["rules"] = fixed
        if isinstance(container.get("parameters"), dict):
            container["parameters"] = p2
        if isinstance(container.get("params"), dict):
            container["params"] = p2
        if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
            container["parameters"] = p2

    def _fix_acl_apply(container: Dict[str, Any]) -> None:
        """
        Best-effort extraction for apply-only prompts like:
          "Apply ACL 3000 inbound on GigabitEthernet1/0/1 on SW1"
        If the wizard already supplied apply_to/interfaces/direction, do nothing.
        """
        p = container.get("parameters")
        if not isinstance(p, dict):
            p = container.get("params")
        if not isinstance(p, dict):
            return
        p2 = dict(p)

        # Always fill acl_name from the prompt if missing, even when the wizard already supplied apply_to.
        if not str(p2.get("acl_name") or "").strip():
            m2 = re.search(r"\bacl\s+([0-9]{1,6}|[A-Za-z][A-Za-z0-9_-]{0,63})\b", text, re.I)
            if m2:
                p2["acl_name"] = str(m2.group(1)).strip()

        tl = text.lower()
        direction_from_prompt: Optional[str] = None
        if re.search(r"\binbound\b", tl) or re.search(r"\b in\b", tl):
            direction_from_prompt = "in"
        if re.search(r"\boutbound\b", tl) or re.search(r"\b out\b", tl):
            direction_from_prompt = "out"

        # If the wizard already supplied apply_to (authoritative), don't override it.
        if isinstance(p2.get("apply_to"), list) and p2.get("apply_to"):
            if not str(p2.get("acl_direction") or "").strip():
                first = str((p2.get("apply_to") or [""])[0] or "").strip()
                mdir = re.search(r"\b(in|out)\b\s*$", first, re.I)
                if mdir:
                    p2["acl_direction"] = mdir.group(1).lower()
                elif direction_from_prompt:
                    p2["acl_direction"] = direction_from_prompt
            if isinstance(container.get("parameters"), dict):
                container["parameters"] = p2
            if isinstance(container.get("params"), dict):
                container["params"] = p2
            if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
                container["parameters"] = p2
            return
        if isinstance(p2.get("interfaces"), list) and p2.get("interfaces") and str(p2.get("acl_direction") or "").strip():
            if isinstance(container.get("parameters"), dict):
                container["parameters"] = p2
            if isinstance(container.get("params"), dict):
                container["params"] = p2
            if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
                container["parameters"] = p2
            return

        applyish = bool(
            re.search(r"\bapply\s+acl\b", tl)
            or "access-group" in tl
            or "traffic-filter" in tl
            or "inbound on" in tl
            or "outbound on" in tl
        )
        if not applyish:
            return

        direction: Optional[str] = None
        if direction_from_prompt:
            direction = direction_from_prompt

        iface: Optional[str] = None
        m = re.search(
            r"\b(GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet|Port-channel|PortChannel|Loopback|Vlan|Gi|Te|Fa|Eth|Po)\s*\d+(?:/\d+){0,3}\b",
            text,
            re.I,
        )
        if m:
            iface = str(m.group(0)).strip()

        if iface:
            p2["interfaces"] = p2.get("interfaces") or [iface]
        if direction:
            p2["acl_direction"] = p2.get("acl_direction") or direction
        if iface and direction:
            p2["apply_to"] = [{"interface": iface, "direction": direction}]

        if isinstance(container.get("parameters"), dict):
            container["parameters"] = p2
        if isinstance(container.get("params"), dict):
            container["params"] = p2
        if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
            container["parameters"] = p2

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    if act in ("configure_acl", "apply_acl_to_interface") or feat == "acl":
        _fix_rules(parsed_intent)
        _fix_acl_apply(parsed_intent)
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower().replace("-", "_")
        if sa in ("configure_acl", "apply_acl_to_interface") or sf == "acl":
            _fix_rules(sec)
            _fix_acl_apply(sec)


def prompt_contains_explicit_ospf_process_id(intent_description: str) -> bool:
    """
    True when the user text clearly states an OSPF routing process number (not a device name like "router 2").
    Used to decide whether an LLM-supplied process_id is trustworthy.
    """
    text = str(intent_description or "").strip()
    if not text:
        return False
    tl = text.lower()
    if re.search(r"\brouter\s+ospf\s+\d+\b", tl):
        return True
    if re.search(r"\bospf\s+process(?:\s+id)?\s+\d+\b", tl):
        return True
    if re.search(r"\bprocess\s+id\s+\d+\b", tl) and re.search(r"\bospf\b", tl):
        return True
    return False


def prompt_contains_explicit_ospf_network_advertisement(intent_description: str) -> bool:
    """
    True when the user text states what to advertise into OSPF (CIDR and/or network + mask + area).
    Vague lines like "Configure OSPF on devices" return False so LLM-invented networks are dropped.
    """
    text = str(intent_description or "").strip()
    if not text:
        return False
    if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b", text):
        return True
    # Cisco-style: network 192.168.1.0 0.0.0.255 area 0
    if re.search(
        r"\bnetwork\s+\d{1,3}(?:\.\d{1,3}){3}(?:\s+\d{1,3}(?:\.\d{1,3}){3})?\s+area\s+\d+",
        text,
        re.I,
    ):
        return True
    if re.search(r"\bospf\b", text, re.I) and re.search(
        r"\bnetwork\s+\d{1,3}(?:\.\d{1,3}){3}\s+\d{1,3}(?:\.\d{1,3}){3}\b",
        text,
        re.I,
    ):
        return True
    return False


def prompt_contains_explicit_ospf_router_id_in_prompt(intent_description: str) -> bool:
    text = str(intent_description or "").strip()
    if not text:
        return False
    return bool(re.search(r"\brouter[\s_-]*id\s+\d{1,3}(?:\.\d{1,3}){3}\b", text, re.I))


def prompt_contains_explicit_static_route_next_hop(intent_description: str) -> bool:
    """
    True when the prompt explicitly states the route target (next-hop IP or egress interface),
    e.g. "via 192.0.2.1" / "next hop 192.0.2.1" / "via GigabitEthernet0/1".
    """
    text = str(intent_description or "").strip()
    if not text:
        return False
    # via <ip> / via <iface>
    if re.search(r"\bvia\s+(?:\d{1,3}(?:\.\d{1,3}){3})\b", text, re.I):
        return True
    if re.search(
        r"\bvia\s+(?:GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet|Gi|Te|Fa|Eth)\s*\d+(?:/\d+){1,2}\b",
        text,
        re.I,
    ):
        return True
    if re.search(r"\b(?:next\s*hop|gateway)\s+\d{1,3}(?:\.\d{1,3}){3}\b", text, re.I):
        return True
    return False


def strip_llm_static_route_next_hop_if_not_explicit_in_prompt(
    intent_description: str,
    parsed_intent: Dict[str, Any],
    *,
    client_supplied_next_hop: bool = False,
) -> None:
    """
    Remove LLM-invented `next_hop` for static routes when the prompt does not state it,
    so the UI must ask. Never strip when the client already provided `next_hop`.
    """
    if not isinstance(parsed_intent, dict):
        return
    if client_supplied_next_hop or prompt_contains_explicit_static_route_next_hop(intent_description or ""):
        return

    def _strip(container: Dict[str, Any]) -> None:
        p = container.get("parameters")
        if not isinstance(p, dict):
            p = container.get("params")
        if not isinstance(p, dict):
            return
        p = dict(p)
        p.pop("next_hop", None)
        p.pop("gateway", None)
        p.pop("next_hop_ip", None)
        p.pop("route_gateway", None)
        if isinstance(container.get("parameters"), dict):
            container["parameters"] = p
        if isinstance(container.get("params"), dict):
            container["params"] = p
        if not isinstance(container.get("parameters"), dict) and not isinstance(container.get("params"), dict):
            container["parameters"] = p

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    if act == "configure_static_route" or feat in ("static_route", "routing"):
        _strip(parsed_intent)
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower().replace("-", "_")
        if sa == "configure_static_route" or sf in ("static_route", "routing"):
            _strip(sec)


def client_supplied_ospf_advertisement_params(params: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(params, dict):
        return False
    nets = params.get("networks")
    if isinstance(nets, list) and any(
        isinstance(x, dict) and str(x.get("network") or "").strip() for x in nets
    ):
        return True
    if isinstance(nets, str) and nets.strip():
        return True
    if str(params.get("network_address") or "").strip():
        return True
    return False


def client_supplied_ospf_router_id_param(params: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(params, dict):
        return False
    v = params.get("router_id")
    return v is not None and str(v).strip() != ""


def strip_llm_ospf_advertisement_guesses_if_not_explicit_in_prompt(
    intent_description: str,
    parsed_intent: Dict[str, Any],
    *,
    client_supplied_advertisement: bool = False,
    client_supplied_router_id: bool = False,
) -> None:
    """
    Remove LLM- or template-hallucinated OSPF advertisement data when the user did not state
    a network/CIDR (and related) in natural language. Optional router_id is cleared the same way.
    """
    if not isinstance(parsed_intent, dict):
        return
    desc = intent_description or ""
    explicit_net = prompt_contains_explicit_ospf_network_advertisement(desc)
    explicit_rid = prompt_contains_explicit_ospf_router_id_in_prompt(desc)

    def _strip(container: Dict[str, Any]) -> None:
        p = container.get("parameters")
        if not isinstance(p, dict):
            return
        if not explicit_net and not client_supplied_advertisement:
            p.pop("networks", None)
            p.pop("ospf_networks", None)
            p.pop("network_address", None)
            p.pop("prefix_length", None)
            p.pop("area", None)
        if not explicit_rid and not client_supplied_router_id:
            p.pop("router_id", None)
        container["parameters"] = p

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower()
    if act == "configure_ospf" or feat == "ospf":
        _strip(parsed_intent)
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower()
        if sa == "configure_ospf" or sf == "ospf":
            _strip(sec)


def strip_llm_ospf_process_id_if_not_explicit_in_prompt(
    intent_description: str,
    parsed_intent: Dict[str, Any],
    *,
    client_supplied_process_id: bool = False,
) -> None:
    """
    Remove process_id / ospf_process_id from OSPF intents when the prompt does not state
    an explicit OSPF process ID, so the UI wizard must ask. Keeps values when the client
    already answered (merged params) or the prompt names the process explicitly.
    """
    if not isinstance(parsed_intent, dict):
        return
    if client_supplied_process_id or prompt_contains_explicit_ospf_process_id(intent_description or ""):
        return

    def _strip(container: Dict[str, Any]) -> None:
        p = container.get("parameters")
        if not isinstance(p, dict):
            return
        p.pop("process_id", None)
        p.pop("ospf_process_id", None)
        container["parameters"] = p

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feat = str(parsed_intent.get("feature") or "").strip().lower()
    if act == "configure_ospf" or feat == "ospf":
        _strip(parsed_intent)
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower()
        if sa == "configure_ospf" or sf == "ospf":
            _strip(sec)


def ospf_redistribution_non_empty(raw: Any) -> bool:
    """True when redistribution / redistribute params list or string names at least one protocol."""
    if raw is None:
        return False
    if isinstance(raw, list):
        return any(str(x).strip() for x in raw)
    if isinstance(raw, str) and raw.strip():
        for item in re.split(r"[\s,;]+", raw.strip().lower()):
            if item in ("static", "connected", "bgp", "rip", "isis", "ospf"):
                return True
    return False


def ospf_passive_interfaces_non_empty(raw: Any) -> bool:
    if raw is None:
        return False
    if isinstance(raw, list):
        return any(str(x).strip() for x in raw)
    if isinstance(raw, str):
        return bool(raw.strip())
    return False


def _effective_vlan_id_for_port_binding(parameters: Dict[str, Any], intent_description: str) -> Optional[int]:
    """Best VLAN id for binding access/trunk lists (params + NL extract)."""
    for raw in (parameters.get("vlan_id"),):
        if raw is None or str(raw).strip() == "":
            continue
        try:
            v = int(raw)
            if 1 <= v <= 4094:
                return v
        except (TypeError, ValueError):
            pass
    vlans = parameters.get("vlans")
    if isinstance(vlans, list) and vlans:
        first = vlans[0]
        if isinstance(first, dict):
            vid = first.get("id")
            if vid is not None:
                try:
                    v = int(vid)
                    if 1 <= v <= 4094:
                        return v
                except (TypeError, ValueError):
                    pass
    ext = extract_create_vlan_params_from_natural_language(intent_description or "")
    vid2 = ext.get("vlan_id")
    if vid2 is not None:
        try:
            v = int(vid2)
            if 1 <= v <= 4094:
                return v
        except (TypeError, ValueError):
            pass
    return None


def _coerce_one_interface_dict_to_trunk_allowed(
    item: Dict[str, Any],
    vlan_id: Optional[int],
) -> Dict[str, Any]:
    out = dict(item)
    mode = str(out.get("mode") or "").strip().lower()
    av = out.get("access_vlan")
    try:
        av_int = int(av) if av is not None and str(av).strip() != "" else None
    except (TypeError, ValueError):
        av_int = None
    if av_int is not None and not (1 <= av_int <= 4094):
        av_int = None

    tv_existing: List[int] = []
    for key in ("trunk_vlans", "allowed_vlans"):
        raw = out.get(key)
        if raw is None:
            continue
        if isinstance(raw, str) and raw.strip().lower() == "all":
            out["mode"] = "trunk"
            return out
        if isinstance(raw, list):
            for x in raw:
                try:
                    xi = int(x)
                    if 1 <= xi <= 4094:
                        tv_existing.append(xi)
                except (TypeError, ValueError):
                    pass
        elif isinstance(raw, str) and raw.strip():
            for part in raw.replace(" ", "").split(","):
                if part.isdigit():
                    try:
                        xi = int(part)
                        if 1 <= xi <= 4094:
                            tv_existing.append(xi)
                    except ValueError:
                        pass

    if mode == "trunk" and tv_existing:
        out["mode"] = "trunk"
        out.pop("access_vlan", None)
        if "trunk_vlans" not in out or not out.get("trunk_vlans"):
            out["trunk_vlans"] = sorted(set(tv_existing))
        return out

    # access, missing mode, or trunk missing allowed list — normalize to trunk + VLAN list
    out["mode"] = "trunk"
    out.pop("access_vlan", None)
    chosen = vlan_id or av_int
    if chosen is not None:
        merged = sorted(set(tv_existing + [chosen]))
        out["trunk_vlans"] = merged
    elif tv_existing:
        out["trunk_vlans"] = sorted(set(tv_existing))
    return out


def _coerce_parameters_interfaces_for_trunk_prompt(
    intent_description: str,
    parameters: Dict[str, Any],
) -> None:
    if not user_prompt_indicates_trunk_l2_binding(intent_description):
        return
    vlan_id = _effective_vlan_id_for_port_binding(parameters, intent_description)
    for key in ("interfaces", "interface_binding", "interfaces_config"):
        raw_list = parameters.get(key)
        if not isinstance(raw_list, list) or not raw_list:
            continue
        new_list: List[Any] = []
        for item in raw_list:
            if isinstance(item, dict):
                new_list.append(_coerce_one_interface_dict_to_trunk_allowed(item, vlan_id))
            elif isinstance(item, str) and item.strip():
                name = item.strip()
                row: Dict[str, Any] = {"name": name, "mode": "trunk"}
                if vlan_id is not None:
                    row["trunk_vlans"] = [vlan_id]
                new_list.append(row)
            else:
                new_list.append(item)
        parameters[key] = new_list


def correct_vlan_interface_binding_from_trunk_prompt(
    intent_description: str,
    parsed_intent: Dict[str, Any],
) -> None:
    """
    In-place fix: trunk wording in the user message + access-shaped interface rows from the LLM.
    """
    if not intent_description or not str(intent_description).strip():
        return
    if not isinstance(parsed_intent, dict):
        return
    p = parsed_intent.get("parameters")
    if isinstance(p, dict) and (p.get("interfaces") or p.get("interface_binding") or p.get("interfaces_config")):
        _coerce_parameters_interfaces_for_trunk_prompt(intent_description, p)
        parsed_intent["parameters"] = p
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sp = sec.get("parameters")
        if isinstance(sp, dict) and (sp.get("interfaces") or sp.get("interface_binding") or sp.get("interfaces_config")):
            _coerce_parameters_interfaces_for_trunk_prompt(intent_description, sp)


def _expand_all_interfaces_preview_tokens_for_inspector(
    intent_for_slice: Dict[str, Any],
    planned_preview: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Inspector-only expansion for shutdown_interface `interfaces_to_shutdown=["ALL"]`.
    Keeps execution params unchanged, but renders explicit planned interfaces for admin visibility.
    """
    out = {
        "action": intent_for_slice.get("action"),
        "feature": intent_for_slice.get("feature"),
        "parameters": dict(intent_for_slice.get("parameters") or {}),
        "resolved_devices": list(intent_for_slice.get("resolved_devices") or []),
    }
    params = dict(out.get("parameters") or {})
    action = str(out.get("action") or "").strip().lower().replace("-", "_")
    if action != "shutdown_interface":
        return out
    raw = params.get("interfaces_to_shutdown")
    if not isinstance(raw, list):
        return out
    has_all = any(str(x).strip().lower() in ("all", "*", "any", "every") for x in raw)
    if not has_all:
        return out
    devices = list((planned_preview or {}).get("devices") or [])
    expanded: List[str] = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        hn = str(d.get("hostname") or "").strip()
        if not hn:
            continue
        for iface in d.get("interfaces") or []:
            if not isinstance(iface, dict):
                continue
            name = str(iface.get("name") or "").strip()
            if not name:
                continue
            expanded.append(f"{hn}:{name}")
    if expanded:
        params["interfaces_to_shutdown"] = expanded
        out["parameters"] = params
    return out


def user_prompt_requests_vlan_interface_binding(description: str) -> bool:
    """
    True when the user is asking to map a VLAN to specific ports (access/trunk/tagging),
    not only to create the VLAN in the switching database on target devices.

    Used to ignore LLM-hallucinated interface lists for prompts like
    "Create VLAN 67 on access switches 1–3" (device scope, not port binding).
    """
    if not description or not str(description).strip():
        return False
    tl = re.sub(r"\s+", " ", str(description).strip().lower())

    if re.search(r"\btrunk\b", tl):
        return True
    if re.search(r"\buntagged\b", tl) or re.search(r"\btagged\b", tl):
        return True

    phrases = (
        "native vlan",
        "native-vlan",
        "allowed vlan",
        "allow vlan",
        "allowed vlans",
        "access vlan",
        "voice vlan",
        "switchport",
        "tag vlan",
        "pvid",
        " as access",
        " as trunk",
        "access port",
        "access ports",
        "trunk port",
        "trunk ports",
        "configure interface",
        "on interface",
        " on port ",
        " on gi",
        " on gigabit",
        " on ethernet",
        " on ge-",
        "uplink ",
        "etherchannel",
        "port-channel",
        " lag ",
        " lags ",
    )
    for p in phrases:
        if p in tl:
            return True

    if re.search(
        r"\b(?:gigabitethernet|ten-?gig|tengigabitethernet|fastethernet|port-channel|etherchannel|bundle-ethernet|xe-|ae\d)",
        tl,
    ):
        return True
    if re.search(r"\b(?:gi|te|fa|eth)\s*\d", tl):
        return True
    if re.search(r"\bethernet\s*\d+/\d+", tl):
        return True
    if re.search(r"\bge-\d+/\d+/\d+", tl):
        return True
    if re.search(r"\bge\s*\d+/\d+/\d+", tl):
        return True
    return False


_VLAN_DB_ONLY_STRIP_PARAM_KEYS = frozenset({
    "interfaces",
    "interfaces_config",
    "interfaces_config_use_nettopo",
    "interface_binding",
    "default_mode",
})


def coerce_vlan_database_only_intent(
    parsed_intent: Dict[str, Any],
    intent_description: str,
    *,
    user_supplied_interface_params: bool = False,
) -> bool:
    """
    When feature is VLAN and the user did not ask for interface assignment, drop
    interface-related parameters and force action ``create_vlan`` so preview/planning
    validate against the VLAN DB schema (not ``configure_interface``).

    Mutates ``parsed_intent`` in place (``action``, ``parameters``). Returns True if changed.

    If the caller merged explicit ``interfaces`` / ``interfaces_config`` from the API
    (e.g. user picked ports in the UI), pass ``user_supplied_interface_params=True`` so
    we do not strip those selections.
    """
    feat = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    if feat != "vlan":
        return False
    if user_supplied_interface_params:
        return False
    if user_prompt_requests_vlan_interface_binding(intent_description or ""):
        return False

    params = dict(parsed_intent.get("parameters") or {})
    changed = False
    for k in _VLAN_DB_ONLY_STRIP_PARAM_KEYS:
        if k in params:
            params.pop(k, None)
            changed = True

    act = str(parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    if act != "create_vlan":
        parsed_intent["action"] = "create_vlan"
        changed = True

    if changed:
        parsed_intent["parameters"] = params
        # Parser may have set this when it invented interfaces.
        if parsed_intent.get("requires_resolution") is True:
            parsed_intent["requires_resolution"] = False
    return changed


def is_vlan_database_only_intent(
    *,
    feature_lower: str,
    schema_action: str,
    parameters: Optional[Dict[str, Any]],
    intent_description: str,
) -> bool:
    """
    True when the workflow is VLAN database creation only: no port binding language
    in the prompt and no interfaces payload. Used to skip interface inventory checks
    and hide interface pickers in preview/plan.
    """
    fl = str(feature_lower or "").strip().lower().replace("-", "_")
    sa = str(schema_action or "").strip().lower().replace("-", "_")
    if fl != "vlan" or sa != "create_vlan":
        return False
    if user_prompt_requests_vlan_interface_binding(intent_description or ""):
        return False
    p = parameters or {}
    if p.get("interfaces") or p.get("interfaces_config"):
        return False
    return True


def get_schema_for_parsed_intent(parsed_intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Resolve schema from parsed_intent.action or parsed_intent.feature.
    Returns loaded schema dict or None.
    """
    action = (parsed_intent.get("action") or "").strip().lower().replace("-", "_")
    feature = (parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    params = dict(parsed_intent.get("parameters") or parsed_intent.get("params") or {})
    canonical = str(params.get("_canonical_operation") or params.get("canonical_operation") or "").strip().lower().replace("-", "_")

    if feature == "ospf" or action == "configure_ospf" or canonical in ("configure_passive_interface", "configure_ospf_passive_interface", "configure_ospf_redistribute"):
        if canonical == "configure_ospf_redistribute":
            schema = load_schema("configure_ospf_redistribute")
            if schema:
                return schema
        if canonical in ("configure_passive_interface", "configure_ospf_passive_interface"):
            schema = load_schema("configure_ospf_passive_interface")
            if schema:
                return schema
        red = params.get("redistribution") or params.get("redistribute")
        has_red = ospf_redistribution_non_empty(red)
        if has_red and not params.get("networks") and not ospf_passive_interfaces_non_empty(
            params.get("passive_interfaces")
        ):
            schema = load_schema("configure_ospf_redistribute")
            if schema:
                return schema
        passive = params.get("passive_interfaces")
        has_passive = ospf_passive_interfaces_non_empty(passive)
        if has_passive and not params.get("networks"):
            schema = load_schema("configure_ospf_passive_interface")
            if schema:
                return schema

    if action and action not in ("unknown", "configure", "clarify"):
        schema = load_schema(action)
        if schema:
            return schema
    if feature:
        return load_schema(feature)
    return None


_INTENT_OPERATION_REFERENCE_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "intent_operation_reference.md",
    Path(__file__).resolve().parent / "intent_operation_reference.md",
)


def get_intent_operation_reference_for_prompt(max_chars: int = 14000) -> str:
    """
    Load the canonical human/LLM intent operations document (VLAN DB vs interface, etc.).
    Tries ``docs/intent_operation_reference.md`` first, then a copy next to intent_schemas.
    """
    for path in _INTENT_OPERATION_REFERENCE_CANDIDATES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if len(text) > max_chars:
                return text[:max_chars] + "\n\n… (truncated for prompt size)"
            return text
        except Exception as exc:
            logger.warning("Failed to read intent operation reference %s: %s", path, exc)
    return (
        "(Document not found: add docs/intent_operation_reference.md — "
        "see intent_schemas/*.yml for required/optional per action.)"
    )


def get_required_params_hint_for_prompt() -> str:
    """
    Compact per-operation summary from intent_schemas/*.yml for the LLM parse prompt.
    The full narrative lives in ``get_intent_operation_reference_for_prompt()``.
    """
    lines = []
    for name in (
        "shutdown_interface",
        "configure_interface",
        "create_vlan",
        "configure_ospf",
        "configure_ospf_passive_interface",
        "configure_ospf_redistribute",
        "configure_static_route",
        "configure_acl",
        "configure_qos",
    ):
        schema = load_schema(name)
        if not schema:
            continue
        action = (schema.get("action") or name).strip()
        feature = (schema.get("feature") or "").strip()
        required = list(schema.get("required") or [])
        optional = list(schema.get("optional") or [])
        lines.append(
            f"  - action={action!r} feature={feature!r} → required: {required}; optional: {optional}"
        )
    if not lines:
        return "(none)"
    return "\n".join(lines)


def schema_field_to_param_key(schema: Dict[str, Any], schema_field: str) -> str:
    """
    Map YAML schema field name to API / UI parameter key (single source of truth with Studio).
    E.g. target_devices → target_hosts; shutdown schema "interfaces" → interfaces_to_shutdown.
    """
    field_to_param: Dict[str, str] = {"target_devices": "target_hosts"}
    if (schema.get("action") or "").strip().lower().replace("-", "_") == "shutdown_interface":
        field_to_param["interfaces"] = "interfaces_to_shutdown"
    return field_to_param.get(schema_field, schema_field)


def get_editable_fields_for_schema(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return a list of editable fields for UI (e.g. PlaybookStudioOverlay confirm step).
    Each item: { "field": str, "label": str, "required": bool, "type": "string" | "number" }.
    """
    required = set(schema.get("required") or [])
    optional = set(schema.get("optional") or [])
    questions = schema.get("questions") or {}
    out: List[Dict[str, Any]] = []
    for field in list(required) + [f for f in optional if f not in required]:
        param_key = schema_field_to_param_key(schema, field)
        label = (questions.get(field) or param_key.replace("_", " ")).strip()
        if label.endswith("?"):
            label = label[:-1]
        type_hint = "number" if field in ("vlan_id",) else "string"
        if field == "interface_binding":
            type_hint = "interfaces_choice"  # No / Access / Trunk + interface selector from planned context
        out.append({
            "field": param_key,
            "label": label,
            "required": field in required,
            "type": type_hint,
        })
    return out


def _format_inspector_value_preview(value: Any) -> str:
    """Short string for preview modal / API (lists of scalars → comma-separated)."""
    if value is None:
        return ""
    if isinstance(value, list):
        if value and all(not isinstance(x, (dict, list)) for x in value):
            return ", ".join(str(x) for x in value)
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    if isinstance(value, dict):
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    return str(value)


def _build_one_schema_inspector_slice(
    slice_intent: Dict[str, Any],
    *,
    role: str,
    index: int,
    title: str,
) -> Dict[str, Any]:
    """
    One primary or secondary intent: resolved schema file, required/optional fields,
    current values, missing required, validation errors (for Playbook Studio / API).
    """
    action_raw = str(slice_intent.get("action") or "").strip()
    feature_raw = str(slice_intent.get("feature") or "").strip()
    schema = get_schema_for_parsed_intent(slice_intent)
    if not schema:
        return {
            "role": role,
            "index": index,
            "title": title,
            "action": action_raw or None,
            "feature": feature_raw or None,
            "schema_action": None,
            "schema_file": None,
            "missing_required": [],
            "validation_errors": [],
            "fields": [],
            "note": "No intent schema matched this slice (unknown action/feature or non-config intent).",
        }

    schema_action = str(schema.get("action") or "").strip() or action_raw
    schema_file = f"{schema_action}.yml" if schema_action else None
    type_key = schema_action or ""
    missing, errors = validate_completeness(
        slice_intent,
        schema,
        action_or_feature=type_key if type_key else None,
    )
    flat = _intent_to_flat_fields(slice_intent, schema)
    required_set = set(schema.get("required") or [])
    optional_set = set(schema.get("optional") or [])
    questions = schema.get("questions") or {}
    ordered_fields: List[str] = list(schema.get("required") or [])
    for f in schema.get("optional") or []:
        if f not in required_set:
            ordered_fields.append(f)

    fields_out: List[Dict[str, Any]] = []
    for sf in ordered_fields:
        param_key = schema_field_to_param_key(schema, sf)
        raw_val = flat.get(sf)
        empty = _is_empty(raw_val)
        if sf in required_set:
            status = "missing" if empty else "filled"
        else:
            status = "optional_empty" if empty else "filled"
        label = str(questions.get(sf) or param_key.replace("_", " ")).strip()
        if label.endswith("?"):
            label = label[:-1]
        fields_out.append({
            "schema_field": sf,
            "param_key": param_key,
            "label": label,
            "required": sf in required_set,
            "status": status,
            "value_preview": _format_inspector_value_preview(raw_val) if not empty else "",
            "question": questions.get(sf),
        })

    return {
        "role": role,
        "index": index,
        "title": title,
        "action": action_raw or None,
        "feature": feature_raw or None,
        "schema_action": schema_action,
        "schema_file": schema_file,
        "missing_required": list(missing),
        "validation_errors": list(errors or []),
        "fields": fields_out,
        "note": None,
    }


def build_intent_schema_inspector_payload(
    parsed_intent: Dict[str, Any],
    *,
    resolved_hosts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Google-Form-style inspection payload: primary + each secondary_intents slice with
    operation-specific YAML schema fields, required vs optional, values, and gaps.
    """
    rh = list(parsed_intent.get("resolved_devices") or resolved_hosts or [])
    slices: List[Dict[str, Any]] = []

    planned_preview = parsed_intent.get("planned_all_interfaces_preview")
    primary_intent = {
        "action": parsed_intent.get("action"),
        "feature": parsed_intent.get("feature"),
        "parameters": dict(parsed_intent.get("parameters") or {}),
        "resolved_devices": rh,
    }
    primary_intent = _expand_all_interfaces_preview_tokens_for_inspector(
        primary_intent,
        planned_preview if isinstance(planned_preview, dict) else None,
    )
    slices.append(
        _build_one_schema_inspector_slice(
            primary_intent,
            role="primary",
            index=0,
            title="Primary operation",
        )
    )

    for idx, sec in enumerate(parsed_intent.get("secondary_intents") or []):
        if not isinstance(sec, dict):
            continue
        sec_intent = {
            "action": sec.get("action"),
            "feature": sec.get("feature"),
            "parameters": dict(sec.get("parameters") or {}),
            "resolved_devices": rh,
        }
        slices.append(
            _build_one_schema_inspector_slice(
                sec_intent,
                role="secondary",
                index=idx + 1,
                title=f"Secondary operation #{idx + 1}",
            )
        )

    return {"slices": slices}
