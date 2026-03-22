"""
M22 Ansible Orchestration — FastAPI router.

Endpoints:

  POST /ansible/jobs/{job_id}/plan-ansible   — LLM generates playbook, stored on job
  POST /ansible/jobs/{job_id}/check          — ansible-playbook --check (dry-run diff)
  POST /ansible/jobs/{job_id}/approve        — operator approval gate (admin only)
  POST /ansible/jobs/{job_id}/execute        — run approved playbook (admin only)
  GET  /ansible/jobs/{job_id}/result         — fetch last run result

Approval flow:
  plan-ansible → check (operator reviews diff) → approve → execute

All destructive changes require explicit operator approval before execute is allowed.
"""

import json
import logging
import difflib
import re
import asyncio
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ansible.bundle_artifacts import AnsibleArtifactBundle, bundle_from_dict
from ansible.context_entity_validation import (
    build_planned_all_interfaces_preview,
    enrich_params_disambiguation_trunk_vlan_controls,
    explicit_interface_names_from_request_params,
    intent_requests_all_interfaces,
    intersection_interfaces_from_disambiguation_devices,
    is_spurious_device_hint_token,
    should_validate_interface_names_against_inventory,
    validate_static_route_next_hop_ip_against_context,
    validate_interface_params_against_context,
)
from ansible.intent_validation import (
    SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION,
    build_intent_schema_inspector_payload,
    coerce_vlan_database_only_intent,
    get_editable_fields_for_schema,
    get_schema_for_parsed_intent,
    interface_intent_params_satisfied,
    is_vlan_database_only_intent,
    merge_extracted_vlan_params_into_flat_params,
    merge_extracted_vlan_params_into_parsed_intent,
    merge_prompt_aware_disambiguation_for_schema,
    resolve_missing_fields,
    user_prompt_requests_vlan_interface_binding,
    validate_completeness,
)
from ansible.intent_validation import _intent_to_flat_fields, _is_empty as _param_is_empty
from ansible.intent_tree_engine import evaluate_intent_tree
from ansible.reference_resolver import (
    fill_playbook_from_reference,
    format_vlan_interface_reasoning_line,
    get_vlan_interfaces_summary,
    resolve_interfaces,
    validate_vlan_intent,
    vendors_from_db,
    vendors_from_planning_context,
)
from ansible.multi_intent_operations import (
    merged_params_for_vlan_interfaces_reasoning,
    refresh_preview_vlan_interfaces_reasoning_block,
    strip_stale_secondary_params_after_primary_authoritative,
)
from ansible.planning_context import (
    build_scoped_planning_context,
    infer_vendor_hint,
    normalize_role,
    normalize_vendor,
    planning_context_readiness,
    planning_context_to_prompt_text,
    reconcile_inventory_state,
    topology_override_from_planning_context,
)
from config_engine.safety import validate_playbook_yaml

logger = logging.getLogger("ansible.api")


def _default_manifest_llm_provider() -> str:
    v = (os.getenv("LLM_PROVIDER") or "ollama").strip().lower()
    return v if v in ("ollama", "gemini") else "ollama"


def _default_manifest_llm_model() -> str:
    if _default_manifest_llm_provider() == "gemini":
        return os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    from llm.ollama_provider import DEFAULT_OLLAMA_MODEL

    return os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

router = APIRouter(prefix="/ansible", tags=["Ansible Orchestration (M22)"])

# ── Lazy-loaded shared instances (injected by backend.py) ───────────────────
_builder  = None   # PlaybookBuilder
_agent    = None   # NetworkAgent (for RAG context)
_pipeline = None   # DeploymentPipeline
_db       = None   # DatabaseManager


def _get_safe_mode_default() -> bool:
    """Server default for safe_mode when request omits it. Config: ANSIBLE_SAFE_MODE_DEFAULT (default true)."""
    return os.getenv("ANSIBLE_SAFE_MODE_DEFAULT", "true").strip().lower() in ("true", "1", "yes")


def _plan_ansible_include_agent_full_context() -> bool:
    """
    NetworkAgent.build_full_context() (topology / digest injection) is expensive and often marginal
    when planning_context + kb_snippets already constrain the LLM.

    **Default: disabled** (unset or empty). Set ``ANSIBLE_PLAN_AGENT_FULL_CONTEXT=1`` to re-enable.
    """
    return os.getenv("ANSIBLE_PLAN_AGENT_FULL_CONTEXT", "").strip().lower() in ("true", "1", "yes")


def _plan_ansible_include_vector_rag() -> bool:
    """
    Chroma / embedding retrieval for ``kb_snippets`` during plan-ansible.

    **Default: enabled.** Set ``ANSIBLE_PLAN_VECTOR_RAG=0`` to skip (faster LLM plans; weaker doc hints).
    """
    v = os.getenv("ANSIBLE_PLAN_VECTOR_RAG", "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def _coerce_params_for_validation(params: Dict[str, Any]) -> None:
    """Coerce known numeric/array params so type validation and playbook generation get expected types."""
    if not params:
        return
    vlan_id = params.get("vlan_id")
    if vlan_id is not None and str(vlan_id).strip() != "":
        try:
            params["vlan_id"] = int(vlan_id)
        except (TypeError, ValueError):
            pass
    if params.get("target_hosts") is not None and isinstance(params["target_hosts"], str):
        params["target_hosts"] = [params["target_hosts"]]
    if params.get("target_devices") is not None and isinstance(params["target_devices"], str):
        params["target_devices"] = [params["target_devices"]]
    pid = params.get("process_id")
    if pid is not None and str(pid).strip() != "":
        try:
            params["process_id"] = int(pid)
        except (TypeError, ValueError):
            pass
    ad = params.get("admin_distance")
    if ad is not None and str(ad).strip() != "":
        try:
            params["admin_distance"] = int(ad)
        except (TypeError, ValueError):
            pass
    for key in ("networks", "rules", "classification_rules"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            stripped = val.strip()
            if stripped.startswith("[") or stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        params[key] = parsed
                    elif isinstance(parsed, dict) and key != "networks":
                        params[key] = [parsed]
                    elif key == "networks" and isinstance(parsed, dict):
                        params[key] = [stripped]
                except json.JSONDecodeError:
                    if key == "networks":
                        params[key] = [x.strip() for x in re.split(r"[\s,]+", stripped) if x.strip()]
                    else:
                        params[key] = [stripped]
            elif key == "networks":
                params[key] = [x.strip() for x in re.split(r"[\s,]+", stripped) if x.strip()]
            else:
                params[key] = [stripped]
    if isinstance(params.get("acl_type"), str):
        params["acl_type"] = params["acl_type"].strip().lower()
    if isinstance(params.get("qos_type"), str):
        params["qos_type"] = params["qos_type"].strip().lower()

    # Configure_interface conditional shared values:
    # If the UI collected a shared `access_vlan` / `trunk_vlans` / `subnet_mask`,
    # propagate them into each interface object before JSON-Schema validation.
    if isinstance(params.get("interfaces"), list):
        # Access VLAN
        av = params.get("access_vlan")
        if av is not None and str(av).strip() != "":
            try:
                av_int = int(av)
            except (TypeError, ValueError):
                av_int = None
            if av_int is not None:
                for iface in params["interfaces"]:
                    if isinstance(iface, dict) and str(iface.get("mode") or "access").strip().lower() == "access":
                        iface["access_vlan"] = av_int

        # Trunk VLANs (allow comma-separated string from UI)
        tv = params.get("trunk_vlans")
        trunk_vlans_parsed: Optional[List[int]] = None
        if tv is not None and str(tv).strip() != "":
            if isinstance(tv, list):
                out: List[int] = []
                for x in tv:
                    try:
                        out.append(int(x))
                    except (TypeError, ValueError):
                        pass
                trunk_vlans_parsed = out
            else:
                s = str(tv).strip()
                parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
                out = []
                for p in parts:
                    try:
                        out.append(int(p))
                    except (TypeError, ValueError):
                        pass
                trunk_vlans_parsed = out
        if trunk_vlans_parsed is not None:
            for iface in params["interfaces"]:
                if isinstance(iface, dict) and str(iface.get("mode") or "").strip().lower() == "trunk":
                    iface["trunk_vlans"] = trunk_vlans_parsed

        # IP subnet mask (shared)
        sm = params.get("subnet_mask")
        if sm is not None and str(sm).strip() != "":
            subnet_mask = str(sm).strip()
            for iface in params["interfaces"]:
                if isinstance(iface, dict) and iface.get("ip_address"):
                    iface["subnet_mask"] = subnet_mask


def _make_inventory_lookup_fn():
    """Build inventory_lookup_fn for resolve_missing_fields: from_inventory uses DB (and RAG when available)."""
    def lookup(field: str, intent: Dict[str, Any], context: Dict[str, Any]) -> tuple:
        # value, detail = (None, None) when not resolved
        if _db is None:
            return None, None
        if field == "target_devices":
            devices = _db.get_all_devices()
            scope = intent.get("scope") or {}
            params = intent.get("parameters") or intent.get("params") or {}
            role = scope.get("device_role") or params.get("device_role")
            if role:
                role_norm = normalize_role(role)
                hostnames = [d.hostname for d in devices if d.hostname and normalize_role(getattr(d, "role", "")) == role_norm]
            else:
                hostnames = [d.hostname for d in devices if d.hostname]
            if not hostnames:
                return None, None
            return hostnames, f"Resolved {len(hostnames)} device(s) from inventory"
        if field == "interfaces":
            params = intent.get("parameters") or intent.get("params") or {}
            hosts = intent.get("resolved_devices") or params.get("target_hosts") or intent.get("target_hosts") or []
            if not hosts:
                hosts = params.get("target_hosts") or []
            if isinstance(hosts, str):
                hosts = [hosts]
            names = []
            for h in hosts:
                dev = _db.get_device_by_hostname(str(h).strip())
                if not dev or not getattr(dev, "id", None):
                    continue
                for iface in _db.get_interfaces_for_device(dev.id):
                    if getattr(iface, "name", None):
                        names.append(iface.name)
            if not names:
                return None, None
            return names, f"Resolved {len(names)} interface(s) from inventory for {len(hosts)} device(s)"
        return None, None
    return lookup


def setup_ansible(builder, agent, pipeline, db_manager) -> None:
    """Called from backend.py lifespan to inject shared instances."""
    global _builder, _agent, _pipeline, _db
    _builder  = builder
    _agent    = agent
    _pipeline = pipeline
    _db       = db_manager
    logger.info("Ansible orchestration layer initialized")


def _require_builder():
    if _builder is None:
        raise HTTPException(503, "Ansible orchestration layer not initialized")
    return _builder


# Words that often leak into device_hostnames from intent parsing (e.g. "VLAN for Cisco and Huawei")
# and must not be treated as hostnames unless they exist in the DB.
_NON_HOSTNAME_WORDS = frozenset({
    "and", "or", "put", "for", "the", "on", "to", "with", "from", "by", "in", "at",
    "cisco", "huawei", "juniper", "aruba", "ios", "vrp", "junos", "nxos", "xe",
    "vlan", "named", "test", "create", "huaweicreate", "ciscocreate", "junipercreate",
    "interface", "interfaces",  # feature names that must not be treated as device hostnames
    "switch", "switches",  # e.g. from "Access Switch 1, 2, and 3" — not a hostname
    # Prompt boilerplate: "Create VLAN on devices" → do not treat "devices" as a hostname
    "device", "devices", "host", "hosts", "router", "routers", "network",
    # "on access switch 1,2,3" — token "access" must not expand to all access devices via _resolve_target_hosts
    "access",
    "core",
    "distribution",
    "dist",
    "edge",
    "leaf",
    "spine",
})


def _looks_like_device_hostname(s: str) -> bool:
    """False for interface names, parentheticals, or punctuation that often pollute intent parsing."""
    if not s or len(s) < 2:
        return False
    if "(" in s or ")" in s or s.rstrip().endswith(";"):
        return False
    low = s.strip().lower()
    if low in _NON_HOSTNAME_WORDS:
        return False
    # Intent words that look like hostnames: "VLAN", "HuaweiCreate", etc.
    if low.endswith("create") or low in ("vlan", "named", "test"):
        return False
    # Interface-like: Gi0/1, Gi1/0/1, Te0/0, 1/0/2, etc.
    if re.match(r"^(?:Gi|Te|Fa|Eth|Ethernet|GE)\s*\d+/\d+(/\d+)?$", s, re.IGNORECASE):
        return False
    if re.match(r"^\d+/\d+(/\d+)?$", s):
        return False
    # Hostname-like: starts with letter, alphanumeric/underscore/hyphen only
    return bool(s[0].isalpha() and re.match(r"^[a-z0-9_\-]+$", s, re.IGNORECASE))


def _extract_vlan_interface_from_description(intent_description: str) -> Optional[Dict[str, Any]]:
    """
    When the LLM does not fill parameters.interfaces, try to extract interface + trunk/access
    from the prompt text (e.g. "configure GigabitEthernet1/0/1 as trunk allowed VLANs 10,20,67").
    Returns a dict to merge into intent.params: interfaces, mode, trunk_vlans (or None if nothing found).
    """
    if not (intent_description or "").strip():
        return None
    text = intent_description.strip()
    # Interface name: GigabitEthernet1/0/1, Gi1/0/1, GE1/0/1, Ethernet0/1, 1/0/1, etc.
    iface_match = re.search(
        r"\b(?:GigabitEthernet|Gi|GE|Ethernet|Eth|TenGigabitEthernet|Te|FastEthernet|Fa)\s*(\d+(?:/\d+)+|\d+)\b",
        text,
        re.IGNORECASE,
    )
    if not iface_match:
        # Huawei-style: "1/0/1" or "GigabitEthernet1/0/1" as one word
        iface_match = re.search(r"\b(\d+/\d+(?:/\d+)?)\b", text)
    if not iface_match:
        return None
    iface_name = iface_match.group(0).replace(" ", "")
    # Huawei-style "1/0/1" alone: use as-is (VRP accepts it) or prepend GigabitEthernet
    if re.match(r"^\d+/\d+", iface_name) and "GigabitEthernet" not in text[: max(0, iface_match.start())].lower():
        if "gigabit" in text.lower() or "ge " in text.lower() or " gi" in text.lower():
            iface_name = "GigabitEthernet" + iface_name
    # Trunk + allowed VLANs (only take list that appears after "trunk" to avoid capturing VLAN id from "VLAN 67")
    trunk = "trunk" in text.lower()
    allowed: List[int] = []
    if trunk:
        trunk_pos = text.lower().find("trunk")
        tail = text[trunk_pos:] if trunk_pos >= 0 else text
        m = re.search(r"(?:allowed\s+)?(?:VLANs?\s+)?(\d+(?:\s*,\s*\d+)+)", tail, re.IGNORECASE)
        if m:
            for part in re.split(r"\s*,\s*", m.group(1)):
                if part.strip().isdigit():
                    allowed.append(int(part.strip()))
    if trunk and allowed:
        return {
            "interfaces": [iface_name],
            "mode": "trunk",
            "trunk_vlans": ",".join(str(v) for v in allowed),
        }
    if not trunk and iface_match:
        # Access: interface mentioned without trunk -> access with VLAN from context (we don't have vlan id here; template will use vlan_id)
        return {"interfaces": [iface_name], "mode": "access"}
    return None


def _resolve_target_hosts(target_hosts: List[str]) -> List[str]:
    """
    Expand semantic group names (all, access, switches, core, distribution) to
    actual device hostnames from the device DB. Fixes intent like "all access
    switches" being parsed as literal ["all", "access", "switches"].
    """
    if not target_hosts or _db is None:
        return list(target_hosts) if target_hosts else []

    devices = _db.get_all_devices()
    all_hostnames = [d.hostname for d in devices if d.hostname]
    role_lower = {d.hostname: (d.role or "").lower() for d in devices if d.hostname}
    name_lower = {d.hostname: (d.hostname or "").lower() for d in devices if d.hostname}

    def is_access(h: str) -> bool:
        r = role_lower.get(h, "")
        n = name_lower.get(h, "")
        return r == "access" or n.startswith("acc_")

    def is_core(h: str) -> bool:
        r = role_lower.get(h, "")
        n = name_lower.get(h, "")
        return r == "core" or n.startswith("core_")

    def is_distribution(h: str) -> bool:
        r = role_lower.get(h, "")
        n = name_lower.get(h, "")
        return r == "distribution" or n.startswith("dist_")

    tokens_lower = [str(x).strip().lower() for x in target_hosts if str(x).strip()]
    if "all" in tokens_lower and ("access" in tokens_lower or "switches" in tokens_lower):
        return [h for h in all_hostnames if is_access(h)]
    if "all" in tokens_lower and ("core" in tokens_lower or "distribution" in tokens_lower or "dist" in tokens_lower):
        return [h for h in all_hostnames if is_core(h) or is_distribution(h)]

    expanded: List[str] = []
    seen: set = set()
    for raw in target_hosts:
        t = str(raw).strip().lower()
        # Normalize punctuation from chat lists: "Acc_Cisco_2," / "Acc_Cisco_2."
        t = re.sub(r"[,\.;:]+$", "", t)
        if not t:
            continue
        if t == "all":
            for h in all_hostnames:
                if h not in seen:
                    seen.add(h)
                    expanded.append(h)
            continue
        if t == "switches":
            for h in all_hostnames:
                if h not in seen:
                    seen.add(h)
                    expanded.append(h)
            continue
        # Do **not** expand bare tokens "access", "core", "dist", "distribution" — phrases like
        # "on access switch 1, 2, and 3" leak the word "access" and would fan out to every access
        # device. Role-wide scope still works via compound tokens above ("all" + "access", etc.).
        # Skip tokens that are known non-hostname words (e.g. from "for Cisco and Huawei")
        if t in _NON_HOSTNAME_WORDS:
            continue
        # Concrete hostname: keep only if it exists in DB
        for d in devices:
            if d.hostname and d.hostname.lower() == t:
                if d.hostname not in seen:
                    seen.add(d.hostname)
                    expanded.append(d.hostname)
                break
        else:
            # Not a known group and not in DB — add only if it looks like a real hostname (not interface/garbage)
            orig = str(raw).strip()
            if _looks_like_device_hostname(orig):
                if orig not in seen:
                    seen.add(orig)
                    expanded.append(orig)
    return expanded if expanded else []


_EXPLICIT_TARGET_REASON = "User-selected target (Target inventory or API)"


def _deduce_target_devices(
    intent_description: str,
    resolved_hosts: List[str],
    *,
    explicit_user_targets: bool = False,
) -> dict[str, Any]:
    """
    Build a backend-only explanation of how target devices were inferred.

    The goal is to keep device selection logic on the backend and return
    a readable deduction trail to preview / create-and-plan responses.

    When ``explicit_user_targets`` is True, hostnames came from the UI / API — do not label them
    as weak "inventory similarity" matches (that text interacts badly with bulk-resolution heuristics).
    """
    lower = (intent_description or "").lower()
    clues: list[str] = []
    if "access" in lower:
        clues.append("role=access")
    if "distribution" in lower or "dist" in lower:
        clues.append("role=distribution")
    if "core" in lower:
        clues.append("role=core")
    if re.search(r"\b(?:switch|switches)\b", lower):
        clues.append("keyword=switches")
    selector_ints = _extract_requested_numbers(intent_description)
    if not selector_ints:
        # Fallback: single number after switch keyword
        selector_match = re.search(
            r"\b(?:access\s+switch(?:es)?|distribution\s+switch(?:es)?|core\s+switch(?:es)?|switch(?:es)?)\s+(\d+)",
            lower,
        )
        selector_ints = [int(selector_match.group(1))] if selector_match else []
    selector_numbers = [str(n) for n in selector_ints]
    if selector_numbers:
        clues.append(f"indices={', '.join(selector_numbers)}")

    if not resolved_hosts:
        return {
            "deduced": [],
            "why": "No target devices could be resolved from the prompt or inventory.",
            "clues": clues,
            "mapping": [],
        }

    why_parts: list[str] = []
    if explicit_user_targets:
        why_parts.append(
            "Target device(s) were chosen explicitly in **Target inventory** or the device picker (not inferred only from chat text)."
        )
    if "keyword=switches" in clues and len(resolved_hosts) > 1:
        why_parts.append("The prompt used a broad switch keyword, so the backend kept the resolved switch set.")
    if any(c.startswith("role=") for c in clues):
        why_parts.append("Role keywords were matched against inventory roles and hostname prefixes.")
    if selector_numbers:
        why_parts.append("Numeric selectors were treated as positional filters within the matched role group.")
    if not why_parts:
        why_parts.append("The backend matched the device names/roles directly from inventory.")

    mapping: list[dict[str, Any]] = []
    lower_hosts = (intent_description or "").lower()
    for host in resolved_hosts:
        if explicit_user_targets:
            mapping.append({"hostname": host, "reason": _EXPLICIT_TARGET_REASON})
            continue
        host_lower = host.lower()
        host_map: list[str] = []
        num = _number_from_hostname(host)
        if "access" in lower_hosts and ("acc" in host_lower or "access" in host_lower):
            host_map.append("matched role=access")
        if "distribution" in lower_hosts and ("dist" in host_lower or "distribution" in host_lower):
            host_map.append("matched role=distribution")
        if "core" in lower_hosts and ("core" in host_lower):
            host_map.append("matched role=core")
        if num is not None and selector_ints and num in selector_ints:
            host_map.append(f"matched numeric selector {num}")
        mapping.append({"hostname": host, "reason": "; ".join(host_map) if host_map else "matched by inventory similarity"})

    return {
        "deduced": resolved_hosts,
        "why": " ".join(why_parts),
        "clues": clues,
        "mapping": mapping,
    }


_SIMILARITY_ONLY_REASON = "matched by inventory similarity"


def _extract_device_like_tokens(intent_description: str) -> List[str]:
    """Best-effort extraction of device-ish tokens from prompt text."""
    text = str(intent_description or "")
    if not text:
        return []
    # Common hostname-like words containing at least one digit (SW1, Dist_Cisco_3, R2, etc.)
    raw = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", text)
    seen = set()
    out: List[str] = []
    for tok in raw:
        t = tok.strip()
        if not t:
            continue
        tl = t.lower()
        if tl in _NON_HOSTNAME_WORDS:
            continue
        if tl not in seen:
            seen.add(tl)
            out.append(t)
    return out


def _build_devices_disambiguation(
    intent_description: str,
    resolved_hosts: List[str],
    db_manager: Any,
) -> Optional[Dict[str, Any]]:
    """
    Build a consistent payload for frontend device confirmation UI.
    """
    if db_manager is None:
        return None
    try:
        all_hosts = sorted(
            list(
                {
                    str(getattr(d, "hostname", "")).strip()
                    for d in (db_manager.get_all_devices() or [])
                    if str(getattr(d, "hostname", "")).strip()
                }
            )
        )
    except Exception:
        all_hosts = []

    mentioned = _extract_device_like_tokens(intent_description)
    ambiguous_candidates: List[str] = []
    missing_tokens: List[str] = []
    for token in mentioned:
        exact = [h for h in all_hosts if h.lower() == token.lower()]
        if len(exact) == 1:
            continue
        if len(exact) > 1:
            ambiguous_candidates.extend(exact)
            continue
        fuzzy = difflib.get_close_matches(token, all_hosts, n=6, cutoff=0.5)
        if len(fuzzy) >= 2:
            ambiguous_candidates.extend(fuzzy)
        elif len(fuzzy) == 0:
            missing_tokens.append(token)

    # If no explicit tokens but scope is unclear, offer full inventory for multi-select.
    if not ambiguous_candidates and not missing_tokens and not resolved_hosts and all_hosts:
        ambiguous_candidates = list(all_hosts)

    ambiguous_unique = []
    seen = set()
    for h in ambiguous_candidates:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            ambiguous_unique.append(h)

    if not ambiguous_unique and not missing_tokens:
        return None

    candidate_devices = ambiguous_unique or resolved_hosts or list(all_hosts)
    return {
        "ambiguous_devices": ambiguous_unique,
        "missing_devices": missing_tokens,
        "ask_user_selection": True,
        "candidate_devices": candidate_devices,
        "question": "Which target device(s) should be used from planned inventory?",
    }


def _db_hostname_lower_to_canonical(db_manager: Any) -> Dict[str, str]:
    """Map lowercase hostname -> canonical hostname from device DB."""
    out: Dict[str, str] = {}
    if db_manager is None:
        return out
    try:
        for d in db_manager.get_all_devices() or []:
            h = getattr(d, "hostname", None)
            if not h:
                continue
            h = str(h).strip()
            if h:
                out[h.lower()] = h
    except Exception:
        pass
    return out


def _all_inventory_hostnames_sorted(db_manager: Any) -> List[str]:
    try:
        return sorted(
            {
                str(getattr(d, "hostname", "")).strip()
                for d in (db_manager.get_all_devices() or [])
                if str(getattr(d, "hostname", "")).strip()
            }
        )
    except Exception:
        return []


def _compact_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _score_device_hint_vs_hostname(hint_raw: str, hostname: str) -> float:
    """Higher = stronger match between user/LLM hint (e.g. SW1, Router 1) and inventory hostname."""
    hint = str(hint_raw or "").strip()
    if not hint:
        return 0.0
    hn = str(hostname or "").strip().lower()
    if not hn:
        return 0.0
    ch = _compact_alnum(hostname)
    ct = _compact_alnum(hint)
    score = 0.0
    hl = hint.lower()
    if hl in hn:
        score += 48.0
    if ct and len(ct) >= 2 and ct in ch:
        score += 42.0
    try:
        if re.search(r"(?:^|[^a-z0-9])" + re.escape(hl) + r"(?:[^a-z0-9]|$)", hn):
            score += 18.0
    except re.error:
        pass
    for part in re.split(r"[\s_\-]+", hl):
        if len(part) >= 2 and part in hn:
            score += 12.0
    ratio = difflib.SequenceMatcher(None, ct, ch).ratio()
    score += ratio * 22.0
    return score


def _device_hint_ranked_matches(hint: str, all_hosts: List[str], max_candidates: int = 24) -> List[str]:
    """Return inventory hostnames that plausibly match the hint, best-first."""
    if not all_hosts or not str(hint or "").strip():
        return []
    scored = [(_score_device_hint_vs_hostname(hint, host), host) for host in all_hosts]
    scored = [(s, h) for s, h in scored if s >= 1.0]
    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    if not scored:
        return []
    top_s = scored[0][0]
    threshold = max(14.0, top_s * 0.42)
    out: List[str] = []
    seen: set[str] = set()
    for s, h in scored:
        if s < threshold:
            break
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
        if len(out) >= max_candidates:
            break
    return out


def _resolve_unknown_device_aliases(
    unknown_tokens: List[str],
    db_manager: Any,
) -> tuple[List[str], Optional[Dict[str, Any]]]:
    """
    Map aliases not equal to inventory hostnames (e.g. SW1) to real devices.

    Returns:
      - auto_mapped: canonical hostnames when each hint resolves to exactly one strong match
      - disambiguation: UI payload when 0 matches (full inventory) or 2+ plausible matches (shortlist)
    """
    cmap = _db_hostname_lower_to_canonical(db_manager)
    all_hosts = sorted(set(cmap.values()))
    uniq: List[str] = []
    seen_t: set[str] = set()
    for t in unknown_tokens:
        k = str(t).strip()
        if k and k.lower() not in seen_t:
            seen_t.add(k.lower())
            uniq.append(k)
    if not uniq or not all_hosts:
        return [], None

    # Ignore L2 English words / interface-looking tokens the LLM put in ``resolved_devices``.
    meaningful = [t for t in uniq if not is_spurious_device_hint_token(t)]
    if not meaningful:
        return [], None

    auto: List[str] = []
    ambiguous_union: List[str] = []
    need_full_inventory = False

    for tok in meaningful:
        cands = _device_hint_ranked_matches(tok, all_hosts)
        if len(cands) == 0:
            need_full_inventory = True
        elif len(cands) == 1:
            h = cands[0]
            if h not in auto:
                auto.append(h)
        else:
            for h in cands:
                if h not in ambiguous_union:
                    ambiguous_union.append(h)

    q_toks = ", ".join(f"`{t}`" for t in meaningful)

    if need_full_inventory:
        return [], {
            "ambiguous_devices": [],
            "missing_devices": list(meaningful),
            "unknown_inventory_aliases": list(meaningful),
            "ask_user_selection": True,
            "candidate_devices": list(all_hosts),
            "disambiguation_kind": "not_in_inventory",
            "question": (
                f"{q_toks} did not match any inventory hostname closely enough. "
                "Select one or more target devices from planned inventory (multi-select), then continue."
            ),
        }

    if ambiguous_union:
        return [], {
            "ambiguous_devices": list(ambiguous_union),
            "missing_devices": list(meaningful),
            "unknown_inventory_aliases": list(meaningful),
            "ask_user_selection": True,
            "candidate_devices": list(ambiguous_union),
            "disambiguation_kind": "ambiguous_hint",
            "question": (
                f"Several devices in planned inventory could match {q_toks}. "
                "Select which hostname(s) you meant (multi-select), then continue."
            ),
        }

    return list(auto), None


def _filter_resolved_hosts_to_planned_inventory(
    resolved: List[str],
    db_manager: Any,
) -> tuple[List[str], List[str]]:
    """
    Keep only hostnames that exist in the device DB. Return (canonical_inventory_hosts, unknown_tokens).
    LLM/user aliases like 'SW1' that are not real inventory hostnames are returned as unknown.
    """
    if not resolved or db_manager is None:
        return list(resolved or []), []
    cmap = _db_hostname_lower_to_canonical(db_manager)
    # No inventory loaded — do not strip LLM placeholders (tests / empty DB).
    if not cmap:
        return list(resolved or []), []
    valid: List[str] = []
    unknown: List[str] = []
    seen: set[str] = set()
    for raw in resolved:
        t = str(raw).strip()
        if not t:
            continue
        canon = cmap.get(t.lower())
        if canon:
            if canon not in seen:
                seen.add(canon)
                valid.append(canon)
        else:
            # LLM often emits L2 words ("trunk", "port") or interface-looking tokens as
            # pseudo-hostnames — never treat those as inventory aliases.
            if is_spurious_device_hint_token(t):
                continue
            unknown.append(t)
    return valid, unknown


def _build_devices_disambiguation_for_unknown_inventory_tokens(
    unknown_tokens: List[str],
    db_manager: Any,
) -> Optional[Dict[str, Any]]:
    """Backward-compatible wrapper: hint-aware shortlist or full inventory."""
    if not unknown_tokens or db_manager is None:
        return None
    _, dd = _resolve_unknown_device_aliases(unknown_tokens, db_manager)
    return dd


def _intent_explicitly_requests_broad_device_scope(intent_description: str) -> bool:
    """
    True when the user clearly asked for a wide device set (all switches, all access, etc.).
    In that case we should not treat a large resolved set as ambiguous.
    """
    t = re.sub(r"\s+", " ", (intent_description or "").strip().lower())
    if not t:
        return False
    if re.search(r"\ball\b", t) and any(
        w in t for w in ("switch", "switches", "device", "devices", "router", "routers", "host", "hosts")
    ):
        return True
    for phrase in (
        "all access",
        "all core",
        "all distribution",
        "every switch",
        "every device",
        "entire network",
        "whole network",
        "network wide",
        "all routers",
    ):
        if phrase in t:
            return True
    if re.search(r"\b(access|distribution|dist|core)\s+(switch|switches)\b", t):
        return True
    return False


def _narrow_resolved_by_hostnames_in_prompt(
    intent_description: str,
    resolved: List[str],
    db_manager: Any,
) -> List[str]:
    """
    If the prompt explicitly names inventory hostnames, keep only those hosts in the resolved set.
    """
    if not intent_description or not resolved or db_manager is None:
        return resolved
    lower = intent_description.lower()
    mentioned = [h for h in resolved if h and str(h).lower() in lower]
    if not mentioned:
        return resolved
    return mentioned


def _markdown_section_2_target_devices(
    intent_description: str,
    resolved: List[str],
    device_deduction: Optional[Dict[str, Any]] = None,
    *,
    explicit_user_targets: bool = False,
) -> str:
    """Rebuild '### 2. Target devices' markdown to match final resolved host list."""
    devices_block = "### 2. Target devices\n"
    devices_block += (
        f"- **Resolved:** {', '.join(resolved) if resolved else '(none — pick specific host(s) in **Target inventory** or answer the device question below)'}. "
        "You can change them in **Target inventory** below."
    )
    dd = device_deduction if device_deduction is not None else _deduce_target_devices(
        intent_description, resolved, explicit_user_targets=explicit_user_targets
    )
    if dd.get("mapping"):
        devices_block += "\n- **Why these devices:**\n"
        for item in dd["mapping"]:
            devices_block += f"  - {item.get('hostname')}: {item.get('reason')}\n"
    return devices_block


def _strip_device_picker_shortlist_after_confirmation(parsed_intent: Optional[Dict[str, Any]]) -> None:
    """
    Once ``resolved_devices`` is populated (UI picker / target_hosts), drop layer-switch
    shortlist keys so pipeline trace and the next preview do not loop on ``select_planned_devices``.
    """
    if not parsed_intent or not isinstance(parsed_intent, dict):
        return
    rd = parsed_intent.get("resolved_devices") or []
    if not isinstance(rd, list) or not any(str(x).strip() for x in rd):
        return
    p = dict(parsed_intent.get("parameters") or {})
    if "_target_device_candidates" not in p and "_target_device_candidate_reason" not in p:
        return
    p.pop("_target_device_candidates", None)
    p.pop("_target_device_candidate_reason", None)
    parsed_intent["parameters"] = p


def _merge_client_authoritative_preview_params(
    parsed_intent: Dict[str, Any],
    req_params: Optional[Dict[str, Any]],
    *,
    db_manager: Any = None,
) -> None:
    """
    Overlay ``req.params`` onto ``parsed_intent['parameters']`` immediately before the
    response is built so wizard / chat answers win over LLM placeholders. Keeps
    ``intent_schema_inspector`` and Playbook Studio fields aligned with user input.
    """
    if not req_params:
        return
    p = dict(parsed_intent.get("parameters") or {})
    for k, v in req_params.items():
        if k in ("secondary_intents", "_secondary_intents"):
            continue
        if v is None:
            continue
        if isinstance(v, str) and not str(v).strip():
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        if isinstance(v, dict) and len(v) == 0:
            continue
        p[k] = v
    parsed_intent["parameters"] = p

    def _planned_only(hosts: List[str]) -> List[str]:
        h = [str(x).strip() for x in hosts if str(x).strip()]
        if not h or db_manager is None:
            return h
        try:
            ok, _bad = _filter_resolved_hosts_to_planned_inventory(h, db_manager)
            return list(ok)
        except Exception:
            return h

    prev_rd = [str(h).strip() for h in (parsed_intent.get("resolved_devices") or []) if str(h).strip()]
    th = req_params.get("target_hosts")
    td = req_params.get("target_devices")

    def _pick_targets(raw_hosts: List[str]) -> List[str]:
        ok_raw = _planned_only(raw_hosts)
        ok_prev = _planned_only(prev_rd)
        if ok_raw:
            return ok_raw
        if ok_prev:
            return ok_prev
        return raw_hosts

    if isinstance(th, list) and any(str(h).strip() for h in th):
        raw = [str(h).strip() for h in th if str(h).strip()]
        chosen = _pick_targets(raw)
        parsed_intent["resolved_devices"] = list(chosen)
        p2 = dict(parsed_intent.get("parameters") or {})
        p2["target_hosts"] = list(chosen)
        p2["target_devices"] = list(chosen)
        p2.pop("_target_device_candidates", None)
        p2.pop("_target_device_candidate_reason", None)
        parsed_intent["parameters"] = p2
    elif isinstance(td, list) and any(str(h).strip() for h in td):
        raw = [str(h).strip() for h in td if str(h).strip()]
        chosen = _pick_targets(raw)
        parsed_intent["resolved_devices"] = list(chosen)
        p2 = dict(parsed_intent.get("parameters") or {})
        p2["target_hosts"] = list(chosen)
        p2["target_devices"] = list(chosen)
        p2.pop("_target_device_candidates", None)
        p2.pop("_target_device_candidate_reason", None)
        parsed_intent["parameters"] = p2
    elif isinstance(td, str) and td.strip():
        raw = [td.strip()]
        chosen = _pick_targets(raw)
        parsed_intent["resolved_devices"] = list(chosen)
        p2 = dict(parsed_intent.get("parameters") or {})
        p2["target_hosts"] = list(chosen)
        p2["target_devices"] = list(chosen)
        p2.pop("_target_device_candidates", None)
        p2.pop("_target_device_candidate_reason", None)
        parsed_intent["parameters"] = p2


def _sync_preview_device_deduction_and_reasoning(
    parsed_intent: Dict[str, Any],
    reasoning: List[str],
    intent_description: str,
    resolved: List[str],
    *,
    explicit_user_targets: bool = False,
) -> None:
    """Align parsed_intent.device_deduction and markdown Section 2 with final resolved_hosts."""
    dd = _deduce_target_devices(intent_description, resolved, explicit_user_targets=explicit_user_targets)
    parsed_intent["device_deduction"] = dd
    for idx, r in enumerate(reasoning):
        if isinstance(r, str) and r.startswith("### 2. Target devices"):
            reasoning[idx] = _markdown_section_2_target_devices(
                intent_description, resolved, dd, explicit_user_targets=explicit_user_targets
            )
            return


def _target_resolution_is_unclear(
    intent_description: str,
    resolved: List[str],
    mapping: List[Dict[str, Any]],
    db_manager: Any,
    *,
    explicit_user_targets: bool = False,
) -> bool:
    """
    True when we should not trust the parser's device list (e.g. LLM returned most of inventory
    with only weak "inventory similarity" explanations and the prompt never scoped devices).

    When ``explicit_user_targets`` is True (UI / API supplied ``target_hosts``), never treat the
    selection as unclear — otherwise two explicit picks both labeled "similarity" hit the 75% rule.
    """
    if explicit_user_targets:
        return False
    if len(resolved) < 2:
        return False
    if _intent_explicitly_requests_broad_device_scope(intent_description):
        return False
    try:
        n_inv = len([d for d in db_manager.get_all_devices() if getattr(d, "hostname", None)])
    except Exception:
        n_inv = 0
    if n_inv and len(resolved) >= max(4, int(0.5 * n_inv)):
        return True
    if not mapping:
        return len(resolved) >= 3
    sim = sum(
        1
        for m in mapping
        if str((m.get("reason") or "").strip()) == _SIMILARITY_ONLY_REASON
    )
    return (sim / max(len(mapping), 1)) >= 0.75


def _build_topo_from_db(hostnames: List[str]) -> dict:
    """Build a minimal topology dict from the device DB for inventory when topology file is empty."""
    if not hostnames or _db is None:
        return {"nodes": {}}
    nodes = {}
    for d in _db.get_all_devices():
        if not d.hostname or (hostnames and d.hostname not in hostnames):
            continue
        vendor = (d.vendor or "").lower().replace("-", "_").replace(" ", "_")
        nodes[d.hostname] = {
            "id": d.hostname,
            "hostname": d.hostname,
            "name": d.hostname,
            "mgmt_ip": d.management_ip or d.hostname,
            "ip": d.management_ip or d.hostname,
            "vendor": vendor,
        }
    return {"nodes": nodes}


def _provider_for_request(provider: Optional[str], model: Optional[str], ollama_base_url: Optional[str]):
    if provider and str(provider).strip():
        from llm.api import _instantiate_runtime_provider

        return _instantiate_runtime_provider(str(provider).strip(), model, ollama_base_url)
    if _agent is not None and hasattr(_agent, "_provider"):
        return getattr(_agent, "_provider")
    if _builder is not None and hasattr(_builder, "_provider"):
        return getattr(_builder, "_provider")
    return None


def _build_intent_parse_context(intent_description: str) -> str:
    if _db is None:
        return ""
    try:
        from rag.context_bundle import ContextBundleBuilder

        bundle = ContextBundleBuilder(db_manager=_db).build_bundle(
            include_inventory=True,
            include_planned_topology=True,
            include_telemetry=False,
            include_sdn=False,
            include_config=False,
            include_deployment=False,
            include_incidents=False,
            include_threats=False,
            include_drift=False,
            include_knowledge=False,
            include_freshness_warnings=True,
            query=intent_description,
        )
        return bundle.to_prompt_text()
    except Exception as exc:
        logger.warning("Could not build intent parse context: %s", exc)
        return ""


def _policy_validate_intent(intent_json: str, user_role: str) -> dict[str, Any]:
    from llm.policy import PolicyEngine

    engine = PolicyEngine(db_manager=_db)
    raw = engine.validate(intent_json, user_role=user_role)
    return json.loads(raw)


def _resolve_backend_intent_request(req, user: dict) -> tuple["PlanAnsibleRequest", dict[str, Any], dict[str, Any]]:
    from llm.intent import IntentParser, IntentType, ParsedIntent

    parser = IntentParser(
        provider=_provider_for_request(req.provider, req.model, req.ollama_base_url),
        db_manager=_db,
    )
    parsed = ParsedIntent.model_validate_json(
        parser.parse(
            req.intent_description,
            topology_context=_build_intent_parse_context(req.intent_description),
        )
    )

    if req.feature:
        parsed.feature = str(req.feature).strip()
    if req.vendor:
        parsed.vendor = normalize_vendor(req.vendor)
    if req.params is not None:
        # Merge so request can override specific keys without wiping LLM output (e.g. empty {} from frontend)
        base = dict(parsed.parameters or {})
        for k, v in (req.params or {}).items():
            base[k] = v
        _coerce_params_for_validation(base)
        parsed.parameters = base

    params_after = dict(parsed.parameters or {})
    explicit_targets: Optional[List[str]] = None
    if params_after.get("target_hosts") is not None:
        th = params_after["target_hosts"]
        explicit_targets = th if isinstance(th, list) else [th] if th else []
    elif params_after.get("target_devices") is not None:
        td = params_after["target_devices"]
        explicit_targets = td if isinstance(td, list) else [td] if td else []

    # Prefer explicit target list from API or params over LLM-resolved bulk inventory.
    # If the client sent only `['all']` but params carry a concrete list (Studio overlay), prefer params.
    if req.target_hosts:
        parsed.scope.device_hostnames = list(req.target_hosts)
        parsed.scope.all_devices = False
        th_in = list(req.target_hosts)
        if (
            len(th_in) == 1
            and str(th_in[0]).strip().lower() == "all"
            and explicit_targets
        ):
            parsed.resolved_devices = _resolve_target_hosts(explicit_targets)
        else:
            parsed.resolved_devices = _resolve_target_hosts(th_in)
    elif explicit_targets:
        parsed.resolved_devices = _resolve_target_hosts(explicit_targets)
    elif parsed.resolved_devices:
        parsed.resolved_devices = _resolve_target_hosts(parsed.resolved_devices)
    else:
        parsed.resolved_devices = []

    parsed.resolved_devices = _narrow_resolved_by_hostnames_in_prompt(
        req.intent_description, list(parsed.resolved_devices or []), _db
    )
    _nr, _ = _apply_numeric_subset_fallback(
        req.intent_description, list(parsed.resolved_devices or [])
    )
    parsed.resolved_devices = _nr

    if _db is not None and parsed.resolved_devices and _db_hostname_lower_to_canonical(_db):
        _ok_hosts, _bad_hosts = _filter_resolved_hosts_to_planned_inventory(parsed.resolved_devices, _db)
        _ok_hosts = list(_ok_hosts)
        if _bad_hosts:
            _auto_m, _dd = _resolve_unknown_device_aliases(_bad_hosts, _db)
            for h in _auto_m:
                if h not in _ok_hosts:
                    _ok_hosts.append(h)
            if _dd:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "message": "Target device hint is ambiguous or not in inventory; select hostname(s) in the UI.",
                        "missing_fields": ["target_hosts"],
                        "questions": [
                            {
                                "field": "target_hosts",
                                "question": str(_dd.get("question") or "Select target device(s) from planned inventory."),
                            }
                        ],
                        "devices_disambiguation": _dd,
                    },
                )
        parsed.resolved_devices = _ok_hosts

    # User/studio explicitly chose targets — do not reject as "unclear bulk" (same idea as preview + tree).
    user_explicit_targets_create = bool(req.target_hosts) or bool(explicit_targets)

    switch_disambiguation_create = _switch_role_disambiguation(req.intent_description)
    _map_create = _deduce_target_devices(
        req.intent_description, parsed.resolved_devices, explicit_user_targets=user_explicit_targets_create
    ).get("mapping") or []
    if (
        not switch_disambiguation_create
        and parsed.resolved_devices
        and not user_explicit_targets_create
        and _target_resolution_is_unclear(
            req.intent_description,
            parsed.resolved_devices,
            _map_create,
            _db,
            explicit_user_targets=user_explicit_targets_create,
        )
    ):
        raise HTTPException(
            422,
            detail={
                "message": (
                    "Target devices are not clear from your prompt. Specify hostnames "
                    "(e.g. Acc_Cisco_1) or a clear scope (e.g. all access switches), "
                    "using Target inventory or your message."
                ),
                "requires_target_clarification": True,
            },
        )

    device_deduction = _deduce_target_devices(
        req.intent_description, parsed.resolved_devices, explicit_user_targets=user_explicit_targets_create
    )
    resolved_vendor = ""
    if parsed.resolved_devices and _db:
        vendors_in_targets: set[str] = set()
        for host in parsed.resolved_devices:
            dev = _db.get_device_by_hostname(host)
            if dev and getattr(dev, "vendor", None):
                v = normalize_vendor(dev.vendor)
                if v:
                    vendors_in_targets.add(v)
        if len(vendors_in_targets) == 1:
            resolved_vendor = next(iter(vendors_in_targets))
    if resolved_vendor:
        parsed.vendor = resolved_vendor
    elif not parsed.vendor:
        parsed.vendor = infer_vendor_hint(parsed.resolved_devices, _db)

    # Parser may set requires_disambiguation for "pick target hostnames" while resolved_devices
    # is still empty. After merging API target_hosts / params.target_hosts, hostnames are
    # authoritative — clear stale flags so create-and-plan does not 422 in a loop.
    if parsed.resolved_devices and any(str(h).strip() for h in parsed.resolved_devices):
        parsed.requires_disambiguation = False
        parsed.disambiguation_message = ""
        _p = dict(parsed.parameters or {})
        _p.pop("_target_device_candidates", None)
        _p.pop("_target_device_candidate_reason", None)
        parsed.parameters = _p

    if parsed.requires_disambiguation:
        raise HTTPException(422, parsed.disambiguation_message or "Intent requires disambiguation before planning.")
    if parsed.intent_type not in {IntentType.CONFIGURE, IntentType.DEPLOY}:
        raise HTTPException(422, f"Intent '{parsed.intent_type.value}' is not a configuration change.")
    if not parsed.feature:
        raise HTTPException(422, "Intent parser did not resolve a configuration feature.")
    if not parsed.resolved_devices:
        raise HTTPException(422, "Intent parser did not resolve any target devices.")

    # VLAN DB-only prompts: drop LLM-invented interfaces / configure_interface so planning
    # does not cross-check bogus Host:Interface hints against target inventory.
    _coerce_for_plan = {
        "action": parsed.action or "",
        "feature": parsed.feature or "",
        "parameters": dict(parsed.parameters or {}),
    }
    _user_iface_params = bool(
        req.params is not None
        and (
            (req.params.get("interfaces") or req.params.get("interfaces_config"))
        )
    )
    if coerce_vlan_database_only_intent(
        _coerce_for_plan,
        req.intent_description or "",
        user_supplied_interface_params=_user_iface_params,
    ):
        parsed.action = _coerce_for_plan["action"]
        parsed.parameters = _coerce_for_plan["parameters"]
        if getattr(parsed, "requires_resolution", False):
            parsed.requires_resolution = False

    policy = _policy_validate_intent(
        parsed.model_dump_json(),
        user_role=str(user.get("role") or "operator"),
    )
    if policy.get("approved") is not True:
        violations = policy.get("violations") or []
        raise HTTPException(
            422,
            detail={
                "message": "Parsed intent was rejected by policy validation.",
                "violations": [v.get("message", str(v)) for v in violations],
                "blast_radius": policy.get("blast_radius"),
                "intent": parsed.model_dump(),
                "policy": policy,
            },
        )

    plan_params_merged = dict(parsed.parameters or {})
    if str(parsed.feature or "").strip().lower() == "vlan":
        merge_extracted_vlan_params_into_flat_params(plan_params_merged, req.intent_description or "")
    plan_req = PlanAnsibleRequest(
        intent_description=req.intent_description,
        target_hosts=parsed.resolved_devices,
        vendor=normalize_vendor(parsed.vendor or infer_vendor_hint(parsed.resolved_devices, _db)),
        feature=parsed.feature,
        params=plan_params_merged,
        max_interfaces=getattr(req, "max_interfaces", 5),
        requires_resolution=getattr(parsed, "requires_resolution", False),
        auto_refine=getattr(req, "auto_refine", False),
        max_refinement_attempts=getattr(req, "max_refinement_attempts", 1),
        verify_with_dry_run=getattr(req, "verify_with_dry_run", False),
        provider=req.provider,
        model=req.model,
        ollama_base_url=req.ollama_base_url,
        safe_mode=req.safe_mode if getattr(req, "safe_mode", None) is not None else _get_safe_mode_default(),
    )
    parsed_dict = parsed.model_dump()
    params_dict = dict(parsed_dict.get("parameters") or {})
    if str(parsed_dict.get("feature") or "").strip().lower() == "vlan":
        merge_extracted_vlan_params_into_flat_params(params_dict, req.intent_description or "")
    raw_secondary = params_dict.pop("_secondary_intents", None)
    parsed_dict["secondary_intents"] = _normalize_secondary_intents(raw_secondary)
    parsed_dict["parameters"] = params_dict
    strip_stale_secondary_params_after_primary_authoritative(
        parsed_dict,
        req.params,
    )
    parsed_dict["device_deduction"] = device_deduction
    return plan_req, parsed_dict, policy


async def _ensure_planning_context(
    *,
    target_hosts: List[str],
    vendor: str,
    intent_description: str,
    feature: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if _db is None or not hasattr(_db, "get_all_devices"):
        return _minimal_planning_context(
            target_hosts,
            vendor=vendor,
            intent_description=intent_description,
            feature=feature,
        ), {"reconciled": False, "issues": ["database_inventory_unavailable"]}

    planning_context = build_scoped_planning_context(
        _db,
        target_hosts,
        vendor_filter=vendor or None,
        query=intent_description,
        feature=feature,
    )
    readiness = planning_context_readiness(planning_context)
    if readiness.get("ready"):
        return planning_context, {"reconciled": False, "issues": []}

    reconciliation = await run_in_threadpool(reconcile_inventory_state, _db)
    planning_context = build_scoped_planning_context(
        _db,
        target_hosts,
        vendor_filter=vendor or None,
        query=intent_description,
        feature=feature,
    )
    post_reconcile = planning_context_readiness(planning_context)
    if not post_reconcile.get("ready"):
        issues = post_reconcile.get("issues", [])
        logger.error(
            "Planning context not ready after reconciliation: %s",
            issues,
            extra={"reconciliation": reconciliation},
        )
        raise HTTPException(
            422,
            detail={
                "message": "Inventory reconciliation completed but planning context is still incomplete.",
                "issues": issues,
                "reconciliation": reconciliation,
            },
        )
    return planning_context, {
        "reconciled": True,
        "issues": readiness.get("issues", []),
        "reconciliation": reconciliation,
    }


def _playbook_inject_serial(playbook_yaml: str, serial: int = 1) -> str:
    """Inject serial: N into the first play so hosts run one-at-a-time. Helps paramiko with Cisco."""
    if not playbook_yaml or serial < 1:
        return playbook_yaml
    if "serial:" in playbook_yaml and re.search(r"^\s*serial:\s*\d+", playbook_yaml, re.MULTILINE):
        return playbook_yaml
    # Insert after first play's gather_facts or hosts so first play runs serially
    return re.sub(
        r"^(\s*gather_facts:\s*\w+\s*)$",
        rf"\1\n  serial: {serial}",
        playbook_yaml,
        count=1,
        flags=re.MULTILINE,
    )


def _playbook_hosts_to_db_only(playbook_yaml: str, allowed_hosts: List[str]) -> str:
    """Rewrite the first play's hosts: line to only include hostnames in allowed_hosts
    (e.g. only DB-registered devices), so non-DB names like Acc_Huawei_1 never appear in the run."""
    if not playbook_yaml or not allowed_hosts:
        return playbook_yaml
    allowed = set(h.strip() for h in allowed_hosts if str(h).strip())
    if not allowed:
        return playbook_yaml
    # Match first play's hosts: value (e.g. "host1,host2" or host1,host2)
    def _repl(m: re.Match) -> str:
        prefix = m.group(1)  # indentation and "hosts:"
        raw = m.group(2).strip().strip('"\'')
        if not raw or raw.lower() in ("all",):
            return m.group(0)
        parts = [p.strip() for p in re.split(r"[,]+", raw) if p.strip()]
        kept = [p for p in parts if p in allowed]
        if not kept:
            return m.group(0)
        # If playbook targets a single host and it is allowed, keep it (don't expand to full job list)
        if len(parts) == 1 and parts[0] in allowed:
            return m.group(0)
        return prefix + ",".join(kept) + "\n"
    return re.sub(r"^(\s*hosts:\s*)(.+)$", _repl, playbook_yaml, count=1, flags=re.MULTILINE)


def _filter_inventory_to_db_hosts(inventory: dict[str, Any]) -> None:
    """
    Remove any host that is not in the device DB. Ensures only registered devices
    (e.g. Acc_Huawei_3) appear; crawler-only names (e.g. Acc_Huawei_1) are dropped.
    """
    if _db is None:
        return
    all_group = inventory.get("all") or {}
    hosts = (all_group.get("hosts") or {}) if isinstance(all_group, dict) else {}
    if not hosts:
        return
    to_remove: set[str] = set()
    for hostname in hosts:
        try:
            if not _db.get_device_by_hostname(hostname):
                to_remove.add(hostname)
        except Exception:
            to_remove.add(hostname)
    if not to_remove:
        return
    for hostname in to_remove:
        hosts.pop(hostname, None)
    children = (all_group.get("children") or {}) if isinstance(all_group, dict) else {}
    for group_data in children.values():
        if isinstance(group_data, dict):
            group_hosts = group_data.get("hosts")
            if isinstance(group_hosts, dict):
                for hostname in to_remove:
                    group_hosts.pop(hostname, None)


def _deduplicate_inventory_by_ip(inventory: dict[str, Any]) -> None:
    """
    Keep one host per management IP; prefer the hostname registered for that IP
    in the device DB (e.g. NetBox Acc_Huawei_3 over crawler Acc_Huawei_1).
    Removes duplicate hostnames from inventory["all"]["hosts"] and from all group hosts.
    """
    all_group = inventory.get("all") or {}
    hosts = (all_group.get("hosts") or {}) if isinstance(all_group, dict) else {}
    if not hosts or _db is None:
        return
    by_ip: dict[str, list[str]] = {}
    for hostname, vars in hosts.items():
        if not isinstance(vars, dict):
            continue
        ip = str(vars.get("ansible_host") or "").strip() or hostname
        by_ip.setdefault(ip, []).append(hostname)
    to_remove: set[str] = set()
    for ip, hostnames in by_ip.items():
        if len(hostnames) <= 1:
            continue
        # Prefer the hostname that the DB has for this management IP (NetBox/canonical)
        keep: str | None = None
        try:
            dev = _db.get_device_by_management_ip(ip)
            if dev and dev.hostname and dev.hostname.strip() in hostnames:
                keep = dev.hostname.strip()
        except Exception:
            pass
        if not keep:
            keep = hostnames[0]
        to_remove.update(h for h in hostnames if h != keep)
    if not to_remove:
        return
    for hostname in to_remove:
        hosts.pop(hostname, None)
    children = (all_group.get("children") or {}) if isinstance(all_group, dict) else {}
    for group_data in children.values():
        if isinstance(group_data, dict):
            group_hosts = group_data.get("hosts")
            if isinstance(group_hosts, dict):
                for hostname in to_remove:
                    group_hosts.pop(hostname, None)


def _build_inventory_for_hosts(
    target_hosts: List[str],
    planning_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from ansible.inventory import build_inventory

    topology_override = None
    if planning_context:
        topology_override = topology_override_from_planning_context(planning_context)
    if topology_override is None or not (topology_override.get("nodes") or {}):
        topology_override = _build_topo_from_db(target_hosts)
    inventory = build_inventory(target_hosts=target_hosts or None, topology_override=topology_override)
    _inject_device_credentials_into_inventory(inventory)
    _deduplicate_inventory_by_ip(inventory)
    _filter_inventory_to_db_hosts(inventory)
    return inventory


def _inject_device_credentials_into_inventory(inventory: dict[str, Any]) -> None:
    """
    Override ansible_user and ansible_password for each host with values from the
    local devices table (ssh_user, ssh_password) when set. This makes Ansible
    check/execute use device credentials from the NetBox controller registration.
    """
    if _db is None:
        return
    all_group = inventory.get("all") or {}
    hosts = all_group.get("hosts") or {}
    if not isinstance(hosts, dict):
        return
    for hostname, host_vars in hosts.items():
        if not isinstance(host_vars, dict):
            continue
        try:
            dev = _db.get_device_by_hostname(hostname)
            if dev and (dev.ssh_user or dev.ssh_password):
                if dev.ssh_user:
                    host_vars["ansible_user"] = (dev.ssh_user or "").strip()
                if dev.ssh_password:
                    host_vars["ansible_password"] = (dev.ssh_password or "").strip()
        except Exception:
            pass


def _minimal_planning_context(
    target_hosts: List[str],
    *,
    vendor: str,
    intent_description: str,
    feature: str,
) -> dict[str, Any]:
    return {
        "target_hosts": list(target_hosts or []),
        "feature": str(feature or ""),
        "query": str(intent_description or ""),
        "inventory": {
            "source": "request",
            "source_detail": "Using request-scoped fallback planning context.",
            "freshness": {
                "source": "request",
                "scope": "planned_inventory",
                "freshness_class": "live",
                "confidence": 0.5,
            },
            "total_devices": len(target_hosts or []),
            "interfaces_included": 0,
        },
        "planned_topology": {
            "source": "request",
            "source_detail": "No planned topology context available in fallback mode.",
            "freshness": {
                "source": "request",
                "scope": "planned_topology",
                "freshness_class": "live",
                "confidence": 0.5,
            },
            "link_count": 0,
        },
        "freshness_warnings": [],
        "fallback_evidence": [{"domain": "planned_inventory", "source": "request", "detail": "fallback"}],
        "devices": [
            {
                "hostname": host,
                "management_ip": host,
                "vendor": normalize_vendor(vendor),
                "role": "",
                "site": "",
                "model": None,
                "status": None,
                "serial": None,
                "interfaces": [],
                "neighbors": [],
                "inventory_source": "request",
                "planned_topology_source": "request",
                "live_neighbor_source": None,
            }
            for host in (target_hosts or [])
        ],
    }


def _require_pipeline():
    if _pipeline is None:
        raise HTTPException(503, "Deployment pipeline not initialized")
    return _pipeline


# ── Auth dependency (reuse existing JWT auth) ────────────────────────────────
try:
    from auth.auth import get_current_user
    from auth.rbac import require_role
except ImportError:
    async def get_current_user():   # type: ignore
        return {"username": "anonymous", "role": "admin"}
    def require_role(*_roles: str, **_kwargs: Any):    # type: ignore
        return get_current_user


# ── Request / Response models ────────────────────────────────────────────────

class PlanAnsibleRequest(BaseModel):
    intent_description: str
    target_hosts:       List[str] = []
    vendor:             str = ""
    feature:            str = ""
    params:             Dict[str, Any] = {}
    max_interfaces:     int = 5
    # Default off / single attempt: iterative LLM refinement is slow; Studio can re-generate if needed.
    auto_refine:        bool = False
    max_refinement_attempts: int = 1
    verify_with_dry_run: bool = False
    provider:           Optional[str] = None  # "gemini" | "ollama" for playbook LLM
    model:              Optional[str] = None
    ollama_base_url:    Optional[str] = None
    requires_resolution: bool = False  # interface intent with no interfaces → controller must resolve or abort
    safe_mode:          Optional[bool] = None  # when None, server uses ANSIBLE_SAFE_MODE_DEFAULT (default true)


class PreviewIntentRequest(BaseModel):
    """Request for intent preview (categories + RAG reference templates) before full create-and-plan."""
    intent_description: str
    target_hosts:       List[str] = []
    vendor:             str = ""
    feature:            str = ""
    provider:           Optional[str] = None
    model:              Optional[str] = None
    ollama_base_url:    Optional[str] = None
    params:             Optional[Dict[str, Any]] = None  # e.g. interfaces_to_shutdown after user selects from menu
    safe_mode:          Optional[bool] = None  # when None, server uses ANSIBLE_SAFE_MODE_DEFAULT (default true)
    # When True, skip vector/embedding RAG (fast path for Ansible AI chat). Category+vendor disk scan still fills references.
    skip_rag:           bool = False


def _coalesce_explicit_preview_target_hosts(req: PreviewIntentRequest) -> List[str]:
    """
    Prefer API / UI target lists over LLM ``resolved_devices``.

    Top-level ``target_hosts`` wins when non-empty; otherwise use ``params.target_hosts``
    or ``params.target_devices`` (wizard flows sometimes send only ``params``).
    """
    raw: List[Any] = []
    top = list(req.target_hosts or [])
    if top:
        raw = top
    elif req.params:
        th = req.params.get("target_hosts")
        td = req.params.get("target_devices")
        if th is not None:
            raw = th if isinstance(th, list) else [th]
        elif td is not None:
            raw = td if isinstance(td, list) else [td]
    return [str(h).strip() for h in raw if str(h).strip()]


class CheckResult(BaseModel):
    rc:      int
    changed: bool
    diff:    str
    summary: str
    stderr:  str = ""  # When rc != 0, often contains connection/auth/task errors


class ExecuteResult(BaseModel):
    rc:      int
    success: bool
    changed: bool
    summary: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/plan-ansible", summary="LLM generates Ansible playbook for a job")
async def plan_ansible(
    job_id:  int,
    req:     PlanAnsibleRequest,
    user:    dict = Depends(get_current_user),
):
    """
    Ask the LLM to generate an Ansible playbook for this job and store it.
    The playbook now goes through a bounded refine-and-verify loop before being stored.
    Device dry-run contact remains optional and disabled unless requested.
    """
    builder = _require_builder()
    resolved_hosts = _resolve_target_hosts(req.target_hosts)
    if _db is not None and resolved_hosts and _db_hostname_lower_to_canonical(_db):
        _rh_ok, _rh_bad = _filter_resolved_hosts_to_planned_inventory(resolved_hosts, _db)
        _rh_ok = list(_rh_ok)
        if _rh_bad:
            _auto_m, _dd = _resolve_unknown_device_aliases(_rh_bad, _db)
            for h in _auto_m:
                if h not in _rh_ok:
                    _rh_ok.append(h)
            if _dd:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "message": "Target device hint is ambiguous or not in inventory; select hostname(s) in the UI.",
                        "missing_fields": ["target_hosts"],
                        "questions": [
                            {
                                "field": "target_hosts",
                                "question": str(_dd.get("question") or "Select target device(s) from planned inventory."),
                            }
                        ],
                        "devices_disambiguation": _dd,
                        "target_hosts": [],
                    },
                )
        resolved_hosts = _rh_ok
    effective_vendor = normalize_vendor(req.vendor or infer_vendor_hint(resolved_hosts, _db))
    plan_params = dict(req.params or {})
    if str(req.feature or "").strip().lower() == "vlan":
        merge_extracted_vlan_params_into_flat_params(plan_params, req.intent_description or "")
        _vlan_user_if = bool(plan_params.get("interfaces") or plan_params.get("interfaces_config"))
        _vlan_coerce = {"action": "", "feature": "vlan", "parameters": plan_params}
        if coerce_vlan_database_only_intent(
            _vlan_coerce,
            req.intent_description or "",
            user_supplied_interface_params=_vlan_user_if,
        ):
            plan_params = _vlan_coerce["parameters"]
    # Frontend heuristic may send feature "general" for "Shut down Gi0/1 …" — infer interface from params
    _params_hint = dict(plan_params)
    planning_feature = str(req.feature or "").strip().lower()
    if planning_feature in ("", "general"):
        if _params_hint.get("interfaces_to_shutdown") or (
            isinstance(_params_hint.get("interfaces"), list) and len(_params_hint.get("interfaces") or []) > 0
        ):
            planning_feature = "interface"
    planning_context, planning_meta = await _ensure_planning_context(
        target_hosts=resolved_hosts,
        vendor=effective_vendor,
        intent_description=req.intent_description,
        feature=planning_feature or req.feature,
    )

    # Optional agent RAG for LLM playbook path. Reference-first **vlan** / **interface** skip
    # vector retrieval here (deferred only if we need LLM fallback). ``build_full_context`` is
    # **off by default for all features** (ANSIBLE_PLAN_AGENT_FULL_CONTEXT=1 to enable).
    rag_context = ""
    kb_snippets: list[str] = []
    plan_feature_for_rag = str(req.feature or "").strip().lower()
    skip_upfront_vector_rag = plan_feature_for_rag in ("vlan", "interface")
    include_full_ctx = _plan_ansible_include_agent_full_context()
    include_vector_rag = _plan_ansible_include_vector_rag()
    if _agent is not None and not skip_upfront_vector_rag:
        if include_full_ctx:
            try:
                rag_context = _agent._context.build_full_context(
                    device_hostnames=resolved_hosts,
                    query=req.intent_description,
                )
            except Exception as exc:
                logger.warning("Could not fetch RAG context: %s", exc)

        if include_vector_rag:
            try:
                if hasattr(_agent, "_retriever") and _agent._retriever:
                    query = req.intent_description
                    if req.feature:
                        query = f"{query} Ansible playbook reference feature {req.feature}"
                    if effective_vendor:
                        query = f"{query} vendor {effective_vendor}"
                    kb_snippets = [
                        r["text"]
                        for r in _agent._retriever.retrieve(
                            query,
                            k=3,
                            vendor_filter=effective_vendor or None,
                            doc_type_filter="ansible_reference",
                        )
                    ]
                    if not kb_snippets:
                        kb_snippets = [
                            r["text"]
                            for r in _agent._retriever.retrieve(query, k=2)
                        ]
                    kb_snippets = kb_snippets[:2]
            except Exception as exc:
                logger.debug("Vector-RAG retrieval skipped: %s", exc)
    intent = {
        "description":   req.intent_description,
        "target_hosts":  resolved_hosts,
        "resolved_devices": resolved_hosts,
        "vendor":        effective_vendor,
        "feature":       req.feature,
        "params":        dict(plan_params),
        "max_interfaces": req.max_interfaces,
        "planning_context": planning_context,
        "device_inventory_context": planning_context_to_prompt_text(planning_context),
    }

    # Tree engine expects a parsed_intent-like structure.
    # In plan-ansible we don't have the full LLM parse yet, so we construct
    # the minimum keys needed by `intent_tree_engine` (Phase 1: target_devices
    # + interface; and Phase 2 best-effort for static_route leaves).
    parsed_intent = {
        "action": None,
        "feature": req.feature,
        "parameters": dict(plan_params),
        "device_deduction": {},
    }

    # Tree engine (Phase 1): root-first enforcement before schema validation.
    # If device scope is unclear, force missing target_devices so we ask/abort
    # rather than executing against a bulk inventory guess.
    tree_res = None
    devices_disambiguation: Optional[Dict[str, Any]] = None
    plan_explicit_targets = bool(req.target_hosts)
    try:
        tree_res = evaluate_intent_tree(
            intent_description=req.intent_description,
            parsed_intent=parsed_intent,
            resolved_hosts=list(resolved_hosts or []),
            db_manager=_db,
            explicit_targets_from_request=plan_explicit_targets,
        )
        if tree_res.needs_target_devices:
            resolved_hosts = []
            intent["target_hosts"] = []
            intent["resolved_devices"] = []
            if isinstance(parsed_intent, dict):
                parsed_intent["resolved_devices"] = []
            devices_disambiguation = _build_devices_disambiguation(
                req.intent_description, resolved_hosts, _db
            )
    except Exception as exc:
        logger.debug("intent_tree_engine evaluation failed: %s", exc)

    if tree_res and tree_res.needs_target_devices:
        raise HTTPException(
            422,
            detail={
                "message": "Target devices are unclear. Please select specific devices.",
                "missing_fields": ["target_devices"],
                "questions": [
                    {
                        "field": "target_devices",
                        "question": "Which device(s) should this change apply to?",
                    }
                ],
                "devices_disambiguation": devices_disambiguation,
            },
        )

    # Fail fast for tree-required leaves surfaced as UI questions.
    if (
        tree_res
        and not tree_res.needs_target_devices
        and tree_res.params_disambiguation
        and tree_res.params_disambiguation.get("fields")
    ):
        fields = list(tree_res.params_disambiguation.get("fields") or [])
        missing_fields = [f.get("field") for f in fields if isinstance(f, dict) and f.get("field")]
        questions = [
            {"field": f.get("field"), "question": f.get("question")}
            for f in fields
            if isinstance(f, dict) and f.get("field") and f.get("question")
        ]
        if missing_fields and questions:
            raise HTTPException(
                422,
                detail={
                    "message": "Missing required parameters. Please provide them via preview (params) or clarify your intent.",
                    "missing_fields": missing_fields,
                    "questions": questions,
                },
            )

    # Intent schema validation: missing required params -> resolve or 422 (per implementation plan)
    resolution_trace: List[Dict[str, Any]] = []
    # For interface-changing intents, the frontend sends only `feature="interface"` (no explicit action).
    # Infer shutdown vs configure from the prompt so schema + type validation run against the correct
    # intent schema (shutdown_interface vs configure_interface).
    inferred_interface_action: Optional[str] = None
    feature_lower_req = str(req.feature or "").strip().lower().replace("-", "_")
    if feature_lower_req == "interface":
        # Normalize hyphen variants so "Admin‑down" / "Admin—down" still matches.
        t_raw = str(req.intent_description or "").strip().lower()
        t_raw = (
            t_raw.replace("–", "-")  # en-dash
            .replace("—", "-")  # em-dash
            .replace("−", "-")  # minus sign
        )
        t = re.sub(r"\s+", " ", t_raw)

        shutdown_markers = (
            "admin-down",
            "admin down",
            "shutdown",
            "shut down",
            "disable",
            "turn off",
            "turn on",
            "enable",
            "bring up",
            "administratively up",
            "admin-up",
            "admin up",
            "bring down",
            "administratively down",
            "admin-down all interfaces",
            "admin down all interfaces",
            "turn on all interfaces",
            "turn on all the interfaces",
            "enable all interfaces",
            "bring up all interfaces",
        )

        # Prefer explicit params hints from the frontend when available.
        params_hint = plan_params if isinstance(plan_params, dict) else {}
        interfaces_to_shutdown = params_hint.get("interfaces_to_shutdown")
        shutdown_from_params = bool(
            interfaces_to_shutdown
            or params_hint.get("interfaces_shutdown_use_nettopo") is True
        )

        shutdown_from_prompt = any(m in t for m in shutdown_markers)
        if shutdown_from_prompt or shutdown_from_params:
            inferred_interface_action = "shutdown_interface"
        else:
            inferred_interface_action = "configure_interface"

    # Type validation (intent_validation.validate_types) relies on `action_or_feature`.
    # For interface intents, prefer the prompt-inferred action so "turn on/enable"
    # validates against shutdown_interface (no access_vlan requirement), even if
    # the frontend/LLM set req.action to configure_interface.
    action_or_feature = inferred_interface_action or getattr(req, "action", None) or req.feature
    schema = get_schema_for_parsed_intent(
        {"feature": req.feature, "action": inferred_interface_action or getattr(req, "action", None)}
    )

    # "All interfaces" shutdown: type schema for shutdown_interface requires a non-empty
    # interfaces_to_shutdown array during validation. We provide a minimal placeholder
    # and let the later all-interfaces expansion convert it into nettopo-driven shutdown.
    wants_all_ifaces = intent_requests_all_interfaces(req.intent_description or "")
    if (
        wants_all_ifaces
        and feature_lower_req == "interface"
        and inferred_interface_action == "shutdown_interface"
        and isinstance(intent.get("params"), dict)
    ):
        p = intent["params"]
        if not p.get("interfaces_to_shutdown"):
            p["interfaces_to_shutdown"] = ["ALL"]
            intent["params"] = p
    if schema:
        # Conditional interface leaves for configure_interface (shared VLANs + ip/subnet).
        # Raise 422 with "questions" so the UI can prompt users instead of surfacing
        # raw JSON-Schema conditional validation errors.
        schema_action_key = (schema.get("action") or "").strip().lower().replace("-", "_")
        if schema_action_key == "configure_interface":
            p = intent.get("params") or {}
            interfaces = p.get("interfaces") or []
            conditional_missing: List[Dict[str, str]] = []
            if isinstance(interfaces, list) and interfaces:
                default_mode = str(p.get("default_mode") or "").strip().lower() or None
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
                            missing_trunk_vlans = True
                    ip_address = iface.get("ip_address")
                    if ip_address not in (None, ""):
                        ip_addrs.add(str(ip_address))
                        if iface.get("subnet_mask") in (None, ""):
                            missing_subnet_mask = True

                if len(modes) == 1:
                    only_mode = next(iter(modes))
                    if only_mode == "access" and missing_access_vlan:
                        conditional_missing.append({
                            "field": "access_vlan",
                            "question": "Which access VLAN should be applied to all selected interfaces?",
                        })
                    if only_mode == "trunk" and missing_trunk_vlans:
                        conditional_missing.append({
                            "field": "trunk_vlans",
                            "question": (
                                "Which VLAN IDs should be allowed on this trunk? "
                                "(Pick from the list when using chat preview — options follow intent and planned context.)"
                            ),
                        })

                if missing_subnet_mask and len(ip_addrs) == 1:
                    conditional_missing.append({
                        "field": "subnet_mask",
                        "question": "Subnet mask for the provided interface IP address(es)?",
                    })

            if conditional_missing:
                raise HTTPException(
                    422,
                    detail={
                        "message": "Missing required interface parameters for configure_interface.",
                        "missing_fields": [m["field"] for m in conditional_missing],
                        "questions": [{"field": m["field"], "question": m["question"]} for m in conditional_missing],
                    },
                )

        missing_fields, validation_errors = validate_completeness(intent, schema, action_or_feature=action_or_feature)
        if validation_errors:
            raise HTTPException(
                422,
                detail={
                    "message": "Parameter validation failed.",
                    "validation_errors": validation_errors,
                },
            )
        if missing_fields:
            context = {
                "planning_context": planning_context,
                "resolve_interfaces_fn": resolve_interfaces,
                "inventory_lookup_fn": _make_inventory_lookup_fn(),
            }
            safe_mode = req.safe_mode if getattr(req, "safe_mode", None) is not None else _get_safe_mode_default()
            resolved_intent, ask_user_fields, resolution_trace = resolve_missing_fields(
                intent, schema, context=context, safe_mode=safe_mode
            )
            if ask_user_fields:
                raise HTTPException(
                    422,
                    detail={
                        "message": "Missing required parameters. Please provide them via preview (params) or clarify your intent.",
                        "missing_fields": [f["field"] for f in ask_user_fields],
                        "questions": [{"field": f["field"], "question": f["question"]} for f in ask_user_fields],
                    },
                )
            intent = resolved_intent

    # Template-first: if we have a reference playbook for this feature/vendor, fill it from intent only (no LLM rewrite)
    orchestration = None
    feature_lower = (req.feature or "").strip().lower()
    is_vlan = feature_lower == "vlan"
    is_interface = feature_lower == "interface"

    # Interface feature: topology resolution only when params still lack interfaces (ignore stale requires_resolution).
    if is_interface:
        p = intent.get("params") or {}
        if not interface_intent_params_satisfied(p):
            logger.info("No interfaces provided by user")
            logger.info("Attempting topology resolution")
            resolved_names = resolve_interfaces(intent, planning_context)
            if not resolved_names:
                logger.error("No topology data found → abort")
                raise HTTPException(
                    422,
                    detail={
                        "message": "No interfaces specified and topology could not resolve any. Please specify which interfaces (e.g. Gi0/1, Gi0/2) or ensure device inventory has interface data.",
                        "feature": "interface",
                        "target_hosts": resolved_hosts,
                    },
                )
            intent.setdefault("params", {})["interfaces_to_shutdown"] = resolved_names

    vendors = vendors_from_planning_context(planning_context)
    # planning_context can have empty vendors when: (1) devices=[] (no inventory for hostnames),
    # (2) every device has vendor unset (bundle/DB didn't set it). Fall back to DB lookup by hostname.
    if not vendors and resolved_hosts and _db is not None:
        vendors = vendors_from_db(resolved_hosts, _db)
    if not vendors and effective_vendor:
        vendors = [effective_vendor]

    if is_vlan:
        # When LLM did not fill interfaces, try to extract from prompt (e.g. "configure Gi1/0/1 as trunk allowed 10,20,67")
        if user_prompt_requests_vlan_interface_binding(req.intent_description or ""):
            if not (intent.get("params") or {}).get("interfaces"):
                extracted = _extract_vlan_interface_from_description(req.intent_description)
                if extracted:
                    intent.setdefault("params", {}).update(extracted)
        valid, errors = validate_vlan_intent(intent)
        if not valid and errors:
            raise HTTPException(
                422,
                detail={
                    "message": "VLAN intent failed schema validation. Please fix the intent or provide clarification.",
                    "issues": errors,
                },
            )

    # "All interfaces" shutdown: replace LLM-guessed ranges with inventory intersection; skip per-iface validation spam.
    p_inv = intent.get("params") or {}
    schema_action_plan = (schema.get("action") or "").strip().lower().replace("-", "_") if schema else ""
    all_if_shutdown_expanded = False
    if (
        is_interface
        and schema_action_plan in ("shutdown_interface", "configure_interface")
        and intent_requests_all_interfaces(req.intent_description or "")
        and resolved_hosts
    ):
        try:
            intent.setdefault("params", {})
            if schema_action_plan == "shutdown_interface":
                # Shutdown-all: force per-host shutdown-only names from `nettopo_interfaces` (mgmt excluded).
                intent["params"].pop("interfaces_to_shutdown", None)
                intent["params"].pop("interfaces", None)
                intent["params"].pop("interfaces_config", None)
                intent["params"].pop("interfaces_config_use_nettopo", None)
                intent["params"]["interfaces_shutdown_use_nettopo"] = True

                # Admin intent: "turn on/enable/bring up" should set enabled=true.
                prompt_l = str(req.intent_description or "").lower()
                admin_up = any(
                    x in prompt_l
                    for x in (
                        "turn on",
                        "enable",
                        "bring up",
                        "administratively up",
                        "admin-up",
                        "admin up",
                        "no shutdown",
                    )
                )
                admin_down = any(
                    x in prompt_l
                    for x in (
                        "turn off",
                        "disable",
                        "shut down",
                        "shutdown",
                        "bring down",
                        "administratively down",
                        "admin-down",
                        "admin down",
                    )
                )
                intent["params"]["interfaces_admin_enabled"] = bool(admin_up and not admin_down)
            else:
                # Configure-all: keep a single default interface object (mode/VLAN/IP/...) so schema passes,
                # but replace interface names at template runtime via nettopo_interfaces.
                default_obj: Dict[str, Any] = {}
                raw_ifaces = intent["params"].get("interfaces")
                if isinstance(raw_ifaces, list) and raw_ifaces:
                    first = raw_ifaces[0]
                    if isinstance(first, dict):
                        default_obj = dict(first)
                elif isinstance(intent["params"].get("interfaces_config"), list) and intent["params"].get("interfaces_config"):
                    first = intent["params"]["interfaces_config"][0]
                    if isinstance(first, dict):
                        default_obj = dict(first)

                if not default_obj:
                    default_obj = {"name": "ALL", "mode": "access"}
                default_obj["name"] = str(default_obj.get("name") or "ALL")

                intent["params"].pop("interfaces_to_shutdown", None)
                intent["params"].pop("interfaces_config", None)
                intent["params"].pop("interfaces_shutdown_use_nettopo", None)
                intent["params"]["interfaces_config_use_nettopo"] = True
                intent["params"]["interfaces"] = [default_obj]
            p_inv = intent.get("params") or {}
            all_if_shutdown_expanded = True
        except Exception as exc:
            logger.debug("plan_ansible all-interfaces expansion skipped: %s", exc)

    p_inv = intent.get("params") or {}
    _plan_secondary_intents: List[Dict[str, Any]] = list(intent.get("secondary_intents") or [])
    _plan_iface_override = explicit_interface_names_from_request_params(req.params) if req.params else []

    # Cross-check user-supplied interface names against planned inventory before template fill.
    # ``collect_user_interface_names_from_params`` also reads ``_secondary_intents`` inside ``p_inv``.
    # Top-level ``intent.secondary_intents`` must be passed explicitly (same as preview).
    # When the client sent explicit ``interfaces`` / ``interfaces_config`` on the request, validate
    # only those names (ignore LLM-only tokens still merged into ``p_inv`` or secondaries).
    if (
        should_validate_interface_names_against_inventory(
            schema_action_plan,
            feature_lower,
            p_inv,
            secondary_intents=_plan_secondary_intents or None,
            interface_names_override=_plan_iface_override or None,
        )
        and not all_if_shutdown_expanded
    ):
        vr = validate_interface_params_against_context(
            p_inv,
            resolved_hosts,
            planning_context,
            secondary_intents=_plan_secondary_intents or None,
            interface_names_override=_plan_iface_override or None,
        )
        if not vr.ok:
            iface_dis = _interfaces_disambiguation_from_planning(
                planning_context,
                resolved_hosts,
                question="Select valid interface(s) from planned inventory.",
            )
            raise HTTPException(
                422,
                detail={
                    "message": "One or more interface names are not present in planned inventory for the target device(s).",
                    "invalid_entities": vr.invalid_entities,
                    "validation_notice": vr.validation_notice,
                    "params_need_context_fix": True,
                    "interfaces_disambiguation": iface_dis,
                    "target_hosts": resolved_hosts,
                },
            )

    # Static route: when next_hop is an IP, validate it against known interface subnets from planning context.
    if schema_action_plan == "configure_static_route" and resolved_hosts:
        vr_nh = validate_static_route_next_hop_ip_against_context(p_inv, resolved_hosts, planning_context)
        if not vr_nh.ok:
            raise HTTPException(
                422,
                detail={
                    "message": "Provided next-hop IP is not consistent with planned network context.",
                    "invalid_entities": vr_nh.invalid_entities,
                    "validation_notice": vr_nh.validation_notice,
                    "params_need_context_fix": True,
                    "target_hosts": resolved_hosts,
                },
            )

    template_filled = fill_playbook_from_reference(
        feature=req.feature or "",
        vendors=vendors,
        intent=intent,
        planning_context=planning_context,
    )
    # For VLAN, never use LLM — only reference template. Retry fill with minimal defaults if first attempt returned None.
    if is_vlan and template_filled is None and resolved_hosts:
        fallback_intent = dict(intent)
        fallback_intent["target_hosts"] = list(resolved_hosts)
        fallback_intent.setdefault("params", {})
        fallback_intent["params"].setdefault("vlan_id", "200")
        fallback_intent["params"].setdefault("vlan_name", "TEST")
        template_filled = fill_playbook_from_reference(
            feature=req.feature or "",
            vendors=vendors,
            intent=fallback_intent,
            planning_context=planning_context,
        )
    if (is_vlan or is_interface) and template_filled is None:
        logger.error("VLAN template fill returned None (vendors=%s); refusing to fall back to LLM", vendors)
        raise HTTPException(
            422,
            detail={
                "message": f"{'VLAN' if is_vlan else 'Interface'} playbook must be generated from the reference template. Template fill failed or no reference path found.",
                "feature": "vlan" if is_vlan else "interface",
                "vendors": vendors,
                "target_hosts": resolved_hosts,
            },
        )

    if template_filled is not None:
        # Use reference template as-is; only values (hosts, vlan_id, etc.) were filled. Validate with multi_vendor
        # when playbook has multiple vendor modules so step10 allows cisco/juniper/huawei.
        validation_intent = dict(intent)
        if len(vendors) > 1:
            validation_intent["vendor"] = "multi_vendor"
        validation = await _validate_playbook_candidate(
            playbook_yaml=template_filled,
            intent=validation_intent,
            job_id=job_id,
            verify_with_dry_run=req.verify_with_dry_run,
        )
        if validation.get("passed", False):
            orchestration = {
                "success": True,
                "playbook": template_filled,
                "attempts_used": 1,
                "max_attempts": 1,
                "final_validation": validation,
                "history": [{"attempt": 1, "passed": True, "issues": []}],
                "source": "reference_template",
            }
        elif is_vlan or is_interface:
            # Default safety: VLAN/interface should use reference only (no LLM fallback).
            # Exception: configure_interface with VLAN/IP semantics on Cisco+Juniper
            # can’t be safely expressed by deterministic reference templates (they mainly
            # toggle enabled/shutdown). In that case, allow LLM/config-engine generation.
            allow_interface_llm_fallback = False
            if (
                is_interface
                and schema_action_plan == "configure_interface"
                and any(v in vendors for v in ("cisco_ios", "juniper_junos"))
            ):
                p = intent.get("params") or {}
                interfaces = p.get("interfaces") or []
                if isinstance(interfaces, list):
                    for iface in interfaces:
                        if not isinstance(iface, dict):
                            continue
                        if iface.get("access_vlan") is not None:
                            allow_interface_llm_fallback = True
                            break
                        if iface.get("trunk_vlans") is not None:
                            allow_interface_llm_fallback = True
                            break
                        if iface.get("allowed_vlans") is not None:
                            allow_interface_llm_fallback = True
                            break
                        if iface.get("ip_address") is not None:
                            allow_interface_llm_fallback = True
                            break

            if allow_interface_llm_fallback:
                logger.warning(
                    "Reference interface validation failed; allowing LLM fallback for Cisco/Juniper VLAN/IP semantics. issues=%s",
                    validation.get("issues"),
                )
            else:
                logger.error("VLAN/interface reference template validation failed: %s", validation.get("issues"))
                raise HTTPException(
                    422,
                    detail={
                        "message": f"{'VLAN' if is_vlan else 'Interface'} reference playbook failed validation. Fix template or inventory; LLM fallback is disabled for this feature.",
                        "validation_issues": validation.get("issues", []),
                        "errors": validation.get("errors", []),
                    },
                )

    # For VLAN/interface always use reference-style single playbook (no LLM).
    use_bundle = bool(getattr(builder, "supports_role_bundle", lambda *_: False)(req.feature))
    if is_vlan or is_interface:
        use_bundle = False
    if orchestration is None and _agent is not None and skip_upfront_vector_rag:
        # Deferred RAG: reference path missed and we need LLM (e.g. interface VLAN/IP fallback).
        if include_full_ctx and not rag_context:
            try:
                rag_context = _agent._context.build_full_context(
                    device_hostnames=resolved_hosts,
                    query=req.intent_description,
                )
            except Exception as exc:
                logger.warning("Could not fetch RAG context (deferred): %s", exc)
        if include_vector_rag and not kb_snippets:
            try:
                if hasattr(_agent, "_retriever") and _agent._retriever:
                    query = req.intent_description
                    if req.feature:
                        query = f"{query} Ansible playbook reference feature {req.feature}"
                    if effective_vendor:
                        query = f"{query} vendor {effective_vendor}"
                    kb_snippets = [
                        r["text"]
                        for r in _agent._retriever.retrieve(
                            query,
                            k=3,
                            vendor_filter=effective_vendor or None,
                            doc_type_filter="ansible_reference",
                        )
                    ]
                    if not kb_snippets:
                        kb_snippets = [
                            r["text"]
                            for r in _agent._retriever.retrieve(query, k=2)
                        ]
                    kb_snippets = kb_snippets[:2]
            except Exception as exc:
                logger.debug("Deferred vector-RAG retrieval skipped: %s", exc)

    if orchestration is None:
        try:
            if use_bundle:
                orchestration = await _build_bundle_with_refinement(
                    job_id=job_id,
                    intent=intent,
                    rag_context=rag_context,
                    kb_snippets=kb_snippets,
                    auto_refine=req.auto_refine,
                    max_attempts=req.max_refinement_attempts,
                    verify_with_dry_run=req.verify_with_dry_run,
                    provider=req.provider,
                    model=req.model,
                    ollama_base_url=req.ollama_base_url,
                )
            else:
                orchestration = await _build_playbook_with_refinement(
                    job_id=job_id,
                    intent=intent,
                    rag_context=rag_context,
                    kb_snippets=kb_snippets,
                    auto_refine=req.auto_refine,
                    max_attempts=req.max_refinement_attempts,
                    verify_with_dry_run=req.verify_with_dry_run,
                    provider=req.provider,
                    model=req.model,
                    ollama_base_url=req.ollama_base_url,
                )
        except HTTPException:
            raise
        except Exception as exc:
            detail = str(exc).strip() or repr(exc)
            logger.exception(
                "Playbook build failed job=%d type=%s detail=%s",
                job_id,
                type(exc).__name__,
                detail,
            )
            raise HTTPException(422, f"Playbook generation failed: {detail}")

    if not orchestration.get("success", False):
        detail = {
            "message": "Playbook generation failed after bounded refinement attempts",
            "job_id": job_id,
            "attempts_used": orchestration.get("attempts_used", 0),
            "max_attempts": orchestration.get("max_attempts", req.max_refinement_attempts),
            "escalation_reason": orchestration.get("escalation_reason", "validation_failed"),
            "issues": orchestration.get("final_validation", {}).get("issues", []),
            "refinement_history": orchestration.get("history", []),
        }
        logger.error(
            "Playbook generation failed job=%d: %s (issues=%s)",
            job_id,
            detail.get("escalation_reason"),
            detail.get("issues"),
        )
        raise HTTPException(
            422,
            detail=detail,
        )

    playbook_yaml = str(orchestration.get("playbook") or "")
    bundle_payload = orchestration.get("bundle")

    if _db is not None:
        try:
            if bundle_payload:
                bundle_manifest = dict(bundle_payload)
                bundle_manifest.setdefault("manifest", {})
                bundle_manifest["manifest"].update(
                    {
                        "job_id": job_id,
                        "target_hosts": resolved_hosts,
                        "provider": req.provider or _default_manifest_llm_provider(),
                        "model": req.model or _default_manifest_llm_model(),
                        "artifact_type": "bundle",
                        "inventory_reconciled": bool(planning_meta.get("reconciled")),
                    }
                )
                _persist_job_bundle(job_id=job_id, bundle=bundle_manifest, playbook_yaml=playbook_yaml)
                _append_playbook_version(
                    _db,
                    job_id,
                    playbook_yaml,
                    notes="Original bundle generation",
                    bundle=bundle_manifest,
                )
                bundle_payload = bundle_manifest
            else:
                # Playbook-only (e.g. from reference template): write to generated/job_XX/playbook.yml so files API and UI can read it
                entrypoint_rel = f"playbooks/generated/job_{job_id}/playbook.yml"
                entrypoint_virtual = f"generated/job_{job_id}/playbook.yml"
                try:
                    disk_path = _safe_path(entrypoint_rel)
                    disk_path.parent.mkdir(parents=True, exist_ok=True)
                    disk_path.write_text(playbook_yaml, encoding="utf-8")
                except HTTPException:
                    raise
                except Exception as exc:
                    logger.warning("Could not write playbook to workspace: %s", exc)
                with _db._connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET ansible_playbook = ?, ansible_entrypoint_path = ?, stage = 'plan', "
                        "status = 'pending' WHERE id = ?",
                        (playbook_yaml, entrypoint_virtual, job_id),
                    )
                _append_playbook_version(
                    _db,
                    job_id,
                    playbook_yaml,
                    notes="Original playbook generation",
                )
        except Exception as exc:
            logger.warning("Could not persist playbook to DB: %s", exc)

    # Response entrypoint_path: virtual path so frontend can GET /files/generated/job_XX/playbook.yml
    if bundle_payload:
        response_entrypoint = (bundle_payload or {}).get("entrypoint_path")
    else:
        response_entrypoint = f"generated/job_{job_id}/playbook.yml"

    logger.info("Playbook generated for job=%d by user=%s", job_id, user.get("username"))
    return {
        "job_id":   job_id,
        "status":   "playbook_generated",
        "playbook": playbook_yaml,
        "artifact_type": "bundle" if bundle_payload else "playbook",
        "entrypoint_path": response_entrypoint,
        "files": (bundle_payload or {}).get("files", []),
        "manifest": {
            **((bundle_payload or {}).get("manifest", {})),
            "inventory_reconciled": bool(planning_meta.get("reconciled")),
            "resolution_trace": resolution_trace,
        },
        "refinement": {
            "auto_refine": req.auto_refine,
            "verify_with_dry_run": req.verify_with_dry_run,
            "attempts_used": orchestration.get("attempts_used", 1),
            "max_attempts": orchestration.get("max_attempts", req.max_refinement_attempts),
            "history": orchestration.get("history", []),
            "validation": orchestration.get("final_validation", {}),
        },
    }


# ── Phase 6: Multi-Candidate Playbook Generation (UC14) ──────────────────

@router.post(
    "/jobs/{job_id}/plan-candidates",
    summary="LLM generates multiple candidate playbooks with different approaches",
)
async def plan_ansible_candidates(
    job_id:  int,
    req:     PlanAnsibleRequest,
    n:       int = 3,
    user:    dict = Depends(get_current_user),
):
    """
    Generate *n* candidate playbooks (conservative, balanced, aggressive)
    for the same intent.  The operator picks the best one.
    """
    builder = _require_builder()

    rag_context = ""
    kb_snippets: list[str] = []
    if _agent is not None:
        try:
            rag_context = _agent._context.build_full_context()
        except Exception:
            pass
        try:
            if hasattr(_agent, "_retriever") and _agent._retriever:
                kb_snippets = [
                    r["text"]
                    for r in _agent._retriever.retrieve(req.intent_description, k=3)
                ]
        except Exception:
            pass

    resolved_hosts = _resolve_target_hosts(req.target_hosts)
    intent = {
        "description":   req.intent_description,
        "target_hosts":  resolved_hosts,
        "vendor":        req.vendor,
        "feature":       req.feature,
        "params":        req.params,
        "max_interfaces": req.max_interfaces,
    }

    try:
        candidates = await builder.build_candidates(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            n=min(n, 5),
            run_id=str(job_id),
        )
    except Exception as exc:
        raise HTTPException(422, f"Multi-candidate generation failed: {exc}")

    logger.info("Generated %d candidates for job=%d", len(candidates), job_id)
    return {
        "job_id": job_id,
        "candidates": candidates,
    }


@router.post(
    "/jobs/{job_id}/select-candidate",
    summary="Select one of the generated candidate playbooks",
)
async def select_candidate(
    job_id: int,
    candidate_index: int = 0,
    playbook_yaml: str = "",
    user:  dict = Depends(get_current_user),
):
    """
    After reviewing candidates, operator selects one by index.
    The selected playbook is stored against the job.
    """
    if not playbook_yaml:
        raise HTTPException(400, "playbook_yaml is required")

    if _db is not None:
        with _db._connect() as conn:
            conn.execute(
                "UPDATE jobs SET ansible_playbook = ?, stage = 'plan', "
                "status = 'pending' WHERE id = ?",
                (playbook_yaml, job_id),
            )

    return {
        "job_id": job_id,
        "selected_index": candidate_index,
        "status": "candidate_selected",
    }


@router.post(
    "/jobs/{job_id}/check",
    response_model=CheckResult,
    summary="Dry-run the playbook (--check --diff) — no device changes",
)
async def check_playbook(
    job_id: int,
    user:   dict = Depends(get_current_user),
):
    """
    Run ansible-playbook --check --diff against the stored playbook.
    Returns per-host diff for review.  No changes are applied; any authenticated user may run check.
    """
    artifact = _load_job_artifact(job_id)
    from ansible.runner import run_playbook

    try:
        planning_context = (
            build_scoped_planning_context(
                _db,
                artifact["target_hosts"] or [],
                query=artifact["playbook_yaml"],
            )
            if _db is not None and hasattr(_db, "get_all_devices")
            else _minimal_planning_context(
                artifact["target_hosts"] or [],
                vendor="",
                intent_description=artifact["playbook_yaml"],
                feature="",
            )
        )
    except Exception:
        planning_context = _minimal_planning_context(
            artifact["target_hosts"] or [],
            vendor="",
            intent_description=artifact["playbook_yaml"],
            feature="",
        )
    inventory = _build_inventory_for_hosts(artifact["target_hosts"], planning_context)
    inv_hosts = list((inventory.get("all") or {}).get("hosts") or {})
    playbook_content = _playbook_hosts_to_db_only(artifact["playbook_yaml"], inv_hosts)
    result = await run_in_threadpool(
        run_playbook,
        inventory=inventory,
        extra_vars={},
        check_mode=True,
        job_id=job_id,
        playbook_content=playbook_content,
        playbook_path=artifact["entrypoint_path"] or None,
        project_files=artifact["project_files"],
    )

    logger.info("Check run job=%d rc=%d user=%s", job_id, result.rc, user.get("username"))
    return CheckResult(
        rc=result.rc,
        changed=result.changed,
        diff=result.stdout[-10000:] if result.stdout else "",
        summary=result.summary,
        stderr=(result.stderr or "")[-8000:],  # So UI can show why dry-run failed
    )


@router.post("/jobs/{job_id}/approve", summary="Operator approves job for execution")
async def approve_job(
    job_id: int,
    user:   dict = Depends(require_role("admin")),
):
    """
    Set job status to 'approved'.  Required before /execute will run.
    Only admins may approve.
    """
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'approved', stage = 'approve' WHERE id = ?",
            (job_id,),
        )

    logger.info("Job %d approved by %s", job_id, user.get("username"))
    return {"job_id": job_id, "status": "approved", "approved_by": user.get("username")}


@router.post(
    "/jobs/{job_id}/execute",
    response_model=ExecuteResult,
    summary="Execute the approved Ansible playbook",
)
async def execute_playbook(
    job_id: int,
    user:   dict = Depends(require_role("admin")),
):
    """
    Execute the stored playbook.  Job must be in 'approved' state.

    Execution flow:
      1. Route (Ansible native / CLI / SSH fallback)
      2. Pre-snapshot
      3. ansible-runner run (or SSH fallback)
      4. Post-change verification via Context-Injection RAG
      5. Commit or rollback
    """
    # ── Gate: must be approved ─────────────────────────────────────────────
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT status, ansible_playbook, ansible_artifact_manifest, generated_config, rollback_config "
            "FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    # Execution gate:
    # - Normal flow: job must be 'approved'
    # - To support repeated deploys without forcing re-approve after a prior run,
    #   allow 'success'/'failed' jobs as well (they were executable only after approval).
    if row["status"] not in ("approved", "success", "failed"):
        raise HTTPException(
            403,
            f"Job {job_id} must be 'approved' before execution. Current status: {row['status']}"
        )
    if row["status"] in ("success", "failed") and row.get("stage") not in ("commit", "rollback"):
        raise HTTPException(
            403,
            f"Job {job_id} is not in an executable stage. Current status: {row['status']} stage: {row.get('stage')}",
        )

    artifact = _load_job_artifact(job_id)
    if not artifact["playbook_yaml"]:
        raise HTTPException(
            422,
            "No Ansible playbook stored for this job. Run /plan-ansible first."
        )

    from ansible.runner import run_playbook

    try:
        planning_context = (
            build_scoped_planning_context(
                _db,
                artifact["target_hosts"] or [],
                query=artifact["playbook_yaml"],
            )
            if _db is not None and hasattr(_db, "get_all_devices")
            else _minimal_planning_context(
                artifact["target_hosts"] or [],
                vendor="",
                intent_description=artifact["playbook_yaml"],
                feature="",
            )
        )
    except Exception:
        planning_context = _minimal_planning_context(
            artifact["target_hosts"] or [],
            vendor="",
            intent_description=artifact["playbook_yaml"],
            feature="",
        )
    inventory = _build_inventory_for_hosts(artifact["target_hosts"] or [], planning_context)
    inv_hosts = list((inventory.get("all") or {}).get("hosts") or {})
    playbook_content = _playbook_hosts_to_db_only(artifact["playbook_yaml"], inv_hosts)

    # Mark running
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', stage = 'apply' WHERE id = ?",
            (job_id,)
        )

    result = await run_in_threadpool(
        run_playbook,
        inventory=inventory,
        extra_vars={},
        check_mode=False,
        job_id=job_id,
        playbook_content=playbook_content,
        playbook_path=artifact["entrypoint_path"] or None,
        project_files=artifact["project_files"],
    )

    final_status = "success" if result.success else "failed"
    with _db._connect() as conn:
        conn.execute(
            # Keep job in an "approved" state so it can be re-executed
            # without forcing the user to approve again.
            "UPDATE jobs SET status = ?, stage = ?, error_message = ? WHERE id = ?",
            (
                "approved",
                "commit" if result.success else "rollback",
                (result.summary if not result.success else None),
                job_id,
            ),
        )

    logger.info(
        "Execution complete job=%d rc=%d changed=%s user=%s",
        job_id, result.rc, result.changed, user.get("username"),
    )
    return ExecuteResult(
        rc=result.rc,
        success=result.success,
        changed=result.changed,
        summary=result.summary,
    )


@router.get("/jobs/{job_id}/result", summary="Get last run result for a job")
async def get_result(
    job_id: int,
    user:   dict = Depends(get_current_user),
):
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT id, status, stage, error_message, ansible_playbook, "
            "ansible_artifact_manifest, ansible_entrypoint_path "
            "FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
        logs = conn.execute(
            "SELECT stage, status, details, created_at FROM job_steps "
            "WHERE job_id = ? ORDER BY id",
            (job_id,)
        ).fetchall()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    out = {
        "job_id":  job_id,
        "status":  row["status"],
        "stage":   row["stage"],
        "error":   row["error_message"],
        "has_playbook": bool(row["ansible_playbook"] or row["ansible_artifact_manifest"]),
        "entrypoint_path": row["ansible_entrypoint_path"],
        "artifact_type": "bundle" if row["ansible_artifact_manifest"] else "playbook",
        "steps":   [dict(r) for r in logs],
    }
    # Include bundle file list so chat/file UI can show all generated files
    if row["ansible_artifact_manifest"]:
        try:
            bundle = json.loads(row["ansible_artifact_manifest"])
            out["files"] = [{"path": f.get("path", "")} for f in (bundle.get("files") or []) if f.get("path")]
        except Exception:
            out["files"] = []
    else:
        out["files"] = []
    return out


@router.get("/jobs/{job_id}/inventory", summary="Get effective inventory for a job (hosts, IPs, credential source)")
async def get_job_inventory(
    job_id: int,
    user: dict = Depends(get_current_user),
):
    """
    Returns the list of hosts that will be targeted by this job, with management IP
    and credential source. No secrets are returned.
    """
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    artifact = _load_job_artifact(job_id)
    target_hosts = artifact.get("target_hosts") or []
    if not target_hosts:
        return {
            "job_id": job_id,
            "hosts": [],
            "credentials_note": "No target hosts in this job.",
        }

    planning_context = None
    if _db is not None:
        planning_context = await run_in_threadpool(
            build_scoped_planning_context,
            _db,
            target_hosts,
        )
    if not planning_context or not (planning_context.get("devices")):
        planning_context = _minimal_planning_context(target_hosts, vendor="")
    inventory = _build_inventory_for_hosts(target_hosts, planning_context)
    all_hosts = (inventory.get("all") or {}).get("hosts") or {}
    if isinstance(all_hosts, dict):
        host_list = []
        for hostname, vars in all_hosts.items():
            if not isinstance(vars, dict):
                vars = {}
            host_list.append({
                "hostname": hostname,
                "ansible_host": vars.get("ansible_host") or hostname,
                "vendor": vars.get("nettopo_vendor") or "",
                "role": vars.get("nettopo_role") or "",
                "credentials_source": _inventory_credentials_source(vars),
            })
        host_list.sort(key=lambda h: (h["hostname"],))
    else:
        host_list = []

    # Admin-only: show SSH username/password from local DB (devices.ssh_user, devices.ssh_password).
    # Blank values mean: device not in DB, or device row has NULL/empty ssh_user/ssh_password (e.g.
    # device was synced from NetBox, or credentials were never set in the NetBox controller form).
    if host_list and _db is not None and (user.get("role") == "admin"):
        for h in host_list:
            h["ssh_user"] = ""
            h["ssh_password"] = ""
            try:
                dev = _db.get_device_by_hostname(h["hostname"])
                if dev:
                    h["ssh_user"] = (dev.ssh_user or "").strip()
                    h["ssh_password"] = (dev.ssh_password or "").strip()
                    if dev.ssh_user or dev.ssh_password:
                        h["credentials_source"] = "device_inventory (overrides vault)"
            except Exception:
                pass

    credentials_note = (
        "SSH username/password shown here (admin only) are from the local device inventory (devices table). "
        "Playbooks use Ansible Vault (vault/net_creds.yml) at run time."
    )
    return {
        "job_id": job_id,
        "hosts": host_list,
        "credentials_note": credentials_note,
    }


def _inventory_credentials_source(host_vars: dict) -> str:
    """Return a short, non-secret description of which vault vars this host uses."""
    vendor = (host_vars.get("nettopo_vendor") or "").replace(".", "_")
    if vendor:
        return f"vault (vendor: {vendor})"
    return "vault (shared)"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_job_playbook(job_id: int) -> tuple[str, list[str]]:
    """Load playbook YAML and target hosts from DB. Raises HTTPException on error."""
    artifact = _load_job_artifact(job_id)
    return artifact["playbook_yaml"], artifact["target_hosts"]


def _normalize_refinement_errors(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for issue in issues:
        normalized.append(
            {
                "line": issue.get("line"),
                "message": issue.get("message", ""),
                "rule": f"{issue.get('phase', 'validation')}::{issue.get('rule', 'unknown')}",
            }
        )
    return normalized


def _format_issue_summary(issue: dict[str, Any]) -> str:
    prefix = f"[{issue.get('phase', 'validation')}/{issue.get('rule', 'unknown')}]"
    line = issue.get("line")
    if line is not None:
        prefix += f" line {line}"
    return f"{prefix} {issue.get('message', '')}".strip()


def _dry_run_is_refinable(result_text: str) -> bool:
    normalized = (result_text or "").lower()
    non_refinable_markers = (
        "unreachable",
        "authentication failed",
        "permission denied",
        "timed out",
        "connection refused",
        "no route to host",
    )
    return not any(marker in normalized for marker in non_refinable_markers)


async def _validate_playbook_candidate(
    playbook_yaml: str,
    intent: dict,
    job_id: int,
    verify_with_dry_run: bool,
) -> dict[str, Any]:
    builder = _require_builder()
    validation = builder.validate_playbook(intent=intent, playbook_yaml=playbook_yaml)
    issues = [dict(issue) for issue in validation.get("issues") or []]

    requested_hosts = [str(h).strip() for h in (intent.get("target_hosts") or []) if str(h).strip()]
    planning_context = dict(intent.get("planning_context") or {})
    inventory = _build_inventory_for_hosts(requested_hosts, planning_context or None)
    inventory_hosts = ((inventory.get("all") or {}).get("hosts") or {}) if isinstance(inventory, dict) else {}
    missing_hosts = [host for host in requested_hosts if host not in inventory_hosts]
    if missing_hosts:
        issues.append(
            {
                "phase": "inventory",
                "rule": "missing_inventory_hosts",
                "message": f"Requested targets are not present in inventory/topology: {', '.join(missing_hosts)}",
                "refinable": False,
            }
        )

    if verify_with_dry_run and not missing_hosts:
        from ansible.runner import run_playbook

        dry_run_result = await run_in_threadpool(
            run_playbook,
            playbook_content=playbook_yaml,
            inventory=inventory,
            extra_vars={},
            check_mode=True,
            job_id=job_id,
        )
        if not dry_run_result.success:
            result_text = "\n".join(part for part in [dry_run_result.stderr, dry_run_result.stdout] if part).strip()
            issues.append(
                {
                    "phase": "dry_run",
                    "rule": "ansible_check_failed",
                    "message": result_text or dry_run_result.summary or "Ansible dry-run failed",
                    "refinable": _dry_run_is_refinable(result_text),
                }
            )

    for issue in issues:
        issue.setdefault("refinable", True)

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "errors": [_format_issue_summary(issue) for issue in issues],
    }


async def _validate_bundle_candidate(
    *,
    bundle: AnsibleArtifactBundle,
    intent: dict,
    job_id: int,
    verify_with_dry_run: bool,
) -> dict[str, Any]:
    builder = _require_builder()
    validation = builder.validate_bundle(intent=intent, bundle=bundle)
    issues = [dict(issue) for issue in validation.get("issues") or []]

    requested_hosts = [str(h).strip() for h in (intent.get("target_hosts") or []) if str(h).strip()]
    planning_context = dict(intent.get("planning_context") or {})
    inventory = _build_inventory_for_hosts(requested_hosts, planning_context or None)
    inventory_hosts = ((inventory.get("all") or {}).get("hosts") or {}) if isinstance(inventory, dict) else {}
    missing_hosts = [host for host in requested_hosts if host not in inventory_hosts]
    if missing_hosts:
        issues.append(
            {
                "phase": "inventory",
                "rule": "missing_inventory_hosts",
                "message": f"Requested targets are not present in inventory/topology: {', '.join(missing_hosts)}",
                "refinable": False,
            }
        )

    if verify_with_dry_run and not missing_hosts:
        from ansible.runner import run_playbook

        dry_run_result = await run_in_threadpool(
            run_playbook,
            inventory=inventory,
            extra_vars={},
            check_mode=True,
            job_id=job_id,
            playbook_path=bundle.entrypoint_path,
            project_files=[artifact.to_dict() for artifact in bundle.files],
        )
        if not dry_run_result.success:
            result_text = "\n".join(part for part in [dry_run_result.stderr, dry_run_result.stdout] if part).strip()
            issues.append(
                {
                    "phase": "dry_run",
                    "rule": "ansible_check_failed",
                    "message": result_text or dry_run_result.summary or "Ansible dry-run failed",
                    "refinable": _dry_run_is_refinable(result_text),
                }
            )

    for issue in issues:
        issue.setdefault("refinable", True)

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "errors": [_format_issue_summary(issue) for issue in issues],
    }


def _builder_for_request(
    provider: Optional[str],
    model: Optional[str],
    ollama_base_url: Optional[str],
):
    """Return a PlaybookBuilder for this request: request-scoped if provider/model set, else global."""
    if provider and str(provider).strip().lower() in ("gemini", "ollama"):
        from llm.api import _instantiate_runtime_provider
        from ansible.builder import PlaybookBuilder
        p = _instantiate_runtime_provider(
            str(provider).strip().lower(),
            model and str(model).strip() or None,
            ollama_base_url and str(ollama_base_url).strip() or None,
        )
        return PlaybookBuilder(p)
    return _require_builder()


async def _build_playbook_with_refinement(
    job_id: int,
    intent: dict,
    rag_context: str,
    kb_snippets: list[str],
    auto_refine: bool,
    max_attempts: int,
    verify_with_dry_run: bool,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
) -> dict[str, Any]:
    builder = _builder_for_request(provider, model, ollama_base_url)
    bounded_attempts = max(1, min(int(max_attempts or 1), 5))
    history: list[dict[str, Any]] = []

    from ansible.builder import repair_playbook_yaml

    current_playbook = repair_playbook_yaml(
        await builder.build_draft(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            run_id=str(job_id),
        )
    )

    for attempt in range(1, bounded_attempts + 1):
        validation = await _validate_playbook_candidate(
            playbook_yaml=current_playbook,
            intent=intent,
            job_id=job_id,
            verify_with_dry_run=verify_with_dry_run,
        )
        entry = {
            "attempt": attempt,
            "passed": validation.get("passed", False),
            "issues": validation.get("issues", []),
        }
        history.append(entry)

        note_prefix = "validated" if validation.get("passed", False) else "validation_failed"
        if _db is not None:
            _append_playbook_version(
                _db,
                job_id,
                current_playbook,
                notes=f"{note_prefix} attempt {attempt}: " + "; ".join(validation.get("errors") or [note_prefix]),
                set_current=bool(validation.get("passed", False)),
            )

        if validation.get("passed", False):
            return {
                "success": True,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
            }

        refinable_issues = [issue for issue in validation.get("issues", []) if issue.get("refinable", True)]
        if not auto_refine:
            return {
                "success": False,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "auto_refine_disabled",
            }
        if not refinable_issues:
            return {
                "success": False,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "non_refinable_validation_failure",
            }
        if attempt >= bounded_attempts:
            break

        refined_playbook, changes = await _ai_refine_playbook(
            original=current_playbook,
            errors=_normalize_refinement_errors(refinable_issues),
            feedback="Preserve working tasks and fix only the reported validation failures.",
            builder=builder,
        )
        if refined_playbook.strip() == current_playbook.strip():
            return {
                "success": False,
                "playbook": current_playbook,
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "refinement_made_no_material_change",
            }

        entry["changes"] = changes
        current_playbook = repair_playbook_yaml(refined_playbook)

    final_validation = await _validate_playbook_candidate(
        playbook_yaml=current_playbook,
        intent=intent,
        job_id=job_id,
        verify_with_dry_run=verify_with_dry_run,
    )
    return {
        "success": False,
        "playbook": current_playbook,
        "attempts_used": bounded_attempts,
        "max_attempts": bounded_attempts,
        "final_validation": final_validation,
        "history": history,
        "escalation_reason": "max_refinement_attempts_reached",
    }


async def _build_bundle_with_refinement(
    job_id: int,
    intent: dict,
    rag_context: str,
    kb_snippets: list[str],
    auto_refine: bool,
    max_attempts: int,
    verify_with_dry_run: bool,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
) -> dict[str, Any]:
    builder = _builder_for_request(provider, model, ollama_base_url)
    bounded_attempts = max(1, min(int(max_attempts or 1), 5))
    history: list[dict[str, Any]] = []
    extra_guidance = ""
    current_bundle = await builder.build_bundle_draft(
        intent=intent,
        rag_context=rag_context,
        kb_snippets=kb_snippets,
        run_id=str(job_id),
    )

    for attempt in range(1, bounded_attempts + 1):
        validation = await _validate_bundle_candidate(
            bundle=current_bundle,
            intent=intent,
            job_id=job_id,
            verify_with_dry_run=verify_with_dry_run,
        )
        history.append(
            {
                "attempt": attempt,
                "passed": validation.get("passed", False),
                "issues": validation.get("issues", []),
            }
        )
        if validation.get("passed", False):
            return {
                "success": True,
                "bundle": current_bundle.to_dict(),
                "playbook": current_bundle.file_map().get(current_bundle.normalized_entrypoint(), ""),
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
            }
        refinable_issues = [issue for issue in validation.get("issues", []) if issue.get("refinable", True)]
        if not auto_refine or not refinable_issues or attempt >= bounded_attempts:
            return {
                "success": False,
                "bundle": current_bundle.to_dict(),
                "playbook": current_bundle.file_map().get(current_bundle.normalized_entrypoint(), ""),
                "attempts_used": attempt,
                "max_attempts": bounded_attempts,
                "final_validation": validation,
                "history": history,
                "escalation_reason": "bundle_validation_failed",
            }
        extra_guidance = (
            "Previous bundle validation failed. Regenerate the full bundle and fix these issues only:\n"
            + "\n".join(f"- {item}" for item in validation.get("errors") or [])
        )
        current_bundle = await builder.build_bundle_draft(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            run_id=str(job_id),
            extra_guidance=extra_guidance,
        )

    return {
        "success": False,
        "bundle": current_bundle.to_dict(),
        "playbook": current_bundle.file_map().get(current_bundle.normalized_entrypoint(), ""),
        "attempts_used": bounded_attempts,
        "max_attempts": bounded_attempts,
        "final_validation": {"passed": False, "issues": [], "errors": []},
        "history": history,
        "escalation_reason": "max_refinement_attempts_reached",
    }


# =============================================================================
# M23 ─ Ansible Workflow Studio  (new endpoints)
# =============================================================================

ANSIBLE_BASE = Path(__file__).parent
# Two main sections: Reference (templates) and Generated (AI output). Then project dirs.
_ALLOWED_ROOTS = ("reference", "generated", "Test_Reference", "playbooks", "roles", "inventory", "awx")
# reference/ and generated/ are read-only from UI; generated/* maps to playbooks/generated and roles/generated
_READONLY_ROOTS = frozenset({"reference", "roles", "awx", "generated", "Test_Reference"})
_WRITABLE_ROOTS = ("playbooks", "inventory")
_GENERATED_ROLE_ROOT = "roles/generated"
_GENERATED_PLAYBOOK_ROOT = "playbooks/generated"


def _safe_path(relative: str) -> Path:
    """
    Resolve *relative* against ANSIBLE_BASE and ensure it stays within
    one of the allowed sub-trees.  Raises HTTPException 400 on traversal.
    """
    resolved = (ANSIBLE_BASE / relative).resolve()
    try:
        resolved.relative_to(ANSIBLE_BASE.resolve())
    except ValueError:
        raise HTTPException(400, "Path traversal detected")
    # Must be inside one of the permitted root dirs
    rel_parts = resolved.relative_to(ANSIBLE_BASE.resolve()).parts
    if not rel_parts or rel_parts[0] not in _ALLOWED_ROOTS:
        raise HTTPException(403, f"Access restricted to: {_ALLOWED_ROOTS}")
    # Never expose vault contents
    if "vault" in rel_parts or ".vault_pass" in rel_parts:
        raise HTTPException(403, "Vault contents are not accessible via the API")
    return resolved


def _build_tree(root: Path, base: Path, exclude_dir_names: tuple[str, ...] = ()) -> list:
    """Recursively build a file-node tree dict. exclude_dir_names: skip these dir names (e.g. 'generated')."""
    result = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return result
    for entry in entries:
        rel = str(entry.relative_to(base)).replace("\\", "/")
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.name in exclude_dir_names and entry.is_dir():
            continue
        if entry.is_dir():
            result.append({
                "type": "dir",
                "name": entry.name,
                "path": rel,
                "children": _build_tree(entry, base, exclude_dir_names),
            })
        else:
            st = entry.stat()
            result.append({
                "type": "file",
                "name": entry.name,
                "path": rel,
                "size": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
    return result


def _build_tree_with_path_prefix(root: Path, prefix: str) -> dict:
    """Build a single dir node with path prefix for virtual 'generated' tree."""
    children = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        entries = []
    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        child_path = f"{prefix}/{entry.name}".replace("//", "/")
        if entry.is_dir():
            children.append(_build_tree_with_path_prefix(entry, child_path))
        else:
            st = entry.stat()
            children.append({
                "type": "file",
                "name": entry.name,
                "path": child_path,
                "size": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
    return {
        "type": "dir",
        "name": root.name,
        "path": prefix,
        "children": children,
    }


def _build_generated_tree() -> list:
    """Build virtual tree for 'generated' root: merges playbooks/generated/job_* and roles/generated/job_* by job id."""
    playbooks_root = ANSIBLE_BASE / "playbooks" / "generated"
    roles_root = ANSIBLE_BASE / "roles" / "generated"
    job_ids = set()
    if playbooks_root.exists():
        for d in playbooks_root.iterdir():
            if d.is_dir() and d.name.startswith("job_"):
                job_ids.add(d.name)
    if roles_root.exists():
        for d in roles_root.iterdir():
            if d.is_dir() and d.name.startswith("job_"):
                job_ids.add(d.name)
    result = []
    for jid in sorted(job_ids, key=lambda x: (len(x), x)):
        children = []
        pb_dir = playbooks_root / jid
        if pb_dir.exists():
            for entry in sorted(pb_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if entry.is_file() and not entry.name.startswith("."):
                    st = entry.stat()
                    children.append({
                        "type": "file",
                        "name": entry.name,
                        "path": f"generated/{jid}/{entry.name}",
                        "size": st.st_size,
                        "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    })
        role_dir = roles_root / jid
        if role_dir.exists():
            for entry in sorted(role_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if entry.is_dir() and not entry.name.startswith("."):
                    children.append(_build_tree_with_path_prefix(entry, f"generated/{jid}/{entry.name}"))
        result.append({
            "type": "dir",
            "name": jid,
            "path": f"generated/{jid}",
            "children": sorted(children, key=lambda n: (n["type"] != "dir", n["name"].lower())),
        })
    return result


def _generated_virtual_to_real(virtual_path: str) -> str | None:
    """
    Map virtual path under generated/ to real path.
    generated/job_X/playbook.yml -> playbooks/generated/job_X/playbook.yml
    generated/job_X/vlan/... -> roles/generated/job_X/vlan/...
    """
    parts = virtual_path.replace("\\", "/").strip("/").split("/")
    if len(parts) < 2 or parts[0] != "generated" or not parts[1].startswith("job_"):
        return None
    jid = parts[1]
    rest = "/".join(parts[2:])
    pb_dir = ANSIBLE_BASE / "playbooks" / "generated" / jid
    roles_dir = ANSIBLE_BASE / "roles" / "generated" / jid
    # Virtual job folder (generated/job_XX) has no rest; caller uses _generated_job_real_paths
    if not rest:
        return None
    if rest and "/" not in rest and (pb_dir / rest).exists() and (pb_dir / rest).is_file():
        return f"playbooks/generated/{jid}/{rest}"
    if rest and "/" in rest:
        return f"roles/generated/{jid}/{rest}"
    if rest and (roles_dir / rest).exists():
        return f"roles/generated/{jid}/{rest}"
    return f"roles/generated/{jid}/{rest}"


def _generated_job_real_paths(virtual_path: str) -> list[str]:
    """
    For virtual path generated/job_XX, return real paths to delete (playbook dir + role dir).
    Allows deleting the whole job folder from AI Output.
    """
    parts = virtual_path.replace("\\", "/").strip("/").split("/")
    if len(parts) < 2 or parts[0] != "generated" or not parts[1].startswith("job_"):
        return []
    jid = parts[1]
    rest = "/".join(parts[2:])
    if rest:
        return []  # only expand the job root, not subpaths
    return [
        f"playbooks/generated/{jid}",
        f"roles/generated/{jid}",
    ]


def _safe_writable_path(relative: str) -> tuple[Path, tuple[str, ...]]:
    """
    Resolve path and enforce writable roots only.
    Returns (resolved_path, rel_parts).
    """
    path = _safe_path(relative)
    rel_parts = Path(relative).parts
    relative_norm = str(relative).replace("\\", "/").strip("/")
    allow_generated_role = relative_norm.startswith(f"{_GENERATED_ROLE_ROOT}/")
    if not rel_parts or (rel_parts[0] not in _WRITABLE_ROOTS and not allow_generated_role):
        raise HTTPException(
            403,
            f"Only {_WRITABLE_ROOTS} and {_GENERATED_ROLE_ROOT}/ are writable. "
            "Hand-maintained roles/ and awx/ remain read-only.",
        )
    return path, rel_parts


def _entrypoint_content(bundle: dict[str, Any]) -> str:
    entrypoint_path = str(bundle.get("entrypoint_path") or "").replace("\\", "/").strip("/")
    for item in bundle.get("files") or []:
        if str(item.get("path") or "").replace("\\", "/").strip("/") == entrypoint_path:
            return str(item.get("content") or "")
    return ""


def _candidate_entrypoint_paths_for_disk(entrypoint_path: str) -> list[str]:
    """
    Plan-ansible stores a virtual entrypoint `generated/job_<id>/playbook.yml` in the DB,
    but writes the real file to `playbooks/generated/job_<id>/playbook.yml`.
    Try both so manual editor changes are picked up by Check/Deploy.
    """
    path_str = str(entrypoint_path or "").replace("\\", "/").strip("/")
    if not path_str:
        return []
    out: list[str] = [path_str]
    if path_str.startswith("generated/job_") and not path_str.startswith("playbooks/"):
        out.append(f"playbooks/{path_str}")
    return out


def _entrypoint_content_from_disk(entrypoint_path: str) -> str | None:
    """
    If the entrypoint file exists on disk under the Ansible project, return its content.
    This allows manual edits (or accepted diffs) to the playbook file to be used
    by the UI and by Check/Deploy instead of the stored DB version.
    Returns None if path is empty, invalid, or file does not exist.
    """
    for rel in _candidate_entrypoint_paths_for_disk(entrypoint_path):
        try:
            disk_path = _safe_path(rel)
        except HTTPException:
            continue
        if not disk_path.is_file():
            continue
        try:
            return disk_path.read_text(encoding="utf-8")
        except Exception:
            continue
    return None


def _persist_bundle_to_workspace(bundle: dict[str, Any]) -> None:
    for item in bundle.get("files") or []:
        rel_path = str(item.get("path") or "").replace("\\", "/").strip("/")
        if not rel_path:
            continue
        path = _safe_path(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item.get("content") or ""), encoding="utf-8")


def _bundle_target_hosts(bundle: dict[str, Any], fallback_playbook: str = "") -> list[str]:
    manifest = bundle.get("manifest") or {}
    hosts = manifest.get("target_hosts")
    if isinstance(hosts, list) and hosts:
        return [str(host).strip() for host in hosts if str(host).strip()]
    entrypoint_yaml = _entrypoint_content(bundle) or fallback_playbook
    if not entrypoint_yaml:
        return []
    try:
        import yaml

        plays = yaml.safe_load(entrypoint_yaml)
        hosts_value = plays[0].get("hosts", "") if plays else ""
        if isinstance(hosts_value, str) and hosts_value not in ("", "all"):
            return [item.strip() for item in hosts_value.split(",") if item.strip()]
    except Exception:
        return []
    return []


def _persist_job_bundle(
    *,
    job_id: int,
    bundle: dict[str, Any],
    playbook_yaml: str,
    status_value: str = "pending",
    stage_value: str = "plan",
) -> None:
    if _db is None:
        return
    _persist_bundle_to_workspace(bundle)
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET ansible_playbook = ?, ansible_artifact_manifest = ?, "
            "ansible_entrypoint_path = ?, stage = ?, status = ? WHERE id = ?",
            (
                playbook_yaml,
                json.dumps(bundle),
                str(bundle.get("entrypoint_path") or ""),
                stage_value,
                status_value,
                job_id,
            ),
        )


def _load_job_artifact(job_id: int) -> dict[str, Any]:
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        row = conn.execute(
            "SELECT ansible_playbook, ansible_artifact_manifest, ansible_entrypoint_path "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    bundle_raw = row["ansible_artifact_manifest"] if "ansible_artifact_manifest" in row.keys() else None
    if bundle_raw:
        try:
            bundle = bundle_from_dict(json.loads(bundle_raw)).to_dict()
        except Exception as exc:
            raise HTTPException(500, f"Stored Ansible bundle is invalid: {exc}")
        entrypoint_path = str(bundle.get("entrypoint_path") or "").replace("\\", "/").strip("/")
        playbook_yaml = _entrypoint_content(bundle)
        # Prefer disk content so manual/editor edits are used by UI and Check/Deploy
        disk_yaml = _entrypoint_content_from_disk(entrypoint_path)
        if disk_yaml is not None:
            playbook_yaml = disk_yaml
            # Update bundle's entrypoint file content so project_files (and runner) use disk version
            for item in bundle.get("files") or []:
                if str(item.get("path") or "").replace("\\", "/").strip("/") == entrypoint_path:
                    item["content"] = disk_yaml
                    break
        return {
            "mode": "bundle",
            "bundle": bundle,
            "playbook_yaml": playbook_yaml,
            "entrypoint_path": entrypoint_path or str(bundle.get("entrypoint_path") or ""),
            "target_hosts": _bundle_target_hosts(bundle),
            "project_files": list(bundle.get("files") or []),
        }

    playbook_yaml = row["ansible_playbook"]
    entrypoint_path = str(row["ansible_entrypoint_path"] or "").replace("\\", "/").strip("/")
    if not playbook_yaml and not entrypoint_path:
        raise HTTPException(422, "No Ansible playbook stored. Run /plan-ansible first.")
    # Prefer disk content when entrypoint path exists on disk
    disk_yaml = _entrypoint_content_from_disk(entrypoint_path) if entrypoint_path else None
    if disk_yaml is not None:
        playbook_yaml = disk_yaml
    elif not playbook_yaml:
        raise HTTPException(422, "No Ansible playbook stored. Run /plan-ansible first.")
    return {
        "mode": "playbook",
        "bundle": None,
        "playbook_yaml": playbook_yaml,
        "entrypoint_path": entrypoint_path or str(row["ansible_entrypoint_path"] or ""),
        "target_hosts": _bundle_target_hosts({}, playbook_yaml),
        "project_files": None,
    }


# ── M23: preview-intent (categories + RAG templates before create-and-plan) ───

def _get_access_devices_ordered() -> List[str]:
    """Return access-layer device hostnames in stable order."""
    if _db is None:
        return []
    devices = _db.get_all_devices()
    access = [
        d.hostname for d in devices if d.hostname
        and ((d.role or "").strip().lower() in ("access", "access_switch") or (d.hostname or "").lower().startswith("acc_"))
    ]
    return sorted(access)


def _number_from_hostname(hostname: str) -> Optional[int]:
    """Extract the number that best identifies this device (e.g. Acc_Cisco_1 -> 1, Acc_Huawei_3 -> 3)."""
    if not hostname:
        return None
    # Prefer trailing _N or trailing N (e.g. Acc_Cisco_1, Acc_Huawei_3, Switch2)
    m = re.search(r"_(\d+)$", hostname)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", hostname)
    if m:
        return int(m.group(1))
    # Any distinct number in the name
    nums = re.findall(r"\d+", hostname)
    return int(nums[0]) if nums else None


def _extract_requested_numbers(intent_description: str) -> List[int]:
    """Parse device selector numbers only when the prompt clearly talks about switches/roles."""
    lower = (intent_description or "").lower()
    if not any(word in lower for word in ("switch", "switches", "access", "distribution", "core")):
        return []
    prefix = r"(?:switch(?:es)?|access\s+switch(?:es)?|distribution\s+switch(?:es)?|core\s+switch(?:es)?)\s+"
    # Match "access switch 1, 2, and 3" or "switch 1, 2, 3"
    m = re.search(prefix + r"(\d+)\s*,\s*(\d+)\s*,?\s*(?:and\s+)?(\d+)\b", intent_description, re.IGNORECASE)
    if m:
        return [int(m.group(1)), int(m.group(2)), int(m.group(3))]
    # Match "access switch 1 and 2" or "switch 1, 2"
    m = re.search(prefix + r"(\d+)\s*(?:,\s*|and\s+)(\d+)\b", intent_description, re.IGNORECASE)
    if m:
        return [int(m.group(1)), int(m.group(2))]
    # Single selector: "access switch 3" (avoid matching bare numbers elsewhere)
    m = re.search(prefix + r"(\d+)\b", intent_description, re.IGNORECASE)
    if m:
        return [int(m.group(1))]
    return []


def _switch_role_disambiguation(intent_description: str) -> Optional[dict[str, Any]]:
    """
    Detect ambiguous prompts like "Switch 1 and Switch 2" (no access/dist/core),
    and offer role-scoped options to the UI.
    """
    if _db is None:
        return None
    text = (intent_description or "").strip()
    lower = text.lower()
    if not text:
        return None
    # Must mention switch + numbers.
    if "switch" not in lower:
        return None
    nums = re.findall(r"\b(\d+)\b", lower)
    if not nums:
        return None
    # If already scoped by role keyword, do not disambiguate.
    if any(k in lower for k in ("access", "distribution", "dist", "core")):
        return None

    # Only treat numbers near "switch" as selectors (avoid VLAN IDs etc.)
    selector_nums = _extract_requested_numbers(text)
    if not selector_nums:
        # Fallback: "switch 1 and switch 2"
        selector_nums = [int(n) for n in nums[:2] if str(n).isdigit()]
    if not selector_nums:
        return None

    # Build role buckets using inventory role or hostname prefix.
    role_to_hosts: dict[str, list[str]] = {"core": [], "distribution": [], "access": []}
    for d in _db.get_all_devices():
        host = (getattr(d, "hostname", None) or "").strip()
        if not host:
            continue
        hlow = host.lower()
        role = (getattr(d, "role", None) or "").strip().lower()
        if role in ("access", "access_switch") or hlow.startswith("acc_"):
            bucket = "access"
        elif role in ("distribution", "dist") or hlow.startswith("dist_"):
            bucket = "distribution"
        elif role in ("core", "core_switch") or hlow.startswith("core_"):
            bucket = "core"
        else:
            continue
        role_to_hosts[bucket].append(host)

    def pick_for_role(r: str) -> list[str]:
        hosts = role_to_hosts.get(r) or []
        if not hosts:
            return []
        # Map by numeric suffix in hostname
        num_to_host: dict[int, str] = {}
        for h in sorted(hosts):
            n = _number_from_hostname(h)
            if n is not None and n not in num_to_host:
                num_to_host[n] = h
        chosen: list[str] = []
        for n in selector_nums:
            if n in num_to_host:
                chosen.append(num_to_host[n])
        return chosen

    access = pick_for_role("access")
    dist = pick_for_role("distribution")
    core = pick_for_role("core")

    roles_with_matches = [r for r, hs in (("core", core), ("distribution", dist), ("access", access)) if len(hs) == len(selector_nums) and hs]
    # If multiple roles match cleanly, always disambiguate.
    if len(roles_with_matches) <= 1:
        # Otherwise, if the prompt is "switch <numbers>" and inventory has multiple layers,
        # still offer clarification rather than guessing or expanding too broadly.
        has_any_layer = any(role_to_hosts.get(r) for r in ("core", "distribution", "access"))
        multi_layers = sum(1 for r in ("core", "distribution", "access") if role_to_hosts.get(r)) >= 2
        if not (has_any_layer and multi_layers):
            return None

    nums_label = ", ".join(str(n) for n in selector_nums)
    options = [
        {"id": "core", "label": f"Core Switch {nums_label}", "clarification": f"core switch {nums_label}", "matches": core},
        {"id": "distribution", "label": f"Distribution Switch {nums_label}", "clarification": f"distribution switch {nums_label}", "matches": dist},
        {"id": "access", "label": f"Access Switch {nums_label}", "clarification": f"access switch {nums_label}", "matches": access},
        {"id": "custom", "label": "Enter your custom answer", "clarification": "", "matches": []},
    ]
    return {
        "kind": "target_devices",
        "question": "Which Switch(es) did you mean by “Switch {nums}”?".format(nums=nums_label),
        "selector_numbers": selector_nums,
        "options": options,
    }


def _apply_numeric_subset_fallback(intent_description: str, resolved: List[str]) -> tuple[List[str], str]:
    """
    When user said e.g. 'access switch 1, 2, and 3' but we got too many devices,
    pick devices whose names match those numbers (e.g. Acc_Cisco_1, Acc_Cisco_2, Acc_Huawei_3).
    Order in the prompt does not matter; match by how close the device name is to the number.
    """
    if not resolved or _db is None:
        return resolved, ""
    lower = (intent_description or "").lower()
    # Only apply for access-layer wording — avoids mis-mapping "distribution/core switch N" using access inventory.
    if "access" not in lower:
        return resolved, ""
    requested = _extract_requested_numbers(intent_description)
    if not requested:
        return resolved, ""
    access = _get_access_devices_ordered()
    if len(access) < len(requested):
        return resolved, ""
    if len(resolved) <= len(requested):
        return resolved, ""
    # Build: for each requested number N, choose the access device whose name implies N
    number_to_hostname: Dict[int, str] = {}
    for h in access:
        n = _number_from_hostname(h)
        if n is not None and n not in number_to_hostname:
            number_to_hostname[n] = h
    subset = []
    for n in requested:
        if n in number_to_hostname:
            subset.append(number_to_hostname[n])
    if len(subset) < len(requested):
        return resolved, ""
    # Deduplicate preserving order
    seen: set = set()
    out = [h for h in subset if h not in seen and not seen.add(h)]
    nums_str = ", ".join(str(n) for n in requested)
    return out, f"Your prompt mentioned **\"{nums_str}\"** — matched to devices by number in name: **{', '.join(out)}**."


def _devices_mentioned_summary(parsed_dict: dict, intent_description: str) -> str:
    """Return empty string to avoid showing potentially misleading 'Devices mentioned' text."""
    return ""


# Substrings (lowercased) that identify management interfaces to exclude from shutdown selection
_MGMT_INTERFACE_SUBSTRINGS = ("management", "mgmt", "meth", "vty", "console")


def _is_management_interface(iface: dict, device_management_ip: Optional[str]) -> bool:
    """True if this interface should be treated as management (excluded from shutdown list)."""
    name = str(iface.get("name") or iface.get("interface") or "").strip().lower()
    if not name:
        return False
    if any(m in name for m in _MGMT_INTERFACE_SUBSTRINGS):
        return True
    if device_management_ip:
        iface_ip = str(iface.get("ip_address") or iface.get("ip") or "").strip()
        if iface_ip and iface_ip == str(device_management_ip).strip():
            return True
    return False


def _interfaces_disambiguation_from_planning(
    planning_context: dict,
    resolved_hosts: List[str],
    question: str = "Please select the interfaces you want to shutdown.",
) -> Optional[Dict[str, Any]]:
    """
    Build per-device interface list for disambiguation (excluding management).
    Returns { "question": str, "devices": [ { "hostname": str, "interfaces": [ { "name": str } ] } ] } or None.
    """
    devices_in = list(planning_context.get("devices") or [])
    host_set = {str(h).strip() for h in resolved_hosts if str(h).strip()}
    out_devices: List[Dict[str, Any]] = []
    for d in devices_in:
        hostname = str(d.get("hostname") or "").strip()
        if hostname not in host_set:
            continue
        mgmt_ip = d.get("management_ip")
        interfaces = []
        for iface in d.get("interfaces") or []:
            if _is_management_interface(iface, mgmt_ip):
                continue
            name = str(iface.get("name") or iface.get("interface") or "").strip()
            if name:
                interfaces.append({"name": name})
        out_devices.append({"hostname": hostname, "interfaces": interfaces})
    if not out_devices:
        return None
    return {"question": question, "devices": out_devices}


def _req_params_has_explicit_interfaces(req_params: Optional[Dict[str, Any]]) -> bool:
    """
    True when the client already sent interface bindings (chat picker, Studio merge, etc.).
    Used so we do not re-attach the in-chat interface picker after the user confirmed.
    """
    if not req_params:
        return False
    i = req_params.get("interfaces")
    ic = req_params.get("interfaces_config")
    if isinstance(i, list) and len(i) > 0:
        return True
    if isinstance(ic, list) and len(ic) > 0:
        return True
    for key in ("secondary_intents", "_secondary_intents"):
        secs = req_params.get(key)
        if not isinstance(secs, list):
            continue
        for s in secs:
            if not isinstance(s, dict):
                continue
            p = s.get("parameters")
            if not isinstance(p, dict):
                continue
            si = p.get("interfaces")
            sic = p.get("interfaces_config")
            if isinstance(si, list) and len(si) > 0:
                return True
            if isinstance(sic, list) and len(sic) > 0:
                return True
    return False


def _merge_req_params_secondary_intents_into_parsed_intent(
    parsed_intent: Dict[str, Any],
    req_params: Optional[Dict[str, Any]],
) -> None:
    """
    Apply preview wizard / API patches for ``params.secondary_intents`` onto
    ``parsed_intent['secondary_intents']``. These keys must not be merged into primary
    ``parameters``.
    """
    if not req_params or not isinstance(req_params.get("secondary_intents"), list):
        return
    patches = _normalize_secondary_intents(req_params["secondary_intents"])
    if not patches:
        return
    existing = list(parsed_intent.get("secondary_intents") or [])
    for i, patch in enumerate(patches):
        if i < len(existing):
            ep = dict(existing[i].get("parameters") or {})
            pp = dict(patch.get("parameters") or {})
            ep.update({k: v for k, v in pp.items() if v is not None})
            existing[i] = {**existing[i], **patch, "parameters": ep}
        else:
            existing.append(patch)
    parsed_intent["secondary_intents"] = existing


def _append_secondary_intent_schema_disambiguation(
    parsed_intent: Dict[str, Any],
    resolved_hosts: List[str],
    params_disambiguation: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    For each ``secondary_intents`` slice, run the same operation-specific schema completeness
    checks as the primary intent and merge missing fields into ``params_disambiguation`` with
    ``secondary_intent_index`` so the chat wizard can collect answers per operation.
    """
    secondaries = list(parsed_intent.get("secondary_intents") or [])
    if not secondaries:
        return params_disambiguation
    fields_list: List[Dict[str, Any]] = list((params_disambiguation or {}).get("fields") or [])
    seen: Set[Tuple[Any, ...]] = set()
    for f in fields_list:
        if not isinstance(f, dict):
            continue
        seen.add(
            (
                f.get("field"),
                f.get("secondary_intent_index"),
            )
        )

    for idx, sec in enumerate(secondaries):
        if not isinstance(sec, dict):
            continue
        sec_action = (sec.get("action") or "").strip() or "configure"
        sec_feature = (sec.get("feature") or "").strip()
        sec_params = dict(sec.get("parameters") or {})
        sec_intent = {
            "action": sec_action,
            "feature": sec_feature,
            "parameters": sec_params,
            "resolved_devices": list(resolved_hosts or []),
        }
        sec_schema = get_schema_for_parsed_intent(sec_intent)
        if not sec_schema:
            continue
        type_key = (sec_schema.get("action") or "").strip() or sec_action
        missing_sec, _err_sec = validate_completeness(
            sec_intent, sec_schema, action_or_feature=type_key if sec_schema else None
        )
        questions_sec = (sec_schema or {}).get("questions") or {}
        sec_label = f"{sec_action} ({sec_feature})" if sec_feature else sec_action
        for field in missing_sec:
            key = (field, idx)
            if key in seen:
                continue
            fields_list.append(
                {
                    "field": field,
                    "question": questions_sec.get(field) or f"Please provide: {field}",
                    "secondary_intent_index": idx,
                    "label": f"Also required: {sec_label}",
                }
            )
            seen.add(key)

        schema_action_key = (sec_schema.get("action") or "").strip().lower().replace("-", "_")
        hook = SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION.get(schema_action_key)
        if hook:
            for e in hook(sec_params):
                key = (e["field"], idx)
                if key in seen:
                    continue
                fields_list.append(
                    {
                        "field": e["field"],
                        "question": e["question"],
                        "secondary_intent_index": idx,
                        "label": f"Also required: {sec_label}",
                    }
                )
                seen.add(key)

    if not fields_list:
        return params_disambiguation
    return {
        "question": (params_disambiguation or {}).get("question") or "Please provide the following.",
        "fields": fields_list,
    }


def _normalize_secondary_intents(raw: Any) -> List[Dict[str, Any]]:
    """
    Normalize LLM secondary_intents to a list of { action, feature, parameters }.
    Used so preview and create-and-plan can validate/display multiple intents.
    """
    if not raw or not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip() or "configure"
        feat = str(item.get("feature") or "").strip()
        params = dict(item.get("parameters") or {})
        # Skip empty/invalid entries
        if not feat and not action:
            continue
        if not feat and action:
            # Infer feature from action (e.g. configure_ospf -> ospf)
            feat = action.replace("configure_", "").replace("create_", "").replace("-", "_") or "general"
        out.append({"action": action, "feature": feat, "parameters": params})
    return out


def _annotate_reasoning_interfaces_with_planned_inventory(
    reasoning: List[str],
    *,
    inv_result: Any,
    resolved_hosts: List[str],
) -> None:
    """
    ``_preview_parse_and_rag`` appends an optimistic **Interfaces to configure** line from the
    parser/LLM before target hosts and planned inventory are finalized. When
    ``validate_interface_params_against_context`` fails, replace that block so the preview does
    not show bogus interface names (e.g. Gi1/0/1) as if they were already valid for the
    selected device(s).
    """
    if not reasoning or inv_result is None:
        return
    notice = str(getattr(inv_result, "validation_notice", "") or "").strip()
    if not notice:
        return
    hosts_txt = ", ".join(str(h) for h in resolved_hosts if str(h).strip()) or "(unknown targets)"
    replacement = (
        "- **Interfaces to configure (cross-checked with planned inventory):**\n"
        f"  - **Selected target device(s):** {hosts_txt}\n"
        "  - ⚠️ **The interface name(s) from your intent are not valid for this device in planned context.**\n\n"
        + "\n".join(f"  {line}" for line in notice.splitlines())
    )
    for idx, block in enumerate(reasoning):
        if (
            isinstance(block, str)
            and "**Interfaces to configure:**" in block
            and "cross-checked with planned inventory" not in block
        ):
            reasoning[idx] = replacement
            return


def _merge_intent_pipeline_interactive_asks(
    parsed_intent: Dict[str, Any],
    params_disambiguation: Optional[Dict[str, Any]],
    devices_disambiguation: Optional[Dict[str, Any]],
    db_manager: Any,
    *,
    preview_resolved_hosts: Optional[List[str]] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Map refactored pipeline ``interactive_asks`` + cross-check into chat/preview wizards
    (devices_disambiguation, params_disambiguation) when the main preview path did not already set them.
    """
    trace = parsed_intent.get("intent_pipeline") or {}
    asks = trace.get("interactive_asks") or []
    if not asks:
        return params_disambiguation, devices_disambiguation

    out_dd = devices_disambiguation
    out_pd = params_disambiguation

    # Chat / Target inventory picks are authoritative: never surface stale pipeline device asks.
    authoritative_planned_targets = False
    if preview_resolved_hosts and db_manager is not None:
        try:
            rh = [str(h).strip() for h in preview_resolved_hosts if str(h).strip()]
            if rh:
                ok, bad = _filter_resolved_hosts_to_planned_inventory(rh, db_manager)
                authoritative_planned_targets = bool(ok) and not bad and len(ok) == len(rh)
        except Exception:
            authoritative_planned_targets = False

    for ask in asks:
        kind = str(ask.get("kind") or "")
        if authoritative_planned_targets and kind in ("invalid_inventory", "select_planned_devices"):
            continue
        if kind == "invalid_inventory" and not out_dd and db_manager is not None:
            candidates = [str(x) for x in (ask.get("suggestions") or []) if str(x).strip()]
            if not candidates:
                candidates = _all_inventory_hostnames_sorted(db_manager)
            if candidates:
                out_dd = {
                    "ambiguous_devices": [],
                    "missing_devices": [],
                    "unknown_inventory_aliases": [str(ask.get("unknown_device_name") or ask.get("field") or "target_devices")],
                    "ask_user_selection": True,
                    "multi_select": bool(ask.get("multi_select", True)),
                    "planned_network_context": bool(ask.get("planned_network_context", True)),
                    "candidate_devices": candidates,
                    "disambiguation_kind": "not_in_inventory",
                    "question": str(
                        ask.get("question")
                        or "That device was not found in the planned network context. Pick valid device(s)."
                    ),
                }
        elif kind == "select_planned_devices" and not out_dd and db_manager is not None:
            shortlist = [str(x) for x in (ask.get("suggestions") or []) if str(x).strip()]
            full_list = _all_inventory_hostnames_sorted(db_manager)
            if shortlist or full_list:
                out_dd = {
                    "ambiguous_devices": [],
                    "missing_devices": [],
                    "unknown_inventory_aliases": ["target_devices"],
                    "ask_user_selection": True,
                    "multi_select": bool(ask.get("multi_select", True)),
                    "planned_network_context": bool(ask.get("planned_network_context", True)),
                    "candidate_shortlist": bool(ask.get("candidate_shortlist", True)),
                    "candidate_devices": shortlist,
                    "full_planned_devices": full_list,
                    "disambiguation_kind": "select_from_planned_network",
                    "question": str(
                        ask.get("question")
                        or "Select target device(s) from the planned network."
                    ),
                }
        elif kind == "missing_required":
            field = str(ask.get("field") or "").strip()
            if not field:
                continue
            existing_fields = [
                f for f in (out_pd or {}).get("fields") or [] if isinstance(f, dict)
            ]
            seen = {str(f.get("field")) for f in existing_fields}
            if field in seen:
                continue
            row = {"field": field, "question": str(ask.get("question") or f"Please provide: {field}")}
            if out_pd:
                out_pd = {
                    "question": str(out_pd.get("question") or "Please provide the following."),
                    "fields": existing_fields + [row],
                }
            else:
                out_pd = {"question": "Please provide the following.", "fields": [row]}

    return out_pd, out_dd


def _preview_references_category_disk_scan(
    categories: List[str],
    effective_vendor: str,
    *,
    single_vendor: bool,
    multi_vendor_targets: bool,
) -> List[Dict[str, Any]]:
    """
    Resolve reference playbook paths from intent categories + vendor by scanning ``reference/``
    on disk — same fallback as full RAG preview, without embedding retrieval (much faster).
    """
    references: List[Dict[str, Any]] = []
    seen_paths_fb: Set[str] = set()
    ev = (effective_vendor or "").strip().lower().replace("-", "_")
    if single_vendor:
        fallback_vendors = [ev] if ev else ["multi_vendor"]
    elif multi_vendor_targets:
        fallback_vendors = ["multi_vendor"]
    else:
        fallback_vendors = ["multi_vendor", ev] if ev else ["multi_vendor"]
    for feat in [c.strip().lower().replace("-", "_") for c in categories if c]:
        for vend in fallback_vendors:
            vend = (vend or "").strip().lower().replace("-", "_")
            if not vend:
                continue
            for playbook_name in ("playbook_template.yml", "playbook.yml"):
                path = f"reference/{feat}/{vend}/{playbook_name}"
                if path in seen_paths_fb:
                    continue
                ref_path = (ANSIBLE_BASE.joinpath(*path.split("/"))).resolve()
                if ref_path.is_file():
                    seen_paths_fb.add(path)
                    references.append(
                        {
                            "doc_name": ref_path.name,
                            "feature": feat,
                            "vendor": vend,
                            "path": path,
                        }
                    )
                    break
    return references


def _preview_parse_and_rag(
    intent_description: str,
    target_hosts: List[str],
    vendor: str,
    feature: str,
    provider: Optional[str],
    model: Optional[str],
    ollama_base_url: Optional[str],
    request_params: Optional[Dict[str, Any]] = None,
    *,
    skip_rag: bool = False,
) -> tuple[list[str], list[dict], dict, bool, list[str], list[str]]:
    """
    Parse intent (lightweight: no policy raise) and run RAG for ansible_reference.
    Returns (categories, references, parsed_intent_dict, policy_approved, reasoning_steps, resolved_hosts).
    """
    from llm.intent import IntentParser, IntentType, ParsedIntent

    categories: list[str] = []
    parsed_dict: dict = {}
    policy_approved = False
    reasoning: list[str] = []

    try:
        parser = IntentParser(
            provider=_provider_for_request(provider, model, ollama_base_url),
            db_manager=_db,
        )
        parsed = ParsedIntent.model_validate_json(
            parser.parse(
                intent_description,
                topology_context=_build_intent_parse_context(intent_description),
            )
        )
        if feature or parsed.feature:
            categories = [str(feature or parsed.feature).strip()]
        if parsed.feature and parsed.feature not in categories:
            categories.append(parsed.feature)
        parsed_dict = parsed.model_dump()
        # Normalize and expose secondary intents (multi-intent pipeline)
        params_dict = dict(parsed_dict.get("parameters") or {})
        raw_secondary = params_dict.pop("_secondary_intents", None)
        secondary_list = _normalize_secondary_intents(raw_secondary)
        parsed_dict["secondary_intents"] = secondary_list
        parsed_dict["parameters"] = params_dict
        if params_dict.get("_intent_parse_error"):
            reasoning.insert(
                0,
                "### ⚠️ Intent parser (v2) failed\n"
                "- The refactored parser could not complete (LLM error, timeout, or invalid JSON). "
                "Check your API key or Ollama URL/model, or increase `INTENT_PARSE_LLM_TIMEOUT_SEC`. "
                "Optional fallback: set `INTENT_USE_LEGACY_PARSER=1` for the old monolithic prompt.\n",
            )
        for sec in secondary_list:
            f = (sec.get("feature") or "").strip()
            if f and f not in categories:
                categories.append(f)
        intent_type = getattr(parsed.intent_type, "value", parsed.intent_type)
        intent_action = parsed.action or "configure"
        intent_feature = (parsed.feature or "").strip() or (categories[0] if categories else "general")
        reasoning.append(
            "### 1. Intent\n"
            f"- **Type:** {intent_type} · **Action:** {intent_action}\n"
            f"- **Feature:** {intent_feature}\n"
            f"- **Categories (for templates):** {', '.join(categories) if categories else '—'}. You can add or remove above."
        )
        if secondary_list:
            reasoning.append(
                f"- **Also detected (secondary):** {', '.join(s.get('feature', '') or s.get('action', '') for s in secondary_list)}. These will be validated; primary intent is generated first."
            )
        # For VLAN, show which interfaces will be configured (mode + VLANs)
        is_vlan = (parsed.feature or "").strip().lower() == "vlan" or any((c or "").strip().lower() == "vlan" for c in categories)
        if is_vlan:
            preview_intent = {
                "params": dict(parsed_dict.get("parameters") or {}),
                "target_hosts": list(parsed_dict.get("resolved_devices") or []),
            }
            if not preview_intent["params"].get("interfaces"):
                extracted = _extract_vlan_interface_from_description(intent_description)
                if extracted:
                    preview_intent["params"].update(extracted)
            ifaces_summary = get_vlan_interfaces_summary(preview_intent)
            if ifaces_summary:
                parsed_dict["interfaces_summary"] = ifaces_summary
                lines = [format_vlan_interface_reasoning_line(s) for s in ifaces_summary]
                reasoning.append("- **Interfaces to configure:**\n" + "\n".join(lines))
            else:
                parsed_dict["interfaces_summary"] = []
                reasoning.append("- **Interfaces to configure:** (none — VLAN creation only)")
            # Validate VLAN intent at preview so user sees schema errors (e.g. VLAN ID 1–4094) before generating
            valid, validation_errors = validate_vlan_intent(preview_intent)
            if not valid and validation_errors:
                parsed_dict["vlan_validation_errors"] = validation_errors
                reasoning.append("- ⚠️ **VLAN validation failed:** " + "; ".join(validation_errors) + ". Please correct your prompt (e.g. VLAN ID must be 1–4094) before generating.")
            else:
                parsed_dict["vlan_validation_errors"] = []
        if parsed.intent_type in (IntentType.CONFIGURE, IntentType.DEPLOY):
            policy = _policy_validate_intent(
                parsed.model_dump_json(),
                user_role="operator",
            )
            policy_approved = policy.get("approved") is True
            if not policy_approved:
                reasoning.append("⚠️ Policy did not approve this intent for execution; you may still generate the playbook for review.")
    except Exception as exc:
        logger.debug("Preview parse/policy failed: %s", exc)
        reasoning.append(f"### 1. Intent\nIntent parsing encountered an issue: {exc}. Using provided feature/vendor if any.")
        if not categories and feature:
            categories = [feature]

    # Target hosts: prefer explicit API `target_hosts` (UI inventory) over LLM `resolved_devices`.
    # Also accept params.target_hosts / params.target_devices when the client only sends `params` (wizard flows).
    merged_targets: List[str] = list(target_hosts or [])
    if not merged_targets and request_params:
        for key in ("target_hosts", "target_devices"):
            v = request_params.get(key)
            if v is None:
                continue
            if isinstance(v, list) and len(v) > 0:
                merged_targets = list(v)
                break
            if isinstance(v, str) and v.strip():
                merged_targets = [v.strip()]
                break
    explicit_targets_from_ui = bool(merged_targets)

    parsed_resolved: List[str] = list(parsed_dict.get("resolved_devices") or []) if parsed_dict else []
    if merged_targets:
        resolved: List[str] = _resolve_target_hosts(merged_targets)
    else:
        resolved = _resolve_target_hosts(parsed_resolved)
    resolved = _narrow_resolved_by_hostnames_in_prompt(intent_description, resolved, _db)
    # Do not re-map "access switch 1,2,3" when the client already sent explicit hostnames —
    # numeric subset would shrink the list and append §1c reasoning that then contradicts
    # ``req.params.target_hosts`` / coalesced API targets (e.g. six picks vs three inferred).
    if explicit_targets_from_ui:
        _numeric_subset_note = ""
    else:
        resolved, _numeric_subset_note = _apply_numeric_subset_fallback(intent_description, resolved)
    switch_disambiguation_early = _switch_role_disambiguation(intent_description)
    _dd_pre = _deduce_target_devices(intent_description, resolved, explicit_user_targets=explicit_targets_from_ui)
    _map_pre: List[Dict[str, Any]] = list(_dd_pre.get("mapping") or [])
    if (
        not switch_disambiguation_early
        and resolved
        and not explicit_targets_from_ui
        and _target_resolution_is_unclear(
            intent_description, resolved, _map_pre, _db, explicit_user_targets=explicit_targets_from_ui
        )
    ):
        resolved = []
        if parsed_dict:
            parsed_dict["resolved_devices"] = []
        reasoning.insert(
            1,
            "### 1b. Target devices need clarification\n"
            "- Your message does not clearly name which device(s) to change.\n"
            "- Pick specific hostnames in **Target inventory** or answer **Which devices?** when shown — "
            "the backend will not assume the whole network.",
        )
    elif parsed_dict:
        parsed_dict["resolved_devices"] = list(resolved)
        if _numeric_subset_note:
            reasoning.append(
                "### 1c. Target scope (numeric selection)\n- " + _numeric_subset_note.strip()
            )

    # Map LLM/user device hints to inventory: unique auto-map, ambiguous shortlist, or full inventory.
    pending_devices_disambiguation: Optional[Dict[str, Any]] = None
    inventory_unknown_device_tokens: List[str] = []
    if _db is not None and resolved:
        _inv_valid, _inv_unknown = _filter_resolved_hosts_to_planned_inventory(resolved, _db)
        resolved = list(_inv_valid)
        if _inv_unknown:
            _auto_hosts, _dd = _resolve_unknown_device_aliases(_inv_unknown, _db)
            for h in _auto_hosts:
                if h not in resolved:
                    resolved.append(h)
            if _dd:
                inventory_unknown_device_tokens = list(_inv_unknown)
                pending_devices_disambiguation = _dd
                _rq = str(_dd.get("question") or "").strip()
                reasoning.insert(
                    1,
                    "### 1c. Target device(s) need confirmation\n"
                    + (_rq + "\n" if _rq else "- Pick hostname(s) in the chat device picker or **Target inventory**.\n"),
                )
            elif _auto_hosts and _inv_unknown:
                reasoning.insert(
                    1,
                    "### 1c. Mapped device hint(s)\n"
                    f"- Interpreted hint(s) `{', '.join(_inv_unknown)}` as inventory host(s): **{', '.join(_auto_hosts)}**.\n",
                )

    if parsed_dict is not None:
        parsed_dict["resolved_devices"] = list(resolved)
        parsed_dict["inventory_unknown_device_tokens"] = inventory_unknown_device_tokens
        if pending_devices_disambiguation:
            parsed_dict["pending_devices_disambiguation"] = pending_devices_disambiguation
        else:
            parsed_dict.pop("pending_devices_disambiguation", None)

    # Parser left no targets: device step first — offer full planned inventory for multi-select.
    _feat_prev = str(parsed_dict.get("feature") or "").strip().lower() if parsed_dict else ""
    _cats_prev = {str(c).strip().lower().replace("-", "_") for c in (categories or []) if str(c).strip()}
    _needs_device_pick = _feat_prev in (
        "interface",
        "vlan",
        "acl",
        "qos",
        "ospf",
        "static_route",
        "routing",
        "general",
    ) or bool(
        _cats_prev
        & {"interface", "vlan", "acl", "qos", "ospf", "static_route", "routing", "general"}
    )
    if (
        parsed_dict is not None
        and _db is not None
        and _db_hostname_lower_to_canonical(_db)
        and not resolved
        and not parsed_dict.get("pending_devices_disambiguation")
        and _needs_device_pick
        and not switch_disambiguation_early
        and not _intent_explicitly_requests_broad_device_scope(intent_description)
    ):
        _all_h = sorted(set(_db_hostname_lower_to_canonical(_db).values()))
        if _all_h:
            pending_devices_disambiguation = {
                "ambiguous_devices": [],
                "missing_devices": [],
                "unknown_inventory_aliases": [],
                "ask_user_selection": True,
                "candidate_devices": list(_all_h),
                "disambiguation_kind": "missing_targets",
                "question": (
                    "No target device was inferred from your prompt. "
                    "Select one or more devices from planned inventory (multi-select), then continue."
                ),
            }
            parsed_dict["pending_devices_disambiguation"] = pending_devices_disambiguation
            parsed_dict["inventory_unknown_device_tokens"] = []
            reasoning.insert(
                1,
                "### 1c. Select target device(s)\n"
                "- No inventory hostname was resolved. Choose device(s) in the chat picker or **Target inventory**, then continue.\n",
            )

    # Fallback only when the backend parser could not resolve anything at all.
    subset_reason = ""
    scope_summary = _devices_mentioned_summary(parsed_dict, intent_description)
    device_deduction = _deduce_target_devices(
        intent_description, resolved, explicit_user_targets=explicit_targets_from_ui
    )
    # Section 2: Target devices (one block)
    sec2 = _markdown_section_2_target_devices(
        intent_description, resolved, device_deduction, explicit_user_targets=explicit_targets_from_ui
    )
    _sec2_prefix = ""
    if subset_reason:
        _sec2_prefix += f"- {subset_reason}\n"
    if scope_summary:
        _sec2_prefix += f"- {scope_summary}\n"
    if _sec2_prefix:
        sec2 = sec2.replace("### 2. Target devices\n", f"### 2. Target devices\n{_sec2_prefix}", 1)
    reasoning.append(sec2)

    vendors_in_targets: set[str] = set()
    if resolved and _db:
        for h in resolved:
            dev = _db.get_device_by_hostname(h)
            if dev and getattr(dev, "vendor", None):
                v = normalize_vendor(dev.vendor)
                if v:
                    vendors_in_targets.add(v)
    effective_vendor = ""
    if len(vendors_in_targets) == 1:
        effective_vendor = next(iter(vendors_in_targets))
    elif vendor:
        effective_vendor = normalize_vendor(vendor)
    elif resolved and _db:
        effective_vendor = normalize_vendor(infer_vendor_hint(resolved, _db) or "")
    multi_vendor_targets = len(vendors_in_targets) > 1
    single_vendor = bool(effective_vendor and len(vendors_in_targets) <= 1)
    if multi_vendor_targets:
        vendor_block = f"- **Vendors in scope:** {', '.join(sorted(vendors_in_targets))}\n- **Recommendation:** Use the single multi-vendor template so one playbook covers all vendors. Only multi-vendor reference templates are shown."
    elif effective_vendor:
        vendor_block = f"- **Vendor hint:** {effective_vendor}. Only vendor-specific reference templates are shown (multi-vendor hidden)."
    else:
        vendor_block = "- No vendor filter; reference templates may include multi-vendor and vendor-specific."
    reasoning.append("### 3. Vendor & templates\n" + vendor_block)

    references: list[dict] = []
    if skip_rag:
        references = _preview_references_category_disk_scan(
            categories,
            effective_vendor,
            single_vendor=single_vendor,
            multi_vendor_targets=multi_vendor_targets,
        )
        reasoning.append(
            "### 4. Reference templates (RAG)\n"
            f"- **Fast preview:** skipped vector/embedding search; **{len(references)}** template path(s) from category + vendor. "
            "Playbook Studio runs full RAG when you change targets or after debounced refresh."
        )
    elif _agent is not None and hasattr(_agent, "_retriever") and _agent._retriever:
        try:
            query = intent_description
            if categories:
                query = f"{query} Ansible playbook reference feature {' '.join(categories)}"
            if effective_vendor:
                query = f"{query} vendor {effective_vendor}"
            # When single vendor, filter RAG by vendor so multi_vendor docs are not retrieved
            rag_vendor_filter = effective_vendor if single_vendor else None
            raw = _agent._retriever.retrieve(
                query,
                k=4,
                vendor_filter=rag_vendor_filter,
                doc_type_filter="ansible_reference",
            )
            if not raw:
                raw = _agent._retriever.retrieve(query, k=3, vendor_filter=rag_vendor_filter)
            rag_note = f"- RAG retrieved **{len(raw)}** ansible_reference chunk(s). Matching playbook paths are listed below."
            seen_paths: set[str] = set()
            for r in raw:
                feat = (r.get("applicable_feature") or "").strip().lower()
                vend = (r.get("vendor") or "").strip().lower().replace("-", "_")
                if single_vendor and vend == "multi_vendor":
                    continue
                if multi_vendor_targets and vend != "multi_vendor":
                    continue
                doc_name = (r.get("doc_name") or "reference.md").strip()
                if not feat:
                    continue
                for playbook_name in ("playbook_template.yml", "playbook.yml"):
                    # Build path from parts so Windows and metadata casing don't break resolution
                    path = f"reference/{feat}/{vend}/{playbook_name}"
                    if path in seen_paths:
                        continue
                    ref_path = (ANSIBLE_BASE.joinpath(*path.split("/"))).resolve()
                    if ref_path.is_file():
                        seen_paths.add(path)
                        references.append({
                            "doc_name": doc_name,
                            "feature": feat,
                            "vendor": vend,
                            "path": path,
                        })
                        break
            if not references and (raw or categories):
                # Fallback: resolve by intent categories + vendor so templates still show when RAG metadata is missing/wrong
                seen_paths_fb: set[str] = set()
                fallback_vendors = [effective_vendor] if single_vendor else (["multi_vendor"] if multi_vendor_targets else (["multi_vendor", effective_vendor] if effective_vendor else ["multi_vendor"]))
                for feat in [c.strip().lower().replace("-", "_") for c in categories if c]:
                    for vend in fallback_vendors:
                        vend = (vend or "").strip().lower().replace("-", "_")
                        if not vend:
                            continue
                        for playbook_name in ("playbook_template.yml", "playbook.yml"):
                            path = f"reference/{feat}/{vend}/{playbook_name}"
                            if path in seen_paths_fb:
                                continue
                            ref_path = (ANSIBLE_BASE.joinpath(*path.split("/"))).resolve()
                            if ref_path.is_file():
                                seen_paths_fb.add(path)
                                references.append({"doc_name": ref_path.name, "feature": feat, "vendor": vend, "path": path})
                                break
                if not references and raw:
                    rag_note += "\n- No reference playbook files were found on disk for the retrieved docs; you can add references manually below."
                elif references:
                    rag_note += "\n- Templates were inferred from intent categories and vendor; you can remove or add more below."
            elif not references and raw:
                rag_note += "\n- No reference playbook files were found on disk; you can add references manually below."
            elif references:
                rag_note += "\n- You can remove or add templates in **Reference templates** before generating."
            reasoning.append("### 4. Reference templates (RAG)\n" + rag_note)
        except Exception as exc:
            logger.debug("Preview RAG retrieval failed: %s", exc)
            reasoning.append(f"### 4. Reference templates (RAG)\n- RAG retrieval failed: {exc}. You can add reference templates manually below.")
    else:
        reasoning.append("### 4. Reference templates (RAG)\n- RAG retriever is not available. Add reference templates manually if needed.")

    parsed_dict["device_deduction"] = device_deduction
    # Refactored pipeline trace (segments, categories, catalog validation, cross-check, interactive asks).
    if parsed_dict:
        if resolved and any(str(x).strip() for x in resolved):
            _strip_device_picker_shortlist_after_confirmation(parsed_dict)
        try:
            from ansible.intent_pipeline.orchestrator import build_pipeline_trace, merge_cross_check_into_dict

            _tr = build_pipeline_trace(intent_description, parsed_dict, _db)
            parsed_dict["intent_pipeline"] = _tr
            parsed_dict = merge_cross_check_into_dict(parsed_dict, _tr["cross_check"])
            _seg = _tr.get("segment_summary") or ""
            _cbs = _tr.get("categories_by_segment")
            if _seg or _cbs:
                reasoning.insert(
                    1,
                    "### 1a. Intent pipeline (refactored)\n"
                    f"- **Split / summary:** {_seg or 'single segment'}\n"
                    f"- **Categories per segment:** `{_cbs}`\n"
                    f"- **Catalog missing fields:** {_tr.get('catalog_missing_fields') or []}\n",
                )
        except Exception as _ipt_exc:
            logger.debug("Attach intent_pipeline in preview: %s", _ipt_exc)

    # Disambiguation: ambiguous "Switch 1 and Switch 2" without role keywords (computed earlier).
    if switch_disambiguation_early:
        parsed_dict["disambiguation"] = switch_disambiguation_early
        # Put it near the top so user sees it immediately.
        reasoning.insert(
            1,
            "### 1b. Clarification needed\n"
            "- **Your prompt is ambiguous** (e.g. “Switch 1/2” could be core, distribution, or access).\n"
            "- Please pick one of the options in the chat to proceed.",
        )
    else:
        parsed_dict["disambiguation"] = None

    return categories, references, parsed_dict, policy_approved, reasoning, resolved


@router.post(
    "/preview-intent",
    summary="Preview intent categories and RAG reference templates before generate",
)
async def preview_intent(
    req:  PreviewIntentRequest,
    user: dict = Depends(get_current_user),
):
    """
    Returns intent categories (e.g. VLAN, interface) and which reference templates
    RAG would use, so the UI can show an overlay and let the user remove irrelevant
    refs or confirm before calling create-and-plan.
    """
    categories, references, parsed_intent, policy_approved, reasoning, resolved_hosts = _preview_parse_and_rag(
        intent_description=req.intent_description,
        target_hosts=req.target_hosts,
        vendor=req.vendor,
        feature=req.feature,
        provider=req.provider,
        model=req.model,
        ollama_base_url=req.ollama_base_url,
        request_params=req.params,
        skip_rag=bool(req.skip_rag),
    )
    # Merge request params (e.g. interfaces_to_shutdown, vlan_id, vlan_name, target_hosts) into parsed intent
    if req.params:
        p = dict(parsed_intent.get("parameters") or {})
        p.update(
            {
                k: v
                for k, v in req.params.items()
                if v is not None and k != "secondary_intents"
            }
        )
        _coerce_params_for_validation(p)
        parsed_intent["parameters"] = p
        _merge_req_params_secondary_intents_into_parsed_intent(parsed_intent, req.params)
        strip_stale_secondary_params_after_primary_authoritative(
            parsed_intent,
            req.params,
        )
        # Same configure_interface coercion (access/trunk/subnet propagation) as primary ``parameters``.
        for _sec in parsed_intent.get("secondary_intents") or []:
            if isinstance(_sec, dict) and isinstance(_sec.get("parameters"), dict):
                _coerce_params_for_validation(_sec["parameters"])
        # So that validator and downstream see resolved devices when user supplies target_hosts/target_devices
        if req.params.get("target_hosts"):
            th = req.params["target_hosts"]
            parsed_intent["resolved_devices"] = th if isinstance(th, list) else [th] if th else list(parsed_intent.get("resolved_devices") or [])
        elif req.params.get("target_devices"):
            td = req.params["target_devices"]
            parsed_intent["resolved_devices"] = td if isinstance(td, list) else [td] if td else list(parsed_intent.get("resolved_devices") or [])
        _strip_device_picker_shortlist_after_confirmation(parsed_intent)

    # Authoritative device scope: when the client sends explicit hostnames (chat picker,
    # Studio chips, or params), replace any bulk LLM ``resolved_devices`` and drop stale
    # device-confirmation reasoning from the parse phase.
    _explicit_coalesced = _coalesce_explicit_preview_target_hosts(req)
    if _explicit_coalesced:
        _resolved_explicit = _resolve_target_hosts(_explicit_coalesced)
        if _db is not None:
            _valid_hosts, _ = _filter_resolved_hosts_to_planned_inventory(_resolved_explicit, _db)
            _resolved_explicit = list(_valid_hosts)
        if _resolved_explicit:
            parsed_intent["resolved_devices"] = list(_resolved_explicit)
            resolved_hosts = list(_resolved_explicit)
            parsed_intent.pop("pending_devices_disambiguation", None)
            parsed_intent["inventory_unknown_device_tokens"] = []
            reasoning[:] = [
                r
                for r in reasoning
                if not (
                    isinstance(r, str)
                    and (
                        r.startswith("### 1c. Target device(s) need confirmation")
                        or r.startswith("### 1c. Select target device(s)")
                        or r.startswith("### 1c. Target scope (numeric selection)")
                    )
                )
            ]
        else:
            resolved_hosts = list(parsed_intent.get("resolved_devices") or [])
    else:
        resolved_hosts = list(parsed_intent.get("resolved_devices") or [])
    _strip_device_picker_shortlist_after_confirmation(parsed_intent)
    # Re-check vague bulk resolution after param merge (stale resolved_hosts from _preview_parse_and_rag otherwise).
    _explicit_targets_in_request = bool(
        req.target_hosts
        or (
            req.params
            and (req.params.get("target_hosts") or req.params.get("target_devices"))
        )
    )
    if (
        not _explicit_targets_in_request
        and not parsed_intent.get("disambiguation")
        and resolved_hosts
        and _target_resolution_is_unclear(
            req.intent_description,
            resolved_hosts,
            list((parsed_intent.get("device_deduction") or {}).get("mapping") or []),
            _db,
            explicit_user_targets=_explicit_targets_in_request,
        )
    ):
        parsed_intent["resolved_devices"] = []
        resolved_hosts = []
        parsed_intent["device_deduction"] = _deduce_target_devices(req.intent_description, [])

    # Deterministic VLAN id/name from natural language (LLM often drops them on long device phrases).
    merge_extracted_vlan_params_into_parsed_intent(parsed_intent, req.intent_description or "")

    # Tree engine (Phase 1): root-first enforcement before schema validation.
    tree_res = None
    devices_disambiguation: Optional[Dict[str, Any]] = None
    try:
        tree_res = evaluate_intent_tree(
            intent_description=req.intent_description,
            parsed_intent=parsed_intent,
            resolved_hosts=list(resolved_hosts or []),
            db_manager=_db,
            explicit_targets_from_request=_explicit_targets_in_request,
        )
        if tree_res.needs_target_devices:
            parsed_intent["resolved_devices"] = []
            resolved_hosts = []
            devices_disambiguation = _build_devices_disambiguation(
                req.intent_description,
                resolved_hosts,
                _db,
            )
    except Exception as exc:
        logger.debug("intent_tree_engine evaluation failed: %s", exc)

    pending_dd = parsed_intent.pop("pending_devices_disambiguation", None)
    if pending_dd:
        devices_disambiguation = pending_dd
        if not resolved_hosts and tree_res is not None:
            tree_res.needs_target_devices = True
            parsed_intent["resolved_devices"] = []
            resolved_hosts = []

    inv_bad = [
        str(x).strip()
        for x in (parsed_intent.get("inventory_unknown_device_tokens") or [])
        if str(x).strip() and not is_spurious_device_hint_token(str(x))
    ]
    if inv_bad and _db is not None and not devices_disambiguation:
        dd_inv = _build_devices_disambiguation_for_unknown_inventory_tokens(inv_bad, _db)
        if dd_inv:
            devices_disambiguation = dd_inv
        if not resolved_hosts and tree_res is not None:
            tree_res.needs_target_devices = True
            parsed_intent["resolved_devices"] = []
            resolved_hosts = []

    # Tree may flag missing required params (e.g. create_vlan vlan_id/name). Do **not** 422 here:
    # AnsibleChatPanel expects HTTP 200 + ``params_disambiguation`` to run the in-chat wizard
    # before opening Playbook Studio.

    _preview_user_iface = bool(
        req.params
        and (req.params.get("interfaces") or req.params.get("interfaces_config"))
    )
    coerce_vlan_database_only_intent(
        parsed_intent,
        req.intent_description or "",
        user_supplied_interface_params=_preview_user_iface,
    )

    params = dict(parsed_intent.get("parameters") or {})

    # Intent schema validation and resolution (per-action parameter dependency tree)
    params_disambiguation: Optional[Dict[str, Any]] = None

    # For interface intents, the LLM may still return `configure_interface` even when
    # the user asked to "turn on/enable/bring up" (admin-up) all interfaces.
    # In that case we must validate against `shutdown_interface` so VLAN leaves
    # are not required.
    inferred_interface_action: Optional[str] = None
    feat_l = str(parsed_intent.get("feature") or "").strip().lower().replace("-", "_")
    if feat_l == "interface":
        t_raw = str(req.intent_description or "").strip().lower()
        t = re.sub(r"\s+", " ", t_raw)
        shutdown_markers = (
            "admin-down",
            "admin down",
            "shutdown",
            "shut down",
            "disable",
            "turn off",
            "turn on",
            "enable",
            "bring up",
            "administratively up",
            "admin-up",
            "admin up",
            "bring down",
            "administratively down",
        )
        # If the prompt includes L2/L3 configuration hints, keep the LLM's configure_interface.
        looks_like_vlan_or_mode_config = any(k in t for k in ("trunk", "access vlan", "vlan", "ip address", "subnet", "routed"))
        if any(m in t for m in shutdown_markers) and not looks_like_vlan_or_mode_config:
            inferred_interface_action = "shutdown_interface"

    if inferred_interface_action:
        parsed_intent["action"] = inferred_interface_action

        # Align "all interfaces" execution flags with shutdown_interface templates.
        # This prevents configure_interface-specific leaves (like access_vlan) from being required.
        if intent_requests_all_interfaces(req.intent_description or "") and resolved_hosts:
            p = dict(parsed_intent.get("parameters") or {})
            # Remove configure-all flags if present (configure_interface path).
            p.pop("interfaces_config_use_nettopo", None)
            p.pop("interfaces_config", None)
            # Enable shutdown-all nettopo expansion.
            p["interfaces_shutdown_use_nettopo"] = True
            # Minimal placeholder so shutdown_interface.schema.json type validation passes.
            if not p.get("interfaces_to_shutdown"):
                p["interfaces_to_shutdown"] = ["ALL"]

            # Admin intent: for "turn on/enable/bring up" interfaces, set enabled=true.
            prompt_l = str(req.intent_description or "").lower()
            admin_up = any(
                x in prompt_l
                for x in (
                    "turn on",
                    "enable",
                    "bring up",
                    "administratively up",
                    "admin-up",
                    "admin up",
                    "no shutdown",
                )
            )
            admin_down = any(
                x in prompt_l
                for x in (
                    "turn off",
                    "disable",
                    "shut down",
                    "shutdown",
                    "bring down",
                    "administratively down",
                    "admin-down",
                    "admin down",
                )
            )
            p["interfaces_admin_enabled"] = bool(admin_up and not admin_down)
            parsed_intent["parameters"] = p

    action_or_feature = (parsed_intent.get("action") or parsed_intent.get("feature") or "").strip() or None
    schema = get_schema_for_parsed_intent(parsed_intent)
    if schema:
        # Prefer schema action for JSON-Schema type validation (e.g. configure_interface.schema.json)
        type_validation_key = (schema.get("action") or "").strip() or action_or_feature
        missing_fields, _val_skip = validate_completeness(
            parsed_intent, schema, action_or_feature=type_validation_key
        )
        if missing_fields:
            planning_context_for_resolve: Optional[Dict[str, Any]] = None
            if resolved_hosts and _db is not None:
                try:
                    planning_context_for_resolve = await run_in_threadpool(
                        build_scoped_planning_context,
                        _db,
                        resolved_hosts,
                        feature=str(parsed_intent.get("feature") or ""),
                        query=req.intent_description or "",
                    )
                except Exception as exc:
                    logger.debug("Preview planning_context for resolver failed: %s", exc)
            context = {
                "planning_context": planning_context_for_resolve,
                "resolve_interfaces_fn": resolve_interfaces,
                "inventory_lookup_fn": _make_inventory_lookup_fn(),
            }
            safe_mode = req.safe_mode if getattr(req, "safe_mode", None) is not None else _get_safe_mode_default()
            _resolved_intent, _ask_user_fields, _resolution_trace = resolve_missing_fields(
                parsed_intent, schema, context=context, safe_mode=safe_mode
            )
            parsed_intent.update(_resolved_intent)
            params = dict(parsed_intent.get("parameters") or {})

        missing_final, validation_errors_final = validate_completeness(
            parsed_intent, schema, action_or_feature=type_validation_key
        )
        if validation_errors_final:
            parsed_intent["validation_errors"] = validation_errors_final
        else:
            parsed_intent.pop("validation_errors", None)

        questions = schema.get("questions") or {}
        disambiguation_fields = [
            {
                "field": field,
                "question": questions.get(field) or f"Please provide: {field}",
            }
            for field in missing_final
        ]
        if disambiguation_fields:
            params_disambiguation = {
                "question": "Please provide the following.",
                "fields": disambiguation_fields,
            }

        # Conditional leaves per schema action (e.g. configure_interface access/trunk/subnet).
        # JSON-Schema conditional errors are not converted into UI questions automatically,
        # so we provide best-effort controller-side disambiguation via
        # ``SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION`` (primary + secondary slices).
        schema_action_key = (schema.get("action") or "").strip().lower().replace("-", "_")
        _cond_hook = SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION.get(schema_action_key)
        if _cond_hook:
            shared_fields = _cond_hook(dict(parsed_intent.get("parameters") or {}))
            if shared_fields:
                fields_list: List[Dict[str, Any]] = list((params_disambiguation or {}).get("fields") or [])
                seen_fields = {f.get("field") for f in fields_list if isinstance(f, dict)}
                for e in shared_fields:
                    if e["field"] not in seen_fields:
                        fields_list.append(e)
                params_disambiguation = {
                    "question": (params_disambiguation or {}).get("question") or "Please provide the following.",
                    "fields": fields_list,
                }

    # VLAN (create_vlan): prompts may omit ID/name; merge disambiguation for primary and any
    # secondary_intents slice whose schema action is create_vlan (multi-intent composite prompts).
    # Use merged parameters so wizard-supplied vlan_id/vlan_name are not asked again (raw text unchanged).
    _params_merged = dict(parsed_intent.get("parameters") or {})
    _trusted_param_keys: Optional[Set[str]] = None
    if req.params:

        def _trusted_preview_param_value(val: Any) -> bool:
            if val is None or val == [] or val == {}:
                return False
            if isinstance(val, str) and not val.strip():
                return False
            return True

        _trusted_param_keys = {str(k) for k, v in req.params.items() if _trusted_preview_param_value(v)}
    params_disambiguation = merge_prompt_aware_disambiguation_for_schema(
        req.intent_description or "",
        schema,
        params_disambiguation,
        parameters=_params_merged,
        trusted_param_keys=_trusted_param_keys,
    )
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sec_schema = get_schema_for_parsed_intent(
            {
                "action": sec.get("action"),
                "feature": sec.get("feature"),
                "parameters": dict(sec.get("parameters") or {}),
            }
        )
        params_disambiguation = merge_prompt_aware_disambiguation_for_schema(
            req.intent_description or "",
            sec_schema,
            params_disambiguation,
            parameters=_params_merged,
            trusted_param_keys=_trusted_param_keys,
        )

    params_disambiguation = _append_secondary_intent_schema_disambiguation(
        parsed_intent,
        list(resolved_hosts or []),
        params_disambiguation,
    )

    # Rebuild pipeline trace after merges so cross-check / asks match authoritative targets + params.
    if parsed_intent and _db is not None:
        try:
            from ansible.intent_pipeline.orchestrator import build_pipeline_trace, merge_cross_check_into_dict

            _rh_sync = [str(h).strip() for h in (resolved_hosts or []) if str(h).strip()]
            parsed_intent["resolved_devices"] = list(_rh_sync)
            _tr_fresh = build_pipeline_trace(req.intent_description or "", parsed_intent, _db)
            parsed_intent["intent_pipeline"] = _tr_fresh
            parsed_intent = merge_cross_check_into_dict(parsed_intent, _tr_fresh["cross_check"])
        except Exception as _tr_refresh_exc:
            logger.debug("Refresh intent_pipeline before interactive merge: %s", _tr_refresh_exc)

    params_disambiguation, devices_disambiguation = _merge_intent_pipeline_interactive_asks(
        parsed_intent,
        params_disambiguation,
        devices_disambiguation,
        _db,
        preview_resolved_hosts=list(resolved_hosts or []),
    )

    # Refresh params after schema/VLAN mutations; clear stale LLM flag when interfaces are present.
    params = dict(parsed_intent.get("parameters") or {})
    if interface_intent_params_satisfied(params):
        parsed_intent["requires_resolution"] = False

    # Build a lightweight, structured reasoning trace for the UI (no raw LLM chain-of-thought).
    device_deduction: Dict[str, Any] = dict(
        parsed_intent.get("device_deduction") or params.get("device_deduction") or {}
    )
    mapping = device_deduction.get("mapping") or []
    reasoning_steps: list[dict] = []
    # Step 1: parsed intent summary.
    try:
        reasoning_steps.append({
            "title": "1. Parse user intent",
            "detail": (
                f"type={parsed_intent.get('intent_type')}  "
                f"action={parsed_intent.get('action')}  "
                f"feature={parsed_intent.get('feature') or '(auto)'}  "
                f"vendor={parsed_intent.get('vendor') or '(auto)'}"
            ).strip(),
        })
        _ip_tr = parsed_intent.get("intent_pipeline")
        if isinstance(_ip_tr, dict) and (_ip_tr.get("segment_summary") or _ip_tr.get("categories_by_segment")):
            reasoning_steps.append({
                "title": "1a. Pipeline — segments & categories",
                "detail": (
                    f"{_ip_tr.get('segment_summary', '')} | "
                    f"categories_by_segment={_ip_tr.get('categories_by_segment')}"
                ).strip(),
            })
    except Exception:
        pass
    # Step 2: device resolution summary.
    try:
        if resolved_hosts:
            host_lines = []
            for host in resolved_hosts:
                reason = ""
                for entry in mapping:
                    if entry.get("hostname") == host:
                        reason = str(entry.get("reason") or "").strip()
                        break
                if reason:
                    host_lines.append(f"- {host}: {reason}")
                else:
                    host_lines.append(f"- {host}")
            reasoning_steps.append({
                "title": "2. Resolve target devices",
                "detail": "\n".join(host_lines) if host_lines else ", ".join(resolved_hosts),
            })
        elif schema and "target_devices" in (schema.get("required") or []):
            reasoning_steps.append({
                "title": "2. Resolve target devices",
                "detail": (
                    "No targets selected — the prompt did not clearly scope devices. "
                    "Pick hostnames in Target inventory or answer **Which devices?** when shown."
                ),
            })
    except Exception:
        pass
    # Step 3: template / reference selection (high level only).
    try:
        ref_summaries = []
        for ref in references:
            ref_summaries.append(
                f"- {ref.get('path') or ref.get('doc_name')}: "
                f"feature={ref.get('feature')} vendor={ref.get('vendor')}"
            )
        if ref_summaries:
            reasoning_steps.append({
                "title": "3. Select reference templates",
                "detail": "\n".join(ref_summaries),
            })
    except Exception:
        pass

    # Interface disambiguation + cross-check names against planned inventory (wrong Gi0/1 vs Ethernet0/1).
    interfaces_disambiguation: Optional[Dict[str, Any]] = None
    device_disambiguation = parsed_intent.get("disambiguation")
    schema_action_iface = (schema.get("action") or "").strip().lower().replace("-", "_") if schema else ""
    feat_lower_preview = str(parsed_intent.get("feature") or "").strip().lower()
    has_iface_list = interface_intent_params_satisfied(params)
    vlan_database_only_preview = is_vlan_database_only_intent(
        feature_lower=feat_lower_preview,
        schema_action=schema_action_iface,
        parameters=params,
        intent_description=req.intent_description or "",
    )

    all_if_shutdown_expanded_preview = False
    planning_context_preview: Optional[Dict[str, Any]] = None
    if (
        _db is not None
        and resolved_hosts
        and not device_disambiguation
        and schema_action_iface in ("shutdown_interface", "configure_interface")
        and intent_requests_all_interfaces(req.intent_description or "")
    ):
        try:
            p_ai = dict(parsed_intent.get("parameters") or {})
            if schema_action_iface == "shutdown_interface":
                # Shutdown-all: force per-host shutdown-only names from `nettopo_interfaces` (mgmt excluded).
                p_ai.pop("interfaces_to_shutdown", None)
                if isinstance(p_ai.get("interfaces"), list):
                    p_ai.pop("interfaces", None)
                if isinstance(p_ai.get("interfaces_config"), list):
                    p_ai.pop("interfaces_config", None)
                p_ai.pop("interfaces_config_use_nettopo", None)
                p_ai.pop("interfaces_shutdown_use_nettopo", None)
                p_ai["interfaces_shutdown_use_nettopo"] = True
            else:
                # Configure-all: keep one LLM/config default object (mode/VLAN/IP) so schema validation passes,
                # but replace interface *names* via per-host nettopo interfaces at template runtime.
                default_obj: Dict[str, Any] = {}
                raw_ifaces = p_ai.get("interfaces")
                if isinstance(raw_ifaces, list) and raw_ifaces:
                    first = raw_ifaces[0]
                    if isinstance(first, dict):
                        default_obj = dict(first)
                elif isinstance(p_ai.get("interfaces_config"), list) and p_ai.get("interfaces_config"):
                    first = p_ai.get("interfaces_config")[0]
                    if isinstance(first, dict):
                        default_obj = dict(first)

                # Ensure list exists (schema requires `interfaces` for configure_interface).
                if not default_obj:
                    default_obj = {"name": "ALL", "mode": "access"}
                # Placeholder name; templates will overwrite with per-host nettopo names.
                default_obj["name"] = str(default_obj.get("name") or "ALL")

                p_ai.pop("interfaces_to_shutdown", None)
                p_ai.pop("interfaces_config", None)
                p_ai.pop("interfaces_shutdown_use_nettopo", None)
                p_ai["interfaces_config_use_nettopo"] = True
                p_ai["interfaces"] = [default_obj]

            parsed_intent["parameters"] = p_ai
            params = p_ai
            has_iface_list = interface_intent_params_satisfied(p_ai)
            all_if_shutdown_expanded_preview = True
            if has_iface_list:
                parsed_intent["requires_resolution"] = False
        except Exception as exc:
            logger.debug("Preview all-interfaces expansion failed: %s", exc)

    # Per-device interface list from planned context when "all interfaces" uses nettopo expansion.
    if (
        all_if_shutdown_expanded_preview
        and resolved_hosts
        and _db is not None
        and not device_disambiguation
    ):
        try:
            if planning_context_preview is None:
                planning_context_preview = await run_in_threadpool(
                    build_scoped_planning_context,
                    _db,
                    resolved_hosts,
                    feature="interface",
                    query=req.intent_description or "",
                )
            _pif = build_planned_all_interfaces_preview(planning_context_preview, resolved_hosts)
            parsed_intent["planned_all_interfaces_preview"] = _pif
            note = str(_pif.get("note") or "").strip()
            lines_pf: List[str] = [
                "### 5. Planned interfaces (all-interfaces scope)",
            ]
            if note:
                lines_pf.append(f"- {note}")
            else:
                lines_pf.append("- From planned network context (management-style interfaces excluded):")
            for devp in _pif.get("devices") or []:
                if not isinstance(devp, dict):
                    continue
                hn = str(devp.get("hostname") or "")
                cnt = int(devp.get("interface_count") or 0)
                iflst = [
                    str(i.get("name") or "")
                    for i in (devp.get("interfaces") or [])
                    if isinstance(i, dict) and str(i.get("name") or "").strip()
                ]
                preview_names = ", ".join(iflst[:24])
                if len(iflst) > 24:
                    preview_names += ", …"
                lines_pf.append(
                    f"  - **{hn}**: **{cnt}** interface(s) — {preview_names or '(none listed)'}"
                )
            if not _pif.get("devices"):
                lines_pf.append(
                    "  - (No matching devices in planning context — confirm **Target inventory** hostnames.)"
                )
            reasoning.append("\n".join(lines_pf))
        except Exception as exc:
            logger.debug("Preview planned all-interfaces summary failed: %s", exc)

    iface_names_invalid = False
    inv_validation_result: Optional[Any] = None
    _preview_secondary_intents: List[Dict[str, Any]] = list(
        parsed_intent.get("secondary_intents") or []
    )
    _preview_iface_override = explicit_interface_names_from_request_params(req.params) if req.params else []

    if (
        not all_if_shutdown_expanded_preview
        and _db is not None
        and resolved_hosts
        and should_validate_interface_names_against_inventory(
            schema_action_iface,
            feat_lower_preview,
            params,
            secondary_intents=_preview_secondary_intents or None,
            interface_names_override=_preview_iface_override or None,
        )
    ):
        try:
            pf = (
                "interface"
                if schema_action_iface
                in (
                    "shutdown_interface",
                    "configure_interface",
                    "configure_acl",
                    "configure_qos",
                    "configure_ospf",
                    "configure_static_route",
                )
                else "vlan"
            )
            planning_context_preview = await run_in_threadpool(
                build_scoped_planning_context,
                _db,
                resolved_hosts,
                feature=pf,
                query=req.intent_description or "",
            )
            inv_validation_result = validate_interface_params_against_context(
                params,
                resolved_hosts,
                planning_context_preview,
                secondary_intents=_preview_secondary_intents or None,
                interface_names_override=_preview_iface_override or None,
            )
            iface_names_invalid = not inv_validation_result.ok
            # Static route next-hop IP semantic validation against planned context.
            if schema_action_iface == "configure_static_route":
                vr_nh = validate_static_route_next_hop_ip_against_context(
                    params, resolved_hosts, planning_context_preview
                )
                if not vr_nh.ok:
                    if inv_validation_result and not inv_validation_result.ok:
                        inv_validation_result.invalid_entities.extend(vr_nh.invalid_entities)
                        inv_validation_result.validation_notice = "\n\n".join(
                            [inv_validation_result.validation_notice, vr_nh.validation_notice]
                        ).strip()
                    else:
                        inv_validation_result = vr_nh
                    iface_names_invalid = True
        except Exception as exc:
            logger.debug("Preview interface inventory validation failed: %s", exc)

    # Static route: validate next_hop IP against planned interface subnets even when
    # no interface-like token exists (e.g. next_hop is an IP).
    if (
        not all_if_shutdown_expanded_preview
        and _db is not None
        and resolved_hosts
        and schema_action_iface == "configure_static_route"
    ):
        try:
            if planning_context_preview is None:
                planning_context_preview = await run_in_threadpool(
                    build_scoped_planning_context,
                    _db,
                    resolved_hosts,
                    feature="interface",
                    query=req.intent_description or "",
                )
            vr_nh = validate_static_route_next_hop_ip_against_context(
                params, resolved_hosts, planning_context_preview
            )
            if not vr_nh.ok:
                if inv_validation_result and not inv_validation_result.ok:
                    inv_validation_result.invalid_entities.extend(vr_nh.invalid_entities)
                    inv_validation_result.validation_notice = "\n\n".join(
                        [inv_validation_result.validation_notice, vr_nh.validation_notice]
                    ).strip()
                else:
                    inv_validation_result = vr_nh
                iface_names_invalid = True
        except Exception as exc:
            logger.debug("Preview static_route next_hop validation failed: %s", exc)

    # Reasoning must reflect merged client params (Playbook Studio: interfaces, trunk, native VLAN).
    # `parsed_intent["parameters"]` is updated earlier, but re-merge `req.params` here so a single
    # preview call always refreshes the "Interfaces to configure" line from authoritative UI keys.
    _params_for_reasoning = dict(parsed_intent.get("parameters") or {})
    if req.params:
        for k, v in req.params.items():
            if k == "secondary_intents":
                continue
            if v is not None:
                _params_for_reasoning[k] = v
    _params_for_reasoning = merged_params_for_vlan_interfaces_reasoning(
        parsed_intent, _params_for_reasoning
    )
    refresh_preview_vlan_interfaces_reasoning_block(
        reasoning,
        merged_params=_params_for_reasoning,
        resolved_hosts=list(resolved_hosts or []),
        schema_action=schema_action_iface,
        feature_lower=feat_lower_preview,
    )

    if inv_validation_result is not None:
        parsed_intent["interfaces_inventory_validation"] = {
            "ok": bool(getattr(inv_validation_result, "ok", True)),
            "validation_notice": str(getattr(inv_validation_result, "validation_notice", "") or ""),
            "invalid_entities": list(getattr(inv_validation_result, "invalid_entities", []) or []),
        }
    if (
        iface_names_invalid
        and inv_validation_result is not None
        and not getattr(inv_validation_result, "ok", True)
        and reasoning
    ):
        _annotate_reasoning_interfaces_with_planned_inventory(
            reasoning,
            inv_result=inv_validation_result,
            resolved_hosts=list(resolved_hosts or []),
        )

    needs_interface_picker = (
        not all_if_shutdown_expanded_preview
        and not device_disambiguation
        and bool(resolved_hosts)
        and _db is not None
        and (
            (
                schema_action_iface in ("shutdown_interface", "configure_interface")
                and (not has_iface_list or iface_names_invalid)
            )
            or (
                iface_names_invalid
                and (schema_action_iface == "create_vlan" or feat_lower_preview == "vlan")
            )
            or (
                iface_names_invalid
                and schema_action_iface
                in (
                    "configure_acl",
                    "configure_qos",
                    "configure_ospf",
                    "configure_static_route",
                )
            )
        )
        and not vlan_database_only_preview
    )

    if needs_interface_picker:
        try:
            pc = planning_context_preview
            if pc is None:
                feat_pc = (
                    "interface"
                    if schema_action_iface in ("shutdown_interface", "configure_interface")
                    else "vlan"
                )
                pc = await run_in_threadpool(
                    build_scoped_planning_context,
                    _db,
                    resolved_hosts,
                    feature=feat_pc,
                    query=req.intent_description or "",
                )
            if pc and pc.get("devices"):
                if schema_action_iface == "configure_interface":
                    iface_q = (
                        "Select interfaces to configure (mode can be refined in Playbook Studio)."
                    )
                elif schema_action_iface == "shutdown_interface":
                    iface_q = "Please select the interfaces you want to shutdown."
                else:
                    iface_q = "Please select valid interfaces from planned inventory."
                interfaces_disambiguation = _interfaces_disambiguation_from_planning(
                    pc, resolved_hosts, question=iface_q
                )
                if iface_names_invalid and inv_validation_result:
                    interfaces_disambiguation["invalid_entities"] = list(
                        inv_validation_result.invalid_entities
                    )
                    interfaces_disambiguation["validation_notice"] = inv_validation_result.validation_notice
                    interfaces_disambiguation["params_need_context_fix"] = True
        except Exception as exc:
            logger.debug("Preview interfaces disambiguation failed: %s", exc)

    # Planned-inventory interface checklist (checkboxes per device) for VLAN port binding.
    # Do NOT gate on iface_names_invalid: when the LLM invents "GigabitEthernet…" the user still
    # needs inventory checkboxes to pick the real "Ethernet…" name — validation + checklist stack.
    # Also include configure_interface + VLAN-on-port prompts: primary feature may be "interface"
    # even when the user spoke about VLAN + ports (composite or misclassified).
    interfaces_for_selection: Optional[Dict[str, Any]] = None
    vlan_iface_binding_prompt = user_prompt_requests_vlan_interface_binding(req.intent_description or "")
    vlan_has_iface_params = bool(
        (params.get("interfaces") or params.get("interfaces_config"))
    )
    has_secondary_configure_interface = any(
        isinstance(s, dict)
        and str(s.get("action") or "").strip().lower().replace("-", "_") == "configure_interface"
        for s in (parsed_intent.get("secondary_intents") or [])
    )
    show_planned_inventory_iface_checklist = (
        feat_lower_preview == "vlan"
        or (schema_action_iface == "configure_interface" and vlan_iface_binding_prompt)
        or (has_secondary_configure_interface and vlan_iface_binding_prompt)
    )
    if (
        not device_disambiguation
        and show_planned_inventory_iface_checklist
        and resolved_hosts
        and _db is not None
        and not vlan_database_only_preview
        and (vlan_iface_binding_prompt or vlan_has_iface_params)
    ):
        try:
            planning_context_vlan = planning_context_preview
            if planning_context_vlan is None:
                planning_context_vlan = await run_in_threadpool(
                    build_scoped_planning_context,
                    _db,
                    resolved_hosts,
                    feature="vlan",
                    query=req.intent_description or "",
                )
            if planning_context_vlan and planning_context_vlan.get("devices"):
                interfaces_for_selection = _interfaces_disambiguation_from_planning(
                    planning_context_vlan,
                    resolved_hosts,
                    question="Select interfaces to assign to this VLAN (optional).",
                )
        except Exception as exc:
            logger.debug("Preview interfaces_for_selection (vlan) failed: %s", exc)

    # Chat (AnsibleChatPanel): `interfaces_for_selection` alone only populates Playbook Studio.
    # Without `interfaces_disambiguation`, applyPreviewFollowUps skips the in-chat picker and
    # jumps straight to the Studio modal. Mirror the same checklist when the invalid-name path
    # did not already build `interfaces_disambiguation`, and the client has not yet submitted
    # interfaces via `req.params` (including composite `secondary_intents[].parameters.interfaces`).
    if (
        interfaces_for_selection
        and interfaces_disambiguation is None
        and not device_disambiguation
        and not all_if_shutdown_expanded_preview
        and not _req_params_has_explicit_interfaces(req.params)
    ):
        interfaces_disambiguation = dict(interfaces_for_selection)
        if inv_validation_result is not None and not getattr(inv_validation_result, "ok", True):
            interfaces_disambiguation["invalid_entities"] = list(
                getattr(inv_validation_result, "invalid_entities", []) or []
            )
            vn = str(getattr(inv_validation_result, "validation_notice", "") or "").strip()
            if vn:
                interfaces_disambiguation["validation_notice"] = vn
            interfaces_disambiguation["params_need_context_fix"] = bool(iface_names_invalid)

    params_need_context_fix = bool(iface_names_invalid)

    # One disambiguation per response: if we have both params and interfaces, prefer interface picker
    # only when the param wizard is *only* about interface fields. Previously, any ask_set that
    # contained "interfaces" (e.g. vlan_name + interfaces) wiped the whole wizard — hiding VLAN name.
    _iface_only_param_fields = frozenset({"interfaces", "interfaces_config", "interfaces_to_shutdown"})
    if params_disambiguation and interfaces_disambiguation:
        ask_set = {f["field"] for f in params_disambiguation.get("fields", [])}
        if ask_set == {"interfaces"} and schema_action_iface == "shutdown_interface":
            params_disambiguation = None
        elif iface_names_invalid and ask_set and ask_set.issubset(_iface_only_param_fields):
            params_disambiguation = None

    # Trunk allowed-VLANs: attach multi-select options from intent + planned context (chat UI).
    if params_disambiguation and resolved_hosts and _db is not None:
        try:
            pc_trunk = planning_context_preview
            if pc_trunk is None:
                pc_trunk = await run_in_threadpool(
                    build_scoped_planning_context,
                    _db,
                    resolved_hosts,
                    feature="vlan",
                    query=req.intent_description or "",
                )
            params_disambiguation = enrich_params_disambiguation_trunk_vlan_controls(
                params_disambiguation,
                planning_context=pc_trunk,
                parsed_intent=parsed_intent,
            )
        except Exception as exc:
            logger.debug("Preview trunk VLAN multi-select enrichment failed: %s", exc)

    schema_editable_fields: List[Dict[str, Any]] = (
        get_editable_fields_for_schema(schema) if schema else []
    )

    # Secondary intents: validate each; missing fields are merged into top-level params_disambiguation
    # (fields carry secondary_intent_index). Nested per-secondary wizard removed to avoid duplicate UI.
    secondary_intent_previews: List[Dict[str, Any]] = []
    for idx, sec in enumerate(parsed_intent.get("secondary_intents") or []):
        sec_action = (sec.get("action") or "").strip() or "configure"
        sec_feature = (sec.get("feature") or "").strip()
        sec_params = dict(sec.get("parameters") or {})
        sec_intent = {
            "action": sec_action,
            "feature": sec_feature,
            "parameters": sec_params,
            "resolved_devices": list(resolved_hosts),
        }
        sec_schema = get_schema_for_parsed_intent(sec_intent)
        type_key = (sec_schema.get("action") or "").strip() or sec_action if sec_schema else sec_action
        missing_sec, err_sec = validate_completeness(
            sec_intent, sec_schema or {}, action_or_feature=type_key if sec_schema else None
        )
        secondary_intent_previews.append({
            "intent_index": idx + 1,
            "action": sec_action,
            "feature": sec_feature,
            "missing_fields": missing_sec,
            "validation_errors": err_sec or [],
            "params_disambiguation": None,
        })

    # Authoritative client params (wizard / chat) win over LLM; then align Section 2 + inspector hosts.
    _merge_client_authoritative_preview_params(parsed_intent, req.params, db_manager=_db)
    rd_final = parsed_intent.get("resolved_devices")
    if isinstance(rd_final, list) and any(str(h).strip() for h in rd_final):
        resolved_hosts = [str(h).strip() for h in rd_final if str(h).strip()]
    _sync_preview_device_deduction_and_reasoning(
        parsed_intent,
        reasoning,
        req.intent_description,
        resolved_hosts,
        explicit_user_targets=_explicit_targets_in_request,
    )

    # Drop parser-only device disambiguation flags once hostnames are confirmed (picker / params).
    if resolved_hosts and any(str(h).strip() for h in resolved_hosts):
        parsed_intent["requires_disambiguation"] = False
        parsed_intent["disambiguation_message"] = ""
        # Do not keep a params wizard row asking for targets when hostnames are already resolved.
        if params_disambiguation:
            _target_field_names = frozenset({"target_devices", "target_hosts"})
            _pf = [
                f
                for f in (params_disambiguation.get("fields") or [])
                if isinstance(f, dict) and str(f.get("field") or "") not in _target_field_names
            ]
            if _pf:
                params_disambiguation = {**params_disambiguation, "fields": _pf}
            else:
                params_disambiguation = None

    intent_schema_inspector: Dict[str, Any] = {"slices": []}
    try:
        intent_schema_inspector = build_intent_schema_inspector_payload(
            parsed_intent,
            resolved_hosts=resolved_hosts,
        )
    except Exception as exc:
        logger.debug("build_intent_schema_inspector_payload failed: %s", exc, exc_info=True)
        intent_schema_inspector = {"slices": [], "error": str(exc)}

    return {
        "categories": categories,
        "references": references,
        "parsed_intent": parsed_intent,
        "policy_approved": policy_approved,
        "reasoning": reasoning,
        "target_hosts": resolved_hosts,
        "reasoning_trace": {
            "steps": reasoning_steps,
        },
        "interfaces_disambiguation": interfaces_disambiguation,
        "params_disambiguation": params_disambiguation,
        "schema_editable_fields": schema_editable_fields,
        "interfaces_for_selection": interfaces_for_selection,
        "schema_action": (schema.get("action") if schema else None) or None,
        "secondary_intent_previews": secondary_intent_previews,
        "params_need_context_fix": params_need_context_fix,
        "devices_disambiguation": devices_disambiguation,
        "intent_schema_inspector": intent_schema_inspector,
    }


class IntentParseWorkbenchRequest(BaseModel):
    """Lightweight intent parse + pipeline trace for the Intent Parse workbench (no RAG / create-and-plan)."""

    prompt:            str
    provider:          Optional[str] = None
    model:             Optional[str] = None
    ollama_base_url:   Optional[str] = None


INTENT_CATALOG_ROOT = ANSIBLE_BASE / "intent_catalog"
_TOP_LEVEL_INTENT_YAML = frozenset({"global_fields.yaml", "keyword_map.yaml", "operation_adapter.yaml"})


def _intent_catalog_allowed_relative_paths() -> List[str]:
    """Whitelisted editable paths under ``intent_catalog/`` (UI file picker)."""
    root = INTENT_CATALOG_ROOT
    if not root.is_dir():
        return []
    out: List[str] = []
    for name in sorted(_TOP_LEVEL_INTENT_YAML):
        if (root / name).is_file():
            out.append(name)
    cat_dir = root / "categories"
    if cat_dir.is_dir():
        for p in sorted(cat_dir.glob("*.yaml")):
            out.append(f"categories/{p.name}")
    return out


def _resolve_intent_catalog_file(rel: str) -> tuple[Path, str]:
    """
    Validate relative path and return (absolute Path, normalized rel with /).
    Raises HTTPException on traversal or unknown file.
    """
    raw = (rel or "").strip().replace("\\", "/").strip("/")
    if not raw.endswith(".yaml") or ".." in raw.split("/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid intent catalog path")
    parts = raw.split("/")
    if len(parts) == 1:
        if parts[0] not in _TOP_LEVEL_INTENT_YAML:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown intent catalog file")
    elif len(parts) == 2 and parts[0] == "categories":
        if not re.match(r"^[a-z0-9_-]+\.yaml$", parts[1], re.I):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid category YAML name")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Intent catalog path not allowed")

    full = (INTENT_CATALOG_ROOT.joinpath(*parts)).resolve()
    try:
        full.relative_to(INTENT_CATALOG_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Path escapes intent_catalog") from exc
    if raw not in _intent_catalog_allowed_relative_paths():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intent catalog file not in allowlist or missing on disk")
    return full, raw


class IntentCatalogFileWriteRequest(BaseModel):
    """Replace an intent catalog YAML file (admin only)."""

    content: str


class IntentCatalogSharedFieldModel(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    data_type: str = "string"
    required: bool = False


class IntentCatalogOperationUIModel(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    action: str = ""
    feature: str = ""
    required_parameters: List[str] = Field(default_factory=list)
    implicit_requires_targets: bool = False
    rules: List[str] = Field(default_factory=list)


class IntentCatalogCategoryUIPutBody(BaseModel):
    """Structured category file payload (saved to ``categories/{key}.yaml``)."""

    display_name: str = Field(min_length=1, max_length=200)
    category_description: str = ""
    legacy_feature: str = ""
    shared_fields: List[IntentCatalogSharedFieldModel] = Field(default_factory=list)
    operations: List[IntentCatalogOperationUIModel] = Field(default_factory=list)


def _intent_catalog_payload() -> Dict[str, Any]:
    """Serialize ``intent_catalog/`` + operation adapter for the workbench tree UI."""
    from ansible.intent_pipeline.operation_adapter import list_canonical_operations
    from ansible.intent_pipeline.schema_loader import load_intent_catalog

    catalog = load_intent_catalog()
    categories_out: List[Dict[str, Any]] = []
    for key in sorted(catalog.categories.keys()):
        cat = catalog.categories[key]
        ops = cat.get("operations") or {}
        shared = cat.get("shared_fields") or []
        if not isinstance(shared, list):
            shared = []
        categories_out.append(
            {
                "key": key,
                "display_name": cat.get("display_name") or key,
                "category_description": cat.get("category_description") or "",
                "legacy_feature": cat.get("legacy_feature"),
                "shared_fields": shared,
                "operations": [
                    {
                        "name": oname,
                        "action": (spec or {}).get("action"),
                        "feature": (spec or {}).get("feature"),
                        "required_parameters": (spec or {}).get("required_parameters") or [],
                        "rules": (spec or {}).get("rules") or [],
                    }
                    for oname, spec in ops.items()
                ],
            }
        )
    return {
        "version": 1,
        "global_fields": catalog.global_fields,
        "keyword_map": catalog.keyword_map,
        "categories": categories_out,
        "canonical_adapter": {"mappings": list_canonical_operations()},
    }


def _parse_intent_for_workbench(
    prompt: str,
    provider: Optional[str],
    model: Optional[str],
    ollama_base_url: Optional[str],
) -> str:
    from llm.intent import IntentParser

    topo = _build_intent_parse_context(prompt)
    prov = _provider_for_request(provider, model, ollama_base_url)
    parser = IntentParser(provider=prov, db_manager=_db)
    return parser.parse(prompt, topology_context=topo)


@router.get(
    "/intent-catalog",
    summary="Intent catalog (categories, operations, keyword map) for Intent Parse workbench",
)
async def get_intent_catalog_endpoint(user: dict = Depends(get_current_user)):
    try:
        return _intent_catalog_payload()
    except Exception as exc:
        logger.warning("intent-catalog failed: %s", exc)
        raise HTTPException(503, f"Intent catalog unavailable: {exc}") from exc


_CATEGORY_KEY_RE = re.compile(r"^[a-z0-9_-]+$", re.I)


def _validate_intent_category_put_body(category_key: str, body: IntentCatalogCategoryUIPutBody) -> None:
    from ansible.intent_catalog_serde import DATA_TYPES

    _ident_re = re.compile(r"^[a-z][a-z0-9_]*$", re.I)
    names_sf = [sf.name.strip() for sf in body.shared_fields]
    if len(names_sf) != len(set(names_sf)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Duplicate shared field names")
    for nm in names_sf:
        if not _ident_re.match(nm):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Invalid shared field name (start with letter, then letters/digits/_): {nm!r}",
            )
    for sf in body.shared_fields:
        dt = (sf.data_type or "string").lower()
        if dt not in DATA_TYPES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Invalid data_type for field {sf.name!r}: {sf.data_type!r}",
            )
    op_names = [op.name.strip() for op in body.operations]
    if len(op_names) != len(set(op_names)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Duplicate operation names")
    for op in body.operations:
        if not _ident_re.match(op.name):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Invalid operation name (start with letter, then letters/digits/_): {op.name!r}",
            )
        rp = [str(x).strip() for x in (op.required_parameters or []) if str(x).strip()]
        if len(rp) != len(set(rp)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Duplicate required_parameters for operation {op.name!r}",
            )


@router.get(
    "/intent-catalog/category/{category_key}/ui-model",
    summary="Structured category schema for admin UI (from local YAML file)",
)
async def get_intent_category_ui_model(
    category_key: str,
    user: dict = Depends(get_current_user),
):
    from ansible.intent_catalog_serde import load_category_ui_dict

    if not _CATEGORY_KEY_RE.match(category_key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid category key")
    try:
        return load_category_ui_dict(category_key)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category file not found") from None
    except Exception as exc:
        logger.warning("intent category ui-model read failed: %s", exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.put(
    "/intent-catalog/category/{category_key}/ui-model",
    summary="Save category schema to local YAML (admin only); reloads catalog",
)
async def put_intent_category_ui_model(
    category_key: str,
    body: IntentCatalogCategoryUIPutBody,
    user: dict = Depends(require_role("admin")),
):
    import yaml

    from ansible.intent_catalog_serde import (
        build_category_yaml_document,
        category_yaml_path,
        dump_category_yaml,
    )
    from ansible.intent_pipeline.schema_loader import reload_catalog

    if not _CATEGORY_KEY_RE.match(category_key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid category key")
    _validate_intent_category_put_body(category_key, body)

    path = category_yaml_path(category_key)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category file not found")

    ui_dict: Dict[str, Any] = {
        "category_key": category_key,
        "display_name": body.display_name,
        "category_description": body.category_description or "",
        "legacy_feature": (body.legacy_feature or "").strip() or category_key,
        "shared_fields": [sf.model_dump() for sf in body.shared_fields],
        "operations": [op.model_dump() for op in body.operations],
    }
    doc = build_category_yaml_document(ui_dict)
    text = dump_category_yaml(doc)
    try:
        yaml.safe_load(text)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid YAML after build: {exc}") from exc

    backup = path.read_text(encoding="utf-8")
    try:
        path.write_text(text, encoding="utf-8")
        reload_catalog()
    except Exception as exc:
        try:
            path.write_text(backup, encoding="utf-8")
            reload_catalog()
        except Exception as restore_exc:
            logger.error("Intent category restore failed after bad save: %s", restore_exc)
        logger.warning("Intent category save rejected: %s", exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Catalog reload failed after save (file restored): {exc}",
        ) from exc

    logger.info("Intent category ui-model saved by %s: %s", user.get("username"), category_key)
    return {"category_key": category_key, "status": "saved"}


@router.get(
    "/intent-catalog/editable-files",
    summary="List intent_catalog/*.yaml paths editable from the Intent Parse workbench",
)
async def list_intent_catalog_editable_files(user: dict = Depends(get_current_user)):
    return {"paths": _intent_catalog_allowed_relative_paths()}


@router.get(
    "/intent-catalog/file/{file_path:path}",
    summary="Read raw YAML from intent_catalog (global, keyword_map, categories, operation_adapter)",
)
async def read_intent_catalog_file(
    file_path: str,
    user: dict = Depends(get_current_user),
):
    full, norm = _resolve_intent_catalog_file(file_path)
    if not full.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return {"path": norm, "content": full.read_text(encoding="utf-8")}


@router.put(
    "/intent-catalog/file/{file_path:path}",
    summary="Write intent_catalog YAML; validates YAML and reloads in-memory catalog (admin only)",
)
async def write_intent_catalog_file(
    file_path: str,
    req:  IntentCatalogFileWriteRequest,
    user: dict = Depends(require_role("admin")),
):
    import yaml

    from ansible.intent_pipeline.operation_adapter import reload_operation_adapter
    from ansible.intent_pipeline.schema_loader import reload_catalog

    full, norm = _resolve_intent_catalog_file(file_path)
    try:
        yaml.safe_load(req.content or "")
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid YAML: {exc}") from exc

    backup = full.read_text(encoding="utf-8") if full.is_file() else ""
    try:
        full.write_text(req.content or "", encoding="utf-8")
        reload_operation_adapter()
        reload_catalog()
    except Exception as exc:
        try:
            full.write_text(backup, encoding="utf-8")
            reload_operation_adapter()
            reload_catalog()
        except Exception as restore_exc:
            logger.error("Intent catalog restore failed after bad save: %s", restore_exc)
        logger.warning("Intent catalog save rejected: %s", exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Catalog reload failed after save (file restored): {exc}",
        ) from exc

    logger.info("Intent catalog file updated by %s: %s", user.get("username"), norm)
    return {"path": norm, "status": "saved"}


@router.post(
    "/intent-parse-workbench",
    summary="Parse a prompt with the refactored pipeline trace (split, categories, cross-check, asks)",
)
async def intent_parse_workbench(
    req:  IntentParseWorkbenchRequest,
    user: dict = Depends(get_current_user),
):
    from llm.intent import ParsedIntent

    from ansible.intent_pipeline.orchestrator import enrich_preview_response

    try:
        raw = await run_in_threadpool(
            _parse_intent_for_workbench,
            req.prompt or "",
            req.provider,
            req.model,
            req.ollama_base_url,
        )
    except Exception as exc:
        logger.warning("intent-parse-workbench parse failed: %s", exc)
        raise HTTPException(422, f"Intent parse failed: {exc}") from exc

    try:
        parsed = ParsedIntent.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(422, f"Invalid parsed intent JSON: {exc}") from exc

    flat = parsed.model_dump()
    enriched = enrich_preview_response(flat, req.prompt or "", _db)
    return {
        "parsed_intent_json": raw,
        "parsed_intent": enriched["parsed_intent"],
        "pipeline": enriched["pipeline"],
    }


def _list_reference_playbook_paths() -> list[dict]:
    """Scan reference/ for all playbook.yml and playbook_template.yml; return path, feature, vendor."""
    out: list[dict] = []
    ref_dir = ANSIBLE_BASE / "reference"
    if not ref_dir.exists():
        return out
    for feat_dir in ref_dir.iterdir():
        if not feat_dir.is_dir() or feat_dir.name.startswith("."):
            continue
        feature = feat_dir.name
        for vend_dir in feat_dir.iterdir():
            if not vend_dir.is_dir() or vend_dir.name.startswith("."):
                continue
            vendor = vend_dir.name
            for name in ("playbook_template.yml", "playbook.yml"):
                path = vend_dir / name
                if path.is_file():
                    rel = str(path.relative_to(ANSIBLE_BASE)).replace(os.sep, "/")
                    out.append({"path": rel, "feature": feature, "vendor": vendor})
    return sorted(out, key=lambda x: (x["feature"], x["vendor"]))


@router.get(
    "/reference-paths",
    summary="List all reference playbook paths (for Playbook Studio add-reference)",
)
async def list_reference_paths(user: dict = Depends(get_current_user)):
    """Return paths to every reference playbook so the UI can offer add/remove in the overlay."""
    return {"paths": _list_reference_playbook_paths()}


# ── M23: create-and-plan ───────────────────────────────────────────────────

class CreateAndPlanRequest(BaseModel):
    intent_description: str
    target_hosts:       List[str] = []
    vendor:             str = ""
    feature:            str = ""
    params:             Dict[str, Any] = {}
    device_id:          int = 0
    # When enabled, the generated playbook is validated with an Ansible check-mode
    # dry-run (no deployment/execution).
    verify_with_dry_run: bool = False
    auto_refine:        bool = False
    max_refinement_attempts: int = 1
    provider:           Optional[str] = None  # "gemini" | "ollama" for playbook LLM
    model:              Optional[str] = None
    ollama_base_url:    Optional[str] = None
    safe_mode:          Optional[bool] = None  # when None, server uses ANSIBLE_SAFE_MODE_DEFAULT (default true)


@router.post(
    "/create-and-plan",
    summary="Auto-create a job and generate a playbook from natural language intent",
)
async def create_and_plan(
    req:  CreateAndPlanRequest,
    user: dict = Depends(get_current_user),
):
    """
    Single-shot endpoint for the Ansible Studio AI chat:
    1. Creates a ChangeRequest + Job in the deploy pipeline.
    2. Generates an Ansible playbook via LLM (same as /plan-ansible).
    3. Stores the playbook on the job and returns it.
    """
    if _db is None or _pipeline is None:
        raise HTTPException(503, "Backend not fully initialized")

    plan_req, parsed_intent, policy = _resolve_backend_intent_request(req, user)

    # Resolve device_id: if caller passes 0 / unknown, look up by first target host,
    # or fallback to first available device to avoid FK violations.
    device_id = req.device_id
    if device_id == 0:
        with _db._connect() as conn:
            # 1. Try first target host
            if plan_req.target_hosts:
                row = conn.execute(
                    "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
                    (plan_req.target_hosts[0],),
                ).fetchone()
                if row:
                    device_id = row["id"]
            
            # 2. Fallback to any device if still not resolved
            if device_id == 0:
                row = conn.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()
                if row:
                    device_id = row["id"]

    if device_id == 0:
        raise HTTPException(
            400, "Device inventory is empty. Please add a device before generating playbooks."
        )

    logger.info("create_and_plan: Resolved device_id=%d for hosts=%s", device_id, plan_req.target_hosts)

    # Create the change request + job
    from deploy.models import ChangeRequest, Job, DeploymentRepository
    repo = DeploymentRepository(_db)

    try:
        # 1. New CR
        cr_id = repo.create_request(ChangeRequest(
            title=f"Ansible Studio: {req.intent_description[:60]}",
            description=req.intent_description,
            requester=user.get("username", "ansible_studio"),
        ))
        logger.info("create_and_plan: Created ChangeRequest id=%d", cr_id)

        # 2. New Job linked to first device (for bookkeeping)
        job_id = repo.create_job(Job(request_id=cr_id, device_id=device_id))
        logger.info("create_and_plan: Created Job id=%d linked to device_id=%d", job_id, device_id)
    except Exception as exc:
        logger.error("create_and_plan: Database error during CR/Job creation: %s", exc)
        if "FOREIGN KEY" in str(exc).upper():
             # Detailed FK debug: check if IDs exist
             with _db._connect() as conn:
                 cr_exists = conn.execute("SELECT 1 FROM change_requests WHERE id=?", (cr_id if 'cr_id' in locals() else -1,)).fetchone()
                 dev_exists = conn.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone()
                 logger.error("FK Debug: device_id=%d exists=%s, cr_id=%s exists=%s", 
                              device_id, bool(dev_exists), 
                              cr_id if 'cr_id' in locals() else 'N/A', bool(cr_exists))
        raise HTTPException(status_code=500, detail=f"Database record creation failed: {exc}")

    # Delegate to plan_ansible logic (pass through LLM provider/model for playbook generation)
    result = await plan_ansible(job_id=job_id, req=plan_req, user=user)
    return {
        "job_id":      job_id,
        "request_id":  cr_id,
        "status":      result.get("status"),
        "playbook":    result.get("playbook"),
        "artifact_type": result.get("artifact_type"),
        "entrypoint_path": result.get("entrypoint_path"),
        "files": result.get("files", []),
        "manifest": result.get("manifest", {}),
        "parsed_intent": parsed_intent,
        "policy": policy,
        "target_hosts": plan_req.target_hosts,
    }


# ── Template-based config generation (Config Engine as a helper) ─────────

class CreateFromTemplateRequest(BaseModel):
    vendor:       str
    feature:      str
    params:       Dict[str, Any] = {}
    target_hosts: List[str] = ["all"]
    device_id:    int = 0


@router.post(
    "/create-from-template",
    summary="Generate a playbook from a Config Engine template (no LLM needed)",
)
async def create_from_template(
    req:  CreateFromTemplateRequest,
    user: dict = Depends(get_current_user),
):
    """
    Deterministic path: render vendor config via the Config Engine's Jinja2
    templates, wrap the result in a minimal Ansible playbook, and create a
    ChangeRequest + Job — same pipeline as the LLM route.
    """
    if _db is None:
        raise HTTPException(503, "Backend not fully initialized")

    # ── 1. Render config via Config Engine ──────────────────────────────
    try:
        from config_engine.renderer import TemplateRenderer
        renderer = TemplateRenderer()
        config_text = renderer.render(req.vendor, req.feature, req.params)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, f"Template render failed: {exc}")
    except Exception as exc:
        logger.error("Template render error: %s", exc)
        raise HTTPException(500, f"Template rendering failed: {exc}")

    undo_text = None
    try:
        from config_engine.renderer import TemplateRenderer as _TR
        undo_text = _TR().render_undo(req.vendor, req.feature, req.params)
    except Exception:
        pass  # undo is optional

    # ── 2. Wrap in Ansible playbook YAML ────────────────────────────────
    hosts_str = ",".join(req.target_hosts) if req.target_hosts else "all"

    MODULE_MAP = {
        "cisco_ios":     ("cisco.ios.ios_config",   "commands"),
        "cisco_xe":      ("cisco.ios.ios_config",   "commands"),
        "huawei_vrp":    ("community.network.ce_config", "lines"),
        "juniper_junos": ("junipernetworks.junos.junos_config", "lines"),
    }
    module_name, lines_key = MODULE_MAP.get(req.vendor, ("cisco.ios.ios_config", "commands"))
    config_lines = [l for l in config_text.splitlines() if l.strip()]

    import yaml as _yaml
    playbook = _yaml.dump(
        [{
            "name": f"Config Engine: {req.feature} ({req.vendor})",
            "hosts": hosts_str,
            "gather_facts": False,
            "tasks": [{
                "name": f"Apply {req.feature} configuration",
                module_name: {lines_key: config_lines},
            }],
        }],
        default_flow_style=False,
        sort_keys=False,
    )

    execution_engine = "ansible_native" if req.vendor in {"cisco_ios", "juniper_junos"} else "ansible_cli"
    playbook_validation = validate_playbook_yaml(
        playbook_yaml=playbook,
        vendor=req.vendor,
        feature=req.feature,
        target_hosts=req.target_hosts,
        execution_engine=execution_engine,
    )
    if not playbook_validation.get("passed", False):
        raise HTTPException(
            422,
            {
                "message": "Generated playbook failed Step 10 safety validation.",
                "validation": playbook_validation,
            },
        )

    # ── 3. Create ChangeRequest + Job ───────────────────────────────────
    device_id = req.device_id
    if device_id == 0:
        with _db._connect() as conn:
            if req.target_hosts:
                row = conn.execute(
                    "SELECT id FROM devices WHERE hostname = ? LIMIT 1",
                    (req.target_hosts[0],),
                ).fetchone()
                if row:
                    device_id = row["id"]
            
            if device_id == 0:
                row = conn.execute("SELECT id FROM devices ORDER BY id LIMIT 1").fetchone()
                if row:
                    device_id = row["id"]

    if device_id == 0:
        raise HTTPException(
            400, "Device inventory is empty. Please add a device before using templates."
        )

    from deploy.models import ChangeRequest, Job, DeploymentRepository
    repo = DeploymentRepository(_db)
    cr_id = repo.create_request(ChangeRequest(
        title=f"Template: {req.vendor}/{req.feature}",
        description=f"Config Engine render of {req.feature} for {hosts_str}",
        requester=user.get("username", "ansible_studio"),
    ))
    job_id = repo.create_job(Job(request_id=cr_id, device_id=device_id))

    # Persist the playbook on the job
    with _db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET ansible_playbook = ?, generated_config = ?, "
            "rollback_config = ?, stage = 'plan', status = 'pending' WHERE id = ?",
            (playbook, config_text, undo_text, job_id),
        )

    logger.info(
        "Template-based playbook created job=%d vendor=%s feature=%s user=%s",
        job_id, req.vendor, req.feature, user.get("username"),
    )
    return {
        "job_id":       job_id,
        "request_id":   cr_id,
        "status":       "playbook_generated",
        "playbook":     playbook,
        "config":       config_text,
        "undo_config":  undo_text,
    }


# ── M23: file-server endpoints ────────────────────────────────────────────

@router.get("/files", summary="List ansible file tree (reference, generated, playbooks, roles, inventory, awx)")
async def list_files(user: dict = Depends(get_current_user)):
    tree = []
    for root_name in _ALLOWED_ROOTS:
        if root_name == "generated":
            tree.append({
                "type": "dir",
                "name": root_name,
                "path": root_name,
                "children": _build_generated_tree(),
            })
            continue
        root_dir = ANSIBLE_BASE / root_name
        if root_dir.exists():
            # Hide playbooks/generated and roles/generated here; they appear under AI Output
            exclude = ("generated",) if root_name in ("playbooks", "roles") else ()
            tree.append({
                "type": "dir",
                "name": root_name,
                "path": root_name,
                "children": _build_tree(root_dir, ANSIBLE_BASE, exclude_dir_names=exclude),
            })
    return {"tree": tree}


@router.get("/files/{file_path:path}", summary="Read a single ansible file")
async def read_file(file_path: str, user: dict = Depends(get_current_user)):
    path_to_read = file_path.replace("\\", "/").strip("/")
    if path_to_read.startswith("generated/"):
        real = _generated_virtual_to_real(path_to_read)
        if real:
            path_to_read = real
    path = _safe_path(path_to_read)
    if not path.exists():
        raise HTTPException(404, f"File not found: {file_path}")
    if path.is_dir():
        raise HTTPException(400, "Path is a directory, not a file")
    content = path.read_text(encoding="utf-8", errors="replace")
    st = path.stat()
    return {
        "path": file_path,
        "content": content,
        "size": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
    }


class WriteFileRequest(BaseModel):
    content: str


class CreateFileRequest(BaseModel):
    content: str = ""


class RenameRequest(BaseModel):
    from_path: str
    to_path: str


def _is_writable_relative_path(relative_path: str) -> bool:
    """True if path is under playbooks/, inventory/, or roles/generated/."""
    parts = Path(relative_path.replace("\\", "/").strip("/")).parts
    if not parts:
        return False
    if parts[0] in _WRITABLE_ROOTS:
        return True
    if parts[0] == "roles" and len(parts) >= 2 and parts[1] == "generated":
        return True
    return False


@router.put("/files/{file_path:path}", summary="Save edited ansible file content")
async def write_file(
    file_path: str,
    body:       WriteFileRequest,
    user:       dict = Depends(require_role("operator", "admin")),
):
    path_to_write = file_path.replace("\\", "/").strip("/")
    if path_to_write.startswith("generated/"):
        real = _generated_virtual_to_real(path_to_write)
        if real:
            path_to_write = real
    path = _safe_path(path_to_write)
    if not _is_writable_relative_path(path_to_write):
        raise HTTPException(
            403,
            "Only playbooks/, inventory/, and roles/generated/ (or generated/ in tree) are writable.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    logger.info("File written: %s by %s", file_path, user.get("username"))
    return {"path": file_path, "status": "saved"}


@router.post("/files/create-file/{file_path:path}", summary="Create a new blank ansible file")
async def create_file(
    file_path: str,
    body:      CreateFileRequest,
    user:      dict = Depends(require_role("operator", "admin")),
):
    path, rel_parts = _safe_writable_path(file_path)
    if len(rel_parts) < 2:
        raise HTTPException(400, "Provide a file path under playbooks/, inventory/, or roles/generated/")
    if path.exists():
        raise HTTPException(409, f"Path already exists: {file_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content or "", encoding="utf-8")
    logger.info("File created: %s by %s", file_path, user.get("username"))
    return {"path": file_path, "status": "created"}


@router.post("/files/create-folder/{folder_path:path}", summary="Create a new ansible folder")
async def create_folder(
    folder_path: str,
    user:        dict = Depends(require_role("operator", "admin")),
):
    path, rel_parts = _safe_writable_path(folder_path)
    if len(rel_parts) < 2:
        raise HTTPException(400, "Provide a folder path under playbooks/, inventory/, or roles/generated/")
    if path.exists() and not path.is_dir():
        raise HTTPException(409, f"Path exists as file: {folder_path}")
    path.mkdir(parents=True, exist_ok=True)
    logger.info("Folder created: %s by %s", folder_path, user.get("username"))
    return {"path": folder_path, "status": "created"}


@router.delete("/files/{target_path:path}", summary="Delete ansible file or folder")
async def delete_path(
    target_path: str,
    user:        dict = Depends(require_role("operator", "admin")),
):
    path_to_delete = target_path.replace("\\", "/").strip("/")
    # Virtual job folder (generated/job_XX): delete both playbooks/generated/job_XX and roles/generated/job_XX
    job_real_paths = _generated_job_real_paths(path_to_delete)
    if job_real_paths:
        deleted_any = False
        for real_path in job_real_paths:
            if not _is_writable_relative_path(real_path):
                continue
            try:
                path = _safe_path(real_path)
            except HTTPException:
                continue
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted_any = True
                logger.info("Path deleted: %s (from %s) by %s", real_path, target_path, user.get("username"))
        if not deleted_any:
            raise HTTPException(404, f"Path not found: {target_path}")
        return {"path": target_path, "status": "deleted"}
    if path_to_delete.startswith("generated/"):
        real = _generated_virtual_to_real(path_to_delete)
        if real:
            path_to_delete = real
    path, rel_parts = _safe_writable_path(path_to_delete)
    if len(rel_parts) == 1 and rel_parts[0] in _WRITABLE_ROOTS:
        raise HTTPException(403, f"Cannot delete writable root directory: {rel_parts[0]}")
    if not path.exists():
        raise HTTPException(404, f"Path not found: {target_path}")

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()

    logger.info("Path deleted: %s by %s", target_path, user.get("username"))
    return {"path": target_path, "status": "deleted"}


@router.post("/files/rename", summary="Rename or move a file or folder")
async def rename_path(
    body: RenameRequest,
    user: dict = Depends(require_role("operator", "admin")),
):
    from_raw = body.from_path.replace("\\", "/").strip("/")
    to_raw = body.to_path.replace("\\", "/").strip("/")
    if not from_raw or not to_raw:
        raise HTTPException(400, "from_path and to_path are required")
    if from_raw == to_raw:
        return {"from_path": from_raw, "to_path": to_raw, "status": "unchanged"}
    # Resolve virtual generated/ path for source
    if from_raw.startswith("generated/"):
        real = _generated_virtual_to_real(from_raw)
        if real:
            from_raw = real
    from_path = _safe_path(from_raw)
    to_path = _safe_path(to_raw)
    if not _is_writable_relative_path(from_raw):
        raise HTTPException(403, "Source path is not writable")
    if not _is_writable_relative_path(to_raw):
        raise HTTPException(403, "Destination path is not writable")
    if not from_path.exists():
        raise HTTPException(404, f"Path not found: {body.from_path}")
    if to_path.exists():
        raise HTTPException(409, f"Destination already exists: {body.to_path}")
    to_path.parent.mkdir(parents=True, exist_ok=True)
    from_path.rename(to_path)
    logger.info("Path renamed: %s -> %s by %s", body.from_path, body.to_path, user.get("username"))
    return {"from_path": body.from_path, "to_path": body.to_path, "status": "renamed"}


# ── M23: SSE job-output stream ──────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}/stream",
    summary="SSE stream of ansible job step output (text/event-stream)",
)
async def stream_job_output(
    job_id: int,
    token:  str = Query("", description="JWT bearer token (query param for EventSource)"),
):
    """
    Server-Sent Events stream of job_steps rows as they are written.
    Polls the DB every 1.5 seconds.  Terminates when the job reaches a
    terminal state (success / failed / rolled_back).

    Auth: JWT passed as ?token= query parameter because the browser's native
    EventSource API does not support custom headers.
    """
    # Validate token manually
    if _db is None:
        raise HTTPException(503, "Database not initialized")
    try:
        from auth.auth import decode_token
        decode_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or missing token")

    _TERMINAL = {"success", "failed", "rolled_back"}

    async def event_generator():
        last_step_id = 0
        while True:
            try:
                with _db._connect() as conn:
                    # Fetch new steps since last seen
                    new_steps = conn.execute(
                        "SELECT id, stage, status, details, created_at "
                        "FROM job_steps WHERE job_id = ? AND id > ? ORDER BY id",
                        (job_id, last_step_id),
                    ).fetchall()

                    job_row = conn.execute(
                        "SELECT status FROM jobs WHERE id = ?",
                        (job_id,),
                    ).fetchone()

                for step in new_steps:
                    last_step_id = step["id"]
                    import json as _json
                    data = _json.dumps({
                        "stage":      step["stage"],
                        "status":     step["status"],
                        "details":    (step["details"] or "")[-2000:],
                        "created_at": step["created_at"],
                    })
                    yield f"data: {data}\n\n"

                if job_row and job_row["status"] in _TERMINAL:
                    import json as _json
                    yield f"data: {_json.dumps({'done': True, 'status': job_row['status']})}\n\n"
                    return

            except Exception as exc:
                logger.warning("SSE stream error job=%d: %s", job_id, exc)

            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":                "no-cache",
            "X-Accel-Buffering":            "no",   # disable nginx buffering
            "Access-Control-Allow-Origin":  "*",
        },
    )


# =============================================================================
# M25 Phase-2 ─ Playbook Refine & Version History
# =============================================================================

class ValidationError(BaseModel):
    line:    Optional[int] = None
    message: str
    rule:    str = "syntax"


class RefineRequest(BaseModel):
    """Feedback from playbook validation / user review for AI self-remediation."""
    errors:   List[ValidationError] = []
    feedback: str = ""


class RefineResponse(BaseModel):
    job_id:           int
    version:          int
    refined_playbook: str
    changes:          List[str] = []
    iteration:        int = 1


_REFINE_SYSTEM = """\
You are a senior Ansible engineer.
The user will provide a YAML playbook and a list of validation errors plus feedback.
Return ONLY the corrected YAML playbook — no explanations, no markdown fences,
no commentary.  Preserve all existing working tasks.
Also prepend a YAML comment block listing what you changed, one change per line:
# Changes:
# - <change 1>
# - <change 2>
---
"""


async def _ai_refine_playbook(
    original: str,
    errors: list[dict],
    feedback: str,
    builder: Any = None,
) -> tuple[str, list[str]]:
    """Ask the LLM to fix a playbook given error/feedback."""
    if builder is None and _agent is None and _builder is None:
        raise HTTPException(503, "LLM not available for playbook refinement")

    # Prefer request-scoped builder, then agent, then global builder
    provider = None
    if builder is not None and hasattr(builder, "_provider"):
        provider = builder._provider
    elif _agent is not None:
        provider = _agent._provider
    elif hasattr(_builder, "_provider"):
        provider = _builder._provider

    if provider is None:
        raise HTTPException(503, "LLM provider not available")

    from llm.provider import LLMMessage
    error_lines = "\n".join(
        f"  Line {e.get('line','?')}: [{e.get('rule','?')}] {e.get('message','')}"
        for e in errors
    ) or "  (no specific errors listed)"

    user_content = (
        f"Original playbook:\n```yaml\n{original}\n```\n\n"
        f"Validation errors:\n{error_lines}\n\n"
        f"Additional feedback: {feedback or 'none'}\n\n"
        "Please fix all errors and return the corrected YAML only."
    )

    resp = await provider.generate(
        messages=[
            LLMMessage(role="system", content=_REFINE_SYSTEM),
            LLMMessage(role="user",   content=user_content),
        ],
        tools=None,
        temperature=0.1,
    )
    raw = resp.content.strip()

    # Strip markdown fences if the model ignored instructions
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            stripped = part.lstrip("yaml\n").strip()
            if "---" in stripped or "- name" in stripped:
                raw = stripped
                break

    # Extract change comments
    changes: list[str] = []
    yaml_lines = raw.split("\n")
    clean_lines: list[str] = []
    for line in yaml_lines:
        stripped = line.strip()
        if stripped.startswith("# ") and ":" in stripped:
            changes.append(stripped.lstrip("# ").strip())
        else:
            clean_lines.append(line)
    final_yaml = "\n".join(clean_lines).strip()

    return final_yaml, changes


def _append_playbook_version(
    db,
    job_id: int,
    playbook: str,
    notes: str = "",
    set_current: bool = True,
    bundle: Optional[dict[str, Any]] = None,
) -> int:
    """
    Load current playbook_versions for a job, append a new entry, save back.
    Returns new version number.
    """
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT playbook_versions FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
    except Exception:
        return 1

    versions: list = []
    if row and row[0]:
        try:
            versions = json.loads(row[0])
        except Exception:
            versions = []

    new_version = len(versions) + 1
    versions.append({
        "version":          new_version,
        "playbook":         playbook,
        "entrypoint_path":  (bundle or {}).get("entrypoint_path"),
        "artifact_type":    "bundle" if bundle else "playbook",
        "file_count":       len((bundle or {}).get("files", [])),
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "refinement_notes": notes,
    })

    try:
        with db._connect() as conn:
            if set_current:
                if bundle:
                    conn.execute(
                        "UPDATE jobs SET playbook_versions=?, ansible_playbook=?, "
                        "ansible_artifact_manifest=?, ansible_entrypoint_path=? WHERE id=?",
                        (
                            json.dumps(versions),
                            playbook,
                            json.dumps(bundle),
                            str(bundle.get("entrypoint_path") or ""),
                            job_id,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE jobs SET playbook_versions=?, ansible_playbook=? WHERE id=?",
                        (json.dumps(versions), playbook, job_id),
                    )
            else:
                conn.execute(
                    "UPDATE jobs SET playbook_versions=? WHERE id=?",
                    (json.dumps(versions), job_id),
                )
    except Exception as exc:
        logger.warning("Could not save playbook version: %s", exc)

    return new_version


@router.post(
    "/jobs/{job_id}/refine",
    response_model=RefineResponse,
    summary="AI self-remediation — fix playbook based on validation errors or feedback",
)
async def refine_playbook(
    job_id: int,
    req:    RefineRequest,
    user:   dict = Depends(require_role("operator")),
):
    """
    Sends the current playbook + validation errors/feedback to the LLM.
    The LLM returns a corrected version which is persisted over the existing one.

    Use this in the Ansible Studio "Fix Issues" workflow:
    1. Validate with /check
    2. If errors, call /refine with the error list
    3. Repeat until /check passes
    4. Approve + execute
    """
    playbook_yaml, _ = _load_job_playbook(job_id)

    errors_raw  = [e.model_dump() for e in req.errors]
    refined, changes = await _ai_refine_playbook(
        original=playbook_yaml,
        errors=errors_raw,
        feedback=req.feedback,
    )

    version = _append_playbook_version(
        _db, job_id, refined,
        notes=f"Refinement: {req.feedback or ', '.join([e.message for e in req.errors[:3]])}"
    )

    logger.info("Playbook refined job=%d version=%d user=%s", job_id, version, user.get("username"))
    return RefineResponse(
        job_id=job_id,
        version=version,
        refined_playbook=refined,
        changes=changes,
        iteration=version,
    )


class PlaybookVersion(BaseModel):
    version:          int
    playbook:         str
    created_at:       str
    refinement_notes: str = ""
    artifact_type:    str = "playbook"
    entrypoint_path:  Optional[str] = None
    file_count:       int = 0


@router.get(
    "/jobs/{job_id}/history",
    response_model=List[PlaybookVersion],
    summary="Get full playbook version history for a job",
)
async def get_playbook_history(
    job_id: int,
    user:   dict = Depends(get_current_user),
):
    """
    Return all versions of the playbook for this job, oldest first.

    Version 1 is the original LLM-generated playbook.
    Subsequent versions are refinements from /refine calls.
    """
    if _db is None:
        raise HTTPException(503, "Database not initialized")

    with _db._connect() as conn:
        # Auto-upgrade: add column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN playbook_versions TEXT")
        except Exception:
            pass  # column already exists

        row = conn.execute(
            "SELECT playbook_versions, ansible_playbook FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Job {job_id} not found")

    if row["playbook_versions"]:
        try:
            versions = json.loads(row["playbook_versions"])
            return [PlaybookVersion(**v) for v in versions]
        except Exception:
            pass

    # Fallback: only the current playbook
    if row["ansible_playbook"]:
        return [PlaybookVersion(
            version=1,
            playbook=row["ansible_playbook"],
            created_at=datetime.now(timezone.utc).isoformat(),
            refinement_notes="Original generation",
        )]

    return []