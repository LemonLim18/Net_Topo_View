# Multi-operation single prompt (generic pipeline)

One natural-language message can yield a **primary** `parsed_intent` plus **`secondary_intents[]`**. The backend treats each slice with the **same operation-specific YAML + JSON Schema** as a standalone intent, then merges wizard output into one `params_disambiguation` (with `secondary_intent_index`).

## Where the logic lives

| Concern | Location |
|--------|----------|
| Strip stale secondary copies when primary is authoritative (e.g. user-picked `interfaces`) | `backend/ansible/multi_intent_operations.py` → `strip_stale_secondary_params_after_primary_authoritative` |
| Per-slice completeness + schema questions | `backend/ansible/api.py` → `_append_secondary_intent_schema_disambiguation` |
| Conditional follow-ups not expressible as flat JSON Schema (e.g. access VLAN after interfaces exist) | `backend/ansible/intent_validation.py` → `SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION` |
| “User did not say X in the prompt” but wizard/API may have supplied it | `backend/ansible/intent_validation.py` → `merge_prompt_aware_disambiguation_for_schema` (today: `create_vlan`) |
| Preview reasoning: refresh “Interfaces to configure” after merge | `backend/ansible/multi_intent_operations.py` → `refresh_preview_vlan_interfaces_reasoning_block` |
| Trunk VLAN wizard: multi-select options from intent + planned context | `backend/ansible/context_entity_validation.py` → `enrich_params_disambiguation_trunk_vlan_controls` (called from `api.py` preview) |
| Drop LLM “device” tokens that are L2 words or interface names | `backend/ansible/context_entity_validation.py` → `is_spurious_device_hint_token` |

## Extending a new category

1. Add **`intent_schemas/<action>.yml`** and **`.schema.json`** as for any single operation.
2. **Completeness** is automatic: `_append_secondary_intent_schema_disambiguation` loads `get_schema_for_parsed_intent` per slice and runs `validate_completeness`.
3. If the operation needs **extra wizard fields** based on filled parameters (like `configure_interface`), register:

   ```python
   SECONDARY_INTENT_CONDITIONAL_DISAMBIGUATION_BY_ACTION["your_action"] = your_conditional_fn
   ```

   The callable receives **`parameters`** for that slice and returns `{"field", "question"}` dicts.

4. If the operation needs **prompt vs trusted-params** logic (like VLAN ID/name not in the raw message), add a branch in **`merge_prompt_aware_disambiguation_for_schema`** keyed by `schema["action"]`, reusing the same `trusted_param_keys` pattern as `create_vlan`.
5. If primary + secondary both carry **duplicate binding keys** (e.g. interface lists) and the user corrects primary via the UI, adjust **`AUTHORITATIVE_PRIMARY_INTERFACE_KEYS`** / **`DEFAULT_SECONDARY_INTERFACE_OVERLAP_KEYS`** in `multi_intent_operations.py` only when the same conflict pattern applies.

See also **`docs/intent_operation_reference.md`** (multi-operation section).
