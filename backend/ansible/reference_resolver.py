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
import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ansible.reference_resolver")

_REFERENCE_DIR = Path(__file__).parent / "reference"


def _parse_vlan_int_list(value: Any) -> List[int]:
    """Parse VLAN id(s) from list, comma string, or scalar; ignores non-digits."""
    out: List[int] = []
    if value is None:
        return out
    if isinstance(value, list):
        for x in value:
            s = str(x).strip()
            if s.isdigit():
                try:
                    out.append(int(s))
                except (TypeError, ValueError):
                    continue
        return out
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return out
    s = str(value).strip()
    if not s or s.lower() == "all":
        return out
    for part in s.replace(" ", "").split(","):
        if part.isdigit():
            try:
                out.append(int(part))
            except (TypeError, ValueError):
                continue
    return out


def _iface_trunk_allowed_from_planning(
    planning_context: Optional[Dict[str, Any]],
    target_hosts: List[str],
    iface_name: str,
) -> List[int]:
    """
    Union of allowed/trunk VLAN IDs from planned inventory iface records for this interface
    name across the selected hosts (append semantics vs intent-only replace).
    """
    if not planning_context or not iface_name:
        return []
    stripped = _strip_device_prefix_from_iface_name(iface_name, target_hosts)
    host_set = {str(h).strip().lower() for h in target_hosts if str(h).strip()}
    out: List[int] = []
    for d in planning_context.get("devices") or []:
        if not isinstance(d, dict):
            continue
        hn = str(d.get("hostname") or "").strip()
        if host_set and hn.lower() not in host_set:
            continue
        for iface in d.get("interfaces") or []:
            if not isinstance(iface, dict):
                continue
            iname = str(iface.get("name") or iface.get("interface") or "").strip()
            if iname.lower() != stripped.lower():
                continue
            for key in ("allowed_vlans", "trunk_vlans", "vlans"):
                out.extend(_parse_vlan_int_list(iface.get(key)))
    return sorted(set(out))


def _merge_trunk_allowed_with_planning(
    interfaces: List[Dict[str, Any]],
    vlan_id: int,
    planning_context: Optional[Dict[str, Any]],
    target_hosts: List[str],
) -> List[Dict[str, Any]]:
    """Union intent trunk VLANs with inventory (when present); never widen to 'all' from empty."""
    if not planning_context or not target_hosts:
        return interfaces
    out: List[Dict[str, Any]] = []
    for i in interfaces:
        if not isinstance(i, dict):
            continue
        mode = str(i.get("mode") or "").strip().lower()
        if mode != "trunk":
            out.append(i)
            continue
        av = i.get("allowed_vlans")
        if isinstance(av, str) and av.strip().lower() == "all":
            out.append(dict(i))
            continue
        intent_list = _parse_vlan_int_list(av)
        if not intent_list:
            intent_list = [int(vlan_id)]
        planning_list = _iface_trunk_allowed_from_planning(planning_context, target_hosts, str(i.get("name") or ""))
        merged = sorted(set(intent_list + planning_list))
        row = dict(i)
        row["allowed_vlans"] = merged
        out.append(row)
    return out


def _target_hosts_from_intent(intent: Dict[str, Any]) -> List[str]:
    th = list(intent.get("target_hosts") or [])
    if th:
        return th
    params = intent.get("params") or {}
    if isinstance(params, dict):
        return list(params.get("target_hosts") or params.get("target_devices") or [])
    return []
_VLAN_INTENT_SCHEMA_PATH = _REFERENCE_DIR / "vlan" / "intent_schema.json"

# Known features that have reference playbooks (subdirs under reference/)
_KNOWN_FEATURES = frozenset({"vlan", "interface"})

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
        vlan_id_for_default = int(out["vlan"]["id"]) if isinstance(out.get("vlan"), dict) else None
        for item in raw_ifaces:
            if isinstance(item, dict):
                mode = "trunk" if str(item.get("mode") or "").strip().lower() == "trunk" else "access"
                row: Dict[str, Any] = {
                    "name": str(item.get("name", "")).strip(),
                    "mode": mode,
                }
                av = item.get("access_vlan")
                if av is not None and str(av).strip() != "":
                    try:
                        row["access_vlan"] = int(av)
                    except (TypeError, ValueError):
                        row["access_vlan"] = av
                al = item.get("allowed_vlans")
                if isinstance(al, str) and al.strip().lower() == "all":
                    row["allowed_vlans"] = "all"
                elif isinstance(al, list) and al:
                    row["allowed_vlans"] = al
                elif isinstance(al, str) and al.strip():
                    parsed_al = _parse_vlan_int_list(al)
                    if parsed_al:
                        row["allowed_vlans"] = parsed_al
                tv = item.get("trunk_vlans")
                if tv is not None:
                    if isinstance(tv, str) and tv.strip().lower() == "all":
                        row["allowed_vlans"] = "all"
                    else:
                        parsed_tv = _parse_vlan_int_list(tv)
                        if parsed_tv:
                            prev = row.get("allowed_vlans")
                            if isinstance(prev, list) and prev:
                                row["allowed_vlans"] = sorted(set(prev + parsed_tv))
                            elif prev == "all":
                                pass
                            else:
                                row["allowed_vlans"] = parsed_tv
                if mode == "trunk" and "allowed_vlans" not in row and vlan_id_for_default is not None:
                    row["allowed_vlans"] = [vlan_id_for_default]
                nv = item.get("native_vlan")
                if nv is not None and str(nv).strip() != "":
                    try:
                        row["native_vlan"] = int(nv)
                    except (TypeError, ValueError):
                        pass
                interfaces.append(row)
            else:
                # LLM sent interface name as string; use global mode and trunk_vlans
                name = str(item).strip()
                if not name:
                    continue
                mode = "trunk" if global_mode == "trunk" else "access"
                if mode == "trunk":
                    if allowed_list:
                        interfaces.append({"name": name, "mode": "trunk", "allowed_vlans": allowed_list})
                    elif vlan_id_for_default is not None:
                        interfaces.append({"name": name, "mode": "trunk", "allowed_vlans": [vlan_id_for_default]})
                    else:
                        interfaces.append({"name": name, "mode": "trunk"})
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


def _strip_device_prefix_from_iface_name(iface: str, hostnames: List[str]) -> str:
    """Turn 'SW1:Gi0/1' into 'Gi0/1' when SW1 is a known target host (UI chip format)."""
    s = str(iface or "").strip()
    if ":" not in s:
        return s
    prefix, rest = s.split(":", 1)
    rest = rest.strip()
    if not rest:
        return s
    for h in hostnames:
        if h and prefix.strip().lower() == str(h).strip().lower():
            return rest
    return s


def _normalize_intent_to_interface_shape(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize intent to a consistent interface shape for reference templates.

    Output:
      - target_hosts: list[str]
      - interfaces_config: list[dict]  (preferred; multi-scenario)
      - interfaces_to_shutdown: list[str] (fallback; shutdown-only)

    Accepted inputs (flexible):
      - intent.params.interfaces_config: list[dict]
      - intent.params.interfaces_to_shutdown: list[str]
      - intent.params.interfaces: list[str] or list[dict] (name/shutdown/description/vlan/ip fields)
      - intent.params.name + shutdown/description/vlan/ip_address/subnet_mask (single interface)
    """
    params = dict(intent.get("params") or {})
    out: Dict[str, Any] = {}
    out["target_hosts"] = list(intent.get("target_hosts") or [])

    # If the caller wants per-host nettopo_interfaces expansion (for "all interfaces"),
    # we must ignore any LLM-provided/guessed interface config lists and ensure the
    # templates take the shutdown-only path.
    if bool(params.get("interfaces_shutdown_use_nettopo")):
        return {
            "target_hosts": out["target_hosts"],
            "interfaces_config": [],
            "interfaces_to_shutdown": [],
        }

    interfaces_config = params.get("interfaces_config")
    if isinstance(interfaces_config, list):
        out["interfaces_config"] = [i for i in interfaces_config if isinstance(i, dict) and str(i.get("name") or "").strip()]
    else:
        out["interfaces_config"] = []

    interfaces_to_shutdown = params.get("interfaces_to_shutdown")
    if isinstance(interfaces_to_shutdown, list):
        out["interfaces_to_shutdown"] = [
            _strip_device_prefix_from_iface_name(str(x), out["target_hosts"])
            for x in interfaces_to_shutdown
            if str(x).strip()
        ]
    else:
        out["interfaces_to_shutdown"] = []

    shutdown_explicit = bool(out["interfaces_to_shutdown"])

    # Accept config_engine-ish interface params: interfaces (names or dicts)
    raw_ifaces = params.get("interfaces")
    if isinstance(raw_ifaces, list) and raw_ifaces and not shutdown_explicit:
        # If dict items already, map them into interfaces_config
        if all(isinstance(x, dict) for x in raw_ifaces):
            cfg: list[dict] = []
            for x in raw_ifaces:
                name = str(x.get("name") or x.get("interface") or "").strip()
                if not name:
                    continue
                enabled = None
                if "enabled" in x:
                    enabled = bool(x.get("enabled"))
                elif "shutdown" in x:
                    enabled = (not bool(x.get("shutdown")))
                # Normalize `admin_state` (user-facing) -> `enabled` (template-facing).
                # Default when omitted: enabled=True (admin_state up).
                admin_state = x.get("admin_state")
                if admin_state is not None:
                    lowered = str(admin_state).strip().lower()
                    if lowered in ("up", "enable", "enabled"):
                        enabled = True
                    elif lowered in ("down", "disable", "disabled"):
                        enabled = False
                if enabled is None:
                    enabled = True
                item: dict = {"name": name}
                item["enabled"] = enabled
                if x.get("description"):
                    item["description"] = str(x.get("description"))
                # Mode + L2/L3 fields normalization to the canonical template shape.
                # Template expects:
                #   - item.mode in {'access','trunk'}
                #   - item.access.vlan
                #   - item.trunk.allowed_vlans (+ native_vlan optional)
                #   - item.ip.address + item.ip.mask
                if x.get("mode"):
                    mode = str(x.get("mode")).strip().lower()
                    if mode in ("access", "trunk", "routed"):
                        item["mode"] = mode

                # Access VLAN (schema: access_vlan; legacy shortcut: vlan)
                access_vlan = x.get("access_vlan", x.get("vlan"))
                if access_vlan is not None and str(access_vlan).strip() != "":
                    try:
                        item["mode"] = "access"
                        item["access"] = {"vlan": int(access_vlan)}
                    except Exception:
                        pass

                # Trunk VLANs (schema: trunk_vlans; UI alias: allowed_vlans)
                trunk_vlans_raw = x.get("trunk_vlans", x.get("allowed_vlans"))
                if trunk_vlans_raw is not None and str(trunk_vlans_raw).strip() != "":
                    trunk_vlans: List[int] = []
                    if isinstance(trunk_vlans_raw, list):
                        for v in trunk_vlans_raw:
                            try:
                                trunk_vlans.append(int(v))
                            except (TypeError, ValueError):
                                pass
                    else:
                        s = str(trunk_vlans_raw).strip()
                        parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
                        for p in parts:
                            try:
                                trunk_vlans.append(int(p))
                            except (TypeError, ValueError):
                                pass
                    if trunk_vlans:
                        item["mode"] = "trunk"
                        item["trunk"] = {"allowed_vlans": trunk_vlans}
                        if x.get("native_vlan") not in (None, ""):
                            try:
                                item["trunk"]["native_vlan"] = int(x.get("native_vlan"))
                            except Exception:
                                pass

                # IP shortcut
                if x.get("ip_address"):
                    item["ip"] = {
                        "address": str(x.get("ip_address")),
                        "mask": str(x.get("subnet_mask") or ""),
                    }
                cfg.append(item)
            if cfg and not out["interfaces_config"]:
                out["interfaces_config"] = cfg
        else:
            # Name list: treat as shutdown-only list unless shutdown=false explicitly provided elsewhere
            names = [
                _strip_device_prefix_from_iface_name(str(x), out["target_hosts"])
                for x in raw_ifaces
                if str(x).strip()
            ]
            if names and not out["interfaces_to_shutdown"] and not out["interfaces_config"]:
                # If params.shutdown explicitly false, interpret as enable; else shutdown-only
                if params.get("shutdown") is False:
                    out["interfaces_config"] = [{"name": n, "enabled": True} for n in names]
                else:
                    out["interfaces_to_shutdown"] = names

    # Single interface fields
    if not out["interfaces_config"] and not out["interfaces_to_shutdown"]:
        name = str(params.get("name") or params.get("interface") or "").strip()
        if name:
            if params.get("shutdown") is True:
                out["interfaces_to_shutdown"] = [name]
            else:
                item: dict = {"name": name}
                if "shutdown" in params:
                    item["enabled"] = (not bool(params.get("shutdown")))
                if "admin_state" in params and params.get("admin_state") is not None:
                    lowered = str(params.get("admin_state")).strip().lower()
                    if lowered in ("up", "enable", "enabled"):
                        item["enabled"] = True
                    elif lowered in ("down", "disable", "disabled"):
                        item["enabled"] = False
                # Default when omitted: enabled=True.
                if "enabled" not in item:
                    item["enabled"] = True
                if params.get("description"):
                    item["description"] = str(params.get("description"))
                if params.get("mode") in ("access", "trunk", "routed"):
                    item["mode"] = str(params.get("mode")).strip().lower()
                # Access VLAN (schema: access_vlan; legacy shortcut: vlan)
                access_vlan = params.get("access_vlan", params.get("vlan"))
                if access_vlan is not None and str(access_vlan).strip() != "":
                    try:
                        item["mode"] = "access"
                        item["access"] = {"vlan": int(access_vlan)}
                    except Exception:
                        pass
                # Trunk VLANs (schema: trunk_vlans; UI alias: allowed_vlans)
                trunk_vlans_raw = params.get("trunk_vlans", params.get("allowed_vlans"))
                if trunk_vlans_raw is not None and str(trunk_vlans_raw).strip() != "":
                    trunk_vlans: List[int] = []
                    if isinstance(trunk_vlans_raw, list):
                        for v in trunk_vlans_raw:
                            try:
                                trunk_vlans.append(int(v))
                            except (TypeError, ValueError):
                                pass
                    else:
                        s = str(trunk_vlans_raw).strip()
                        parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
                        for p in parts:
                            try:
                                trunk_vlans.append(int(p))
                            except (TypeError, ValueError):
                                pass
                    if trunk_vlans:
                        item["mode"] = "trunk"
                        item["trunk"] = {"allowed_vlans": trunk_vlans}
                        if params.get("native_vlan") not in (None, ""):
                            try:
                                item["trunk"]["native_vlan"] = int(params.get("native_vlan"))
                            except Exception:
                                pass
                if params.get("ip_address"):
                    item["ip"] = {"address": str(params.get("ip_address")), "mask": str(params.get("subnet_mask") or "")}
                out["interfaces_config"] = [item]

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
    # For vlan/interface with no vendor info (e.g. planning_context empty), default to multi_vendor
    # so we always get the correct reference format instead of LLM/bundle wrong format.
    if feature in {"vlan", "interface"}:
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


def resolve_interfaces(
    intent: Dict[str, Any],
    planning_context: Optional[Dict[str, Any]],
) -> Optional[List[str]]:
    """
    Topology/inventory resolution when the intent has no explicit interface list in params.
    Returns list of interface names from topology/inventory, or None if resolution fails.
    Caller must log and abort when None; no silent fallback.
    """
    target_hosts = list(intent.get("target_hosts") or [])
    if not target_hosts:
        logger.warning("resolve_interfaces: no target_hosts in intent")
        return None
    if not planning_context:
        logger.error("No topology data found → abort (planning_context missing)")
        return None
    devices = list(planning_context.get("devices") or [])
    if not devices:
        logger.error("No topology data found → abort (no devices in planning_context)")
        return None
    host_set = {str(h).strip() for h in target_hosts if str(h).strip()}
    names: List[str] = []
    seen: set = set()
    for d in devices:
        if str(d.get("hostname") or "").strip() not in host_set:
            continue
        for iface in d.get("interfaces") or []:
            name = str(iface.get("name") or iface.get("interface") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    if not names:
        logger.error("No topology data found → abort (no interfaces for target hosts)")
        return None
    logger.info("Resolved %d interface(s) from topology for target hosts", len(names))
    return names


def get_template_vars(
    feature: str,
    intent: Dict[str, Any],
    _template_content: str,
    planning_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the variable dict for template rendering from intent.

    Accepts intent in API shape (params.vlan_id, params.vlan_name, params.interfaces)
    or schema shape (vlan: { id, name }, target_hosts, interfaces).
    Exposes: vlan_id, vlan_name, vlan (dict), interfaces (schema-shaped list),
    interfaces_config_with_vlan (module-ready), target_hosts.
    """
    feature = (feature or "").strip().lower()
    if feature == "interface":
        normalized_i = _normalize_intent_to_interface_shape(intent)
        target_hosts = list(normalized_i.get("target_hosts") or [])
        interfaces_config = list(normalized_i.get("interfaces_config") or [])
        interfaces_to_shutdown = list(normalized_i.get("interfaces_to_shutdown") or [])
        params_x = intent.get("params") or {}
        use_nettopo_shutdown = bool(params_x.get("interfaces_shutdown_use_nettopo"))
        use_nettopo_config = bool(params_x.get("interfaces_config_use_nettopo"))
        # Optional: admin-up vs admin-down behavior for shutdown-style templates.
        # When omitted, templates default to admin-down (disabled).
        interfaces_admin_enabled = params_x.get("interfaces_admin_enabled")
        if use_nettopo_shutdown:
            interfaces_to_shutdown = []
        if use_nettopo_config:
            # Configure-all expansion uses `interfaces_config` (shutdown tasks are optional fallback).
            interfaces_to_shutdown = []
        # No silent fallback unless interfaces_shutdown_use_nettopo (per-host nettopo_interfaces at run time).
        # Pre-serialize for reference/playbook.yml first Jinja pass (avoids relying on Jinja2's tojson filter).
        return {
            "target_hosts": ",".join(str(h).strip() for h in target_hosts if str(h).strip()),
            "interfaces_config_override": interfaces_config,
            "interfaces_to_shutdown_override": interfaces_to_shutdown if interfaces_to_shutdown else None,
            "interfaces_config_json": json.dumps(interfaces_config),
            "interfaces_to_shutdown_json": json.dumps(interfaces_to_shutdown),
            "interfaces_shutdown_use_nettopo": use_nettopo_shutdown,
            "interfaces_config_use_nettopo": use_nettopo_config,
            "interfaces_admin_enabled": interfaces_admin_enabled,
        }

    normalized = _normalize_intent_to_schema_shape(intent)
    vlan_dict = normalized["vlan"]
    schema_interfaces = list(normalized.get("interfaces") or [])
    target_hosts_resolved = _target_hosts_from_intent(intent)
    schema_interfaces = _merge_trunk_allowed_with_planning(
        schema_interfaces, int(vlan_dict["id"]), planning_context, target_hosts_resolved
    )
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
    # "All interfaces" expansion for VLAN interface binding:
    # If the normalized interfaces list contains a sentinel interface named "all",
    # we avoid fill-time interface task generation (which would be wrong across hosts)
    # and instead let VLAN templates expand from per-host `nettopo_interfaces`.
    interfaces_config_with_vlan_use_nettopo = any(
        str(i.get("name") or "").strip().lower() == "all" for i in (schema_interfaces or [])
    )

    default_binding_mode = None
    default_trunk_allowed_vlans: Any = None
    default_trunk_native_vlan: Optional[int] = None
    if interfaces_config_with_vlan_use_nettopo:
        default_iface = next((i for i in schema_interfaces if str(i.get("name") or "").strip().lower() == "all"), None) or {}
        default_binding_mode = str(default_iface.get("mode") or "access").strip().lower()
        allowed = default_iface.get("allowed_vlans")
        vid_int = int(vlan_dict["id"])
        if default_binding_mode == "trunk":
            native_vlan = default_iface.get("native_vlan")
            if native_vlan is not None and str(native_vlan).strip() != "":
                try:
                    default_trunk_native_vlan = int(native_vlan)
                except Exception:
                    default_trunk_native_vlan = None
            if isinstance(allowed, list) and allowed:
                try:
                    default_trunk_allowed_vlans = [int(x) for x in allowed]
                except Exception:
                    default_trunk_allowed_vlans = [vid_int]
            elif isinstance(allowed, str):
                s = allowed.strip().lower()
                if s == "all":
                    default_trunk_allowed_vlans = "all"
                elif s == "":
                    default_trunk_allowed_vlans = [vid_int]
                else:
                    # Accept comma/space separated VLANs
                    parts = [p.strip() for p in allowed.replace(" ", "").split(",") if p.strip()]
                    try:
                        default_trunk_allowed_vlans = [int(p) for p in parts if p.isdigit()]
                    except Exception:
                        default_trunk_allowed_vlans = [vid_int]
                    if not default_trunk_allowed_vlans:
                        default_trunk_allowed_vlans = [vid_int]
            else:
                # Missing / null allowed list: never default to "all" (too broad vs "allow this VLAN on trunk").
                default_trunk_allowed_vlans = [vid_int]

    if not interfaces_config_with_vlan_use_nettopo:
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
                vid_int = int(vlan_dict["id"])
                # cisco.ios.ios_l2_interfaces / juniper junos_l2_interfaces: trunk.allowed_vlans (not trunk_vlans)
                if isinstance(allowed, str) and allowed.strip().lower() == "all":
                    trunk_allowed = "all"
                else:
                    plist = _parse_vlan_int_list(allowed)
                    if not plist:
                        plist = [vid_int]
                    trunk_allowed = ",".join(str(x) for x in plist)
                native_vlan = i.get("native_vlan")
                native_vlan_int: Optional[int] = None
                if native_vlan is not None and str(native_vlan).strip() != "":
                    try:
                        native_vlan_int = int(native_vlan)
                    except Exception:
                        native_vlan_int = None
                interfaces_config_with_vlan.append({
                    "name": name,
                    "mode": "trunk",
                    "trunk": {
                        "allowed_vlans": trunk_allowed,
                        **({"native_vlan": native_vlan_int} if native_vlan_int is not None else {}),
                    },
                })

    params["interfaces_config"] = interfaces_config
    params["interfaces_config_with_vlan"] = interfaces_config_with_vlan
    params["interfaces_config_with_vlan_use_nettopo"] = interfaces_config_with_vlan_use_nettopo
    params["interfaces_binding_mode"] = default_binding_mode or "access"
    params["interfaces_binding_trunk_allowed_vlans"] = default_trunk_allowed_vlans
    params["interfaces_binding_trunk_native_vlan"] = default_trunk_native_vlan
    yaml_str = yaml.dump(interfaces_config_with_vlan, default_flow_style=False, sort_keys=False)
    params["interfaces_config_yaml"] = "\n".join("          " + line for line in yaml_str.strip().splitlines())
    # Indented YAML for config: key — must be indented more than "config:" (8 spaces) so YAML parses as key value
    indent = "          "  # 10 spaces so value is under config:
    params["interfaces_config_with_vlan_yaml"] = "\n".join(indent + line for line in yaml_str.strip().splitlines())
    params["target_hosts"] = ",".join(str(h).strip() for h in target_hosts_resolved if str(h).strip())
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


def _sanitize_ios_l2_trunk_yaml_keys(playbook_yaml: str) -> str:
    """
    cisco.ios.ios_l2_interfaces (and juniper junos_l2_interfaces) require trunk.allowed_vlans,
    not trunk.trunk_vlans. Older templates, cached jobs, or remote deployments may still emit
    the invalid key — rewrite at fill time so dry-runs succeed after template updates.
    """
    if "trunk_vlans:" not in playbook_yaml:
        return playbook_yaml
    # Only rewrite YAML mapping keys (indented trunk_vlans:), not Jinja e.g. iface.trunk.trunk_vlans
    return re.sub(r"(?m)^(\s*)trunk_vlans:(\s|$)", r"\1allowed_vlans:\2", playbook_yaml)


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
        variables = get_template_vars(feature, intent, content, planning_context=planning_context)
        variables["vendors"] = [normalize_vendor(v) for v in vendors if v]
        rendered = render_template(content, variables)
        return _sanitize_ios_l2_trunk_yaml_keys(rendered)
    except Exception as exc:
        logger.warning("Template fill failed for %s: %s", path, exc)
        return None
