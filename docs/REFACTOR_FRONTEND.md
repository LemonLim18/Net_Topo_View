# Intent Parse UI / schema workbench (frontend)

**Related backend & parser:** [REFACTOR_INTENT_PARSER.md](./REFACTOR_INTENT_PARSER.md) — YAML catalog (`intent_catalog/`), pipeline modules (`intent_pipeline/`), LLM integration (`llm/intent.py`), validation, inventory cross-check, canonical → legacy adapter.

**Where schema data lives (edit on disk):** `backend/ansible/intent_catalog/` (`global_fields.yaml`, `keyword_map.yaml`, `categories/*.yaml`, `operation_adapter.yaml`). Validate after edits: `cd backend && python -m ansible.validate_intent_catalog`.

**Quick link — integration table:** [§ Integration: parser, APIs, and files](#integration-parser-apis-and-files) below.

---

## Vision

make Intent Parse a schema editor + graph viewer + inspector panel all in one tab.

The key idea is:

Left side = navigation tree of Category → Operation → Parameter
Center = visual graph of the intent parsing tree / dependency graph
Right side = editable form for the currently selected node
Top = switchable views for category-level, operation-level, dependency-level, and preview/test

That gives you both the Google Forms editor feel and the tree/graph visibility you need.

What to implement
1) A single “Intent Parse” tab with 4 view modes

Inside the tab, let the user switch between:

A. Tree View

This is the main hierarchy view.

Target Devices
INTERFACE
configure_l2_access_port
interface_name
access_vlan
VLAN
create_vlan
vlan_id
vlan_name

Use this when the user wants to understand the full structure.

B. Category View

This is for managing one category at a time.

Example:

INTERFACE
VLAN
ACL
QOS
STATIC_ROUTE
OSPF

When a category is selected, show:

category description
category fields
operations under this category
add / edit / delete category fields
C. Operation View

This is the most important one for your FYP.

When a user selects an operation, show:

operation name
required fields
optional fields
forbidden fields
conditional dependencies
cross-check rules

This is where you define things like:

assign_vlan_to_access_port
required: vlan_id, target_interfaces
dependency: interface_mode = access
D. Preview/Test View

Paste a prompt and see:

detected category
detected operation
extracted parameters
missing parameters
invalid device/interface
ask sequence

This is the demo view.

How to present the UI
Best layout for your use case
Top bar

Buttons:

Tree View
Category View
Operation View
Dependency Graph
Preview/Test
Left panel

A collapsible tree:

Target Devices
└── VLAN
    ├── create_vlan
    ├── rename_vlan
    └── assign_vlan_to_access_port
└── INTERFACE
    ├── configure_l2_access_port
    ├── configure_l2_trunk_port
    └── assign_l3_address
Center panel

Shows the selected view:

tree graph
operation table
parameter form
preview parser result
Right panel

Inspector/editor for the selected item:

edit field name
required/optional toggle
type
validation
dependency rules
delete button

This is the closest to a Google Forms editor, but for network intent schema.

How the hierarchy should work

Your data should be structured like this:

{
  "target_devices": {
    "required": true
  },
  "categories": [
    {
      "name": "VLAN",
      "fields": [
        {"name": "vlan_id", "type": "integer", "required": true},
        {"name": "vlan_name", "type": "string", "required": false}
      ],
      "operations": [
        {
          "name": "create_vlan",
          "required_fields": ["vlan_id"],
          "optional_fields": ["vlan_name"]
        }
      ]
    }
  ]
}

That structure is what your frontend should render.

What the frontend needs to support
Category level

You need CRUD for:

create category
rename category
delete category
add category field
delete category field

Example category fields:

description
device
target_devices
category_name
Operation level

You need CRUD for:

create operation
rename operation
delete operation
set required parameters
set optional parameters
set forbidden parameters
set dependencies

Example:

configure_l2_access_port
required: interface_name, access_vlan
optional: description
dependency: interface_mode = access
Parameter level

You need CRUD for:

add parameter
edit parameter
delete parameter
set type
set validation
set cross-check source
set enum values
set regex rules
How to visualize the intent parsing tree

Use a node graph with 3 node types:

Category nodes
Operation nodes
Parameter nodes
Example tree
Target Devices
├── VLAN
│   ├── create_vlan
│   │   ├── vlan_id (required)
│   │   ├── vlan_name (optional)
│   └── assign_vlan_to_access_port
│       ├── vlan_id (required)
│       ├── target_interfaces (required)
│       └── interface_mode = access (dependency)
├── INTERFACE
│   ├── configure_l2_access_port
│   │   ├── interface_name (required)
│   │   ├── access_vlan (required)
Visual rules
Required fields: red or bold
Optional fields: gray
Cross-checked fields: blue
Missing fields: yellow warning
Invalid fields: red warning
Best frontend component breakdown

Use a structure like this:

IntentParseTab
├── IntentViewSwitcher
├── IntentTreePanel
├── IntentGraphCanvas
├── CategoryEditor
├── OperationEditor
├── ParameterEditor
├── DependencyEditor
├── PromptPreviewPanel
└── ValidationResultPanel
What each component does
IntentViewSwitcher

Switches between:

Tree
Category
Operation
Dependencies
Preview
IntentTreePanel

Shows the hierarchy and lets you click nodes.

IntentGraphCanvas

Shows the visual graph of:

category → operation → parameter
dependency arrows
required/optional markers
CategoryEditor

Lets admin:

add field
delete field
edit category description
OperationEditor

Lets admin:

choose category
create operation
assign required/optional fields
define forbidden fields
define dependencies
ParameterEditor

Lets admin:

edit parameter type
set enum values
set validation
cross-check planned network context
DependencyEditor

Lets admin define:

“required if”
“mutually exclusive”
“must exist in inventory”
“must be validated against planned network context”
PromptPreviewPanel

Paste prompt and see parser result.

ValidationResultPanel

Shows:

missing
ambiguous
invalid
user ask sequence
What the backend API should expose

Your frontend needs endpoints like:

GET /schemas
GET /schemas/categories
POST /schemas/categories
PUT /schemas/categories/:id
DELETE /schemas/categories/:id
GET /schemas/categories/:id/operations
POST /schemas/categories/:id/operations
PUT /schemas/operations/:id
DELETE /schemas/operations/:id
GET /schemas/operations/:id/parameters
POST /schemas/operations/:id/parameters
PUT /schemas/parameters/:id
DELETE /schemas/parameters/:id
POST /parser/preview
POST /parser/validate

That way the UI is not hardcoded. It becomes a live schema editor.

### NetTopoView design note: file-first schemas + in-app edit

For **small, fixed** category/operation definitions, the **source of truth** remains YAML under `backend/ansible/intent_catalog/`.

- **Admins** can edit those files from the UI: Ansible Studio → **Intent parse** → **Edit schema** tab — saves via **`PUT /api/v1/ansible/intent-catalog/file/{path}`** (validates YAML, reloads in-memory catalog; bad saves roll back the file).
- **Operators/viewers** see the same tab read-only (or use **Preview** only).
- **CLI validation** still applies for CI: `python -m ansible.validate_intent_catalog` from `backend/`.

A **large generic REST CRUD** layer (dozens of routes) is still unnecessary; the app uses a **short allowlist** of paths (`global_fields.yaml`, `keyword_map.yaml`, `operation_adapter.yaml`, `categories/*.yaml`).

---

## Integration: parser, APIs, and files

This section **ties REFACTOR_FRONTEND to REFACTOR_INTENT_PARSER** and the actual NetTopoView code paths.

### What is implemented today

| This doc (view / feature) | Code | Backend |
|---------------------------|------|---------|
| **Intent Parse** tab in Ansible Studio | `frontend/views/AnsibleStudioView.tsx` — center toolbar **Playbook** \| **Intent parse** | — |
| Tree / Category / Operation / Preview / **Edit schema** (YAML on server disk) | `frontend/components/ansible/IntentParseWorkbench.tsx` | `GET …/intent-catalog`, `GET …/intent-catalog/editable-files`, `GET/PUT …/intent-catalog/file/{path}` (**PUT admin-only**) |
| Preview/Test — paste prompt, see parse + trace + asks | Same component | `POST /api/v1/ansible/intent-parse-workbench` → `pipeline` + `parsed_intent` ([REFACTOR_INTENT_PARSER.md](./REFACTOR_INTENT_PARSER.md) § NetTopoView) |
| Chat / Playbook Studio — full intent preview + RAG | `frontend/components/ansible/AnsibleChatPanel.tsx` (and Studio) | `POST /api/v1/ansible/preview-intent` |

### Doc placeholder routes → real API paths

| Original placeholder (this doc) | NetTopoView route | Purpose |
|--------------------------------|-------------------|---------|
| `GET /schemas` / categories tree | `GET /api/v1/ansible/intent-catalog` | Categories, operations, required/optional, rules |
| `POST /parser/preview` (workbench) | `POST /api/v1/ansible/intent-parse-workbench` | Lightweight parse + **pipeline** trace (split, categories, catalog validation, cross-check, `interactive_asks`) |
| (full overlay before generate) | `POST /api/v1/ansible/preview-intent` | RAG references, disambiguation, schema inspector, merge with chat params |

Base URL prefix in the app: `/api/v1/ansible/…` (see `frontend/src/services/api.ts`: `getIntentCatalog`, `intentParseWorkbench`, `previewIntent`).

### Intent parser behavior (v2-only by default)

- **Refactored parser** runs for all LLM-backed intent parsing (Gemini, Ollama, etc.). The **legacy** monolithic prompt is **off** unless the backend sets **`INTENT_USE_LEGACY_PARSER=1`**.
- **Local models:** increase **`INTENT_PARSE_LLM_TIMEOUT_SEC`** (default **90** seconds) if Ollama is slow to respond.

### Preview/Test panel: which JSON fields to show

Align the UI with [REFACTOR_INTENT_PARSER.md](./REFACTOR_INTENT_PARSER.md) **pipeline** output (also on `parsed_intent.intent_pipeline` from **`preview-intent`**):

| UI label (this doc) | JSON path (workbench / preview response) |
|---------------------|---------------------------------|
| Detected split / multi-intent | `pipeline.segments`, `pipeline.segment_summary` |
| Detected category (per segment) | `pipeline.categories_by_segment` |
| Extracted parameters | `parsed_intent.parameters`, scope, action/feature |
| Missing parameters | `pipeline.catalog_missing_fields`, `parsed_intent.requires_resolution` |
| Invalid device/interface | `pipeline.cross_check.invalid_devices`, `invalid_interfaces`; also `parsed_intent.parameters._inventory_validation` when merged |
| Ask sequence | `pipeline.interactive_asks` |

### Writing / changing behavior

1. **Prompt rules & operations list** — `backend/ansible/intent_pipeline/prompt_builder.py` + category YAMLs under `intent_catalog/categories/`.  
2. **Canonical fine-grained ops → legacy playbook** — `backend/ansible/intent_catalog/operation_adapter.yaml` + `intent_pipeline/operation_adapter.py`.  
3. **Splitting compound sentences** — `intent_pipeline/intent_splitter.py`.  
4. **Frontend layout only** — `IntentParseWorkbench.tsx` (no schema CRUD yet).

### Not implemented yet (from this doc)

- **Dependency Graph** as a real graph canvas (center panel).  
- **Right inspector** — live edit required/optional/forbidden + save via REST (would need `PUT/POST/DELETE` schema APIs or DB-backed schemas).  
- **`POST /parser/validate`** as a separate endpoint (validation is embedded in workbench + `preview-intent` today).

---

How to organize the page for category and operation level
Category level page

Show:

category name
category description
list of operations
list of shared fields
button to add operation
button to add category field
Operation level page

Show:

operation name
required fields
optional fields
forbidden fields
dependency rules
validation rules
test prompt against this operation

This makes the category page the “overview”, and the operation page the “actual editor”.

My recommendation for your demo

For your FYP, do this first:

Minimum strong version
Tree panel on the left
Graph in the middle
Editor on the right
Preview/Test at the bottom

That alone is enough to look serious.

What to show in demo
Click category
Show operations
Click operation
Show required/optional fields
Paste prompt
Show parser result
Highlight missing or invalid device/interface
Ask user for correction

That will clearly show your system is not just a static form, but a schema-driven intent parser manager.

A simple mental model

Think of it like this:

Category = folder
Operation = form
Parameter = field
Dependency = validation rule
Preview = live test

That is the cleanest way to present it.