"""
Resolve reference playbook templates by feature/vendor and fill them from intent.

Flow:
  1. Resolve which reference playbook to use (RAG-informed or deterministic from feature + vendors).
  2. Load the template (playbook.yml or playbook_template.yml).
  3. Fill placeholders (e.g. {{ vlan_id }}, {{ vlan_name }}) from intent.params only — no LLM rewrite.
  4. Return the filled YAML string.

This keeps the reference template structure intact; only variable values are substituted.

Structured intent shape (LLM / API): vlan: { id, name }, target_hosts, interfaces: [] (optional).
Validated against reference/vlan/intent_schema.json when feature is vlan.
"""

import json
import logging
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ansible.reference_resolver")

_REFERENCE_DIR = Path(__file__).parent / "reference"
_VLAN_INTENT_SCHEMA_PATH = _REFERENCE_DIR / "vlan" / "intent_schema.json"

# Known features that have reference playbooks (subdirs under reference/)
_KNOWN_FEATURES = frozenset({"vlan"})

# Vendors that have single-vendor playbooks under reference/{feature}/{vendor}/
_KNOWN_VENDORS = frozenset({"cisco_ios", "huawei_vrp", "juniper_junos"})

# Multi-vendor template name (under reference/{feature}/multi_vendor/)
_MULTI_VENDOR_TEMPLATE = "playbook_template.yml"
_SINGLE_PLAYBOOK = "playbook.yml"


def _normalize_intent_to_schema_shape(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize intent to the schema shape: vlan: { id, name }, target_hosts, interfaces.
    Accepts intent with intent.vlan or intent.params (vlan_id, vlan_name, interfaces).
    """
    out: Dict[str, Any] = {}
    params = dict(intent.get("params") or {})
    if isinstance(intent.get("vlan"), dict):
        v = intent["vlan"]
        vid = v.get("id") or v.get("vlan_id")
        vname = v.get("name") or v.get("vlan_name")
    elif isinstance(params.get("vlan"), dict):
        v = params["vlan"]
        vid = v.get("id") or v.get("vlan_id")
        vname = v.get("name") or v.get("vlan_name")
    else:
        vid = params.get("vlan_id")
        vname = params.get("vlan_name")
        if vid is None and params.get("vlans"):
            vlans_list = params["vlans"]
            if isinstance(vlans_list, list) and vlans_list and isinstance(vlans_list[0], dict):
                first = vlans_list[0]
                vid = first.get("id") or first.get("vlan_id")
                vname = first.get("name") or first.get("vlan_name")
    if vid is not None:
        try:
            out["vlan"] = {"id": int(vid), "name": str(vname or "").strip() or "VLAN"}
        except (TypeError, ValueError):
            out["vlan"] = {"id": 200, "name": str(vname or "").strip() or "VLAN"}
    else:
        out["vlan"] = {"id": 200, "name": str(vname or "").strip() or "VLAN"}

    out["target_hosts"] = list(intent.get("target_hosts") or [])
    raw_ifaces = intent.get("interfaces")
    if raw_ifaces is None:
        raw_ifaces = params.get("interfaces")
    if isinstance(raw_ifaces, list):
        interfaces: List[Dict[str, Any]] = []
        # Top-level mode/trunk_vlans from LLM (config_engine vlan schema: interfaces as strings + mode + trunk_vlans)
        global_mode = str(params.get("mode") or "access").strip().lower()
        global_trunk_vlans = params.get("trunk_vlans")
        allowed_list: List[int] = []
        if global_trunk_vlans is not None:
            if isinstance(global_trunk_vlans, list):
                allowed_list = [int(x) for x in global_trunk_vlans if str(x).strip().isdigit()]
            elif isinstance(global_trunk_vlans, str) and global_trunk_vlans.strip().lower() != "all":
                for part in global_trunk_vlans.replace(" ", "").split(","):
                    if part.isdigit():
                        allowed_list.append(int(part))
        for item in raw_ifaces:
            if isinstance(item, dict):
                interfaces.append({
                    "name": str(item.get("name", "")).strip(),
                    "mode": "trunk" if str(item.get("mode") or "").strip().lower() == "trunk" else "access",
                    "access_vlan": item.get("access_vlan"),
                    "allowed_vlans": item.get("allowed_vlans") or [],
                    "native_vlan": item.get("native_vlan"),
                })
            else:
                # LLM sent interface name as string; use global mode and trunk_vlans
                name = str(item).strip()
                if not name:
                    continue
                mode = "trunk" if global_mode == "trunk" else "access"
                if mode == "trunk" and allowed_list:
                    interfaces.append({"name": name, "mode": "trunk", "allowed_vlans": allowed_list})
                else:
                    interfaces.append({"name": name, "mode": "access"})
        out["interfaces"] = [i for i in interfaces if i.get("name")]
    else:
        out["interfaces"] = []
    return out


def get_vlan_interfaces_summary(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return a list of { "interface": name, "mode": "access"|"trunk", "vlans": [id, ...] }
    from intent (for preview/reasoning display). Uses same normalization as template fill.
    """
    normalized = _normalize_intent_to_schema_shape(intent)
    out: List[Dict[str, Any]] = []
    for i in normalized.get("interfaces") or []:
        name = (i.get("name") or "").strip()
        if not name:
            continue
        mode = "trunk" if str(i.get("mode") or "").strip().lower() == "trunk" else "access"
        if mode == "trunk":
            vlans = i.get("allowed_vlans") or []
            if isinstance(vlans, str):
                vlans = [int(x) for x in str(vlans).replace(" ", "").split(",") if str(x).strip().isdigit()]
        else:
            av = i.get("access_vlan")
            vlans = [int(av)] if av is not None else []
        out.append({"interface": name, "mode": mode, "vlans": vlans if isinstance(vlans, list) else list(vlans)})
    return out


def validate_vlan_intent(intent: Dict[str, Any]) -> Tuple[bool, Optional[List[str]]]:
    """
    Validate intent against reference/vlan/intent_schema.json.
    Intent can be in API shape (params, target_hosts) or schema shape (vlan, target_hosts, interfaces).
    Returns (True, None) if valid, (False, list of error messages) if invalid.
    """
    normalized = _normalize_intent_to_schema_shape(intent)
    if not _VLAN_INTENT_SCHEMA_PATH.exists():
        return True, None
    try:
        import jsonschema
    except ImportError:
        return True, None
    try:
        schema = json.loads(_VLAN_INTENT_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=normalized, schema=schema)
        return True, None
    except json.JSONDecodeError as e:
        return False, [f"Intent schema file invalid: {e}"]
    except jsonschema.ValidationError as e:
        errors = []
        for err in (getattr(e, "errors", None) or [e]):
            path = "/".join(str(p) for p in (getattr(err, "path", None) or ()))
            msg = getattr(err, "message", str(err))
            errors.append(f"{msg}" + (f" at {path}" if path else ""))
        return False, errors if errors else [str(e)]
    except Exception as e:
        return False, [str(e)]


def resolve_playbook_template_path(
    feature: str,
    vendors: List[str],
) -> Optional[Path]:
    """
    Resolve the path to the reference playbook template for this feature and vendor set.

    - If multiple vendors (e.g. cisco_ios + huawei_vrp): use reference/{feature}/multi_vendor/playbook_template.yml.
    - If a single known vendor: use reference/{feature}/{vendor}/playbook.yml.
    - Otherwise return None (caller should fall back to LLM generation).

    Args:
        feature: e.g. "vlan"
        vendors: list of normalized vendor strings, e.g. ["cisco_ios", "huawei_vrp"]

    Returns:
        Path to the template file, or None if no reference exists.
    """
    feature = (feature or "").strip().lower()
    if not feature or feature not in _KNOWN_FEATURES:
        return None

    base = _REFERENCE_DIR / feature
    if not base.exists():
        return None

    # Normalize and filter to known vendors
    seen = {normalize_vendor(v) for v in vendors if v}
    seen = seen & _KNOWN_VENDORS

    if len(seen) > 1:
        multi_path = base / "multi_vendor" / _MULTI_VENDOR_TEMPLATE
        if multi_path.exists():
            return multi_path
        return None
    if len(seen) == 1:
        vendor = next(iter(seen))
        single_path = base / vendor / _SINGLE_PLAYBOOK
        if single_path.exists():
            return single_path
    # For vlan with no vendor info (e.g. planning_context empty), default to multi_vendor
    # so we always get the correct reference format instead of LLM/bundle wrong format
    if feature == "vlan":
        multi_path = base / "multi_vendor" / _MULTI_VENDOR_TEMPLATE
        if multi_path.exists():
            return multi_path
    return None


def normalize_vendor(value: Any) -> str:
    v = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v else ""


def vendors_from_planning_context(planning_context: Optional[Dict[str, Any]]) -> List[str]:
    """Extract unique normalized vendors from planning_context['devices']."""
    if not planning_context:
        return []
    devices = planning_context.get("devices") or []
    out = []
    seen = set()
    for d in devices:
        v = normalize_vendor(d.get("vendor"))
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def vendors_from_db(hostnames: List[str], db_manager: Any) -> List[str]:
    """
    Get unique normalized vendors for the given hostnames from the device DB.
    Use when planning_context has no vendor info (e.g. bundle inventory empty or vendor not set).
    """
    if not hostnames or db_manager is None:
        return []
    out = []
    seen = set()
    for host in hostnames:
        try:
            dev = db_manager.get_device_by_hostname(host)
            if dev is None:
                continue
            v = normalize_vendor(getattr(dev, "vendor", None))
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        except Exception:
            continue
    return out


def load_template(path: Path) -> str:
    """Load template file content as string."""
    return path.read_text(encoding="utf-8")


def get_template_vars(intent: Dict[str, Any], _template_content: str) -> Dict[str, Any]:
    """
    Build the variable dict for template rendering from intent.

    Accepts intent in API shape (params.vlan_id, params.vlan_name, params.interfaces)
    or schema shape (vlan: { id, name }, target_hosts, interfaces).
    Exposes: vlan_id, vlan_name, vlan (dict), interfaces (schema-shaped list),
    interfaces_config_with_vlan (module-ready), target_hosts.
    """
    normalized = _normalize_intent_to_schema_shape(intent)
    vlan_dict = normalized["vlan"]
    schema_interfaces = normalized["interfaces"]
    vlan_id_str = str(vlan_dict["id"]).strip()
    vlan_name_str = str(vlan_dict.get("name") or "VLAN").strip()

    params: Dict[str, Any] = {
        "vlan_id": vlan_id_str,
        "vlan_name": vlan_name_str,
        "vlan": vlan_dict,
        "interfaces": schema_interfaces,
    }
    params["configure_interfaces"] = bool(schema_interfaces)

    interfaces_config: List[Dict[str, Any]] = []
    interfaces_config_with_vlan: List[Dict[str, Any]] = []
    for i in schema_interfaces:
        name = (i.get("name") or "").strip()
        if not name:
            continue
        mode = "trunk" if str(i.get("mode") or "").strip().lower() == "trunk" else "access"
        interfaces_config.append({"name": name, "mode": mode})
        if mode == "access":
            access_vlan = i.get("access_vlan")
            vid = str(access_vlan if access_vlan is not None else vlan_dict["id"]).strip()
            interfaces_config_with_vlan.append({
                "name": name,
                "mode": "access",
                "access": {"vlan": vid},
            })
        else:
            allowed = i.get("allowed_vlans")
            trunk_vlans = "all"
            if isinstance(allowed, list) and allowed:
                trunk_vlans = ",".join(str(x) for x in allowed)
            elif isinstance(allowed, str):
                trunk_vlans = allowed
            interfaces_config_with_vlan.append({
                "name": name,
                "mode": "trunk",
                "trunk": {"trunk_vlans": trunk_vlans},
            })

    params["interfaces_config"] = interfaces_config
    params["interfaces_config_with_vlan"] = interfaces_config_with_vlan
    yaml_str = yaml.dump(interfaces_config_with_vlan, default_flow_style=False, sort_keys=False)
    params["interfaces_config_yaml"] = "\n".join("          " + line for line in yaml_str.strip().splitlines())
    # Indented YAML for config: key — must be indented more than "config:" (8 spaces) so YAML parses as key value
    indent = "          "  # 10 spaces so value is under config:
    params["interfaces_config_with_vlan_yaml"] = "\n".join(indent + line for line in yaml_str.strip().splitlines())
    target_hosts = list(intent.get("target_hosts") or [])
    params["target_hosts"] = ",".join(str(h).strip() for h in target_hosts if str(h).strip())
    return params


def render_template(template_content: str, variables: Dict[str, Any]) -> str:
    """
    Render the template with the given variables.

    Uses Jinja2-style substitution: {{ vlan_id }}, {{ vlan_name }} in the
    template are replaced by the values in variables. No other logic;
    the reference template is not modified structurally.
    """
    try:
        from jinja2 import Environment, BaseLoader
    except ImportError:
        # Fallback: simple replace for {{ key }} patterns
        out = template_content
        for key, val in variables.items():
            out = out.replace("{{ " + key + " }}", str(val))
            out = out.replace("{{ " + key + "}}", str(val))
            out = out.replace("{{" + key + " }}", str(val))
            out = out.replace("{{" + key + "}}", str(val))
        return out

    env = Environment(
        loader=BaseLoader(),
        variable_start_string="{{ ",
        variable_end_string=" }}",
        # Avoid stripping newlines
        trim_blocks=False,
        lstrip_blocks=False,
    )
    tpl = env.from_string(template_content)
    return tpl.render(**variables)


def fill_playbook_from_reference(
    feature: str,
    vendors: List[str],
    intent: Dict[str, Any],
    planning_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    If a reference template exists for this feature/vendor set, load it,
    fill variables from intent, and return the playbook YAML. Otherwise return None.

    Caller should fall back to LLM generation when this returns None.
    """
    path = resolve_playbook_template_path(feature, vendors)
    if path is None:
        return None
    try:
        content = load_template(path)
        variables = get_template_vars(intent, content)
        variables["vendors"] = [normalize_vendor(v) for v in vendors if v]
        return render_template(content, variables)
    except Exception as exc:
        logger.warning("Template fill failed for %s: %s", path, exc)
        return None
