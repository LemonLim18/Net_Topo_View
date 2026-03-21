# Intent schemas (per-operation YAML + JSON types)

This directory holds **one YAML file per intent `action`** (e.g. `create_vlan.yml`, `configure_interface.yml`).

## Relationship to the canonical doc

**Every operation is defined in two places that must stay aligned:**

1. **`docs/intent_operation_reference.md`** — What each operation means, when to use it, required vs optional fields, and anti-patterns (especially for the LLM parser).
2. **This directory (`*.yml`)** — The fields the backend uses for `validate_completeness`, disambiguation questions, and `resolution_order`.
3. **`*.schema.json`** (alongside each YAML when present) — JSON Schema for **type** validation after parameters are filled.

If you change required/optional behavior, **update the YAML and the doc together** (see the “Source of truth” section at the top of `docs/intent_operation_reference.md`).

## File roles

| File pattern | Contents |
|--------------|----------|
| `<action>.yml` | `action`, `feature`, `required`, `optional`, `resolution_order`, `questions` |
| `<action>.schema.json` | Typed shape for `validate_types()` in `intent_validation.py` |

## Code entry points

- Loader: `ansible.intent_validation.load_schema`, `get_schema_for_parsed_intent`
- LLM prompt text: `get_required_params_hint_for_prompt()`, `get_intent_operation_reference_for_prompt()` (loads the markdown from `docs/`)
