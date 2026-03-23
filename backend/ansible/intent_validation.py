"""
Intent schema tree and validation engine.

Per-action parameter dependency: required/optional fields, resolution_order (from_intent ->
from_inventory -> from_topology -> from_policy -> ask_user), and questions for disambiguation.
Validates completeness and type; resolves missing fields with resolution_trace; respects safe_mode.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
        ifaces = flat.get("interfaces") or []
        norm: List[Dict[str, Any]] = []
        for item in ifaces:
            if isinstance(item, str):
                norm.append({"name": item.strip(), "mode": "access"})
            elif isinstance(item, dict):
                d = dict(item)
                for _null_opt in ("native_vlan", "access_vlan", "allowed_vlans", "trunk_vlans"):
                    if _null_opt in d and d[_null_opt] is None:
                        del d[_null_opt]
                norm.append(d)
            else:
                norm.append({"name": str(item), "mode": "access"})
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
    flat["desired_state"] = params.get("desired_state")
    flat["area_definitions"] = params.get("area_definitions")
    flat["passive_interfaces"] = params.get("passive_interfaces")
    flat["redistribution"] = params.get("redistribution")
    flat["admin_distance"] = params.get("admin_distance")
    flat["vrf_name"] = params.get("vrf_name")
    flat["qos_type"] = params.get("qos_type") or params.get("qos_construct_type")
    flat["classification_rules"] = params.get("classification_rules")
    flat["interface_apply"] = params.get("interface_apply")

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


def configure_interface_conditional_disambiguation_fields(
    parameters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Shared access/trunk/subnet follow-up questions for ``configure_interface`` (primary or secondary).
    """
    shared_fields: List[Dict[str, Any]] = []
    p2 = dict(parameters or {})
    interfaces = p2.get("interfaces") or []
    if not isinstance(interfaces, list) or not interfaces:
        return shared_fields
    default_mode = str(p2.get("default_mode") or "").strip().lower() or None
    modes: set[str] = set()
    missing_access_vlan = False
    missing_trunk_vlans = False
    ip_addrs: set[str] = set()
    missing_subnet_mask = False

    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        mode = str(iface.get("mode") or default_mode or "").strip().lower()
        if mode:
            modes.add(mode)
        if mode == "access":
            if iface.get("access_vlan") in (None, "", []):
                missing_access_vlan = True
        elif mode == "trunk":
            tv = iface.get("trunk_vlans")
            if tv in (None, "", []):
                tv = iface.get("allowed_vlans")
            if tv in (None, "", []):
                missing_trunk_vlans = True
        ip_address = iface.get("ip_address")
        if ip_address not in (None, ""):
            ip_addrs.add(str(ip_address))
            if iface.get("subnet_mask") in (None, ""):
                missing_subnet_mask = True

    if len(modes) == 1:
        only_mode = next(iter(modes))
        if only_mode == "access" and missing_access_vlan:
            shared_fields.append(
                {
                    "field": "access_vlan",
                    "question": "Which access VLAN should be applied to all selected interfaces?",
                }
            )
        if only_mode == "trunk" and missing_trunk_vlans:
            shared_fields.append(
                {
                    "field": "trunk_vlans",
                    "question": (
                        "Which VLAN IDs should be allowed on this trunk? "
                        "(Pick from the list — options come from your intent and planned network context.)"
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


SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION: Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = {
    "configure_interface": configure_interface_conditional_disambiguation_fields,
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
    # Future: elif sa == "configure_ospf": ...
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
