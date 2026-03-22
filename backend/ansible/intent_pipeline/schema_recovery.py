"""
Runtime schema recovery when catalog has no explicit operation entry.

Data-driven recovery (no disk writes):
1. Try ``operation_adapter.yaml`` legacy mapping for the emitted ``action`` name.
2. If ``INTENT_SCHEMA_AUTO_RECOVER=1``, fall back to a permissive synthetic spec so parsing
   continues; logs at WARNING for operators to add YAML later.

Disk remains the source of truth — this module only bridges unknown model output to validation.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SYNTHETIC_SPEC: Dict[str, Any] = {
    "required_parameters": [],
    "implicit_requires_targets": False,
    "optional_parameters": [],
    "forbidden_fields": [],
    "dependencies": [],
    "_schema_recovery": True,
}


def _adapter_legacy_pair(action: str) -> Optional[Tuple[str, str]]:
    try:
        from ansible.intent_pipeline.operation_adapter import apply_operation_adapter

        frag = {"action": str(action or "").strip(), "feature": "", "parameters": {}}
        out = apply_operation_adapter(dict(frag))
        la = str(out.get("action") or "").strip()
        lf = str(out.get("feature") or "").strip()
        if la and (la != frag["action"] or lf):
            return la, lf
    except Exception as exc:
        logger.debug("schema_recovery adapter lookup failed: %s", exc)
    return None


def find_operation_spec_with_recovery(
    catalog: Any,
    action: str,
    feature: str,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    """
    Return (op_name, spec, source) where source is ``catalog`` | ``adapter`` | ``synthetic``.

    ``catalog`` is an IntentCatalog-like object with ``.categories``.
    """
    from ansible.intent_pipeline.schema_loader import find_operation_spec_in_catalog

    hit = find_operation_spec_in_catalog(catalog, action, feature)
    if hit:
        return hit[0], hit[1], "catalog"

    # e.g. assign_vlan_to_access_port → configure_interface / interface
    legacy = _adapter_legacy_pair(action)
    if legacy:
        la, lf = legacy
        hit2 = find_operation_spec_in_catalog(catalog, la, lf)
        if hit2:
            return hit2[0], hit2[1], "adapter"

    if os.getenv("INTENT_SCHEMA_AUTO_RECOVER", "").lower() in ("1", "true", "yes", "on"):
        logger.warning(
            "Intent schema auto-recover: unknown operation action=%r feature=%r — using synthetic spec",
            action,
            feature,
        )
        return str(action or "unknown"), dict(_SYNTHETIC_SPEC), "synthetic"

    return None


def recovery_suggestion_lines(action: str, feature: str) -> List[str]:
    """Human-readable hints for extending intent_catalog YAML."""
    lines = [
        f"Add an operation under the right category YAML, e.g.:",
        f"  {action or 'operation_key'}:",
        f"    action: {action or 'legacy_action'}",
        f"    feature: {feature or 'legacy_feature'}",
        f"    required_parameters: []",
        f"    optional_parameters: []",
        f"    forbidden_fields: []",
        f"    dependencies: []",
        f"    keyword_triggers: []",
    ]
    return lines
