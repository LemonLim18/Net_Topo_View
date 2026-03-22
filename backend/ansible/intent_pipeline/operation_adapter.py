"""
Map canonical_operation + parameters → legacy action/feature/parameters (REFACTOR_INTENT_PARSER.md).
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_ADAPTER_PATH = Path(__file__).resolve().parent.parent / "intent_catalog" / "operation_adapter.yaml"
_cached: Optional[Dict[str, Any]] = None


def _load_adapter() -> Dict[str, Any]:
    global _cached
    if _cached is None:
        with _ADAPTER_PATH.open(encoding="utf-8") as f:
            _cached = yaml.safe_load(f) or {}
    return _cached


def reload_operation_adapter() -> None:
    global _cached
    _cached = None


def list_canonical_operations() -> List[str]:
    """Names of fine-grained operations with a legacy mapping (for catalog / UI)."""
    adapter = _load_adapter()
    return sorted((adapter.get("mappings") or {}).keys())


def apply_operation_adapter(intent_fragment: Dict[str, Any]) -> Dict[str, Any]:
    """
    If ``intent_fragment`` contains ``canonical_operation``, rewrite to legacy action/feature
    and apply ``parameter_aliases`` from operation_adapter.yaml.

    Also accepts ``action`` alone when it matches a mapping key (fine-grained ops such as
    ``assign_vlan_to_access_port``).
    """
    co = intent_fragment.get("canonical_operation")
    act = intent_fragment.get("action")
    key = str(co or "").strip() or str(act or "").strip()
    if not key:
        return intent_fragment

    data = deepcopy(intent_fragment)
    adapter = _load_adapter()
    mappings = (adapter.get("mappings") or {}).get(key)
    if not mappings:
        logger.debug("No operation_adapter mapping for operation=%s", key)
        data.pop("canonical_operation", None)
        return data

    params = dict(data.get("parameters") or {})
    # Preserve fine-grained op for catalog validation after legacy action rewrite.
    params["_canonical_operation"] = key

    data["action"] = mappings.get("legacy_action") or data.get("action")
    data["feature"] = mappings.get("legacy_feature") or data.get("feature")
    aliases = mappings.get("parameter_aliases") or {}
    for src, dst in aliases.items():
        if src in params and dst not in params:
            params[dst] = params.pop(src)
    data["parameters"] = params
    data.pop("canonical_operation", None)
    return data


def adapt_intents_list(intents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [apply_operation_adapter(dict(x)) for x in intents if isinstance(x, dict)]


def normalize_trunk_access_params(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Heuristic: map doc field names into template-oriented keys after adapter.
    """
    params = dict(data.get("parameters") or {})
    ifaces = params.get("interfaces")
    if isinstance(ifaces, list):
        new_ifaces = []
        for item in ifaces:
            if not isinstance(item, dict):
                new_ifaces.append(item)
                continue
            row = dict(item)
            if "allowed_vlans" in row and "trunk_vlans" not in row:
                row["trunk_vlans"] = row.pop("allowed_vlans")
            new_ifaces.append(row)
        params["interfaces"] = new_ifaces
    data["parameters"] = params
    return data
