"""
Build compact v2 parse prompts (smaller than legacy monolithic _PARSE_PROMPT).
"""

from __future__ import annotations

from typing import List, Tuple

from ansible.intent_pipeline.keyword_manager import build_operation_hint_block
from ansible.intent_pipeline.schema_loader import get_catalog


# Distilled rules — keep in sync with docs/REFACTOR_INTENT_PARSER.md § merge / validation.
_V2_CORE_RULES = """\
## Rules (compact)
1. Put **only planned-inventory hostnames** in `scope.device_hostnames` when the user names devices — **exact** names as in the topology/inventory context (case-insensitive match). Do **not** guess or invent hostnames. Short tokens like `SW1` are valid **only** if they appear in `device_alias_hints` in the catalog **or** as an exact hostname in context; otherwise leave `scope.device_hostnames` empty so the UI can ask the admin to pick from the planned network.
2. For **access switch 1, 2, 3** (or distribution/core + numbers), set `scope.device_role` to that layer AND reflect the numbered selection in `scope.device_hostnames` using the **correct** devices from context (not the whole inventory).
3. **VLAN database**: `action` **create_vlan**, `feature` **vlan**, `parameters.vlans` = `[{"id": <int>, "name": "<string>"}]` (required). Do **not** use `vlan_id` / `vlan_name` keys unless you also mirror them into `vlans`.
4. **Ports**: access VLAN on a port → `action` **assign_vlan_to_access_port**, `feature` **interface**, `parameters.interfaces` with `mode` **access** and `access_vlan`. Trunk allowed VLANs → **assign_vlan_to_trunk_port** with `mode` **trunk** and `trunk_vlans`. Native/PVID VLAN on trunk → **set_native_vlan** with `mode` **trunk** and `native_vlan`.
5. **Composite**: VLAN create + port change → primary `create_vlan` with `vlans` + `secondary_intents` holding the interface operation above (not generic `configure_interface` unless unsure).
6. **Generic prompts** ("create vlan on devices" with no id/name): output minimal JSON, empty `vlans` / empty targets, `requires_resolution` true — do not invent devices.
7. **Shutdown interfaces**: `shutdown_interface` + `interfaces_to_shutdown` + targets.
8. If information is missing, set `requires_resolution`: true and still output best-effort JSON.
9. Output **only** valid JSON, no markdown fences.
"""


_V2_JSON_SCHEMA = """\
## JSON Schema (exact keys)
{
  "intent_type": "configure | troubleshoot | query | audit | rollback | deploy",
  "action": "<legacy action e.g. create_vlan, configure_interface, shutdown_interface, configure_static_route, configure_acl, configure_qos, configure_ospf>",
  "feature": "<vlan | interface | acl | qos | static_route | ospf | ...>",
  "vendor": "cisco_ios | huawei_vrp | juniper_junos | \"\"",
  "parameters": { },
  "scope": {
    "device_hostnames": [],
    "device_role": "access | distribution | core | null",
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low | medium | high | critical",
  "justification": "<one line>",
  "requires_resolution": false,
  "secondary_intents": []
}
Each `secondary_intents` item: { "action": "...", "feature": "...", "parameters": { } }.
"""

_V2_MULTI_SCHEMA = """\
## Multi-intent JSON Schema
When the user combines several tasks in one sentence, output:
{
  "intents": [
    {
      "intent_type": "configure",
      "action": "...",
      "feature": "...",
      "vendor": "",
      "parameters": { },
      "scope": { "device_hostnames": [], "device_role": null, "building": null, "vendor_filter": null, "all_devices": false },
      "risk_level": "medium",
      "justification": "",
      "requires_resolution": false,
      "canonical_operation": "<optional fine-grained op from engineering doc>"
    }
  ],
  "justification": "<one line for whole prompt>"
}
Use **one object per segment** in time order. First intent is the primary automation step.
For **each** segment, infer the correct **category** (vlan / interface / acl / qos / static_route / ospf) from that segment's wording; do not reuse the first segment's category for unrelated tail clauses.
You may omit ``intents`` and use the single-object schema instead when there is only one task.
"""


def build_v2_user_prompt(
    user_message: str,
    topology_context: str,
    template_schemas_excerpt: str,
    detected_categories: List[str],
) -> str:
    cat = get_catalog()
    op_block = cat.format_operations_block(detected_categories)
    return f"""You are an **intent parser** for a network automation controller.
Convert the user's message into ONE structured JSON object (same schema as below).

{_V2_CORE_RULES}

{_V2_JSON_SCHEMA}

## Relevant operations (catalog subset — prefer these action/feature pairs)
{op_block}

## Template schema excerpt (parameter shapes — do not invent keys outside these)
{template_schemas_excerpt}

## Topology / inventory context
{topology_context or "(no topology loaded)"}

## User message
{user_message}
"""


def build_multi_intent_user_prompt(
    segments: List[str],
    topology_context: str,
    template_schemas_excerpt: str,
    union_categories: List[str],
) -> str:
    """Prompt when ``split_multi_intent`` found multiple segments."""
    cat = get_catalog()
    op_block = cat.format_operations_block(union_categories)
    seg_lines = "\n".join(f"{i+1}. {s}" for i, s in enumerate(segments))
    full_text = " ".join(segments)
    hint_block = build_operation_hint_block(full_text, cat)
    return f"""You are an **intent parser** for a network automation controller.
The user message has been split into **{len(segments)}** intent segment(s). Emit **one JSON object per segment**
inside an ``intents`` array (see schema). Order must follow the segments below.

{_V2_CORE_RULES}

{_V2_MULTI_SCHEMA}

## Relevant operations (catalog subset)
{op_block}
{hint_block}
## Template schema excerpt
{template_schemas_excerpt}

## Topology / inventory context
{topology_context or "(no topology loaded)"}

## Segments (in order)
{seg_lines}

## Original full user message (context)
{" ".join(segments)}
"""


def detect_categories_for_message(user_message: str) -> Tuple[List[str], str]:
    """Return (category_keys, operations_block_for_all_detected)."""
    cat = get_catalog()
    keys = cat.detect_categories(user_message)
    return keys, cat.format_operations_block(keys)
