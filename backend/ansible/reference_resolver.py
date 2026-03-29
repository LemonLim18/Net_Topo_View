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
import ipaddress
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
_KNOWN_FEATURES = frozenset({"vlan", "interface", "ospf", "static_route", "acl", "qos"})

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
                nv = item.get("native_vlan")
                if nv is not None and str(nv).strip() != "":
                    try:
                        row["native_vlan"] = int(nv)
                    except (TypeError, ValueError):
                        pass
                # Native VLAN is trunk-only; LLM/Studio often omit mode when trunk_vlans + native_vlan are set.
                if row.get("native_vlan") is not None:
                    row["mode"] = "trunk"
                elif item.get("trunk_vlans") is not None and str(item.get("mode") or "").strip().lower() != "access":
                    row["mode"] = "trunk"
                if str(row.get("mode") or "access").lower() == "trunk" and "allowed_vlans" not in row and vlan_id_for_default is not None:
                    row["allowed_vlans"] = [vlan_id_for_default]
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
    Return a list of { "interface": name, "mode": "access"|"trunk", "vlans": [id, ...], "native_vlan"? }
    from intent (for preview/reasoning display). Uses same normalization as template fill.
    """
    normalized = _normalize_intent_to_schema_shape(intent)
    out: List[Dict[str, Any]] = []
    for i in normalized.get("interfaces") or []:
        name = (i.get("name") or "").strip()
        if not name:
            continue
        mode = "trunk" if str(i.get("mode") or "").strip().lower() == "trunk" else "access"
        nv_raw = i.get("native_vlan")
        if nv_raw is not None and str(nv_raw).strip() != "":
            mode = "trunk"
        native_vlan: Optional[int] = None
        if mode == "trunk":
            vlans = i.get("allowed_vlans") or []
            if isinstance(vlans, str):
                vlans = [int(x) for x in str(vlans).replace(" ", "").split(",") if str(x).strip().isdigit()]
            nv = i.get("native_vlan")
            if nv is not None and str(nv).strip() != "":
                try:
                    native_vlan = int(nv)
                except (TypeError, ValueError):
                    native_vlan = None
        else:
            av = i.get("access_vlan")
            vlans = [int(av)] if av is not None else []
        row: Dict[str, Any] = {
            "interface": name,
            "mode": mode,
            "vlans": vlans if isinstance(vlans, list) else list(vlans),
        }
        if native_vlan is not None:
            row["native_vlan"] = native_vlan
        out.append(row)
    return out


def format_vlan_interface_reasoning_line(s: Dict[str, Any]) -> str:
    """
    One bullet for preview reasoning (Playbook Studio + initial parse).
    Uses trunk vs access wording so "native VLAN" is not confused with access VLAN.
    """
    vlan_str = ", ".join(str(v) for v in s.get("vlans") or [])
    mode = (s.get("mode") or "access").lower()
    nv = s.get("native_vlan")
    nv_part = (
        f", native VLAN {nv}"
        if mode == "trunk" and nv is not None and str(nv).strip() != ""
        else ""
    )
    if mode == "trunk":
        vlan_part = f", allowed VLANs {vlan_str}" if vlan_str else ""
    else:
        vlan_part = f", access VLAN {vlan_str}" if vlan_str else ""
    iface = s.get("interface") or s.get("name") or "?"
    return f"  - **{iface}**: {mode}" + vlan_part + nv_part


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
        cleaned_cfg: List[Dict[str, Any]] = []
        for i in interfaces_config:
            if not isinstance(i, dict):
                continue
            name_raw = str(i.get("name") or "").strip()
            if not name_raw:
                continue
            row = dict(i)
            row["name"] = _strip_device_prefix_from_iface_name(name_raw, out["target_hosts"])
            if str(row.get("name") or "").strip():
                cleaned_cfg.append(row)
        out["interfaces_config"] = cleaned_cfg
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
                name = _strip_device_prefix_from_iface_name(
                    str(x.get("name") or x.get("interface") or "").strip(),
                    out["target_hosts"],
                )
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
        name = _strip_device_prefix_from_iface_name(
            str(params.get("name") or params.get("interface") or "").strip(),
            out["target_hosts"],
        )
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
    if feature in ("routing", "static-route", "static"):
        feature = "static_route"
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
    if feature in {"vlan", "interface", "ospf", "static_route", "qos"}:
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
    if feature in ("routing", "static-route", "static"):
        feature = "static_route"
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

    if feature == "ospf":
        params_x = dict(intent.get("params") or {})
        target_hosts = list(
            intent.get("target_hosts")
            or params_x.get("target_hosts")
            or params_x.get("target_devices")
            or []
        )
        pid_raw = params_x.get("process_id", params_x.get("ospf_process_id"))
        process_id: Any = None
        try:
            if pid_raw is not None and str(pid_raw).strip() != "":
                process_id = int(pid_raw)
        except Exception:
            process_id = None

        router_id = str(params_x.get("router_id") or "").strip()
        area_default_raw = params_x.get("area")
        try:
            area_default = int(area_default_raw) if area_default_raw is not None and str(area_default_raw).strip() != "" else 0
        except Exception:
            area_default = 0

        def _wildcard_from_prefix(prefix: int) -> str:
            mask = ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask
            octets = [int(p) for p in str(mask).split(".")]
            return ".".join(str(255 - x) for x in octets)
        def _prefix_from_wildcard(wildcard: str) -> Optional[int]:
            try:
                wc = ipaddress.IPv4Address(str(wildcard).strip())
                mask_int = int(wc) ^ 0xFFFFFFFF
                net = ipaddress.IPv4Network((0, mask_int), strict=False)
                return int(net.prefixlen)
            except Exception:
                return None

        networks: List[Dict[str, Any]] = []
        raw_networks = params_x.get("networks") or params_x.get("ospf_networks") or []
        if isinstance(raw_networks, list):
            for n in raw_networks:
                if isinstance(n, dict):
                    net = str(n.get("network") or "").strip()
                    wc = str(n.get("wildcard") or "").strip()
                    area_raw = n.get("area")
                    try:
                        area_i = int(area_raw) if area_raw is not None and str(area_raw).strip() != "" else area_default
                    except Exception:
                        area_i = area_default
                    if net and wc:
                        pfx = _prefix_from_wildcard(wc)
                        row = {"network": net, "wildcard": wc, "area": area_i}
                        if pfx is not None:
                            row["prefix_length"] = pfx
                        networks.append(row)
                    continue
                s = str(n or "").strip()
                if not s:
                    continue
                m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b", s)
                if not m:
                    continue
                net = m.group(1)
                pfx = int(m.group(2))
                if pfx < 0 or pfx > 32:
                    continue
                am = re.search(r"\barea\s+(\d+)\b", s, re.I)
                area_i = int(am.group(1)) if am else area_default
                networks.append({"network": net, "wildcard": _wildcard_from_prefix(pfx), "area": area_i, "prefix_length": pfx})
        elif isinstance(raw_networks, str) and raw_networks.strip():
            m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b", raw_networks.strip())
            if m:
                net = m.group(1)
                pfx = int(m.group(2))
                if 0 <= pfx <= 32:
                    networks.append({"network": net, "wildcard": _wildcard_from_prefix(pfx), "area": area_default, "prefix_length": pfx})

        redistribution_raw = params_x.get("redistribution")
        if redistribution_raw in (None, "", []):
            redistribution_raw = params_x.get("redistribute")
        redistribution: List[str] = []
        if isinstance(redistribution_raw, list):
            for item in redistribution_raw:
                s = str(item).strip().lower()
                if not s:
                    continue
                if s in ("static", "connected", "bgp", "rip", "isis", "ospf"):
                    redistribution.append(s)
        elif redistribution_raw is not None and str(redistribution_raw).strip():
            for item in re.split(r"[\s,;]+", str(redistribution_raw).strip().lower()):
                if item in ("static", "connected", "bgp", "rip", "isis", "ospf"):
                    redistribution.append(item)

        return {
            "target_hosts": ",".join(str(h).strip() for h in target_hosts if str(h).strip()) or "all",
            "process_id": process_id,
            "router_id": router_id,
            "networks": networks,
            "passive_interfaces": params_x.get("passive_interfaces") or [],
            "redistribution": sorted(set(redistribution)),
        }

    if feature == "static_route":
        params_x = dict(intent.get("params") or {})
        target_hosts = list(
            intent.get("target_hosts")
            or params_x.get("target_hosts")
            or params_x.get("target_devices")
            or []
        )

        destination = str(params_x.get("destination") or "").strip()
        next_hop = str(params_x.get("next_hop") or "").strip()
        admin_distance_raw = params_x.get("admin_distance")
        vrf_name_raw = params_x.get("vrf_name")

        admin_distance: Optional[int] = None
        try:
            if admin_distance_raw is not None and str(admin_distance_raw).strip() != "":
                admin_distance = int(admin_distance_raw)
        except Exception:
            admin_distance = None

        vrf_name = str(vrf_name_raw or "").strip() if vrf_name_raw is not None else ""

        destination_network = ""
        destination_netmask = ""
        prefix_length: Optional[int] = None
        if destination:
            # Accept both CIDR and bare IPv4 (treat bare as /32).
            dest_norm = destination
            if "/" not in dest_norm:
                try:
                    ipaddress.IPv4Address(dest_norm)
                    dest_norm = f"{dest_norm}/32"
                except Exception:
                    dest_norm = destination
            try:
                net = ipaddress.IPv4Network(dest_norm, strict=False)
                destination_network = str(net.network_address)
                destination_netmask = str(net.netmask)
                prefix_length = int(net.prefixlen)
                destination = dest_norm
            except Exception:
                destination_network = str(destination).split("/")[0]

        return {
            "target_hosts": ",".join(str(h).strip() for h in target_hosts if str(h).strip()) or "all",
            "destination": destination,
            "destination_network": destination_network,
            "destination_netmask": destination_netmask,
            "prefix_length": prefix_length if prefix_length is not None else "",
            "next_hop": next_hop,
            "admin_distance": admin_distance,
            "vrf_name": vrf_name,
        }

    if feature == "acl":
        params_x = dict(intent.get("params") or {})
        target_hosts = list(
            intent.get("target_hosts")
            or params_x.get("target_hosts")
            or params_x.get("target_devices")
            or []
        )
        acl_name = str(params_x.get("acl_name") or params_x.get("name") or "").strip()
        acl_type = str(params_x.get("acl_type") or "ipv4").strip().lower()
        rules = params_x.get("rules") or []
        if isinstance(rules, str) and rules.strip():
            # Allow raw CLI lines as a single string.
            rules_out: List[Any] = [rules.strip()]
        elif isinstance(rules, list):
            # Normalize wildcard masks: users often supply CIDR or subnet masks; Cisco/Huawei ACLs use wildcard.
            rules_out = []
            for r in rules:
                if not isinstance(r, dict):
                    rules_out.append(r)
                    continue
                row = dict(r)
                for k in ("source_wildcard", "dest_wildcard"):
                    v = str(row.get(k) or "").strip()
                    if v.startswith("255."):
                        try:
                            octets = [int(p) for p in v.split(".")]
                            row[k] = ".".join(str(255 - x) for x in octets)
                        except Exception:
                            pass
                rules_out.append(row)
        else:
            rules_out = []
        return {
            "target_hosts": ",".join(str(h).strip() for h in target_hosts if str(h).strip()) or "all",
            "acl_name": acl_name,
            "acl_type": acl_type,
            "rules": rules_out,
            "apply_to": params_x.get("apply_to") or [],
        }

    if feature == "qos":
        params_x = dict(intent.get("params") or {})
        target_hosts = list(
            intent.get("target_hosts")
            or params_x.get("target_hosts")
            or params_x.get("target_devices")
            or []
        )

        def _string_or_empty(value: Any) -> str:
            return str(value or "").strip()

        policy_name = _string_or_empty(
            params_x.get("policy_name")
            or params_x.get("qos_policy")
            or params_x.get("policy")
        )
        qos_type = _string_or_empty(params_x.get("qos_type") or params_x.get("qos_construct_type")).lower()
        direction = _string_or_empty(params_x.get("direction") or params_x.get("service_direction")).lower()
        traffic_type = _string_or_empty(params_x.get("traffic_type") or params_x.get("qos_class") or params_x.get("class_name"))
        police_rate = _string_or_empty(
            params_x.get("police_rate")
            or params_x.get("policer_rate")
            or params_x.get("rate_limit")
            or params_x.get("bandwidth_limit")
        )

        marking_dscp: Any = params_x.get("marking_dscp")
        if marking_dscp in (None, "", []):
            marking_dscp = params_x.get("dscp")
        if marking_dscp in (None, "", []):
            marking_dscp = params_x.get("dscp_value")

        def _normalize_dscp_token(value: Any) -> str:
            s = str(value or "").strip().lower()
            if not s:
                return ""
            aliases = {
                "46": "ef",
                "expedited-forwarding": "ef",
            }
            return aliases.get(s, s)

        def _default_dscp_for_traffic(label: str) -> str:
            t = str(label or "").strip().lower()
            if t in ("voice", "voip", "rtp", "sip"):
                return "ef"
            if t in ("video", "streaming"):
                return "af41"
            if t in ("signaling", "control"):
                return "cs3"
            return ""

        raw_desc = _string_or_empty(
            intent.get("raw_user_input")
            or intent.get("intent_description")
            or params_x.get("raw_user_input")
        ).lower()

        classification_rules_raw = params_x.get("classification_rules") or params_x.get("classifier") or []
        rules_in: List[Any]
        if isinstance(classification_rules_raw, str) and classification_rules_raw.strip():
            rules_in = [classification_rules_raw.strip()]
        elif isinstance(classification_rules_raw, list):
            rules_in = classification_rules_raw
        else:
            rules_in = []

        effective_marking_dscp = _normalize_dscp_token(marking_dscp)
        if not effective_marking_dscp:
            effective_marking_dscp = _default_dscp_for_traffic(traffic_type)

        def _normalize_rule(rule: Any, fallback_dscp: str) -> Optional[str]:
            if isinstance(rule, dict):
                rule_type = str(rule.get("type") or rule.get("match") or "").strip().lower()
                rule_value = str(rule.get("value") or rule.get("dscp") or "").strip().lower()
                if rule_type in ("dscp", "ip dscp", "match dscp"):
                    value = _normalize_dscp_token(rule_value or fallback_dscp)
                    return f"match dscp {value}" if value else "match dscp"
                if rule_type:
                    return f"match {rule_type} {rule_value}".strip()
                return None
            text = str(rule or "").strip()
            if not text:
                return None
            low = text.lower()
            body = text[6:].strip() if low.startswith("match ") else text
            body_low = body.lower()

            if body_low in ("dscp", "ip dscp"):
                value = _normalize_dscp_token(fallback_dscp)
                return f"match dscp {value}" if value else "match dscp"
            if body_low.startswith("dscp "):
                value = _normalize_dscp_token(body.split(None, 1)[1])
                return f"match dscp {value}" if value else "match dscp"
            if body_low.startswith("ip dscp "):
                parts = body.split(None, 2)
                value = _normalize_dscp_token(parts[2] if len(parts) > 2 else "")
                return f"match dscp {value}" if value else "match dscp"

            return f"match {body}" if not low.startswith("match ") else f"match {body}"

        classification_rules: List[str] = []
        for r in rules_in:
            nr = _normalize_rule(r, effective_marking_dscp)
            if nr and nr not in classification_rules:
                classification_rules.append(nr)

        # Ensure voice-priority QoS has at least one useful classifier.
        if not classification_rules and effective_marking_dscp:
            classification_rules = [f"match dscp {effective_marking_dscp}"]

        interface_apply_raw = (
            params_x.get("interface_apply")
            or params_x.get("apply_to")
            or params_x.get("interfaces")
            or params_x.get("interface")
            or params_x.get("interface_name")
            or []
        )

        interface_apply: List[Any] = []
        raw_list = interface_apply_raw if isinstance(interface_apply_raw, list) else [interface_apply_raw]
        for ap in raw_list:
            if isinstance(ap, str):
                token = ap.strip()
                if not token:
                    continue
                # keep optional trailing direction token while normalizing iface prefix
                parts = token.split()
                maybe_dir = parts[-1].lower() if parts else ""
                if maybe_dir in ("in", "out", "inbound", "outbound"):
                    iface_part = " ".join(parts[:-1]).strip()
                    iface_norm = _strip_device_prefix_from_iface_name(iface_part, target_hosts)
                    if iface_norm:
                        interface_apply.append(f"{iface_norm} {maybe_dir}")
                else:
                    iface_norm = _strip_device_prefix_from_iface_name(token, target_hosts)
                    if iface_norm:
                        interface_apply.append(iface_norm)
            elif isinstance(ap, dict):
                iface_name = _string_or_empty(
                    ap.get("interface")
                    or ap.get("interface_name")
                    or ap.get("name")
                )
                if not iface_name:
                    continue
                iface_name = _strip_device_prefix_from_iface_name(iface_name, target_hosts)
                direction_item = _string_or_empty(ap.get("direction")).lower()
                normalized_item: Dict[str, str] = {"interface": iface_name}
                if direction_item in ("in", "out", "inbound", "outbound"):
                    normalized_item["direction"] = direction_item
                interface_apply.append(normalized_item)

        priority_percent_raw = params_x.get("priority_percent")
        priority_percent: Any = ""
        if priority_percent_raw not in (None, "", []):
            try:
                priority_percent = int(priority_percent_raw)
            except Exception:
                priority_percent = ""

        priority_hint = _string_or_empty(params_x.get("priority") or params_x.get("qos_priority")).lower()
        priority_enabled = bool(
            priority_hint in ("high", "strict", "priority", "yes", "true", "1")
            or (str(traffic_type or "").strip().lower() in ("voice", "voip"))
            or ("prioritize" in raw_desc and "voice" in raw_desc)
        )

        return {
            "target_hosts": ",".join(str(h).strip() for h in target_hosts if str(h).strip()) or "all",
            "policy_name": policy_name,
            "qos_type": qos_type,
            "classification_rules": classification_rules,
            "interface_apply": interface_apply,
            "direction": direction,
            "marking_dscp": effective_marking_dscp or marking_dscp,
            "traffic_type": traffic_type,
            "police_rate": police_rate,
            "priority_percent": priority_percent,
            "priority_enabled": priority_enabled,
            "conform_action": _string_or_empty(params_x.get("conform_action")),
            "exceed_action": _string_or_empty(params_x.get("exceed_action")),
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
