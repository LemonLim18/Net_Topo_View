# Check, refine, and verify Test_Reference for RAG / Ansible AI LLM

This guide explains how to **run** Test_Reference playbooks manually, **refine** them and their `reference.md`, and **verify** they are suitable for consumption by the RAG-backed Ansible AI (plan-ansible).

---

## 1. How the RAG consumes reference content

- **Only** files under **`ansible/reference/`** are ingested for plan-ansible.  
  **`Test_Reference/`** is **not** ingested. It is a staging area.

- On **RAG Rebuild** (Knowledge Base → Rebuild), the backend:
  - Finds every `reference/{feature}/{vendor}/reference.md`
  - Ingests each with `doc_type=ansible_reference`, `vendor`, and `applicable_feature` (from the path)
  - Stores chunks in the vector store

- When you call **plan-ansible**, the backend:
  - Builds a query from intent + feature + vendor (e.g. `"Configure VLAN on access switches Ansible playbook reference feature vlan vendor cisco_ios"`)
  - Retrieves with `doc_type_filter="ansible_reference"` (and optional vendor filter)
  - Passes the retrieved snippets (`kb_snippets`) to the LLM so the generated playbook follows the same modules and patterns

So: **reference.md** is what the LLM “sees” from RAG. The playbook in the same folder is for humans and for copy-paste; only **reference.md** is ingested unless you change the rebuild logic.

---

## 2. Running Test_Reference playbooks manually

Before promoting to `reference/`, run the playbook yourself to confirm it works.

### 2.1 Prerequisites

- Ansible with required collections (e.g. `cisco.ios`, `community.network` for Huawei).
- Inventory that matches the playbook’s `hosts` (e.g. real or lab devices with correct group names and vars).
- SSH/vault credentials if the playbook uses them.

### 2.2 Per feature/vendor folder

Each Test_Reference scenario has:

- `Test_Reference/{feature}/{vendor}/playbook.yml`
- `Test_Reference/{feature}/{vendor}/inventory.yml`
- `Test_Reference/{feature}/{vendor}/reference.md`

**Option A — Use the folder’s inventory as-is (after filling hosts):**

```bash
cd backend/ansible/Test_Reference/interface/cisco_ios
# Edit inventory.yml: under the group’s hosts, add your device(s), e.g.:
#   vendor_cisco_ios:
#     hosts:
#       my_switch:
#         ansible_host: 192.168.1.46

ansible-playbook -i inventory.yml playbook.yml --check   # dry-run
ansible-playbook -i inventory.yml playbook.yml          # apply
```

**Option B — Use a central inventory and limit to the group:**

```bash
ansible-playbook -i /path/to/your/inventory.yml playbook.yml --limit vendor_cisco_ios --check
ansible-playbook -i /path/to/your/inventory.yml playbook.yml --limit vendor_cisco_ios
```

**Option C — Run from Ansible Studio (UI):**

- Create a job that targets the same devices and use a prompt that matches the feature (e.g. “Set description on interface … on access switches”).
- Run **Check** (dry-run) then **Deploy** and confirm the generated playbook behaves like your Test_Reference playbook (or copy your tested playbook into the job flow for comparison).

### 2.3 What to check when running manually

| Step | Action |
|------|--------|
| **Dry-run** | `ansible-playbook ... --check` completes without module/connection errors; diff shows only intended changes. |
| **Apply** | Run without `--check`; no failed tasks. |
| **Verify on device** | Log in and run `show run` (or equivalent); config matches intent. |
| **Idempotency** | Run the playbook again; no further changes (or only harmless no-op). |

If any step fails, fix the playbook (and vars/inventory) in Test_Reference until all pass.

---

## 3. Refining playbook and reference.md

### 3.1 Playbook

- Use the **exact module names** you want the LLM to emit (e.g. `cisco.ios.ios_vlans`, `cisco.ios.ios_l2_interfaces`).
- Use **inventory group names** consistent with your app (e.g. `access_switches:&vendor_cisco_ios`).
- Prefer **`state: merged`** (or equivalent) for idempotency.
- Add **pre_tasks** (e.g. snapshot) and **block/rescue** if you want the LLM to follow that pattern (see TEST_PROMPTS_AND_ROLLBACK.md).

### 3.2 reference.md — what RAG and the LLM see

**reference.md** is the only file from that folder that is ingested. Make it:

1. **Self-contained and clear**
   - Feature name and vendor in the first line or a short title (e.g. `# Interface — Cisco IOS`).
   - Explicit list of **modules** to use (e.g. `cisco.ios.ios_interfaces`, `ios_l2_interfaces`).
   - **Connection** (e.g. `network_cli`, `ansible_network_os`).
   - **Do NOT use** for deprecated or wrong modules.
   - **Inventory groups** (e.g. `access_switches:&vendor_cisco_ios`).

2. **Keyword-rich for retrieval**
   - Include the **feature** and **vendor** words (e.g. “interface”, “Cisco IOS”, “cisco_ios”) so the plan-ansible query retrieves this doc.
   - Include **intent-like** phrases (e.g. “configure interface description”, “set description on interface”) that users might type.

3. **Operational status**
   - Once you’ve run the playbook successfully, set something like: **Status: Confirmed operational** so future readers (and you) know it’s validated.

Example shape (aligned with `reference/vlan/cisco_ios/reference.md`):

```markdown
# Interface — Cisco IOS (confirmed operational)

- **Connection:** `network_cli` (SSH). Collections: `cisco.ios`.
- **Modules:** `cisco.ios.ios_interfaces` (L2/L3), `cisco.ios.ios_l2_interfaces`, `cisco.ios.ios_l3_interfaces`.
- **Inventory groups:** Use `access_switches:&vendor_cisco_ios` for access switches.
- **Do NOT use:** deprecated modules or raw `cli_config` when a dedicated module exists.
- **Status:** Confirmed operational. Use this pattern when intent is "configure interface" on Cisco IOS.
```

Refine **reference.md** in Test_Reference until it accurately describes the working playbook and is ready to be the only ingested artifact for that feature/vendor.

---

## 4. Verifying suitability for RAG / Ansible AI LLM

### 4.1 Checklist before promoting to reference/

- [ ] **Playbook runs manually**  
  Dry-run and apply succeed; device config and idempotency checked.

- [ ] **reference.md describes the playbook**  
  Modules, connection, groups, and “Do NOT use” match the playbook and your standards.

- [ ] **reference.md is retrieval-friendly**  
  Contains feature name, vendor, and intent-like phrases (so plan-ansible’s query retrieves it).

- [ ] **Folder structure**  
  `reference/{feature}/{vendor}/` will contain at least `reference.md`; you can copy `playbook.yml` and `inventory.yml` from Test_Reference for consistency.

### 4.2 Promote to reference/

When the checklist above is done:

1. Copy (or move) the folder from Test_Reference to reference:
   ```text
   Test_Reference/interface/cisco_ios/  →  reference/interface/cisco_ios/
   ```
   Ensure **reference.md** (and optionally playbook.yml, inventory.yml) are in `reference/{feature}/{vendor}/`.

2. Run **RAG Rebuild**  
   - From the UI: Knowledge Base → Rebuild.  
   - Or: `POST /api/v1/rag/documents/rebuild`.  
   This re-ingests all `reference/**/reference.md` with `doc_type=ansible_reference`.

### 4.3 Verify RAG and LLM behavior after rebuild

1. **Retrieval**
   - Create a job with intent and devices that match the new reference (e.g. “Configure interface description on GigabitEthernet0/1 on access switches”, Cisco IOS devices).
   - Call **plan-ansible** (same feature/vendor as the reference).
   - In backend logs (or any debug you add), confirm that the retrieved snippets (`kb_snippets`) contain text from your new `reference.md` (e.g. “ios_interfaces”, “ios_l2_interfaces”).

2. **Generated playbook**
   - **Correct modules:** Generated playbook uses the modules listed in reference.md (e.g. `cisco.ios.ios_interfaces`), not deprecated or wrong ones.
   - **Correct hosts/groups:** Play uses the expected pattern (e.g. `access_switches:&vendor_cisco_ios`).
   - **No bad patterns:** No deprecated or “Do NOT use” modules.

3. **End-to-end**
   - Run **Check** (dry-run) for that job; fix any module or connection errors.
   - Optionally **Deploy** on a lab device and confirm behavior matches your manually tested Test_Reference playbook.

### 4.4 When is it “suitable for RAG”?

A Test_Reference scenario is **suitable for RAG / Ansible AI LLM** when:

- The playbook runs successfully when executed manually (dry-run, apply, verify, idempotency).
- **reference.md** is the single source of truth for that feature/vendor: accurate, clear, and keyword-rich.
- After copying to **reference/** and running RAG Rebuild, plan-ansible **retrieves** that reference (you can confirm via logs or debug).
- The **generated** playbook **follows** the reference (correct modules, groups, no deprecated use).

If the LLM sometimes ignores the snippet, improve **reference.md** (clearer instructions, explicit “use X” and “do not use Y”) and/or ensure the plan-ansible query includes feature and vendor so retrieval returns this doc.

---

## 5. Quick reference

| Goal | Where | Action |
|------|--------|--------|
| Run playbook manually | Test_Reference/{feature}/{vendor}/ | `ansible-playbook -i inventory.yml playbook.yml [--check]` |
| Refine for LLM | Test_Reference/.../reference.md | Add modules, connection, groups, “Do NOT use”, intent-like phrases; set status when confirmed. |
| Ingest for RAG | reference/ | Copy Test_Reference/{feature}/{vendor}/ → reference/{feature}/{vendor}/; run RAG Rebuild. |
| Verify retrieval | Backend / plan-ansible | After rebuild, run plan-ansible and check that kb_snippets contain the new reference text. |
| Verify output | Generated playbook | Correct modules and hosts; no deprecated modules. |
| E2E | Ansible Studio | Check (dry-run) and optionally Deploy; compare with your manually tested playbook. |

---

## 6. See also

- **README.md** (this folder) — Workflow: test in Test_Reference → move to reference → RAG Rebuild.
- **TEST_PROMPTS_AND_ROLLBACK.md** — Example prompts per feature (e.g. interface, acl, ospf) for access switches and rollback notes.
- **reference/README.md** — Layout of reference/, what’s ingested, and how to test LLM–RAG after consuming references.
