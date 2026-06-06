# LLM-assisted network intent parser — specification & NetTopoView implementation

**Related UI / workbench:** [REFACTOR_FRONTEND.md](./REFACTOR_FRONTEND.md) describes the **Intent parse** tab in Ansible Studio, view modes (Tree, Category, Operation, Preview), and how those map to **live APIs** and **YAML catalog files**.  
**Integration map (end-to-end):** see **§ NetTopoView implementation** at the bottom of this file and **§ Integration: parser, APIs, and files** in REFACTOR_FRONTEND.md.

---

## Original specification (Cursor prompt)

Cursor Full Prompt – LLM-Assisted Network Intent Parser  
You are an expert Python developer. Your task is to **refactor and implement a full network automation intent parser** in Python. This parser must:

1. Parse any network operation prompt (VLAN, INTERFACE, ACL, QOS, STATIC ROUTE, OSPF).
2. Handle multi-intent prompts and split them into separate intents.
3. Extract all required fields from the prompt.
4. Validate required, optional, forbidden fields and conditional dependencies using the schema provided below.
5. **Cross-check all device and interface fields against a planned network context**. If a device or interface does not exist, mark it invalid and ask the user for the correct value.
6. Always follow the **authoritative answer rule**: user selection > planned network context > schema defaults > LLM inference.
7. Output structured intents as JSON, ready to feed Ansible playbooks.

---
## 1️⃣ Inline YAML/JSON Schemas

### Global Fields
```yaml
global_fields:
  target_devices:
    type: array
    required: true
    cross_check: planned_network_context
  category:
    type: string
    required: true
  operation:
    type: string
    required: true
  prompt_raw:
    type: string
    required: true
  parsed_entities:
    type: object
    required: false
  missing_fields:
    type: array
    required: false
  ambiguous_fields:
    type: array
    required: false
  invalid_fields:
    type: array
    required: false

1) INTERFACE
1.1 Category-specific intent schema
category: INTERFACE
shared_fields:
  device: {type: string, required: true, cross_check: planned_network_context}
  interface_name: {type: string, required: true, cross_check: planned_network_context}
  description: {type: string, required: false}
  admin_state: {type: enum, allowed: [up, down], required: false}
  interface_mode: {type: enum, allowed: [l3, access, trunk, hybrid], required: false}
  access_vlan: {type: integer, required: false}
  allowed_vlans: {type: array, required: false}
  native_vlan: {type: integer, required: false}
  ip_address: {type: string, required: false}
  prefix_length: {type: integer, required: false}
  vrf: {type: string, required: false}
  mtu: {type: integer, required: false}
  speed: {type: string, required: false}
  duplex: {type: enum, allowed: [auto, full, half], required: false}
  channel_group: {type: integer, required: false}
  member_interfaces: {type: array, required: false}

1.2 Operation-specific intent schema
operations:
  create_or_update_interface:
    required_fields: [target_devices, interface_name]
    optional_fields: [description, admin_state, interface_mode, mtu, speed, duplex]
    forbidden_fields: [ip_address, prefix_length, vrf]
    dependencies: ["interface_mode must be set if access_vlan or allowed_vlans is used"]
  set_interface_description:
    required_fields: [target_devices, interface_name, description]
  set_interface_admin_state:
    required_fields: [target_devices, interface_name, admin_state]
  assign_l3_address:
    required_fields: [target_devices, interface_name, ip_address, prefix_length]
    optional_fields: [vrf]
    forbidden_fields: [access_vlan, allowed_vlans, native_vlan]
    dependencies: ["interface_mode should be l3"]
  configure_l2_access_port:
    required_fields: [target_devices, interface_name, access_vlan]
    dependencies: ["interface_mode must be access"]
  configure_l2_trunk_port:
    required_fields: [target_devices, interface_name, allowed_vlans]
    optional_fields: [native_vlan]
    dependencies: ["interface_mode must be trunk"]
  configure_port_channel:
    required_fields: [target_devices, channel_group, member_interfaces]

2) VLAN
2.1 Category-specific intent schema
category: VLAN
shared_fields:
  vlan_id: {type: integer, required: true, validation: {min:1, max:4094}}
  vlan_name: {type: string, required: false}
  state: {type: enum, allowed: [present, absent], required: false}
  target_interfaces: {type: array, required: false, cross_check: planned_network_context}
  interface_mode: {type: enum, allowed: [access, trunk], required: false}
  native_vlan: {type: integer, required: false}
  allowed_vlans: {type: array, required: false}
  description: {type: string, required: false}

2.2 Operation-specific intent schema
operations:
  create_vlan:
    required_fields: [vlan_id]
  rename_vlan:
    required_fields: [vlan_id, vlan_name]
  delete_vlan:
    required_fields: [vlan_id]
  assign_vlan_to_access_port:
    required_fields: [vlan_id, target_interfaces]
    dependencies: ["interface_mode must be access"]
  configure_trunk_vlans:
    required_fields: [target_interfaces, allowed_vlans]
    dependencies: ["interface_mode must be trunk"]
  remove_vlan_from_interface:
    required_fields: [vlan_id, target_interfaces]

3) ACL
3.1 Category-specific intent schema
category: ACL
shared_fields:
  acl_name: {type: string, required: true}
  acl_type: {type: enum, allowed: [standard, extended, ipv6], required: true}
  sequence: {type: integer, required: false}
  action: {type: enum, allowed: [permit, deny], required: true}
  protocol: {type: string, required: false}
  src_ip: {type: string, required: false}
  src_wildcard: {type: string, required: false}
  dst_ip: {type: string, required: false}
  dst_wildcard: {type: string, required: false}
  src_port: {type: string, required: false}
  dst_port: {type: string, required: false}
  remark: {type: string, required: false}
  bind_interface: {type: string, required: false, cross_check: planned_network_context}
  bind_direction: {type: enum, allowed: [in, out], required: false}

3.2 Operation-specific intent schema
operations:
  create_acl:
    required_fields: [acl_name, acl_type]
  add_acl_rule:
    required_fields: [acl_name, sequence, action]
  update_acl_rule:
    required_fields: [acl_name, sequence]
  delete_acl_rule:
    required_fields: [acl_name, sequence]
  apply_acl_to_interface:
    required_fields: [acl_name, bind_interface, bind_direction]
  remove_acl_from_interface:
    required_fields: [acl_name, bind_interface, bind_direction]

4) QOS
4.1 Category-specific intent schema
category: QOS
shared_fields:
  policy_name: {type: string, required: true}
  class_name: {type: string, required: false}
  match_criteria: {type: string, required: false}
  action_type: {type: enum, allowed: [bandwidth, priority, police, shape, queue], required: false}
  bandwidth_percent: {type: integer, required: false}
  priority_level: {type: integer, required: false}
  police_rate: {type: string, required: false}
  shape_rate: {type: string, required: false}
  queue_limit: {type: integer, required: false}
  interface_name: {type: string, required: false, cross_check: planned_network_context}
  direction: {type: enum, allowed: [in, out], required: false}

4.2 Operation-specific intent schema
operations:
  create_qos_policy: {required_fields: [policy_name]}
  add_qos_class: {required_fields: [policy_name, class_name, match_criteria]}
  define_qos_action: {required_fields: [policy_name, class_name, action_type]}
  apply_qos_policy_to_interface: {required_fields: [policy_name, interface_name, direction]}
  update_qos_class: {required_fields: [policy_name, class_name]}
  remove_qos_policy_from_interface: {required_fields: [policy_name, interface_name, direction]}


5) STATIC ROUTE
5.1 Category-specific intent schema
category: STATIC_ROUTE
shared_fields:
  vrf: {type: string, required: false}
  destination_network: {type: string, required: true}
  prefix_length: {type: integer, required: true}
  next_hop: {type: string, required: false}
  outgoing_interface: {type: string, required: false, cross_check: planned_network_context}
  administrative_distance: {type: integer, required: false}
  track_object: {type: string, required: false}
  route_tag: {type: string, required: false}
  description: {type: string, required: false}

5.2 Operation-specific intent schema
operations:
  add_static_route: {required_fields: [destination_network, prefix_length]}
  delete_static_route: {required_fields: [destination_network, prefix_length]}
  update_static_route: {required_fields: [destination_network, prefix_length]}
  add_default_route: {required_fields: [next_hop]}
  add_floating_static_route: {required_fields: [destination_network, prefix_length, next_hop, administrative_distance]}


6) OSPF
6.1 Category-specific intent schema
category: OSPF
shared_fields:
  process_id: {type: integer, required: true}
  router_id: {type: string, required: false}
  area_id: {type: string, required: false}
  network: {type: string, required: false}
  wildcard_mask: {type: string, required: false}
  interface_name: {type: string, required: false, cross_check: planned_network_context}
  cost: {type: integer, required: false}
  hello_interval: {type: integer, required: false}
  dead_interval: {type: integer, required: false}
  passive_interface: {type: boolean, required: false}
  authentication: {type: enum, allowed: [none, simple, md5], required: false}
  neighbor_ip: {type: string, required: false}


6.2 Operation-specific intent schema
operations:
  enable_ospf_process: {required_fields: [process_id]}
  advertise_network: {required_fields: [process_id, network, wildcard_mask, area_id]}
  enable_ospf_on_interface: {required_fields: [process_id, interface_name, area_id]}
  set_ospf_router_id: {required_fields: [process_id, router_id]}
  configure_passive_interface: {required_fields: [process_id, interface_name, passive_interface]}
  remove_ospf_configuration: {required_fields: [process_id]}


2️⃣ Keyword Map
keyword_map:
  INTERFACE: {aliases: ["interface","port","link","eth","gigabit","loopback","svi"]}
  VLAN: {aliases: ["vlan","svlan","lan"]}
  ACL: {aliases: ["acl","access list","firewall rule"]}
  QOS: {aliases: ["qos","quality of service","traffic policy"]}
  STATIC_ROUTE: {aliases: ["static route","route","default route"]}
  OSPF: {aliases: ["ospf","open shortest path first"]}

3️⃣ Example Prompts (for testing)
- "Create VLAN 67 named ShuYing on access switch 1, 2, and 3"
- "Configure GigabitEthernet1/0/1 as access port with VLAN 30 on SW1"
- "Add ACL 2000 to permit SSH from 192.168.1.0/24 on SW1"
- "Add a default route via 192.168.1.1 on SW1"
- "Enable OSPF process 1, set router ID 1.1.1.1, and advertise network 10.0.0.0/24 area 0"


4️⃣ Implementation Instructions
Refactor existing parser or write modular Python from scratch.
Include modules:
IntentSplitter → splits multi-intent prompts
SchemaLoader → loads YAML schemas
Validator → checks required, optional, forbidden, conditional fields
ParameterExtractor → extracts parameters from prompts using LLM
InteractiveAsk → queries missing/ambiguous/invalid fields
Always cross-check devices and interfaces against planned network context. Invalid entries must trigger user interaction.
Output structured JSON intents showing category, operation, parameters, missing/ambiguous/invalid fields.
Include test run using the example prompts, showing the full parsed output.


5.
answer_resolution_rules:
  authoritative_source_order:
    - user_selected_answer
    - exact_planned_network_match
    - schema_defaults
    - llm_inference

  override_policy:
    if_user_selected_answer_exists: always_use_it
    if_planned_network_conflicts_with_user_answer: ask_user_to_confirm
    if_missing_required_fields: prompt_user_in_chat
    if_invalid_entity_not_in_inventory: prompt_user_to_select_from_inventory

BIG PICTURE OF SYSTEM FLOW
RAW PROMPT
   ↓
[1] Keyword Classification
   ↓
[2] Multi-Intent Split
   ↓
[3] Category + Operation Mapping
   ↓
[4] Schema Loading (YAML/JSON)
   ↓
[5] Parameter Extraction (LLM + Rules)
   ↓
[6] Validation Layer
   - missing
   - ambiguous
   - invalid (against NetBox / inventory)
   ↓
[7] Interactive Ask (if needed)
   ↓
[8] Final Structured Intent
   ↓
[9] Ansible Playbook Rendering


REAL EXAMPLES
✅ Example 1
Prompt:

Create VLAN 67 named ShuYing on access switch 1, 2, and 3

Step 1 — Classification

Using your keyword_map:

“VLAN” → category = VLAN
“Create” → operation = create_vlan
Step 2 — Extract Entities

LLM / parser extracts:

{
  "vlan_id": 67,
  "vlan_name": "ShuYing",
  "target_devices": ["access switch 1", "access switch 2", "access switch 3"]
}
Step 3 — Load Schema

From VLAN → create_vlan:

required_fields: [vlan_id]
optional_fields: [vlan_name]
Step 4 — Validation
vlan_id ✅
target_devices ✅ (global field)
vlan_name ✅

👉 No missing fields → proceed

Step 5 — Final Intent
{
  "category": "VLAN",
  "operation": "create_vlan",
  "target_devices": ["SW1", "SW2", "SW3"],
  "vlan_id": 67,
  "vlan_name": "ShuYing"
}
💥 Example 2 (MULTI-INTENT — THIS IS WHERE YOU WIN)
Prompt:

Create VLAN 30 and apply it as access VLAN on GigabitEthernet1/0/1 on SW1

Step 1 — Split Intents

Your system MUST split:

Intent A: Create VLAN 30
Intent B: Assign VLAN 30 to interface
Step 2 — Map Each Intent
Intent A
category: VLAN
operation: create_vlan
Intent B
category: VLAN
operation: assign_vlan_to_access_port
Step 3 — Extract Parameters
[
  {
    "operation": "create_vlan",
    "vlan_id": 30,
    "target_devices": ["SW1"]
  },
  {
    "operation": "assign_vlan_to_access_port",
    "vlan_id": 30,
    "target_interfaces": ["GigabitEthernet1/0/1"],
    "target_devices": ["SW1"]
  }
]
Step 4 — Dependency Check

From schema:

assign_vlan_to_access_port:
  dependencies:
    - interface_mode must be access

👉 System auto-add or enforce:

"interface_mode": "access"

⚠️ Example 3 (AMBIGUOUS)
Prompt:

Create VLAN on devices

Extracted:
{
  "operation": "create_vlan",
  "vlan_id": null,
  "target_devices": null
}
Validation Output:
{
  "missing_fields": ["vlan_id", "target_devices"]
}
System Action:

Trigger chat:

❗ Missing required fields:
- VLAN ID
- Target devices

👉 Please select:
[VLAN ID input]
[Device multi-select from NetBox]
🔥 Critical rule (you wrote this correctly)

NEVER GUESS
ALWAYS USE USER ANSWER

That’s your system’s backbone.

🧠 Example 4 (INTERFACE)
Prompt:

Admin-down all interfaces on SW1

Problem:

This is NOT a normal schema case.

Your system should detect:
category: INTERFACE
operation: set_interface_admin_state

BUT:

interface_name = "ALL"
Solution:

Convert to:

{
  "operation": "set_interface_admin_state_bulk",
  "target_devices": ["SW1"],
  "interface_filter": "ALL",
  "admin_state": "down"
}

👉 This is where you extend schema:

set_interface_admin_state_bulk:
  required_fields: [target_devices, admin_state]
  optional_fields: [interface_filter]
🧠 Example 5 (ACL)
Prompt:

Create ACL 2000 to permit SSH from 192.168.1.0/24 on SW1

Parsed:
{
  "acl_name": "2000",
  "action": "permit",
  "protocol": "tcp",
  "src_ip": "192.168.1.0",
  "prefix_length": 24,
  "dst_port": 22
}
System Splits:
Intent A: create_acl
Intent B: add_acl_rule

👉 Again — multi-intent handling = your advantage

🧠 Example 6 (STATIC ROUTE)
Prompt:

Add a default route on router 2

Extracted:
{
  "operation": "add_default_route",
  "next_hop": null
}
Validation:
missing_fields = ["next_hop"]
System asks:
Please provide next-hop IP:
[ input field ]
🧠 Example 7 (OSPF)
Prompt:

Set router ID 1.1.1.1 and advertise 10.0.0.0/24 in area 0 on SW1

Split:
Intent A: set_ospf_router_id
Intent B: advertise_network
Parsed Output:
[
  {
    "operation": "set_ospf_router_id",
    "router_id": "1.1.1.1",
    "process_id": 1
  },
  {
    "operation": "advertise_network",
    "network": "10.0.0.0",
    "prefix_length": 24,
    "area_id": 0
  }
]

Suggested File / Folder Structure
fyp_network_parser/
│
├── main.py                  # Entry point: loads parser, runs test prompts
├── planned_network.json     # Planned network context (devices, interfaces, etc.)
├── schemas/
│   ├── global_fields.yaml
│   ├── vlan.yaml
│   ├── interface.yaml
│   ├── acl.yaml
│   ├── qos.yaml
│   ├── static_route.yaml
│   └── ospf.yaml
├── keyword_map.yaml         # Mapping keywords → categories
├── modules/
│   ├── __init__.py
│   ├── intent_splitter.py
│   ├── schema_loader.py
│   ├── parameter_extractor.py
│   ├── validator.py
│   └── interactive_ask.py
└── tests/
    ├── test_prompts.json    # Example prompts
    └── run_tests.py         # Test runner for showing parsed intents

✅ Why this works:

schemas/ → all your category/operation definitions in YAML. Easy to edit or expand.
modules/ → core parser functionality is modular: splitting, schema loading, validation, etc.
planned_network.json → ensures every device/interface cross-check is centralized.
tests/ → keeps demo/test prompts separated from logic.

---

## NetTopoView implementation (v2 pipeline)

The in-repo layout mirrors the structure above with these paths:

| Doc concept | Location |
|-------------|----------|
| `global_fields.yaml`, category YAMLs, `keyword_map.yaml` | `backend/ansible/intent_catalog/` |
| Loader + compact prompt + catalog validation | `backend/ansible/intent_pipeline/` |
| LLM integration (default on) | `backend/llm/intent.py` → uses v2 **before** the legacy monolithic prompt |
| Catalog smoke check | `python -m ansible.validate_intent_catalog` (from `backend/`, `PYTHONPATH` implicit) |

**Environment (intent parsing):**
- `INTENT_PIPELINE_V2` — default `on`. Set to `0` / `false` only if you also enable legacy (see below); otherwise the parser returns a structured error intent.
- `INTENT_USE_LEGACY_PARSER` — default **off**. Set to `1` / `true` to re-enable the **old monolithic** prompt (operation reference + intent graph + keywords) as fallback when v2 fails or when v2 is disabled.
- `INTENT_PARSE_LLM_TIMEOUT_SEC` — default **90** (seconds) for intent LLM calls; increase for slow local models (Ollama).

**Implemented modules** (`backend/ansible/intent_pipeline/`):

| Step (doc flow) | Module |
|-----------------|--------|
| [1] Keyword classification | `schema_loader.py` + `keyword_map.yaml` |
| [2] Multi-intent split | `intent_splitter.py` (`split_multi_intent`) |
| [3] Category + operation | category YAMLs + LLM; optional `canonical_operation` |
| [3b] Canonical → legacy | `operation_adapter.py` + `intent_catalog/operation_adapter.yaml` |
| [4] Schema loading | `schema_loader.py` |
| [5] Parameter extraction | LLM via `prompt_builder.py`; per-segment hook `parameter_extractor.py` |
| [6] Validation | `validator.py` |
| [6b] Inventory cross-check | `cross_check.py` (`cross_check_parsed_intent`) |
| [7] Interactive ask list | `interactive_ask.py` (`build_interactive_asks`) |
| Orchestration / trace | `orchestrator.py` (`build_pipeline_trace`, `enrich_preview_response`, `prepare_llm_input_dict`) |

**API (workbench):** `GET /api/v1/ansible/intent-catalog`, `POST /api/v1/ansible/intent-parse-workbench`, and **admin** `GET|PUT /api/v1/ansible/intent-catalog/category/{key}/ui-model` (structured save to `categories/{key}.yaml`; no raw YAML editor required in the UI). Raw file `PUT …/intent-catalog/file/…` remains for emergencies (see `backend/ansible/api.py`).

**UI:** Ansible Studio center column → **Intent parse** tab → `frontend/components/ansible/IntentParseWorkbench.tsx` (parse tree with zoom + resizable left rail; category / operation forms; preview).

### How this doc connects to REFACTOR_FRONTEND.md

| Frontend view ([REFACTOR_FRONTEND.md](./REFACTOR_FRONTEND.md)) | Where the data comes from | Response / file |
|--------------------------------|---------------------------|-----------------|
| **Parse tree** — Category → Operation | `GET …/intent-catalog` | `categories[].operations[]` from `intent_catalog/categories/*.yaml` |
| **Category schema** — shared fields + metadata | `GET|PUT …/intent-catalog/category/{key}/ui-model` | `display_name`, `category_description`, `legacy_feature`, `shared_fields`, `operations` |
| **Operation schema** — required + rules | Same structured API | `required_parameters`, `rules` only; **non-listed parameters are optional** (no `optional_parameters`, no forbidden lists) |
| **Preview/Test** — prompt → parse + missing + invalid + asks | `POST …/intent-parse-workbench` | `parsed_intent` + `pipeline` (`segments`, `categories_by_segment`, `catalog_missing_fields`, `cross_check`, `interactive_asks`) |
| **Full playbook overlay** (chat / Studio) | `POST …/preview-intent` | RAG, disambiguation, `intent_schema_inspector` — still the main path before **create-and-plan** |

**Editing the category schema:** admins use the workbench forms (writes YAML via `ui-model`); others can still edit `backend/ansible/intent_catalog/categories/*.yaml` in the repo and run `python -m ansible.validate_intent_catalog` from `backend/`. Global files (`global_fields.yaml`, `keyword_map.yaml`, `operation_adapter.yaml`) are not covered by the category form UI.

**Tests:** `backend/tests/ansible/test_intent_pipeline.py` (run with `pytest` or `python -c` importing tests).

**Env:** `INTENT_PIPELINE_V2` (default on). Per-segment async extraction (`extract_parameters_for_segment`) is available for future multi-LLM flows.

**Scope:** v2 uses **keyword-based category detection** and injects only matching operations + a **subset of template JSON schemas** into the LLM prompt. Multi-segment prompts use `build_multi_intent_user_prompt` and `merge_multi_intent_llm_response`. Post-processing in `llm/intent.py` is shared; **legacy parser is opt-in only** (`INTENT_USE_LEGACY_PARSER`).

**Preview / chat integration:** `POST /preview-intent` attaches `intent_pipeline` to `parsed_intent` (segments, `categories_by_segment`, `catalog_missing_fields`, `cross_check`, `interactive_asks`) and merges pipeline **missing** / **invalid_inventory** asks into `params_disambiguation` / `devices_disambiguation` when the schema path did not already populate them — so Ansible AI chat can run the same wizards and user selections flow into the next preview call.

Per-category schema is **file-backed** (YAML on disk) with structured **admin** save through the workbench; global catalog files remain YAML-only unless edited via the raw file endpoint.