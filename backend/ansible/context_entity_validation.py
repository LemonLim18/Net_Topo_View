"""
Cross-check user-supplied entity names (interfaces, etc.) against planned inventory
from build_scoped_planning_context. Used by preview-intent and plan-ansible.
"""

from __future__ import annotations

import difflib
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


def split_qualified_interface_token(raw: str) -> Tuple[Optional[str], str]:
    """
    Parse 'DeviceHint:InterfaceName' (e.g. SW1:Ethernet0/1) for per-device validation.

    If there is no device qualifier, returns (None, raw_stripped).
    Uses the first ':' only; interface names rarely contain ':' in this product.
    """
    s = str(raw).strip()
    if ":" not in s:
        return None, s
    left, right = s.split(":", 1)
    left, right = left.strip(), right.strip()
    if not left or not right:
        return None, s
    # Reject if "left" looks like a vendor iface prefix (very short heuristic)
    if left.isdigit():
        return None, s
    return left, right


def hosts_for_device_hint(
    hint: str,
    resolved_hosts: List[str],
    planning_context: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Map a user device token (SW1, access switch 1, Acc_Cisco_1) to one or more
    hostnames in resolved_hosts. Empty list means no safe mapping (caller
    should not fan out validation to every host).
    """
    h = str(hint or "").strip()
    if not h:
        return []
    h_cf = h.casefold().replace(" ", "_")
    rh = [str(x).strip() for x in resolved_hosts if str(x).strip()]
    if not rh:
        return []

    # Exact hostname match (case-insensitive)
    exact = [x for x in rh if x.casefold() == h.casefold()]
    if exact:
        return exact

    # Unique substring match (hint contained in exactly one hostname)
    subs = [x for x in rh if h_cf in x.casefold()]
    if len(subs) == 1:
        return subs

    # Prefix / suffix: hostname ends with hint (e.g. ..._1 vs hint "1" — too loose alone)
    ends = [x for x in rh if x.casefold().endswith(h_cf) or x.casefold().endswith("_" + h_cf)]
    if len(ends) == 1:
        return ends

    num: Optional[str] = None
    m = re.match(r"^sw(\d+)$", h_cf)
    if m:
        num = m.group(1)
    if num is None:
        m = re.match(r"^(?:switch|access_switch)_(\d+)$", h_cf)
        if m:
            num = m.group(1)
    if num is None:
        m = re.match(r"^access_switch_(\d+)$", h_cf)
        if m:
            num = m.group(1)

    if num is not None:
        access_hosts: List[str] = []
        if planning_context:
            for d in planning_context.get("devices") or []:
                hn = str(d.get("hostname") or "").strip()
                if hn not in rh:
                    continue
                role = str(d.get("role") or "").strip().lower()
                if "access" in role or role == "access":
                    if hn.casefold().endswith("_" + num) or re.search(rf"[_\-]{num}$", hn):
                        access_hosts.append(hn)
        if len(access_hosts) == 1:
            return access_hosts
        # Any target host whose name ends with _{num}
        by_suffix = [x for x in rh if re.search(rf"[_\-]{num}$", x) or x.endswith("_" + num)]
        if len(by_suffix) == 1:
            return by_suffix

    return []


def normalize_interface_name(s: str) -> str:
    """Conservative normalization: strip, casefold, collapse internal whitespace."""
    if s is None:
        return ""
    t = str(s).strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    return t.casefold()


def collect_user_interface_names_from_params(params: Optional[Dict[str, Any]]) -> List[str]:
    """
    Ordered unique list of interface strings from shutdown / configure / VLAN params.
    """
    if not isinstance(params, dict):
        return []
    out: List[str] = []
    seen: Set[str] = set()

    def add_one(x: Any) -> None:
        if x is None:
            return
        raw = str(x).strip()
        if not raw:
            return
        key = normalize_interface_name(raw)
        if key and key not in seen:
            seen.add(key)
            out.append(raw)

    def _strip_direction_suffix(s: str) -> str:
        # ACL/QoS sometimes provide strings like "Gi0/1 in" or "Gi0/1 out".
        return re.sub(r"\b(in|out)\s*$", "", s, flags=re.IGNORECASE).strip()

    def _looks_like_interface_name(candidate: str) -> bool:
        c = str(candidate).strip()
        if not c:
            return False
        # If it parses as an IP, don't treat as interface.
        try:
            ipaddress.ip_address(c)
            return False
        except Exception:
            pass
        # If qualified "DevHint:Interface", validate right side.
        if ":" in c:
            _, right = c.split(":", 1)
            c = right.strip()
        # Conservative heuristics for interface-ish tokens.
        if "/" in c:
            return True
        if re.match(
            r"(?i)^(?:gi|gigabit|eth|ethernet|fa|fastethernet|te|tengigabitethernet|po|port-channel|vlan|vl|vlanif|fxp|ge-|xe-|ge|xe|et-|et|irb|ae\d+|lo|loopback|mgmt|management)",
            c,
        ):
            return True
        if re.match(r"(?i)^\d+/\d+(?:/\d+)?$", c):
            return True
        return False

    def _extract_interface_tokens_from_text(raw_value: Any) -> List[str]:
        if raw_value is None:
            return []
        if not isinstance(raw_value, str):
            # For list-of-interface tokens, callers usually pass list[str] or array-like;
            # if it's not a string, just treat it as a scalar.
            raw_value = str(raw_value)
        s = str(raw_value)
        # Split by common separators; direction keywords stay inside each part.
        parts = re.split(r"[;,]\s*", s)
        tokens: List[str] = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            p = _strip_direction_suffix(p)
            if not p:
                continue
            if _looks_like_interface_name(p):
                tokens.append(p)
        return tokens

    shutdown = params.get("interfaces_to_shutdown")
    if shutdown is not None:
        if isinstance(shutdown, list):
            for x in shutdown:
                add_one(x)
        else:
            add_one(shutdown)

    raw_if = params.get("interfaces")
    if raw_if is None:
        # Still allow interface-like tokens in other keys for ACL/QoS/OSPF/static_route.
        raw_if = None
    if isinstance(raw_if, list):
        for x in raw_if:
            if isinstance(x, str):
                add_one(x)
            elif isinstance(x, dict):
                add_one(x.get("name") or x.get("interface"))
    else:
        add_one(raw_if)

    # ACL: apply_to can contain interface-like strings (optional directions).
    for k in ("apply_to", "interface_apply", "passive_interfaces"):
        if k in params:
            v = params.get(k)
            if isinstance(v, list):
                for item in v:
                    for tok in _extract_interface_tokens_from_text(item):
                        add_one(tok)
            else:
                for tok in _extract_interface_tokens_from_text(v):
                    add_one(tok)

    # static_route: next_hop may be an interface name (not just an IP).
    if "next_hop" in params:
        nh = params.get("next_hop")
        for tok in _extract_interface_tokens_from_text(nh):
            add_one(tok)
    return out


def interface_names_for_host(planning_context: Optional[Dict[str, Any]], hostname: str) -> List[str]:
    """Canonical interface names for a device (same fields as inventory / disambiguation)."""
    if not planning_context or not hostname:
        return []
    host = str(hostname).strip()
    for d in planning_context.get("devices") or []:
        if str(d.get("hostname") or "").strip() != host:
            continue
        names: List[str] = []
        for iface in d.get("interfaces") or []:
            n = str(iface.get("name") or iface.get("interface") or "").strip()
            if n:
                names.append(n)
        return names
    return []


def _normalized_map(canonical_names: List[str]) -> Dict[str, str]:
    """Map normalized form -> first canonical spelling from inventory."""
    m: Dict[str, str] = {}
    for c in canonical_names:
        k = normalize_interface_name(c)
        if k and k not in m:
            m[k] = c
    return m


def _suggestions_for(user_name: str, canonical_names: List[str], n: int = 3) -> List[str]:
    if not canonical_names:
        return []
    return difflib.get_close_matches(user_name, canonical_names, n=n, cutoff=0.35)


@dataclass
class InterfaceInventoryValidationResult:
    ok: bool
    invalid_entities: List[Dict[str, Any]] = field(default_factory=list)
    validation_notice: str = ""

    @property
    def params_need_context_fix(self) -> bool:
        return not self.ok


def _parse_interface_ip_networks_for_host(
    planning_context: Optional[Dict[str, Any]],
    hostname: str,
) -> List[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Extract interface L3 networks from planning context for one host."""
    if not planning_context or not hostname:
        return []
    host = str(hostname).strip()
    out: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for d in planning_context.get("devices") or []:
        if str(d.get("hostname") or "").strip() != host:
            continue
        for iface in d.get("interfaces") or []:
            # Common shapes in this codebase/inventory snapshots:
            # - {"ip_address": "10.0.0.1", "subnet_mask": "255.255.255.0"}
            # - {"ip": {"address": "10.0.0.1", "mask": "24"}}
            # - {"ip": "10.0.0.1/24"}
            addr = str(iface.get("ip_address") or "").strip()
            mask = str(iface.get("subnet_mask") or "").strip()
            ip_obj = iface.get("ip")
            if isinstance(ip_obj, dict):
                addr = addr or str(ip_obj.get("address") or "").strip()
                mask = mask or str(ip_obj.get("mask") or "").strip()
            elif isinstance(ip_obj, str) and not addr:
                # e.g. "10.0.0.1/24"
                addr = ip_obj.strip()

            if not addr:
                continue
            try:
                if "/" in addr:
                    iface_net = ipaddress.ip_interface(addr).network
                elif mask:
                    # Support dotted mask and prefix-length forms.
                    if mask.startswith("/"):
                        mask = mask[1:]
                    iface_net = ipaddress.ip_interface(f"{addr}/{mask}").network
                else:
                    # Cannot infer subnet without prefix/mask.
                    continue
                out.append(iface_net)
            except Exception:
                continue
        break
    return out


def validate_static_route_next_hop_ip_against_context(
    params: Optional[Dict[str, Any]],
    resolved_hosts: List[str],
    planning_context: Optional[Dict[str, Any]],
) -> InterfaceInventoryValidationResult:
    """
    Validate static-route next_hop when provided as IP:
    - If next_hop is an IP, it should belong to at least one interface subnet
      on each resolved host that has interface L3 data.
    - If next_hop is interface-like or cannot be parsed as IP, this validator is a no-op.
    """
    if not isinstance(params, dict) or not resolved_hosts or not planning_context:
        return InterfaceInventoryValidationResult(ok=True)
    nh_raw = str(params.get("next_hop") or "").strip()
    if not nh_raw:
        return InterfaceInventoryValidationResult(ok=True)

    try:
        nh_ip = ipaddress.ip_address(nh_raw)
    except Exception:
        # Interface-like next_hop is handled by interface-name validation path.
        return InterfaceInventoryValidationResult(ok=True)

    invalid: List[Dict[str, Any]] = []
    checked_hosts = 0
    for host in resolved_hosts:
        nets = _parse_interface_ip_networks_for_host(planning_context, host)
        if not nets:
            continue
        checked_hosts += 1
        if any(nh_ip in n for n in nets):
            continue
        suggestions: List[str] = []
        for n in nets[:3]:
            try:
                suggestions.append(str(n))
            except Exception:
                pass
        invalid.append(
            {
                "hostname": host,
                "value": nh_raw,
                "kind": "next_hop",
                "message": (
                    f"Next-hop IP **`{nh_raw}`** is not in any known interface subnet on **{host}** "
                    "from the planned network context."
                ),
                "suggestions": suggestions,
            }
        )

    if checked_hosts == 0 or not invalid:
        return InterfaceInventoryValidationResult(ok=True)
    return InterfaceInventoryValidationResult(
        ok=False,
        invalid_entities=invalid,
        validation_notice="\n\n".join(e.get("message") or "" for e in invalid if isinstance(e, dict)),
    )


def validate_interface_params_against_context(
    params: Optional[Dict[str, Any]],
    resolved_hosts: List[str],
    planning_context: Optional[Dict[str, Any]],
) -> InterfaceInventoryValidationResult:
    """
    Validates interface names against per-device inventory.

    - Unqualified names (e.g. Ethernet0/1) must exist on every resolved target host
      that has interface inventory.
    - Qualified names (e.g. SW1:Ethernet0/1) are checked only on host(s) that match
      the device hint, so we do not spam every device with false errors.
    """
    names = collect_user_interface_names_from_params(params)
    if not names or not resolved_hosts:
        return InterfaceInventoryValidationResult(ok=True)

    if not planning_context:
        return InterfaceInventoryValidationResult(ok=True)

    invalid: List[Dict[str, Any]] = []

    # Build (interface_to_match, hosts_to_check, original_token_for_ui)
    checks: List[Tuple[str, List[str], str]] = []
    for user_name in names:
        dev_hint, iface_only = split_qualified_interface_token(user_name)
        if dev_hint is None:
            checks.append((iface_only, list(resolved_hosts), user_name))
            continue
        mapped = hosts_for_device_hint(dev_hint, resolved_hosts, planning_context)
        if not mapped:
            invalid.append(
                {
                    "hostname": "",
                    "value": user_name,
                    "kind": "device_hint",
                    "message": (
                        f"Device hint **`{dev_hint}`** from `{user_name}` does not match any of the "
                        f"selected targets ({', '.join(resolved_hosts)}). "
                        "Narrow or correct target devices, then pick interfaces from inventory."
                    ),
                    "suggestions": [],
                }
            )
            continue
        checks.append((iface_only, mapped, user_name))

    seen_failures: Set[Tuple[str, str, str]] = set()
    for iface_only, hosts_to_check, original_token in checks:
        nk = normalize_interface_name(iface_only)
        if not nk:
            continue
        for host in hosts_to_check:
            canonical = interface_names_for_host(planning_context, host)
            if not canonical:
                continue
            norm_map = _normalized_map(canonical)
            if nk in norm_map:
                continue
            key = (host, original_token, nk)
            if key in seen_failures:
                continue
            seen_failures.add(key)
            sug = _suggestions_for(iface_only, canonical)
            hint_txt = ""
            if sug:
                hint_txt = f" Closest in inventory: {', '.join(sug)}."
            # Show canonical interface name only (avoid confusing SW1:Ethernet0/1 as “the interface”)
            msg = (
                f"Interface **`{iface_only}`** is not present on **{host}** in the planned "
                f"network inventory. Please select a valid interface from the list below.{hint_txt}"
            )
            invalid.append(
                {
                    "hostname": host,
                    "value": original_token,
                    "kind": "interface",
                    "message": msg,
                    "suggestions": sug,
                    "interface_for_match": iface_only,
                }
            )

    if not invalid:
        return InterfaceInventoryValidationResult(ok=True)

    lines = [e["message"] for e in invalid]
    notice = "\n\n".join(lines)
    return InterfaceInventoryValidationResult(
        ok=False,
        invalid_entities=invalid,
        validation_notice=notice,
    )


def intent_requests_all_interfaces(text: Optional[str]) -> bool:
    """
    True when natural language clearly asks to affect every interface / port
    (e.g. admin-down all interfaces), so we should expand from inventory instead
    of validating LLM-guessed ranges.
    """
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not t:
        return False
    phrases = (
        "all interfaces",
        "all interface",
        "all ports",
        "all port",
        "shutdown all interfaces",
        "shut down all interfaces",
        "admin-down all",
        "admin down all",
        "shutdown all",
        "shut down all",
        "disable all interfaces",
        "turn off all interfaces",
        "administratively down all",
        "admin down all interfaces",
        "bring down all interfaces",
    )
    return any(p in t for p in phrases)


def intersection_interfaces_from_disambiguation_devices(
    devices: List[Dict[str, Any]],
) -> List[str]:
    """
    Given the `devices` list from interfaces_disambiguation (per-host interface names),
    return sorted intersection of interface names common to every device (non-empty
    per device required for a non-empty result).
    """
    if not devices:
        return []
    sets: List[Set[str]] = []
    for d in devices:
        names = {
            str(i.get("name") or "").strip()
            for i in (d.get("interfaces") or [])
            if str(i.get("name") or "").strip()
        }
        sets.append(names)
    if not sets or any(len(s) == 0 for s in sets):
        return []
    inter = set.intersection(*sets)
    return sorted(inter)


def should_validate_interface_names_against_inventory(
    schema_action: str,
    feature_lower: str,
    params: Optional[Dict[str, Any]],
) -> bool:
    """True when we have interface strings in params and intent is interface or VLAN."""
    if not collect_user_interface_names_from_params(params):
        return False
    sa = str(schema_action or "").strip().lower().replace("-", "_")
    fl = str(feature_lower or "").strip().lower()
    if sa in ("shutdown_interface", "configure_interface"):
        return True
    if sa == "create_vlan" or fl == "vlan":
        return True
    # Non-interface categories that embed interface-like strings.
    if sa in ("configure_acl", "configure_qos", "configure_ospf", "configure_static_route"):
        return True
    return False


_MGMT_IFACE_NAME_RE = re.compile(r"(?i).*(management|mgmt|meth|vty|console).*")


def is_operational_interface_record(iface: Dict[str, Any], device_management_ip: str) -> bool:
    """
    Mirrors playbook Jinja filters for nettopo expansion: drop mgmt-like names and
    interfaces whose ip_address matches device management IP.
    """
    name = str(iface.get("name") or iface.get("interface") or "").strip()
    if not name:
        return False
    if _MGMT_IFACE_NAME_RE.match(name):
        return False
    ip_addr = str(iface.get("ip_address") or "").strip()
    if not ip_addr and isinstance(iface.get("ip"), dict):
        ip_addr = str(iface.get("ip", {}).get("address") or "").strip()
    mgmt = str(device_management_ip or "").strip()
    if mgmt and ip_addr and ip_addr == mgmt:
        return False
    return True


def operational_interface_names_for_planned_device(device: Dict[str, Any]) -> List[str]:
    """Sorted interface names on one planned device, excluding management heuristics."""
    mgmt_ip = str(device.get("management_ip") or "").strip()
    names: List[str] = []
    for iface in device.get("interfaces") or []:
        if not isinstance(iface, dict):
            continue
        n = str(iface.get("name") or iface.get("interface") or "").strip()
        if not n or not is_operational_interface_record(iface, mgmt_ip):
            continue
        names.append(n)
    return sorted(names)


def build_planned_all_interfaces_preview(
    planning_context: Optional[Dict[str, Any]],
    hostnames: List[str],
) -> Dict[str, Any]:
    """
    Structured preview of which interfaces 'all interfaces' would touch per host,
    using the same mgmt-exclusion heuristics as reference templates.
    """
    if not planning_context or not hostnames:
        return {"devices": [], "note": "No planning context or targets to preview."}
    want = {str(h).strip() for h in hostnames if str(h).strip()}
    devices_out: List[Dict[str, Any]] = []
    for d in planning_context.get("devices") or []:
        hn = str(d.get("hostname") or "").strip()
        if hn not in want:
            continue
        ifaces = operational_interface_names_for_planned_device(d if isinstance(d, dict) else {})
        devices_out.append(
            {
                "hostname": hn,
                "interface_count": len(ifaces),
                "interfaces": [{"name": n} for n in ifaces],
            }
        )
    return {
        "devices": devices_out,
        "note": "Lists exclude management-style interface names and interfaces whose IP matches the device management address (same rules as template expansion).",
    }
