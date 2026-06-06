# Intent schemas (per-operation YAML + JSON types)

This directory holds **one YAML file per intent `action`** (e.g. `create_vlan.yml`, `configure_interface.yml`).

## Relationship to the canonical doc and graph

**Every operation is defined in these places (keep them aligned):**

1. **`docs/intent_operation_reference.md`** — Narrative: when to use which `action`/`feature`, anti-patterns.
2. **This directory (`*.yml`)** — **Authoritative** `required` / `optional` / `resolution_order` / `questions` for `validate_completeness`, chat disambiguation, early `evaluate_intent_tree` checks (`build_params_disambiguation_fields_from_intent`), and LLM `get_required_params_hint_for_prompt()`.
3. **`*.schema.json`** (when present) — JSON Schema for **type** validation after parameters are filled.
4. **`../intent_graph.yml`** — Lists each **operation** under a **category**; run `python -m ansible.validate_intent_artifacts` after edits.
5. **`../category_schemas/*.yml`** — Optional vocabulary-only descriptions per category for the LLM (not required/optional).
6. **`../intent_keywords.yml`** — Editable rule-based classification keywords used by `llm/intent.py`.

If you change required/optional behavior, **update the YAML and the doc together** (see the “Source of truth” section at the top of `docs/intent_operation_reference.md`).

## File roles

| File pattern | Contents |
|--------------|----------|
| `<action>.yml` | `action`, `feature`, `required`, `optional`, `resolution_order`, `questions` |
| `<action>.schema.json` | Typed shape for `validate_types()` in `intent_validation.py` |

## Code entry points

- Loader: `ansible.intent_validation.load_schema`, `get_schema_for_parsed_intent`
- LLM prompt text: `get_intent_operation_reference_for_prompt()`, `get_required_params_hint_for_prompt()`, plus `ansible.intent_artifacts` (`get_category_vocabulary_for_prompt`, `get_intent_graph_summary_for_prompt`, `get_intent_keywords_digest_for_prompt`)
