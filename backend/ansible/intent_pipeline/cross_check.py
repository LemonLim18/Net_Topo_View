"""
Cross-check devices and interfaces against planned DB context (REFACTOR_INTENT_PARSER.md).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


def _inventory_host_set(db_manager: Any) -> Tuple[Set[str], Dict[str, str]]:
    """Lowercase hostname → canonical hostname."""
    hosts: Set[str] = set()
    canon: Dict[str, str] = {}
    try:
        for d in db_manager.get_all_devices():
            h = (getattr(d, "hostname", None) or "").strip()
            if not h:
                continue
            hl = h.lower()
            hosts.add(hl)
            canon[hl] = h
    except Exception:
        pass
    return hosts, canon


def all_planned_device_hostnames_sorted(db_manager: Any) -> List[str]:
    """Canonical hostnames from planned inventory (sorted, case-insensitive) — for UI pickers, not fuzzy guesses."""
    _inv, canon = _inventory_host_set(db_manager)
    return sorted(canon.values(), key=lambda x: str(x).lower())


def _interface_names_for_host(db_manager: Any, hostname: str) -> List[str]:
    try:
        for d in db_manager.get_all_devices():
            h = (getattr(d, "hostname", None) or "").strip()
            if h and h.lower() == hostname.lower():
                ifaces = db_manager.get_interfaces_for_device(d.id)
                return [getattr(i, "name", None) or getattr(i, "if_name", None) or str(i) for i in ifaces if i]
    except Exception:
        pass
    return []


def _normalize_iface_token(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _split_interface_host_hint(raw: Any) -> Tuple[Optional[str], str]:
    item = str(raw or "").strip()
    if ":" in item or "@" in item:
        sep = ":" if ":" in item else "@"
        host_part, iface_part = item.split(sep, 1)
        host_part = host_part.strip()
        iface_part = iface_part.strip()
        if host_part and iface_part:
            return host_part, iface_part
    return None, item


def _host_hint_from_iface_dict(item: Dict[str, Any]) -> Optional[str]:
    for key in (
        "hostname",
        "host",
        "device",
        "device_hostname",
        "device_name",
        "target_host",
        "target_hostname",
        "target_device",
    ):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    host_list = item.get("device_hostnames") or item.get("target_hosts")
    if isinstance(host_list, list) and len(host_list) == 1:
        val = str(host_list[0] or "").strip()
        return val or None
    return None


def cross_check_parsed_intent(data: Dict[str, Any], db_manager: Optional[Any]) -> Dict[str, Any]:
    """
    Validate resolved/mentioned devices and interface strings against inventory.

    Returns:
      invalid_devices: [{token, suggestions, multi_select, planned_network_context}]
      invalid_interfaces: [{hostname, value, field, message, suggestions, multi_select}]

    No fuzzy matching: unknown tokens are invalid; ``suggestions`` lists **all** planned
    devices or **all** interfaces on the host for admin multi-select (no difflib heuristics).
    """
    invalid_devices: List[Dict[str, Any]] = []
    invalid_interfaces: List[Dict[str, Any]] = []

    if db_manager is None:
        return {"invalid_devices": invalid_devices, "invalid_interfaces": invalid_interfaces}

    inv_set, inv_canon = _inventory_host_set(db_manager)
    if not inv_set:
        return {"invalid_devices": invalid_devices, "invalid_interfaces": invalid_interfaces}

    planned_devices = all_planned_device_hostnames_sorted(db_manager)

    # Mentioned devices: scope / resolved / explicit target lists (not heuristic deduction).
    candidates: List[str] = []
    scope = data.get("scope") or {}
    if isinstance(scope, dict):
        for h in scope.get("device_hostnames") or []:
            if h:
                candidates.append(str(h).strip())
    for h in data.get("resolved_devices") or []:
        if h:
            candidates.append(str(h).strip())

    params = data.get("parameters") or {}
    if isinstance(params, dict):
        td = params.get("target_devices")
        if isinstance(td, list):
            candidates.extend(str(x).strip() for x in td if x)
        elif td:
            candidates.append(str(td).strip())
        th = params.get("target_hosts")
        if isinstance(th, list):
            candidates.extend(str(x).strip() for x in th if x)
        elif th:
            candidates.append(str(th).strip())

    resolved_raw = [str(h).strip() for h in (data.get("resolved_devices") or []) if str(h).strip()]
    resolved_all_valid = bool(resolved_raw) and all(h.lower() in inv_set for h in resolved_raw)

    seen: Set[str] = set()
    for host in candidates:
        if not host or host.lower() in ("all", "none"):
            continue
        key = host.lower()
        if key in seen:
            continue
        seen.add(key)
        if key not in inv_set:
            # When ``resolved_devices`` are already valid planned hostnames (UI / API pick),
            # treat them as authoritative: ignore narrative or LLM placeholders (SW1, etc.)
            # in scope/parameters — do not emit ``invalid_inventory`` for those tokens.
            if resolved_all_valid:
                continue
            invalid_devices.append(
                {
                    "token": host,
                    "suggestions": list(planned_devices),
                    "multi_select": True,
                    "planned_network_context": True,
                }
            )

    # Resolved hosts for interface checks
    resolved = list(data.get("resolved_devices") or [])
    if not resolved:
        resolved = [inv_canon.get(h.lower(), h) for h in candidates if h.lower() in inv_set]
    if not resolved and isinstance(scope, dict):
        resolved = [
            inv_canon.get(str(h).lower(), str(h))
            for h in (scope.get("device_hostnames") or [])
            if str(h).strip().lower() in inv_set
        ]

    def _is_wildcard_shutdown_list(iflist: Any) -> bool:
        """Admin-down all / any port — skip per-interface inventory validation."""
        if not isinstance(iflist, list):
            return False
        for x in iflist:
            if isinstance(x, str) and x.strip().lower() in ("all", "*", "any", "every"):
                return True
            if isinstance(x, dict) and (
                x.get("all") is True
                or str(x.get("name") or "").strip().lower() in ("all", "*", "any")
            ):
                return True
        return False

    def check_iface_list(iflist: Any, field_name: str) -> None:
        if not isinstance(iflist, list):
            return
        if field_name == "interfaces_to_shutdown" and _is_wildcard_shutdown_list(iflist):
            return
        for item in iflist:
            if isinstance(item, str):
                # "Host:Eth0" or "GigabitEthernet0/1"
                host_part, iface_part = _split_interface_host_hint(item)
                target_hosts = [host_part] if host_part and host_part.lower() in inv_set else resolved
                if not target_hosts:
                    continue
                inorm = _normalize_iface_token(iface_part)
                for th in target_hosts:
                    names = _interface_names_for_host(db_manager, th)
                    if not names:
                        continue
                    norm_names = {_normalize_iface_token(n): n for n in names}
                    if inorm.lower() in {k.lower() for k in norm_names.keys()}:
                        continue
                    invalid_interfaces.append(
                        {
                            "hostname": th,
                            "value": iface_part,
                            "field": field_name,
                            "message": f"Interface '{iface_part}' not found on {th} in planned inventory.",
                            "suggestions": sorted(names, key=str.lower),
                            "multi_select": True,
                            "planned_network_context": True,
                        }
                    )
            elif isinstance(item, dict):
                name_raw = item.get("name") or item.get("interface") or item.get("interface_name")
                if not name_raw or not resolved:
                    continue
                host_part, iface_part = _split_interface_host_hint(name_raw)
                if host_part is None:
                    host_part = _host_hint_from_iface_dict(item)
                target_hosts = [host_part] if host_part and host_part.lower() in inv_set else resolved
                for th in target_hosts:
                    names = _interface_names_for_host(db_manager, th)
                    if not names:
                        continue
                    if any(_normalize_iface_token(str(iface_part)).lower() == _normalize_iface_token(n).lower() for n in names):
                        break
                    invalid_interfaces.append(
                        {
                            "hostname": th,
                            "value": str(iface_part),
                            "field": field_name,
                            "message": (
                                f"Interface '{iface_part}' was not found on '{th}' in the planned network context. "
                                "Select the correct interface from the list below."
                            ),
                            "suggestions": sorted(names, key=str.lower),
                            "multi_select": True,
                            "planned_network_context": True,
                        }
                    )
                    break

    if isinstance(params, dict):
        check_iface_list(params.get("interfaces"), "interfaces")
        check_iface_list(params.get("interfaces_to_shutdown"), "interfaces_to_shutdown")

    secondary_top = data.get("secondary_intents") or []
    if not secondary_top and isinstance(params, dict):
        secondary_top = params.get("_secondary_intents") or []
    for sec in secondary_top:
        if isinstance(sec, dict):
            sp = sec.get("parameters") or {}
            check_iface_list(sp.get("interfaces"), "interfaces")
            check_iface_list(sp.get("interfaces_to_shutdown"), "interfaces_to_shutdown")

    if isinstance(params, dict):
        for sec in params.get("_secondary_intents") or []:
            if isinstance(sec, dict):
                sp = sec.get("parameters") or {}
                check_iface_list(sp.get("interfaces"), "interfaces")
                check_iface_list(sp.get("interfaces_to_shutdown"), "interfaces_to_shutdown")

    # Dedupe interface errors
    seen_if: Set[Tuple[str, str, str]] = set()
    dedup: List[Dict[str, Any]] = []
    for row in invalid_interfaces:
        k = (str(row.get("hostname")), str(row.get("value")), str(row.get("field")))
        if k in seen_if:
            continue
        seen_if.add(k)
        dedup.append(row)

    return {"invalid_devices": invalid_devices, "invalid_interfaces": dedup}


def merge_cross_check_into_dict(data: Dict[str, Any], cross: Dict[str, Any]) -> Dict[str, Any]:
    """Attach cross-check summary for APIs (non-breaking for ParsedIntent consumers)."""
    out = dict(data)
    inv_dev = cross.get("invalid_devices") or []
    inv_if = cross.get("invalid_interfaces") or []
    p = dict(out.get("parameters") or {})
    if inv_dev or inv_if:
        p["_inventory_validation"] = {
            "invalid_devices": inv_dev,
            "invalid_interfaces": inv_if,
        }
    out["parameters"] = p
    return out
