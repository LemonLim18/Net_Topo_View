"""
Stage 1 — Structured Intent Parser.

Converts free-form human language into a validated, machine-readable
intent object (JSON).  This is the first layer in the translation
pipeline:

    Human Language → **Structured Intent** → Policy Engine → Automation
                    ^^^^^^^^^^^^^^^^^^^^^^^^^
                    (this module)

The LLM is used ONLY for understanding — it never touches devices.
It outputs a strict Pydantic model.  Temperature = 0.

Key principle: the LLM interprets, the system enforces.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger("llm.intent")

# Fallback if intent_keywords.yml is missing or empty (keep in sync with intent_keywords.yml).
_ROLE_ALIASES_FALLBACK = {
    "access": "access",
    "access_switch": "access",
    "access-switch": "access",
    "switch": "access",
    "switches": "access",
    "distribution": "distribution",
    "distribution_switch": "distribution",
    "distribution-switch": "distribution",
    "dist": "distribution",
    "core": "core",
    "core_router": "core",
    "core-router": "core",
    "router": "core",
    "routers": "core",
    "firewall": "firewall",
    "network-device": "network-device",
}

_VENDOR_ALIASES_FALLBACK = {
    "cisco": "cisco_ios",
    "cisco-ios": "cisco_ios",
    "cisco_ios": "cisco_ios",
    "cisco-xe": "cisco_ios",
    "cisco_xe": "cisco_ios",
    "juniper": "juniper_junos",
    "juniper-junos": "juniper_junos",
    "juniper_junos": "juniper_junos",
    "junos": "juniper_junos",
    "huawei": "huawei_vrp",
    "huawei-vrp": "huawei_vrp",
    "huawei_vrp": "huawei_vrp",
    "vrp": "huawei_vrp",
}


def _role_aliases() -> Dict[str, str]:
    try:
        from ansible.intent_artifacts import get_role_aliases

        m = get_role_aliases()
        if m:
            return m
    except Exception:
        pass
    return _ROLE_ALIASES_FALLBACK


def _vendor_aliases() -> Dict[str, str]:
    try:
        from ansible.intent_artifacts import get_vendor_aliases

        m = get_vendor_aliases()
        if m:
            return m
    except Exception:
        pass
    return _VENDOR_ALIASES_FALLBACK


def is_generic_vlan_on_devices_message(msg: str) -> bool:
    """Prompts like 'Create VLAN on devices' — never infer targets; ask for everything."""
    lower = (msg or "").lower().strip()
    if re.search(r"\bcreate\s+vlan\b", lower) and re.search(r"\bon\s+devices?\b", lower):
        return True
    return False


def parse_vlan_database_params_from_text(msg: str) -> Dict[str, Any]:
    """
    Extract VLAN id (+ name when present) from imperative create-VLAN text.

    Used by the rule-based create_vlan fast path so catalog validation sees ``vlans``
    when the user already stated id/name in the prompt.
    """
    raw = (msg or "").strip()
    if not raw:
        return {}
    lower = raw.lower()
    if is_generic_vlan_on_devices_message(msg):
        return {}
    m = re.search(r"\bcreate\s+vlan\s+(\d{1,4})\b", lower, re.IGNORECASE)
    if not m:
        m = re.search(r"\badd\s+vlan\s+(\d{1,4})\b", lower, re.IGNORECASE)
    if not m:
        return {}
    try:
        vid = int(m.group(1))
    except ValueError:
        return {}
    if vid < 1 or vid > 4094:
        return {}
    name = f"VLAN {vid}"
    nm = re.search(
        r"(?:named|called)\s+(.+?)(?=\s+on\b|\s+for\b|\s+and\s+set\b|\s+and\s+apply\b|\s*$)",
        raw,
        re.IGNORECASE | re.DOTALL,
    )
    if nm:
        chunk = nm.group(1).strip()
        chunk = re.sub(r"\*+", "", chunk).strip()
        chunk = chunk.strip('"').strip("'").strip()
        if chunk:
            name = chunk
    return {"vlans": [{"id": vid, "name": name}]}


_INTERFACE_NAME_RE = re.compile(
    r"(?i)(?:[A-Za-z0-9_.-]+:)?(?:"
    r"(?:TenGigabitEthernet|GigabitEthernet|FastEthernet|Ethernet|Vlan|Loopback)\d+(?:/\d+)*"
    r"|ge-\d+/\d+/\d+"
    r"|xe-\d+/\d+/\d+"
    r"|et-\d+/\d+/\d+"
    r"|irb\.\d+"
    r"|vlan\.\d+"
    r")"
)


def _vlan_interface_binding_mode_from_text(msg: str) -> Optional[str]:
    lower = re.sub(r"\s+", " ", (msg or "").lower())
    trunk_tokens = (
        "trunk port",
        "trunk ports",
        "as trunk",
        "allowed vlan",
        "allowed vlans",
        "allow-pass vlan",
        "tagged vlan",
    )
    access_tokens = (
        "access port",
        "access ports",
        "as access",
        "access vlan",
        "untagged vlan",
        "default vlan",
    )
    if any(token in lower for token in trunk_tokens):
        return "trunk"
    if any(token in lower for token in access_tokens):
        return "access"
    return None


def _extract_interface_names_from_text(msg: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for match in _INTERFACE_NAME_RE.findall(msg or ""):
        name = str(match or "").strip().rstrip(".,;")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _inventory_switch_index(hostname: str) -> Optional[int]:
    """Best numeric suffix on inventory-style hostnames (e.g. Acc_Cisco_3 → 3)."""
    h = (hostname or "").strip()
    m = re.search(r"_(\d+)$", h)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", h)
    if m:
        return int(m.group(1))
    return None


def extract_layer_switch_role_and_numbers(msg: str) -> Optional[Tuple[str, List[int]]]:
    """
    Detect phrases like "access switch 1, 2, and 3" / "distribution switch 1, 2" / "core switch 1".

    Returns (normalized_role, [indices]) or None. Does not resolve to hostnames — callers
    map to inventory for UI candidate lists only.
    """
    lower = re.sub(r"\s+", " ", (msg or "").lower())
    role: Optional[str] = None
    if re.search(r"\baccess\s+switch(?:es)?\b", lower):
        role = "access"
    elif re.search(r"\b(?:distribution|dist)\s+switch(?:es)?\b", lower):
        role = "distribution"
    elif re.search(r"\bcore\s+switch(?:es)?\b", lower):
        role = "core"
    if not role:
        return None
    m = re.search(
        r"\b(?:access|distribution|dist|core)\s+switch(?:es)?\s+(.+)$",
        lower,
        re.IGNORECASE,
    )
    if not m:
        return None
    tail = m.group(1)
    nums = [int(x) for x in re.findall(r"\b(\d+)\b", tail)]
    if not nums:
        return None
    seen: set[int] = set()
    ordered: List[int] = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return role, ordered


def enumerate_layer_switch_device_candidates(msg: str, inventory_records: List[Dict[str, Any]]) -> List[str]:
    """
    Map layer+number wording to hostnames that share role + trailing index — **candidates only**.

    Never treated as confirmed ``resolved_devices``; UI must multi-select against planned inventory.
    """
    hit = extract_layer_switch_role_and_numbers(msg)
    if not hit:
        return []
    want_role, want_nums = hit
    want_set = set(want_nums)
    out: List[str] = []
    seen_h: set[str] = set()
    for record in inventory_records or []:
        host = str(record.get("hostname") or "").strip()
        if not host or host in seen_h:
            continue
        rec_role = _normalize_role(record.get("role"))
        if not rec_role:
            rec_role = _role_from_hostname(host)
        if rec_role != want_role:
            continue
        idx = _inventory_switch_index(host)
        if idx is None or idx not in want_set:
            continue
        seen_h.add(host)
        out.append(host)
    return sorted(out, key=_natural_key)


def _create_vlan_fast_path_match(msg: str) -> bool:
    if is_generic_vlan_on_devices_message(msg):
        return False
    try:
        from ansible.intent_artifacts import message_matches_create_vlan_imperative_fast_path

        return message_matches_create_vlan_imperative_fast_path(msg)
    except Exception:
        return False


_ALIAS_FILE_PATH = Path(__file__).resolve().parent.parent / "ansible" / "intent_catalog" / "device_alias_hints.yaml"
_DEVICE_ALIAS_MAP: Optional[Dict[str, List[str]]] = None


def _load_device_alias_map() -> Dict[str, List[str]]:
    global _DEVICE_ALIAS_MAP
    if _DEVICE_ALIAS_MAP is not None:
        return _DEVICE_ALIAS_MAP
    out: Dict[str, List[str]] = {}
    try:
        if _ALIAS_FILE_PATH.is_file():
            import yaml

            raw = yaml.safe_load(_ALIAS_FILE_PATH.read_text(encoding="utf-8")) or {}
            aliases = (raw.get("aliases") or {}) if isinstance(raw, dict) else {}
            for k, v in aliases.items():
                key = str(k).strip().upper()
                if isinstance(v, list):
                    out[key] = [str(x).strip() for x in v if str(x).strip()]
                elif v:
                    out[key] = [str(v).strip()]
    except Exception as exc:
        logger.debug("device_alias_hints load failed: %s", exc)
    _DEVICE_ALIAS_MAP = out
    return out


def _extract_explicit_device_tokens(raw: str) -> List[str]:
    """Short tokens (SW1, R1) or inventory-style hostnames; no fuzzy inventory sweep."""
    text = raw or ""
    found: List[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b([Ss][Ww]|[Rr])(\d+)\b", text):
        prefix = "SW" if m.group(1).lower().startswith("s") else "R"
        tok = f"{prefix}{m.group(2)}"
        key = tok.upper()
        if key not in seen:
            seen.add(key)
            found.append(tok)
    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z][A-Za-z0-9]*)+_\d+)\b", text):
        tok = m.group(1)
        if tok.upper() not in seen:
            seen.add(tok.upper())
            found.append(tok)
    return found


def _resolve_device_tokens_to_inventory(
    tokens: List[str],
    inventory_by_hostname: Dict[str, Any],
    all_hosts: List[str],
) -> List[str]:
    if not tokens:
        return []
    aliases = _load_device_alias_map()
    pool = list(dict.fromkeys(list(inventory_by_hostname.keys()) + list(all_hosts)))
    pool_l = {str(h).lower(): h for h in pool if h}
    resolved: List[str] = []
    seen: set[str] = set()
    for raw_tok in tokens:
        t = str(raw_tok).strip()
        if not t:
            continue
        prefers: List[str] = []
        for akey, alist in aliases.items():
            if akey.lower() == t.upper():
                prefers.extend(alist)
        hosts_try = prefers if prefers else [t]
        placed = False
        for cand in hosts_try:
            cl = cand.lower()
            if cl in pool_l:
                h = pool_l[cl]
                if h not in seen:
                    seen.add(h)
                    resolved.append(h)
                placed = True
                break
        if placed:
            continue
    return resolved


def _natural_key(value: str) -> list[Any]:
    """Sort hostnames like Acc_1, Acc_2, Acc_10 in human order."""
    parts = re.split(r"(\d+)", str(value or ""))
    out: list[Any] = []
    for part in parts:
        if part.isdigit():
            out.append(int(part))
        else:
            out.append(part.lower())
    return out


# -----------------------------------------------------------------------
# Intent Types
# -----------------------------------------------------------------------

class IntentType(str, Enum):
    """The kind of action the user wants."""
    CONFIGURE   = "configure"      # create/modify/delete config
    TROUBLESHOOT = "troubleshoot"  # diagnose an issue
    QUERY       = "query"          # read-only information request
    AUDIT       = "audit"          # security or compliance check
    ROLLBACK    = "rollback"       # undo a previous change
    DEPLOY      = "deploy"         # execute a pending change


class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    CRITICAL = "critical"


# -----------------------------------------------------------------------
# Parsed Intent (the structured output)
# -----------------------------------------------------------------------

class IntentScope(BaseModel):
    """Which devices / locations are targeted."""
    device_hostnames: List[str] = Field(default_factory=list, description="Explicit device hostnames")
    device_role: Optional[str] = Field(None, description="e.g. access_switch, core_router, distribution")
    building: Optional[str] = Field(None, description="Physical location filter")
    vendor_filter: Optional[str] = Field(None, description="e.g. cisco_ios, huawei_vrp")
    all_devices: bool = Field(False, description="Apply to every discovered device")


class ParsedIntent(BaseModel):
    """
    The canonical structured intent object produced by the parser.

    This replaces raw human language for all downstream stages.
    """
    id: str = ""
    intent_type: IntentType = IntentType.QUERY
    action: str = ""                              # e.g. "create_vlan", "configure_ospf"
    feature: str = ""                             # template feature key: vlan, ospf, bgp, ...
    vendor: str = ""                              # cisco_ios | huawei_vrp | juniper_junos
    parameters: Dict[str, Any] = Field(default_factory=dict)
    scope: IntentScope = Field(default_factory=IntentScope)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    justification: str = ""                       # why this change is needed
    raw_user_input: str = ""
    requires_disambiguation: bool = False
    disambiguation_message: str = ""
    requires_resolution: bool = False  # e.g. interfaces not specified → controller must resolve or ask
    resolved_devices: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_config_change(self) -> bool:
        return self.intent_type in (IntentType.CONFIGURE, IntentType.DEPLOY)


def _intent_v2_parse_failure_data(user_message: str, exc: BaseException) -> Dict[str, Any]:
    """Structured intent when the refactored parser/LLM path fails (no legacy fallback)."""
    msg = str(exc).strip() or type(exc).__name__
    return {
        "intent_type": "query",
        "action": "unknown",
        "feature": "",
        "vendor": "",
        "parameters": {
            "_intent_parse_error": True,
            "_parse_error_detail": msg[:2000],
        },
        "scope": {"device_hostnames": [], "all_devices": False},
        "risk_level": "low",
        "justification": f"Intent parser (v2) could not complete: {msg[:500]}",
        "requires_disambiguation": True,
        "disambiguation_message": (
            "The refactored intent parser failed. Check your LLM (API key, or Ollama base URL/model). "
            "Optional one-off fallback: set environment variable INTENT_USE_LEGACY_PARSER=1. "
            f"Detail: {msg[:400]}"
        ),
    }


# -----------------------------------------------------------------------
# Prompt for constrained LLM parsing
# -----------------------------------------------------------------------

_PARSE_PROMPT = """\
You are an **intent parser** for a network automation controller.
Your job is to convert the user's natural-language request into a \
strict JSON object.  Output ONLY valid JSON — no commentary, no markdown fences.

## JSON Schema
{{
  "intent_type": "configure | troubleshoot | query | audit | rollback | deploy",
  "action": "<verb>_<noun>  e.g. create_vlan, configure_ospf, show_topology",
  "feature": "<template key>  one of: vlan, ospf, bgp, interface, acl, static_route, ntp, logging, banner, ssh_harden, qos, firewall  (or empty for non-config intents)",
  "vendor": "cisco_ios | huawei_vrp | juniper_junos  (or empty if not specified)",
  "parameters": {{ ... template parameters matching the feature schema ... }},
  "scope": {{
    "device_hostnames": ["R1", "SW1"],
    "device_role": "access_switch | core_router | distribution | null",
    "building": "A | B | null",
    "vendor_filter": "cisco_ios | null",
    "all_devices": false
  }},
  "risk_level": "low | medium | high | critical",
  "justification": "<one-line summary of why this change is needed>",
  "requires_resolution": false,
  "secondary_intents": []
}}

Each element of "secondary_intents" (if any): {{ "action": "e.g. configure_ospf", "feature": "e.g. ospf", "parameters": {{}} }}. Use for a second config task in the same request (e.g. "create VLAN 10 and enable OSPF").

## Available Templates & Their Required Parameters
{template_schemas}

## Operation-specific intent reference (canonical — choose action + feature from here)
{operation_reference}

## Compact required/optional per operation (from intent_schemas; do not guess values)
{required_params_hint}

## Intent graph — categories and operations (operations tie to YAML schemas above)
{intent_graph_summary}

## Category parameter vocabulary (field names; which are required depends on chosen action)
{category_vocabulary}

## Keyword / classifier hints (editable in intent_keywords.yml)
{intent_keywords_digest}

## Current Network Topology
{topology_context}

## Rules
1. If the user mentions a specific device (e.g. "R1", "SW3"), put it in scope.device_hostnames.
2. If the user says "switches" by itself, set all_devices=false and require disambiguation (ask which layer: access/core/distribution, or explicit hostnames). If they say "all switches" or "across building A", use device_role / building / all_devices.
3. **Scope by layer**: If the user says "access switches", "all access", or "access layer only", set scope.device_role to "access" and leave all_devices false. If they say "distribution" or "core" only, set device_role to "distribution" or "core" respectively. Use all_devices true only when they clearly mean the entire network (e.g. "all devices", "every switch in the network").
4. **Subset by number**: If the user specifies a subset by number, combine the role with the number to choose only those devices from that role group. Example: "access switch 3" means the 3rd access device in the role list, not all access switches. "access switch 1, 2, and 3" means device_hostnames = ["<first access device>", "<second access device>", "<third access device>"]. Do NOT set only device_role — that would target all devices of that role.
5. **Generic switch words**: If the user says just "switches" without a role, do NOT set all_devices=true; require disambiguation instead.
6. **VLAN database vs VLAN on ports**: If the user only asks to **create** / **add** a VLAN (ID and name) on devices/switches and does **not** mention specific ports, access/trunk, allowed VLANs, or interface names (Gi/Ethernet/ge-), **action must be `create_vlan`** and **feature must be `vlan`**. Use **`parameters.vlans`**: `[{{"id": <vlan_id>, "name": "<vlan_name>"}}]` (not separate `vlan_id`/`vlan_name` keys). **Never** invent `parameters.interfaces` on the primary.
7. **VLAN on interfaces (ports only)**: If the user assigns VLANs to ports without a separate VLAN-DB step, use **`assign_vlan_to_access_port`**, **`assign_vlan_to_trunk_port`**, or **`set_native_vlan`** with **`feature`: `interface`** and **`parameters.interfaces`** as appropriate (access vs trunk vs native).
7b. **Composite — create VLAN AND configure ports** (multi-intent): Primary **`create_vlan`** / **`vlan`** with **`parameters.vlans`** and targets in `scope.device_hostnames` or `parameters.target_devices` when known. **`secondary_intents`**: one object with **`action`** **`assign_vlan_to_access_port`** (access), **`assign_vlan_to_trunk_port`** (allowed VLANs on trunk), or **`set_native_vlan`** (native/PVID), **`feature`**: **`interface`**, **`parameters.interfaces`** with `name`, `mode`, `access_vlan` / `trunk_vlans` / `native_vlan`. Do **not** use vague `configure_interface` when the operation is clearly one of the above.
   - **Trunk allowed VLANs**: **`assign_vlan_to_trunk_port`**, **`mode`: `trunk`**, **`trunk_vlans`** includes the new VLAN id.
   - **Native VLAN**: **`set_native_vlan`** or trunk interface with **`native_vlan`** and **`mode`: `trunk`** — **never** `mode`: `access` for native VLAN phrases.
8. For CONFIGURE intents, you MUST fill in "feature" and "parameters" matching the operation reference above.
9. **Interface actions**: Use action **shutdown_interface** for shutdown / admin-down / "turn off interfaces". Use action **configure_interface** for L2/L3 interface changes (access/trunk/routed, VLANs, IPs) when not a simple shutdown. For shutdown without listed interfaces, set parameters.interfaces_to_shutdown to null or omit, and set "requires_resolution": true. For configure_interface without listed interfaces, omit parameters.interfaces or set null and set "requires_resolution": true.
10. If you cannot determine the target device, set intent_type="query", action="clarify", \
   and put a clarifying question in justification.
11. risk_level heuristic: bgp/acl/firewall/nat = high; ospf/vlan/stp = medium; ntp/logging/banner = low.
12. For read-only requests (show topology, list devices, check status), use intent_type="query".
13. For security scan requests, use intent_type="audit".
14. **Multi-intent**: If the user request combines multiple configuration tasks, set the primary "action" and "feature" to the **first** task (see rule 7b for VLAN+interface), and put the other task(s) in "secondary_intents" as objects with "action", "feature", and "parameters". Examples: (a) "Create VLAN 10 and enable OSPF on access switch 1" → primary `create_vlan`, secondary OSPF; (b) "Create VLAN 100 named HR and set Gi1/0/1 to access" → primary `create_vlan`, secondary `configure_interface`. Use empty array if only one task.

## User Request
{user_message}

Output the JSON object now:
"""


# -----------------------------------------------------------------------
# Intent Parser
# -----------------------------------------------------------------------

class IntentParser:
    """
    Parses free-form chat messages into structured ``ParsedIntent`` objects.

    Uses Gemini (temperature=0) with a strict output-schema prompt.
    Falls back to a simple rule-based classifier for non-config intents.
    """

    def __init__(self, provider=None, db_manager=None):
        self._provider = self._intent_provider_with_fast_timeout(provider)
        self._db = db_manager

    def _intent_provider_with_fast_timeout(self, provider):
        """Use a short Ollama timeout for intent parsing without changing chat runtime."""
        if provider is None:
            return None
        provider_name = provider.__class__.__name__.lower()
        if "ollama" not in provider_name:
            return provider
        try:
            timeout_sec = int(float(os.getenv("INTENT_PARSE_OLLAMA_TIMEOUT_SEC", "3")))
        except ValueError:
            timeout_sec = 3
        timeout_sec = max(1, timeout_sec)
        try:
            from llm.ollama_provider import OllamaProvider

            return OllamaProvider(
                base_url=getattr(provider, "_base_url", None),
                model=getattr(provider, "_model", None),
                timeout=timeout_sec,
            )
        except Exception:
            return provider

    # ----- public -----

    def parse(self, user_message: str, topology_context: str = "") -> str:
        """
        Parse a user message into a structured intent (returns JSON string).

        This is designed to be called as an LLM *tool* — so the return
        type is ``str`` (JSON-encoded ``ParsedIntent``).
        """
        import uuid

        # Fast-path: simple queries that don't need LLM
        quick = self._try_rule_based(user_message)
        if quick:
            quick.id = str(uuid.uuid4())[:8]
            quick.raw_user_input = user_message
            return json.dumps(quick.model_dump(), default=str)

        # Fast-path: imperative VLAN DB creation ("Create VLAN on …") — no LLM latency
        # Fast-path: create VLAN plus access/trunk interface binding. This
        # covers the common Playbook Studio command shape without waiting on
        # Ollama just to identify create_vlan + interface binding.
        quick_vlan_binding = self._try_rule_based_vlan_interface_binding(user_message)
        if quick_vlan_binding:
            quick_vlan_binding.id = str(uuid.uuid4())[:8]
            quick_vlan_binding.raw_user_input = user_message
            quick_vlan_binding.resolved_devices = self._resolve_devices(quick_vlan_binding)
            self._normalize_vlan_parameters(quick_vlan_binding)
            quick_vlan_binding.parameters["device_deduction"] = self._deduce_device_reason(
                user_message,
                quick_vlan_binding.resolved_devices,
                generic_prompt=False,
                layer_candidates=bool(
                    (quick_vlan_binding.parameters or {}).get("_target_device_candidates")
                ),
            )
            try:
                from ansible.intent_pipeline.validator import annotate_missing_fields

                dumped = annotate_missing_fields(quick_vlan_binding.model_dump())
                quick_vlan_binding.parameters = dict(dumped.get("parameters") or {})
                quick_vlan_binding.requires_resolution = bool(dumped.get("requires_resolution"))
            except Exception:
                pass
            return json.dumps(quick_vlan_binding.model_dump(), default=str)

        quick_cv = self._try_rule_based_create_vlan(user_message)
        if quick_cv:
            quick_cv.id = str(uuid.uuid4())[:8]
            quick_cv.raw_user_input = user_message
            generic_vlan = is_generic_vlan_on_devices_message(user_message)
            quick_cv.parameters = dict(quick_cv.parameters or {})
            if generic_vlan:
                quick_cv.resolved_devices = []
                quick_cv.parameters["_generic_vlan_prompt"] = True
            else:
                records = self._inventory_records()
                layer_cand = enumerate_layer_switch_device_candidates(user_message, records)
                if layer_cand:
                    quick_cv.parameters = dict(quick_cv.parameters or {})
                    quick_cv.parameters["_target_device_candidates"] = layer_cand
                    quick_cv.parameters["_target_device_candidate_reason"] = (
                        "Prompt uses access/distribution/core switch numbers — pick confirmed "
                        "device(s) from this shortlist or from the full planned network list."
                    )
                    quick_cv.requires_disambiguation = True
                    quick_cv.disambiguation_message = (
                        "Select target device(s) from planned inventory. "
                        "Role/number wording is not treated as an exact hostname match."
                    )
                # Exact inventory / alias only — never auto-fill resolved_devices from layer-number heuristics.
                quick_cv.resolved_devices = self._resolve_devices(quick_cv)
            self._normalize_vlan_parameters(quick_cv)
            quick_cv.parameters["device_deduction"] = self._deduce_device_reason(
                user_message,
                quick_cv.resolved_devices,
                generic_prompt=generic_vlan,
                layer_candidates=bool(
                    (quick_cv.parameters or {}).get("_target_device_candidates")
                ),
            )
            # Layer-switch shortlist already set requires_disambiguation + message — do not overwrite.
            _layer_shortlist_pending = bool(
                (quick_cv.parameters or {}).get("_target_device_candidates")
            )
            if (
                quick_cv.is_config_change()
                and not quick_cv.resolved_devices
                and not generic_vlan
                and not _layer_shortlist_pending
            ):
                quick_cv.requires_disambiguation = True
                quick_cv.disambiguation_message = (
                    "Specify target device hostnames for this VLAN (e.g. from planned inventory)."
                )
            try:
                from ansible.intent_pipeline.validator import annotate_missing_fields

                dumped = annotate_missing_fields(quick_cv.model_dump())
                quick_cv.parameters = dict(dumped.get("parameters") or {})
                quick_cv.requires_resolution = bool(dumped.get("requires_resolution"))
            except Exception:
                pass
            return json.dumps(quick_cv.model_dump(), default=str)

        # Refactored pipeline (intent_catalog/) — default. Legacy monolithic parser is **opt-in** only:
        #   INTENT_USE_LEGACY_PARSER=1
        # If v2 fails and legacy is off, return a structured failure intent (no silent fallback).
        legacy_ok = self._legacy_intent_parser_allowed()
        if self._provider and self._intent_pipeline_v2_enabled():
            try:
                from ansible.intent_pipeline.intent_splitter import split_multi_intent
                from ansible.intent_pipeline.orchestrator import prepare_llm_input_dict
                from ansible.intent_pipeline.prompt_builder import (
                    build_multi_intent_user_prompt,
                    build_v2_user_prompt,
                    detect_categories_for_message,
                )

                segments = split_multi_intent(user_message)
                if len(segments) > 1:
                    union_cats: List[str] = []
                    seen_cat: set[str] = set()
                    for seg in segments:
                        cats_seg, _ = detect_categories_for_message(seg)
                        for c in cats_seg:
                            if c not in seen_cat:
                                seen_cat.add(c)
                                union_cats.append(c)
                    excerpt = self._get_template_schemas_for_categories(union_cats)
                    prompt_v2 = build_multi_intent_user_prompt(
                        segments,
                        topology_context or "",
                        excerpt,
                        union_cats,
                    )
                else:
                    cats, _ = detect_categories_for_message(user_message)
                    excerpt = self._get_template_schemas_for_categories(cats)
                    prompt_v2 = build_v2_user_prompt(
                        user_message,
                        topology_context or "",
                        excerpt,
                        cats,
                    )
                raw_json = self._run_llm_sync(prompt_v2)
                data = self._data_from_llm_raw(raw_json)
                data = prepare_llm_input_dict(data)
                return self._finalize_intent_from_parsed_data(data, user_message)
            except Exception as exc:
                logger.warning("Intent pipeline v2 failed (%s)", exc, exc_info=True)
                if not legacy_ok:
                    return self._finalize_intent_from_parsed_data(
                        _intent_v2_parse_failure_data(user_message, exc),
                        user_message,
                    )
        elif self._provider and not self._intent_pipeline_v2_enabled() and not legacy_ok:
            return self._finalize_intent_from_parsed_data(
                _intent_v2_parse_failure_data(
                    user_message,
                    RuntimeError("INTENT_PIPELINE_V2 is disabled; re-enable it or set INTENT_USE_LEGACY_PARSER=1."),
                ),
                user_message,
            )

        # Legacy: full prompt (only when INTENT_USE_LEGACY_PARSER=1)
        if not legacy_ok:
            if self._provider:
                return self._finalize_intent_from_parsed_data(
                    _intent_v2_parse_failure_data(
                        user_message,
                        RuntimeError("Legacy intent parser disabled (INTENT_USE_LEGACY_PARSER not set)."),
                    ),
                    user_message,
                )
            raw_json = json.dumps({
                "intent_type": "query",
                "action": "unknown",
                "feature": "",
                "vendor": "",
                "parameters": {},
                "scope": {"device_hostnames": [], "all_devices": False},
                "risk_level": "low",
                "justification": user_message,
            })
            data = self._data_from_llm_raw(raw_json)
            return self._finalize_intent_from_parsed_data(data, user_message)

        template_schemas = self._get_template_schemas()
        try:
            from ansible.intent_validation import (
                get_intent_operation_reference_for_prompt,
                get_required_params_hint_for_prompt,
            )
            operation_reference = get_intent_operation_reference_for_prompt()
            required_params_hint = get_required_params_hint_for_prompt()
        except Exception:
            operation_reference = "(not available)"
            required_params_hint = "(none)"

        try:
            from ansible.intent_artifacts import (
                get_category_vocabulary_for_prompt,
                get_intent_graph_summary_for_prompt,
                get_intent_keywords_digest_for_prompt,
            )

            category_vocabulary = get_category_vocabulary_for_prompt()
            intent_graph_summary = get_intent_graph_summary_for_prompt()
            intent_keywords_digest = get_intent_keywords_digest_for_prompt()
        except Exception:
            category_vocabulary = "(category vocabulary unavailable)"
            intent_graph_summary = "(intent graph unavailable)"
            intent_keywords_digest = "(keyword digest unavailable)"

        prompt = _PARSE_PROMPT.format(
            template_schemas=template_schemas,
            operation_reference=operation_reference,
            required_params_hint=required_params_hint,
            category_vocabulary=category_vocabulary,
            intent_graph_summary=intent_graph_summary,
            intent_keywords_digest=intent_keywords_digest,
            topology_context=topology_context or "(no topology loaded)",
            user_message=user_message,
        )

        if self._provider:
            raw_json = self._run_llm_sync(prompt)
        else:
            raw_json = json.dumps({
                "intent_type": "query",
                "action": "unknown",
                "feature": "",
                "vendor": "",
                "parameters": {},
                "scope": {"device_hostnames": [], "all_devices": False},
                "risk_level": "low",
                "justification": user_message,
            })

        data = self._data_from_llm_raw(raw_json)
        return self._finalize_intent_from_parsed_data(data, user_message)

    def _intent_pipeline_v2_enabled(self) -> bool:
        return os.getenv("INTENT_PIPELINE_V2", "true").lower() in ("1", "true", "yes", "on")

    def _legacy_intent_parser_allowed(self) -> bool:
        """Monolithic legacy prompt+graph parser — opt-in only (default off for v2-only testing)."""
        return os.getenv("INTENT_USE_LEGACY_PARSER", "").lower() in ("1", "true", "yes", "on")

    def _run_llm_sync(self, prompt: str) -> str:
        """Call the LLM from sync context (handles nested event loops)."""
        import asyncio

        try:
            timeout_sec = float(os.getenv("INTENT_PARSE_LLM_TIMEOUT_SEC", "90"))
        except ValueError:
            timeout_sec = 90.0

        loop: Any = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._call_llm(prompt))
                return future.result(timeout=timeout_sec)
        return asyncio.run(self._call_llm(prompt))

    def _data_from_llm_raw(self, raw_json: str) -> dict:
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            return self._extract_json(raw_json)

    def _sync_scope_target_devices(self, data: dict) -> None:
        """Align scope.device_hostnames with parameters.target_devices when one side is empty."""
        scope = data.setdefault("scope", {})
        if not isinstance(scope, dict):
            data["scope"] = {}
            scope = data["scope"]
        params = data.get("parameters")
        if not isinstance(params, dict):
            return
        td = params.get("target_devices")
        hosts = scope.get("device_hostnames") or []
        has_hosts = bool(hosts and any(str(h).strip() for h in hosts))
        if td is not None and not has_hosts:
            if isinstance(td, list):
                scope["device_hostnames"] = [str(x).strip() for x in td if str(x).strip()]
            elif str(td).strip():
                scope["device_hostnames"] = [str(td).strip()]
            data["scope"] = scope
        hosts2 = scope.get("device_hostnames") or []
        if data.get("action") == "create_vlan" and hosts2 and not params.get("target_devices"):
            params = dict(params)
            params["target_devices"] = [str(h).strip() for h in hosts2 if str(h).strip()]
            data["parameters"] = params

    def _get_template_schemas_for_categories(self, category_keys: List[str]) -> str:
        """Smaller template JSON block for v2 prompt (faster + less noise than all features)."""
        feature_set: set[str] = set()
        for ck in category_keys:
            if ck == "vlan":
                feature_set.update(["vlan", "interface"])
            else:
                feature_set.add(ck)
        try:
            from config_engine.renderer import TemplateRenderer

            renderer = TemplateRenderer()
            templates = renderer.list_templates()
            schemas_dir = Path(__file__).resolve().parent.parent / "config_engine" / "schemas"
            lines: list[str] = []
            seen: set[str] = set()
            for t in templates:
                feat = t.get("feature", "")
                if not feat or feat in seen or feat not in feature_set:
                    continue
                schema_path = schemas_dir / f"{feat}.json"
                if schema_path.exists():
                    seen.add(feat)
                    schema = json.loads(schema_path.read_text())
                    lines.append(f"### {feat}\n```json\n{json.dumps(schema, indent=2)}\n```")
            if lines:
                return "\n\n".join(lines)
        except Exception as exc:
            logger.warning("Could not load subset template schemas: %s", exc)
        return self._get_template_schemas()

    def _normalize_vlan_parameters(self, intent: ParsedIntent) -> None:
        """Map legacy vlan_id/vlan_name into parameters.vlans[] for catalog validation."""
        if intent.action != "create_vlan" or intent.feature != "vlan":
            return
        p = dict(intent.parameters or {})
        vl = p.get("vlans")
        if isinstance(vl, list) and len(vl) > 0:
            intent.parameters = p
            return
        if p.get("vlan_id") is None and not str(p.get("vlan_name") or "").strip():
            intent.parameters = p
            return
        row: Dict[str, Any] = {}
        if p.get("vlan_id") is not None:
            row["id"] = p["vlan_id"]
        if p.get("vlan_name") is not None:
            row["name"] = p["vlan_name"]
        if row:
            p["vlans"] = [row]
        intent.parameters = p

    def _finalize_intent_from_parsed_data(self, data: dict, user_message: str) -> str:
        """Shared post-LLM path: sync fields, topology resolution, then catalog validation."""
        import uuid

        self._sync_scope_target_devices(data)
        intent = self._build_intent(data, user_message)
        intent.id = str(uuid.uuid4())[:8]

        generic_vlan = is_generic_vlan_on_devices_message(user_message)

        if generic_vlan and intent.action == "create_vlan" and intent.feature == "vlan":
            intent.resolved_devices = []
            intent.parameters = dict(intent.parameters or {})
            intent.parameters["_generic_vlan_prompt"] = True
        else:
            intent.resolved_devices = self._resolve_devices(intent)

        intent.parameters = dict(intent.parameters or {})
        # create_vlan: "access switch 1, 2, 3" style → candidate shortlist only (admin confirms).
        if (
            self._db
            and not generic_vlan
            and intent.action == "create_vlan"
            and intent.feature == "vlan"
        ):
            if not intent.parameters.get("_target_device_candidates"):
                lc = enumerate_layer_switch_device_candidates(
                    user_message, self._inventory_records()
                )
                if lc:
                    intent.parameters["_target_device_candidates"] = lc
                    intent.parameters["_target_device_candidate_reason"] = (
                        "Prompt references access/distribution/core switch numbers — "
                        "confirm device(s) from this shortlist or the full planned network."
                    )
            if intent.parameters.get("_target_device_candidates") and not intent.resolved_devices:
                intent.requires_disambiguation = True
                if not (intent.disambiguation_message or "").strip():
                    intent.disambiguation_message = (
                        "Select target device(s) from planned inventory; role/number text is not an exact hostname."
                    )

        self._normalize_vlan_parameters(intent)
        if (
            intent.action == "create_vlan"
            and intent.feature == "vlan"
            and not generic_vlan
        ):
            p = dict(intent.parameters or {})
            vlist = p.get("vlans")
            if not (isinstance(vlist, list) and len(vlist) > 0):
                extra = parse_vlan_database_params_from_text(user_message)
                if extra.get("vlans"):
                    p["vlans"] = extra["vlans"]
                    intent.parameters = p
                    self._normalize_vlan_parameters(intent)

        if _create_vlan_fast_path_match(user_message):
            if intent.feature == "vlan" and (
                intent.action in ("clarify", "unknown", "")
                or intent.intent_type == IntentType.QUERY
            ):
                intent.intent_type = IntentType.CONFIGURE
                intent.action = "create_vlan"
                intent.feature = "vlan"

        lower_msg = user_message.lower()
        looks_like_shutdown_interfaces = (
            any(
                kw in lower_msg
                for kw in (
                    "shutdown the interfaces",
                    "shut down the interfaces",
                    "turn off the interfaces",
                )
            )
            or ("shutdown" in lower_msg and "interface" in lower_msg)
            or ("shut down" in lower_msg and "interface" in lower_msg)
        )
        if not intent.is_config_change() and looks_like_shutdown_interfaces:
            intent.intent_type = IntentType.CONFIGURE
            if not intent.action:
                intent.action = "shutdown_interface"
            if not intent.feature:
                intent.feature = "interface"

        intent.parameters["device_deduction"] = self._deduce_device_reason(
            user_message,
            intent.resolved_devices,
            generic_prompt=bool(generic_vlan and intent.action == "create_vlan" and intent.feature == "vlan"),
            layer_candidates=bool((intent.parameters or {}).get("_target_device_candidates")),
        )
        if intent.feature == "interface" and intent.is_config_change():
            ifaces = intent.parameters.get("interfaces_to_shutdown") or intent.parameters.get("interfaces")
            if not ifaces or (isinstance(ifaces, list) and not any(str(x).strip() for x in ifaces)):
                intent.requires_resolution = True
                logger.info("No interfaces provided by user for interface intent; requires_resolution=true")

        if intent.is_config_change() and not intent.resolved_devices:
            if generic_vlan and intent.action == "create_vlan":
                intent.requires_disambiguation = False
                intent.disambiguation_message = ""
            else:
                intent.requires_disambiguation = True
                if not (intent.disambiguation_message or "").strip():
                    intent.disambiguation_message = (
                        f"Could not resolve target devices for this intent. "
                        f"Scope: {intent.scope.model_dump()}. "
                        f"Please specify device hostnames explicitly (e.g. R1, SW1)."
                    )

        try:
            from ansible.intent_pipeline.validator import annotate_missing_fields

            flat = annotate_missing_fields(intent.model_dump())
            intent.parameters = dict(flat.get("parameters") or {})
            intent.requires_resolution = bool(flat.get("requires_resolution"))
        except Exception:
            pass

        logger.info(
            "Parsed intent: type=%s action=%s feature=%s devices=%s risk=%s",
            intent.intent_type,
            intent.action,
            intent.feature,
            intent.resolved_devices,
            intent.risk_level,
        )
        return json.dumps(intent.model_dump(), default=str)

    # ----- LLM call -----

    async def _call_llm(self, prompt: str) -> str:
        from llm.provider import LLMMessage
        response = await self._provider.generate(
            messages=[LLMMessage(role="user", content=prompt)],
            tools=None,
            temperature=0.0,
        )
        return response.content.strip()

    # ----- Rule-based fast path -----

    def _try_rule_based(self, msg: str) -> Optional[ParsedIntent]:
        """Handle obvious query/audit intents without LLM (patterns from intent_keywords.yml)."""
        lower = msg.lower().strip()
        try:
            from ansible.intent_artifacts import get_audit_keywords, get_query_rule_based_patterns

            query_patterns = get_query_rule_based_patterns()
            audit_words = get_audit_keywords()
        except Exception:
            query_patterns = [
                "show topology",
                "list devices",
                "list all devices",
                "show devices",
                "what is the topology",
                "show path",
                "find path",
                "show interfaces",
                "how many devices",
            ]
            audit_words = ["audit", "security scan", "cis benchmark", "compliance check"]
        for pat in query_patterns:
            if pat in lower:
                return ParsedIntent(
                    intent_type=IntentType.QUERY,
                    action="show_info",
                    risk_level=RiskLevel.LOW,
                    justification="Information query",
                )

        if any(w in lower for w in audit_words):
            return ParsedIntent(
                intent_type=IntentType.AUDIT,
                action="security_audit",
                risk_level=RiskLevel.LOW,
                justification="Security audit request",
            )

        return None

    def _try_rule_based_create_vlan(self, msg: str) -> Optional[ParsedIntent]:
        """
        Rule-based create_vlan intent for short imperative VLAN-database prompts.
        Fills ``parameters.vlans`` from the user text when id (and optional name) are present.
        """
        if not _create_vlan_fast_path_match(msg):
            return None
        params = dict(parse_vlan_database_params_from_text(msg))
        return ParsedIntent(
            intent_type=IntentType.CONFIGURE,
            action="create_vlan",
            feature="vlan",
            risk_level=RiskLevel.MEDIUM,
            justification="Rule-based: imperative create VLAN (VLAN database) request",
            parameters=params,
        )

    def _try_rule_based_vlan_interface_binding(self, msg: str) -> Optional[ParsedIntent]:
        """
        Rule-based create_vlan + access/trunk port binding for common studio prompts.

        Example: "Create vlan 43 on access switch and configure as access port on interface".
        Target devices and exact interfaces can still be supplied/changed by the UI picker.
        """
        mode = _vlan_interface_binding_mode_from_text(msg)
        if mode not in {"access", "trunk"}:
            return None
        params = dict(parse_vlan_database_params_from_text(msg))
        vlans = params.get("vlans")
        if not isinstance(vlans, list) or not vlans:
            return None
        vlan_row = dict(vlans[0] or {})
        vlan_id = vlan_row.get("id")
        vlan_name = vlan_row.get("name") or f"VLAN {vlan_id}"
        try:
            vlan_id_int = int(vlan_id)
        except (TypeError, ValueError):
            return None

        interfaces: List[Dict[str, Any]] = []
        for name in _extract_interface_names_from_text(msg):
            if mode == "trunk":
                interfaces.append({"name": name, "mode": "trunk", "trunk_vlans": [vlan_id_int]})
            else:
                interfaces.append({"name": name, "mode": "access", "access_vlan": vlan_id_int})

        secondary_params: Dict[str, Any] = {
            "interfaces": interfaces,
            "vlan_id": vlan_id_int,
            "vlan_name": vlan_name,
        }
        if mode == "trunk":
            secondary_action = "assign_vlan_to_trunk_port"
            secondary_params["trunk_vlans"] = [vlan_id_int]
        else:
            secondary_action = "assign_vlan_to_access_port"
            secondary_params["access_vlan"] = vlan_id_int

        params.update(
            {
                "vlan_id": vlan_id_int,
                "vlan_name": vlan_name,
                "interface_binding": interfaces,
                "_secondary_intents": [
                    {
                        "action": secondary_action,
                        "feature": "interface",
                        "parameters": secondary_params,
                    }
                ],
            }
        )
        if interfaces:
            params["interfaces"] = list(interfaces)

        layer_cand = enumerate_layer_switch_device_candidates(msg, self._inventory_records())
        requires_disambiguation = False
        disambiguation = ""
        if layer_cand:
            params["_target_device_candidates"] = layer_cand
            params["_target_device_candidate_reason"] = (
                "Prompt uses access/distribution/core switch numbers or role wording; confirm "
                "device(s) from this shortlist or the planned network list."
            )
            requires_disambiguation = True
            disambiguation = "Select target device(s) from planned inventory."
        elif not interfaces:
            requires_disambiguation = True
            disambiguation = "Select interface(s) from planned inventory for the VLAN port binding."

        scope = IntentScope()
        lower = re.sub(r"\s+", " ", (msg or "").lower())
        if re.search(r"\baccess\s+switch(?:es)?\b", lower):
            scope.device_role = "access"
        elif re.search(r"\b(?:distribution|dist)\s+switch(?:es)?\b", lower):
            scope.device_role = "distribution"
        elif re.search(r"\bcore\s+switch(?:es)?\b", lower):
            scope.device_role = "core"

        return ParsedIntent(
            intent_type=IntentType.CONFIGURE,
            action="create_vlan",
            feature="vlan",
            risk_level=RiskLevel.MEDIUM,
            justification="Rule-based: create VLAN and configure VLAN port binding",
            parameters=params,
            scope=scope,
            requires_resolution=not bool(interfaces),
            requires_disambiguation=requires_disambiguation,
            disambiguation_message=disambiguation,
        )

    # ----- Helpers -----

    def _get_template_schemas(self) -> str:
        """Load all template schemas for prompt injection."""
        try:
            from config_engine.renderer import TemplateRenderer
            renderer = TemplateRenderer()
            templates = renderer.list_templates()
            schemas_dir = Path(__file__).resolve().parent.parent / "config_engine" / "schemas"

            lines = []
            seen = set()
            for t in templates:
                feat = t.get("feature", "")
                if feat in seen:
                    continue
                seen.add(feat)
                schema_path = schemas_dir / f"{feat}.json"
                if schema_path.exists():
                    schema = json.loads(schema_path.read_text())
                    lines.append(f"### {feat}\n```json\n{json.dumps(schema, indent=2)}\n```")
            return "\n\n".join(lines) if lines else "(no schemas available)"
        except Exception as e:
            logger.warning("Could not load template schemas: %s", e)
            return "(schemas unavailable)"

    def _extract_json(self, text: str) -> dict:
        """Try to extract a JSON object from LLM output that may have extra text."""
        # Try to find { ... } block
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if start == -1:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        continue
        return {"intent_type": "query", "action": "unknown", "justification": text[:200]}

    def _build_intent(self, data: dict, user_message: str) -> ParsedIntent:
        """Build a ParsedIntent from raw LLM JSON output with safety defaults."""
        scope_data = data.get("scope", {})
        raw_hosts = scope_data.get("device_hostnames", [])
        # Normalize device_hostnames: split tokens like "Acc_1, Acc_2" or "Acc_1 and Acc_2" into separate entries.
        norm_hosts: list[str] = []
        for h in raw_hosts or []:
            text = str(h or "").strip()
            if not text:
                continue
            # Replace common connectors with commas, then split.
            text = re.sub(r"\band\b", ",", text, flags=re.IGNORECASE)
            parts = [p.strip() for p in text.split(",") if p.strip()]
            norm_hosts.extend(parts or [text])

        scope = IntentScope(
            device_hostnames=norm_hosts,
            device_role=scope_data.get("device_role"),
            building=scope_data.get("building"),
            vendor_filter=scope_data.get("vendor_filter"),
            all_devices=scope_data.get("all_devices", False),
        )

        # Map string to enum safely
        try:
            intent_type = IntentType(data.get("intent_type", "query"))
        except ValueError:
            intent_type = IntentType.QUERY

        try:
            risk_level = RiskLevel(data.get("risk_level", "medium"))
        except ValueError:
            risk_level = RiskLevel.MEDIUM

        params = dict(data.get("parameters", {}))
        secondary = data.get("secondary_intents")
        if isinstance(secondary, list) and secondary:
            params["_secondary_intents"] = secondary
        requires_resolution = bool(data.get("requires_resolution", False))
        requires_disambiguation = bool(data.get("requires_disambiguation", False))
        disambiguation_message = str(data.get("disambiguation_message") or "")
        return ParsedIntent(
            intent_type=intent_type,
            action=data.get("action", "unknown"),
            feature=data.get("feature", ""),
            vendor=_normalize_vendor(data.get("vendor")),
            parameters=params,
            scope=scope,
            risk_level=risk_level,
            justification=data.get("justification", ""),
            raw_user_input=user_message,
            requires_disambiguation=requires_disambiguation,
            disambiguation_message=disambiguation_message,
            requires_resolution=requires_resolution,
        )

    def _deduce_device_reason(
        self,
        raw: str,
        resolved_hosts: list[str],
        *,
        generic_prompt: bool = False,
        layer_candidates: bool = False,
    ) -> dict[str, Any]:
        """Return a readable explanation of why each resolved host was chosen."""
        if generic_prompt:
            return {
                "deduced": [],
                "mapping": [],
                "why": "Generic VLAN prompt — specify VLAN id, name, and target devices explicitly.",
                "generic_prompt": True,
            }
        if layer_candidates:
            return {
                "deduced": [],
                "mapping": [],
                "why": (
                    "Layer/switch-number phrase detected — see _target_device_candidates for a shortlist; "
                    "confirm device(s) in UI (not auto-resolved)."
                ),
            }
        if not resolved_hosts:
            return {
                "deduced": [],
                "mapping": [],
                "why": "No target devices resolved from the prompt (exact inventory / alias only).",
            }
        lower_raw = (raw or "").lower()
        mapping: list[dict[str, Any]] = []
        tokens = _extract_explicit_device_tokens(raw or "")
        alias_map = _load_device_alias_map()
        for host in resolved_hosts:
            host_lower = host.lower()
            reasons: list[str] = []
            if any(str(t).lower() == host_lower or str(t).upper() == host.upper() for t in tokens):
                reasons.append("explicit device token in prompt matched inventory hostname (exact)")
            for alias_key, hinted in alias_map.items():
                if host in hinted and alias_key.lower() in lower_raw:
                    reasons.append(f"device_alias_hints entry '{alias_key}' mapped to this hostname")
            if not reasons:
                reasons.append("exact hostname match from scope.device_hostnames or topology inventory")
            mapping.append({"hostname": host, "reason": "; ".join(reasons)})
        return {
            "deduced": resolved_hosts,
            "mapping": mapping,
            "why": "Only exact planned-inventory hostnames (or device_alias_hints) — no fuzzy or role/number guessing.",
        }

    def _resolve_devices(self, intent: ParsedIntent) -> List[str]:
        """
        Expand the intent scope into concrete device hostnames using
        the live topology and device inventory.
        """
        if not self._db:
            return list(intent.scope.device_hostnames)

        topo = self._db.get_topology()
        nodes = topo.get("nodes", {}) if topo else {}
        all_hosts = list(nodes.keys())
        inventory_records = self._inventory_records()
        inventory_by_hostname = {
            str(record.get("hostname") or ""): record
            for record in inventory_records
            if str(record.get("hostname") or "")
        }

        # 1. Explicit hostnames — verify they exist; if scope also has device_role, filter to that role
        if intent.scope.device_hostnames:
            resolved = []
            for h in intent.scope.device_hostnames:
                # Normalize common punctuation from chat text: "Acc_Cisco_2," / "Acc_Cisco_2."
                h_clean = re.sub(r"[,\.;:]+$", "", str(h or "").strip())
                match = next(
                    (
                        n
                        for n in list(inventory_by_hostname.keys()) + all_hosts
                        if n.lower() == h_clean.lower()
                    ),
                    None,
                )
                if match:
                    resolved.append(match)
                else:
                    logger.warning("Device '%s' not found in topology", h_clean or h)
            # When user said "access switches" (etc.), filter to that role
            requested_role = _normalize_role(intent.scope.device_role)
            if requested_role and resolved:
                role_filtered = [
                    name for name in resolved
                    if _role_matches(name, requested_role, inventory_by_hostname)
                ]
                if role_filtered:
                    resolved = role_filtered
                    logger.info("Filtered to %s role: %d devices", requested_role, len(resolved))
            return resolved

        # 1b. Explicit short tokens (SW1, R1, Acc_Cisco_1, …) — alias map + exact inventory only
        toks = _extract_explicit_device_tokens(intent.raw_user_input or "")
        if toks:
            got = _resolve_device_tokens_to_inventory(toks, inventory_by_hostname, all_hosts)
            if got:
                return got

        # 2. all_devices flag
        if intent.scope.all_devices:
            return sorted(inventory_by_hostname.keys() or all_hosts)

        # 3. Role / building / vendor filter — never return whole inventory without a filter
        try:
            requested_vendor = _normalize_vendor(intent.scope.vendor_filter or intent.vendor)
            requested_role = _normalize_role(intent.scope.device_role)
            requested_site = _normalize_site(intent.scope.building)

            if not requested_role and not requested_vendor and not requested_site:
                return []

            resolved: List[str] = []
            for hostname, record in inventory_by_hostname.items():
                record_vendor = _normalize_vendor(record.get("vendor"))
                record_role = _normalize_role(record.get("role"))
                record_site = _normalize_site(record.get("site"))

                if requested_vendor and record_vendor != requested_vendor:
                    continue
                if requested_site and record_site != requested_site:
                    continue
                # Role: match inventory role, or infer from hostname (Acc_/Dist_/Core_) when DB role is generic/missing
                if requested_role:
                    role_match = record_role == requested_role
                    if not role_match:
                        inferred = _role_from_hostname(hostname)
                        role_match = inferred == requested_role
                    if not role_match:
                        continue
                resolved.append(hostname)

            return sorted(resolved)
        except Exception as e:
            logger.warning("Device resolution query failed: %s", e)
            return list(intent.scope.device_hostnames)

    def _inventory_records(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if self._db is None:
            return records

        try:
            from rag.context_bundle import ContextBundleBuilder

            bundle = ContextBundleBuilder(db_manager=self._db).build_bundle(
                include_inventory=True,
                include_planned_topology=False,
                include_telemetry=False,
                include_sdn=False,
                include_config=False,
                include_deployment=False,
                include_incidents=False,
                include_threats=False,
                include_drift=False,
                include_knowledge=False,
                include_freshness_warnings=False,
            )
            records = list(bundle.inventory.devices or [])
        except Exception as exc:
            logger.debug("Context-backed inventory lookup failed: %s", exc)

        if records:
            return records

        return [
            {
                "hostname": device.hostname,
                "vendor": device.vendor,
                "role": device.role,
                "site": None,
            }
            for device in self._db.get_all_devices()
            if device.hostname
        ]


def _role_from_hostname(hostname: str) -> str:
    """Infer device role from hostname prefix (e.g. Acc_, Dist_, Core_) when DB role is missing or generic."""
    name = (hostname or "").strip()
    lower = name.lower()
    if lower.startswith("acc_"):
        return "access"
    if lower.startswith("dist_"):
        return "distribution"
    if lower.startswith("core_"):
        return "core"
    return ""


def _role_matches(
    hostname: str,
    requested_role: str,
    inventory_by_hostname: Dict[str, Any],
) -> bool:
    """True if device matches requested role (inventory role or hostname prefix)."""
    record = inventory_by_hostname.get(hostname, {})
    record_role = _normalize_role(record.get("role"))
    if record_role == requested_role:
        return True
    inferred = _role_from_hostname(hostname)
    return inferred == requested_role


def _normalize_vendor(value: Any) -> str:
    lowered = str(value or "").strip().lower().replace(" ", "_")
    if not lowered:
        return ""
    return _vendor_aliases().get(lowered, lowered)


def _normalize_role(value: Any) -> str:
    lowered = str(value or "").strip().lower().replace(" ", "_")
    if not lowered:
        return ""
    return _role_aliases().get(lowered, lowered)


def _normalize_site(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
