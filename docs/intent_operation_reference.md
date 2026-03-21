# Intent operations reference (canonical)

**Purpose:** One place that defines **operation-specific** intent schemas. The UI may group work under broad **categories** (e.g. “VLAN”), but the **LLM must choose the correct `action` + `feature` pair** — each pair has its own required vs optional parameters.

---

## Source of truth (project rule)

**From this point onward, every intent operation and change must reference and stay in sync with:**

| Artifact | Role |
|----------|------|
| **`docs/intent_operation_reference.md`** (this file) | Human-readable spec + LLM narrative: when to use which `action`/`feature`, required vs optional, anti-patterns. Injected into the intent parser via `get_intent_operation_reference_for_prompt()`. |
| **`backend/ansible/intent_schemas/*.yml`** | Machine-readable intent schema per **action**: `action`, `feature`, `required`, `optional`, `resolution_order`, `questions`. Drives validation, disambiguation, and `get_required_params_hint_for_prompt()`. |
| **`backend/ansible/intent_schemas/*.schema.json`** (where present) | JSON Schema for **type** validation of filled parameters (ranges, shapes). |

**When you add or change an operation:** update the **YAML** first (validation + API behavior), then **this markdown** (tables + wording), then any **`.schema.json`** and code references (`intent_validation.py`, `api.py`, `llm/intent.py`, tests). Do not define required/optional fields only in code or only in the prompt.

**Backend / tree engine:** `evaluate_intent_tree()` applies heuristics for “unclear bulk” targets. If the client sends **explicit** `target_hosts` (or `params.target_hosts` / `target_devices`) on `/ansible/preview-intent` or `/ansible/.../plan-ansible`, those heuristics must **not** clear the selection — otherwise the UI device picker loops forever after multi-select. See `explicit_targets_from_request` in `intent_tree_engine.py`.

**Preview path:** `_preview_parse_and_rag()` used to run `_target_resolution_is_unclear()` **before** `preview_intent` could apply the explicit-target flag, clearing even user-selected hosts (e.g. 3+ devices). It now skips that clear when **`target_hosts`** or **`params.target_hosts` / `target_devices`** is present (same idea as the tree engine). When the prompt does not mention role/hostname clues, `_deduce_target_devices()` used to label every host as **“matched by inventory similarity”**; with two explicit picks that was **100%** similarity-only and tripped the ≥75% “unclear” rule. Explicit UI/API targets now set **`explicit_user_targets`**, which bypasses that heuristic and shows **“User-selected target (Target inventory or API)”** in Section 2 instead.

**Create-and-plan:** `_resolve_backend_intent_request()` had the same **unclear-bulk** 422 when the LLM returned many `resolved_devices` but the studio sent **`target_hosts: ['all']`** as a fallback (expands to full inventory). The handler now (1) prefers **`params.target_hosts`** over a lone **`['all']`** request, and (2) skips the unclear check when **`req.target_hosts`** or **`params` targets** are present. The Playbook Studio UI must not default to **`['all']`** when no host is selected.

**Ansible chat preview follow-ups:** `AnsibleChatPanel` keeps **`previewLockedTargetHosts`** and **`previewAccumulatedParams`** across device picker → params wizard → interface picker so each **`previewIntent`** call resends the user’s device choice and prior answers (e.g. `vlan_id` / `vlan_name`). The lightweight `parseIntent()` no longer defaults missing hosts to **`['all']`** (that caused full-inventory resolution and repeated device/VLAN prompts). Invalid interface names still surface **`interfaces_disambiguation`** with messages from `validate_interface_params_against_context` (e.g. interface not on the selected host).

**Preview wizard vs raw prompt:** `vlan_schema_fields_missing_from_user_prompt()` only inspects **`intent_description`**. Values typed in the chat wizard (e.g. VLAN name **HR**) are merged into **`parameters`** but do not change the original message. **`vlan_schema_fields_missing_from_user_prompt_respecting_params()`** skips `vlan_id` / `vlan_name` when those keys are **trusted** from **`req.params`** (wizard/API), not when the LLM invented them in **`parameters`**, so the UI does not loop on “VLAN name?” forever and does not accept hallucinated names as final.

**Multi-operation preview (primary + `secondary_intents`):** For one user message, the LLM may return **`create_vlan`** as primary and **`configure_interface`** in **`secondary_intents[0]`** (see composite table below). Preview **merges missing required fields from every slice’s operation-specific YAML** (`backend/ansible/intent_schemas/*.yml`) into a **single** top-level **`params_disambiguation`**. Fields that belong to a secondary slice include **`secondary_intent_index`** (0-based) and an optional **`label`** (e.g. “Also required: configure_interface (interface)”). The chat submits answers via **`params.secondary_intents`** patches (merged server-side into `parsed_intent.secondary_intents`); primary keys still merge into **`parameters`** as before. **`secondary_intent_previews`** continues to list **`missing_fields` / `validation_errors`** per slice; nested per-secondary **`params_disambiguation`** is **not** used (avoid duplicate wizards).

**Scaling (3+ operations):** The array **`secondary_intents`** can hold **any number** of slices; the backend loops all indices for schema completeness and the params wizard tags each question with **`secondary_intent_index`**. **Caveat:** the **planned-inventory interface picker** (when names are invalid) applies selections to the **first** secondary whose action is **`configure_interface`** (or feature **`interface`**). If you have **two** distinct interface-configuration tasks in one message, complete one via the picker / wizard, then use a follow-up prompt or fill the other slice’s **`interfaces`** in the params wizard (text) — full multi-picker orchestration for N interface slices is not implemented yet.

**Playbook Studio modal:** “Target interfaces” chips are filled from **`parsed_intent.parameters.interfaces`** **and** from every **`parsed_intent.secondary_intents[].parameters.interfaces`** (composite prompts). Generate passes **`params._secondary_intents`** (same convention as the LLM parser) so create-and-plan does not treat port work as **`interfaces_to_shutdown`**.

**Planned inventory cross-check (devices + interfaces, every slice):** Resolved **target hostnames** are aligned with planned inventory where the backend has inventory (`_filter_resolved_hosts_to_planned_inventory` on relevant paths). **Interface names are not trusted from the LLM alone:** `collect_user_interface_names_from_params()` gathers names from primary **`parameters`** (shutdown/configure/ACL/etc.), from **`parameters._secondary_intents` / `secondary_intents`** when embedded in the request body, and from top-level **`parsed_intent.secondary_intents`** (preview passes this into `validate_interface_params_against_context`). **Composite** prompts (e.g. `create_vlan` + `configure_interface` in `secondary_intents`) are cross-checked the same way as single-action interface intents — invalid names trigger **`interfaces_disambiguation`**, **`interfaces_inventory_validation`**, and reasoning replacement via `backend/ansible/context_entity_validation.py` + `api.py` (no change to **`intent_schemas/*.yml`** required for this behavior).

**Planned inventory vs preview text:** Section **“Interfaces to configure”** in preview reasoning is first filled from the parser/LLM. After `validate_interface_params_against_context()` runs against **planned network context** for the **selected** target host(s), if names fail (e.g. **GigabitEthernet1/0/1** not on **Acc_Cisco_1**), that bullet is **replaced** with a cross-checked explanation and **`parsed_intent.interfaces_inventory_validation`** carries structured `ok` / `validation_notice` / `invalid_entities` — same validation path as **`interfaces_disambiguation`** (not VLAN-only).

**Programmer note (note.md rule 1):** Follow-up prompts that alter **one** operation in a composite without resending **`secondary_intents` / `params`** consistently can desync chat vs studio state; prefer resending accumulated **`previewIntent`** params or splitting into separate messages if orchestration conflicts appear.

---

**Rules for the parser**

1. **`action`** must match the operation row (e.g. `create_vlan`, not `configure_interface` for “just create a VLAN”).
2. **`feature`** is the template/RAG category (e.g. `vlan`, `interface`).
3. **Required** parameters: never invent values. If the user did not state them, omit or leave empty and set `requires_resolution` / let the controller ask — do **not** hallucinate interfaces or host:port strings.
4. **Optional** parameters: do **not** ask the user unless they mentioned them or they are strictly needed for the chosen operation.
5. **Same category, different operations:** “VLAN” can mean **VLAN database only** (`create_vlan`) **or** **L2 port configuration** (`configure_interface` on feature `interface`). Use the decision table below.

---

## VLAN category

| User intent (examples) | `action` | `feature` | Required parameters | Optional (do not ask unless relevant) | Do **not** do |
|------------------------|----------|-----------|----------------------|----------------------------------------|----------------|
| Create / add a VLAN (name + id) on devices/switches; **no** access/trunk/port wording | **`create_vlan`** | **`vlan`** | **`vlan_id`**, **`vlan_name`**, **`target_devices`** (or `target_hosts`) | `desired_state`, `interface_binding` only if user asked to map ports | Do **not** set `action`=`configure_interface`. Do **not** fill `parameters.interfaces` with `Host:Ethernet…` guesses. |
| Put VLAN on ports; access/trunk/native/allowed VLANs on **interfaces** | **`configure_interface`** | **`interface`** | **`target_devices`**, **`interfaces`** (with mode/access_vlan/trunk fields as needed) | `default_mode` | Do **not** use `feature: vlan` for pure L2 port work; use **`interface`** + `configure_interface`. |

**VLAN database-only (`create_vlan`) — required fields**

- **`vlan_id`** — numeric 1–4094 (only if user stated it; do not guess).
- **`vlan_name`** — string (only if user stated it; do not guess).
- **`target_devices`** / **`target_hosts`** — list of hostnames or resolved scope (never invent `Device:Interface` tokens).

**Optional for `create_vlan`:** `interface_binding`, `desired_state` — only when the user explicitly wants interface mapping or absent/present semantics.

**Composite prompts (multi-intent — same user message):** When the user asks to **create/add a VLAN** (ID + name in the switching database) **and** **configure specific interfaces** (access/trunk, interface names) in one sentence, the intent parser must **not** collapse this into a single `configure_interface` / `feature: vlan` blob. Use:

| Step | `action` | `feature` | Role |
|------|----------|-----------|------|
| Primary | **`create_vlan`** | **`vlan`** | VLAN DB: `vlan_id`, `vlan_name`, `target_devices` |
| `secondary_intents[0]` | **`configure_interface`** | **`interface`** | Ports: `interfaces` with `mode`, `access_vlan` / trunk fields |

The LLM JSON already supports `secondary_intents` (see `backend/llm/intent.py`). Preview validates secondaries via `secondary_intent_previews` and **folds** each secondary’s **`intent_schemas` completeness** into **`params_disambiguation`** (see **Multi-operation preview** above). **Playbook execution** may still apply the **primary** intent first until multi-play orchestration is extended for all categories.

---

## Interface category (`feature: interface`)

| User intent | `action` | Required | Optional |
|-------------|----------|----------|----------|
| Shutdown / admin-down / disable interfaces | **`shutdown_interface`** | **`target_devices`**, **`interfaces`** (or `interfaces_to_shutdown` per API) | — |
| L2/L3 changes: access/trunk, VLAN on port, routed IP | **`configure_interface`** | **`target_devices`**, **`interfaces`** | `default_mode` |

---

## Routing: static route (`feature: static_route`)

| `action` | Required | Optional |
|----------|----------|----------|
| **`configure_static_route`** | **`target_devices`**, **`destination`**, **`next_hop`** | `admin_distance`, `vrf_name` |

---

## OSPF (`feature: ospf`)

| `action` | Required | Optional |
|----------|----------|----------|
| **`configure_ospf`** | **`process_id`**, **`target_devices`**, **`networks`** | `router_id`, `area_definitions`, `passive_interfaces`, `redistribution` |

---

## ACL (`feature: acl`)

| `action` | Required | Optional |
|----------|----------|----------|
| **`configure_acl`** | **`acl_name`**, **`acl_type`**, **`rules`**, **`target_devices`** | `apply_to` |

---

## QoS (`feature: qos`)

| `action` | Required | Optional |
|----------|----------|----------|
| **`configure_qos`** | **`policy_name`**, **`target_devices`**, **`qos_type`**, **`classification_rules`** | `interface_apply` |

---

## Revision

- **2026-03:** Clarified VLAN **database-only** vs **interface** operations; required `vlan_id`, `vlan_name`, `target_devices` for `create_vlan`.
- **2026-03:** Declared **dual source of truth**: `docs/intent_operation_reference.md` + `backend/ansible/intent_schemas/*.yml` for all operations going forward.
- **2026-03:** Multi-intent preview: merged **`params_disambiguation`** from primary + **`secondary_intents`** schemas; **`params.secondary_intents`** wizard patches; **`secondary_intent_index`** on fields.
- **2026-03:** Planned-inventory interface validation includes **`secondary_intents`** (not only primary **`parameters`**); **`collect_user_interface_names_from_params`** extended accordingly.
- **2026-03:** **`POST /ansible/preview-intent`**: for **`create_vlan`**, the **Interfaces to configure** line in **`reasoning`** is rebuilt from **merged** `parameters` after `req.params` (wizard / interface picker / target inventory) so it matches **`Ethernet0/x`** (or other inventory names), not a stale LLM guess left from the raw prompt.
- **2026-03:** When the client sends authoritative primary **`interfaces`** / **`interfaces_config`**, **`secondary_intents`** slices that still contain LLM-invented interface names are cleared (unless that slice is explicitly patched in **`req.params.secondary_intents`**) so planned-inventory validation does not fail on a ghost **GigabitEthernet** token alongside the user’s **Ethernet0/x** pick.
- **2026-03:** If **`req.params`** includes non-empty **`interfaces`** / **`interfaces_config`**, planned-inventory validation uses **`interface_names_override`** — only those names are checked, so LLM guesses merged elsewhere do not surface **`interfaces_inventory_validation`** errors in the preview modal.
- **2026-03:** **`explicit_interface_names_from_request_params`** also reads **`interfaces`** / **`interfaces_config`** / **`interfaces_to_shutdown`** under **`secondary_intents`** / **`_secondary_intents`** slices (Playbook Studio used to send port picks only on the secondary for **`create_vlan`** + interface). The Studio now sets **primary** **`params.interfaces`** for **`create_vlan`** when target-interface chips are set, and debounces **`preview-intent`** with merged **`params`** so reasoning and inventory validation match the UI.
- **2026-03:** VLAN normalization no longer emits **`native_vlan`: null** on access (or unset) interfaces — that broke JSON Schema (`integer` vs `null`). **`reference/vlan/intent_schema.json`**, **`create_vlan.schema.json`**, and **`configure_interface.schema.json`** allow **`native_vlan`** to be **`null`** when present; prefer omitting the key when unused.
