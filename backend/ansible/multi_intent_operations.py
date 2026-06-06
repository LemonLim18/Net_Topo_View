"""
Multi-operation single-prompt support (primary ``parsed_intent`` + ``secondary_intents[]``).

This module holds **cross-cutting rules** used by ``preview-intent`` and ``plan-ansible`` so
behavior is not hard-coded only for VLAN+interface. Other categories reuse the same hooks.

Pipeline (see also ``docs/intent_operation_reference.md``):

1. **Normalize** LLM output: ``_normalize_secondary_intents`` (api) → list of
   ``{action, feature, parameters}``.
2. **Merge** client patches: ``req.params`` into primary ``parameters``;
   ``req.params.secondary_intents`` into ``parsed_intent['secondary_intents']`` (slice-wise).
3. **Strip stale secondary copies** when the user sends authoritative primary parameters
   (e.g. inventory-selected ``interfaces``) so LLM tokens in secondary slices do not break
   validation — :func:`strip_stale_secondary_params_after_primary_authoritative`.
4. **Completeness + types**: per-slice ``validate_completeness`` + JSON-Schema
   (``_append_secondary_intent_schema_disambiguation`` in api).
5. **Prompt vs wizard**: ``merge_prompt_aware_disambiguation_for_schema`` in
   ``intent_validation`` (e.g. create_vlan ID/name not in natural language).
6. **Conditional follow-ups**: ``SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION`` in
   ``intent_validation`` (e.g. configure_interface access/trunk/IP).
7. **Planned inventory**: interface name validation + optional reasoning refresh.

Extending a new category:

- Add YAML + ``.schema.json`` under ``intent_schemas/``.
- If the operation shares **interface** overlap with primary, add keys to
  ``AUTHORITATIVE_PRIMARY_INTERFACE_KEYS`` / strip lists only if the same conflict pattern applies.
- Register conditional disambiguation in ``SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION``.
- Optionally register prompt-aware disambiguation in ``merge_prompt_aware_disambiguation_for_schema``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

# When ``req.params`` includes non-empty values for these keys, the client is treated as
# authoritative for L2 binding; secondary slices may still carry LLM-invented duplicates.
AUTHORITATIVE_PRIMARY_INTERFACE_KEYS: Tuple[str, ...] = ("interfaces", "interfaces_config")

# Keys removed from secondary ``parameters`` when primary is authoritative (unless that slice
# is explicitly patched in ``req.params.secondary_intents[i].parameters``).
DEFAULT_SECONDARY_INTERFACE_OVERLAP_KEYS: Tuple[str, ...] = ("interfaces", "interfaces_config")


def strip_stale_secondary_params_after_primary_authoritative(
    parsed_intent: Dict[str, Any],
    req_params: Optional[Dict[str, Any]],
    *,
    authoritative_keys: Tuple[str, ...] = AUTHORITATIVE_PRIMARY_INTERFACE_KEYS,
    secondary_keys_to_clear: Tuple[str, ...] = DEFAULT_SECONDARY_INTERFACE_OVERLAP_KEYS,
) -> None:
    """
    When the client sends explicit primary parameters (e.g. ``interfaces`` from the picker),
    remove overlapping keys from **secondary_intents** slices that still carry LLM guesses,
    unless that index was explicitly patched in ``req.params.secondary_intents``.

    Generic: applies to any composite where primary + secondary both mention the same binding
    (today: create_vlan primary + configure_interface secondary; same pattern if another
    feature duplicates interface lists in a secondary slice).
    """
    if not req_params:
        return
    user_vals = [req_params.get(k) for k in authoritative_keys]
    if not any(_non_empty(v) for v in user_vals):
        return

    secs = list(parsed_intent.get("secondary_intents") or [])
    if not secs:
        return

    patch_list = req_params.get("secondary_intents")
    has_patch_list = isinstance(patch_list, list) and len(patch_list) > 0

    for i, sec in enumerate(secs):
        if not isinstance(sec, dict):
            continue
        if has_patch_list and i < len(patch_list) and isinstance(patch_list[i], dict):
            pp = dict(patch_list[i].get("parameters") or {})
            if any(pp.get(k) for k in secondary_keys_to_clear):
                continue
        sp = dict(sec.get("parameters") or {})
        if not any(sp.get(k) for k in secondary_keys_to_clear):
            continue
        for k in secondary_keys_to_clear:
            sp.pop(k, None)
        sec["parameters"] = sp
    parsed_intent["secondary_intents"] = secs


def _non_empty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, list):
        return len(v) > 0
    if isinstance(v, str):
        return bool(v.strip())
    return True


def merged_params_for_vlan_interfaces_reasoning(
    parsed_intent: Dict[str, Any],
    merged_params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Prefer primary ``parameters.interfaces`` for preview reasoning.

    If empty, copy interfaces from a ``configure_interface`` / ``interface`` secondary slice
    (composite create_vlan + port binding) so **Interfaces to configure** matches Studio
    when bindings live only on ``secondary_intents``.
    """
    out = dict(merged_params or {})
    if out.get("interfaces"):
        return out
    for sec in parsed_intent.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sa = str(sec.get("action") or "").strip().lower().replace("-", "_")
        sf = str(sec.get("feature") or "").strip().lower().replace("-", "_")
        if sa == "configure_interface" or sf in ("interface", "interfaces"):
            ifaces = (sec.get("parameters") or {}).get("interfaces")
            if isinstance(ifaces, list) and len(ifaces) > 0:
                out["interfaces"] = ifaces
                break
    return out


def refresh_preview_vlan_interfaces_reasoning_block(
    reasoning: List[str],
    *,
    merged_params: Dict[str, Any],
    resolved_hosts: List[str],
    schema_action: str,
    feature_lower: str,
) -> None:
    """
    After ``preview_intent`` merges ``req.params``, replace the early **Interfaces to configure**
    reasoning block so it matches merged ``parameters`` (not only the first LLM parse).

    Operation-specific today: **create_vlan** / feature **vlan** uses
    ``get_vlan_interfaces_summary``. Other multi-op summaries can extend this function or add
    parallel hooks.
    """
    from ansible.reference_resolver import format_vlan_interface_reasoning_line, get_vlan_interfaces_summary

    sa = str(schema_action or "").strip().lower().replace("-", "_")
    fl = str(feature_lower or "").strip().lower()
    if sa != "create_vlan" and fl != "vlan":
        return
    preview_intent = {
        "params": merged_params,
        "target_hosts": list(resolved_hosts or []),
    }
    ifaces_summary = get_vlan_interfaces_summary(preview_intent)
    if ifaces_summary:
        lines = [format_vlan_interface_reasoning_line(s) for s in ifaces_summary]
        new_block = "- **Interfaces to configure:**\n" + "\n".join(lines)
    else:
        new_block = "- **Interfaces to configure:** (none — VLAN creation only)"
    replaced = False
    for idx, block in enumerate(reasoning):
        if not isinstance(block, str):
            continue
        if "**Interfaces to configure:**" not in block:
            continue
        if "cross-checked with planned inventory" in block:
            continue
        if "Planned interfaces (all-interfaces scope)" in block:
            continue
        reasoning[idx] = new_block
        replaced = True
        break
    if not replaced and ifaces_summary:
        reasoning.append(new_block)
