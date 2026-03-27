# Ansible reference library (official — consumed by RAG)

This folder holds the **official** Ansible reference per **feature** and **vendor**. Every `reference/{feature}/{vendor}/reference.md` is ingested by RAG on **RAG Rebuild** and used by the LLM to generate playbooks. Test each feature by running plan-ansible with a matching intent and checking the generated playbook against the reference.

---

## Before you test the Ansible AI prompt (checklist)

Do these **before** typing a prompt so preview and generation behave correctly:

1. **Run RAG Rebuild**  
   So the vector store has the latest `reference/**/reference.md` (including VLAN, interface).  
   - **UI:** Use the RAG/Knowledge Base rebuild action if your app exposes it.  
   - **API:** `POST /api/v1/rag/documents/rebuild` (then wait for background job to finish).  
   Without this, "Reference templates" may be empty or wrong and reasoning won’t cite the right docs.

2. **Have devices available for target resolution**  
   "Access switch 1, 2, 3" (or hostnames) are resolved from **planning context** (device DB / NetBox).  
   - Ensure devices exist in the **local device DB** (e.g. after NetBox sync or manual add).  
   - Ensure devices have **hostname** and, for vendor-specific behavior, **vendor** (e.g. `cisco_ios`, `huawei_vrp`).  
   Otherwise you may get "no target devices" or empty `target_hosts`.

3. **Backend and frontend running**  
   Ansible API (preview-intent, create-and-plan) and the frontend must be up so the chat and overlay work.

4. **Optional — for full create-and-plan:**  
   Configure the **Ansible model** (Gemini or Ollama) in the UI; needed for generation. Preview (categories, references, target_hosts) works with just parsing + RAG.

After this, use **How to manually prompt** below to test.

---

## How to manually prompt (Playbook Studio)

1. Open the **Ansible** view and focus the **chat** panel (the text area at the bottom).
2. Type your intent in plain English, e.g.  
   **"Create VLAN 67 named ShuYing on access switch 1, 2, 3"**  
   or **"Shut down interfaces Gi0/1 and Gi0/2 on access_sw1"**.
3. Press **Enter** or click **Send**.  
   This opens the **Playbook Studio** overlay with that text as the intent description.
4. The backend runs **preview-intent** (parse + RAG retrieval) and shows:
   - Intent categories (e.g. VLAN, interface)
   - Resolved target devices
   - **Reference templates** that RAG and the resolver picked (you can add/remove before generating)
   - "How we got here" reasoning
5. Adjust **target hosts** or **reference templates** if needed, then click **Generate** to run create-and-plan.

**API equivalent:** You can call `POST /api/v1/ansible/preview-intent` with `{"intent_description": "Create VLAN 67 named ShuYing on access switch 1,2,3", ...}` and then `POST /api/v1/ansible/create-and-plan` with the same `intent_description` (and optional `target_hosts`, `feature`, `vendor`, `params`).

---

## Which files are fed into RAG (and which template is used)

**Only `reference.md` files are ingested into RAG.** On **RAG Rebuild**, the backend does:

- `reference/**/reference.md` → ingested with `doc_type=ansible_reference`, `applicable_feature`, and `vendor` from the path.

So for **VLAN**, these are the files that should be the main input for the LLM / template decision:

| File | Purpose |
|------|--------|
| `reference/vlan/cisco_ios/reference.md` | Single-vendor Cisco VLAN reference (RAG + fallback when only Cisco) |
| `reference/vlan/huawei_vrp/reference.md` | Single-vendor Huawei VLAN reference |
| `reference/vlan/juniper_junos/reference.md` | Single-vendor Juniper VLAN reference |
| **`reference/vlan/multi_vendor/reference.md`** | **Canonical multi-vendor VLAN reference** — use when mixed vendors or when you want the single-play, role-ready pattern |

**Template selection is deterministic in code**, not by LLM choice:

- **Multiple vendors** (e.g. Cisco + Huawei in target hosts) → resolver picks **`vlan/multi_vendor/playbook_template.yml`** (or `playbook_with_role.yml` if you switch to the role-based flow).
- **Single vendor** (e.g. only Cisco) → resolver picks **`vlan/cisco_ios/playbook.yml`** (and RAG is filtered so multi_vendor docs are not retrieved).
- **No vendor** → fallback to **`vlan/multi_vendor/playbook_template.yml`**.

So: **feed the four `reference.md` files into RAG** (they are auto-ingested on Rebuild). The **playbook YAML** files (`playbook.yml`, `playbook_template.yml`, `playbook_with_role.yml`) are **not** in the vector store; they are chosen by `reference_resolver.resolve_playbook_template_path(feature, vendors)`. Improving the **reference.md** content (especially **multi_vendor/reference.md** for mixed-vendor intents) improves what the LLM sees in RAG and what appears in "Reference templates" and reasoning; the actual file path used for generation is still resolver + feature + vendors.

---

## Layout

```
reference/
  vlan/                   # VLAN (RAG-ingested)
    cisco_ios/
    huawei_vrp/
    juniper_junos/
    multi_vendor/         # Single-play template with when: per vendor
  interface/              # Interface shutdown/enable (RAG-ingested, ref: job_42)
    cisco_ios/
    huawei_vrp/
    juniper_junos/
    multi_vendor/
  ospf/
    cisco_ios/
    huawei_vrp/
    juniper_junos/
    multi_vendor/
   acl/
   qos/
   static_route/
```

Each `{feature}/{vendor}/` has `reference.md`, `playbook.yml`, `inventory.yml`.

---

## Reference scenarios (ingested by RAG)

| Feature     | Cisco IOS | Huawei VRP | Juniper Junos | Multi-vendor template        |
|-------------|-----------|------------|---------------|-----------------------------|
| **vlan**    | ✅        | ✅         | ✅            | ✅ `vlan/multi_vendor/`     |
| **interface** | ✅        | ✅         | ✅            | ✅ `interface/multi_vendor/` (shutdown/enable) |
| **qos**     | ✅        | ✅         | ✅            | ✅ `qos/multi_vendor/`      |
| **ospf**    | ✅        | ✅         | ✅            | ✅ `ospf/multi_vendor/`     |

All core intent features now have official references under `reference/` and are ingested on RAG rebuild.

**RAG ingestion:** On **RAG Rebuild**, each `reference/**/reference.md` is ingested with `doc_type=ansible_reference`, `applicable_feature`, and `vendor` from the path. Plan-ansible retrieves by intent feature and device vendor so the LLM gets the right reference.

**VLAN multi-vendor template:** `vlan/multi_vendor/` provides a single-play pattern (hosts: access_switches, when: per vendor, ignore_unreachable: true) and correct module usage for Cisco, Huawei, and Juniper. Use for “configure VLAN on all access switches” with mixed vendors.

**Interface multi-vendor template:** `interface/multi_vendor/` (reference: job_42) provides the pattern for shutdown/enable interfaces: single play, all tasks inline, `interfaces_to_shutdown` from `nettopo_interfaces`, correct modules per vendor (`cisco.ios.ios_interfaces`, `community.network.ce_config`, `juniper.device.junos_interfaces`). Use for "shutdown interfaces on [host or group]" with mixed vendors.

---

## Testing each folder (validate RAG → LLM output)

1. **Run RAG Rebuild**  
   So all `reference/**/reference.md` are in the vector store (e.g. RAG admin UI or `POST /api/v1/rag/documents/rebuild`).

2. **Test the LLM output**  
   - Create a job with an intent that matches the reference (e.g. VLAN: “Configure VLAN 200 named TEST on all access switches; put it on Gi0/1 and Gi0/2 (Cisco) or Gi1/0/1 and 1/0/2 (Huawei).”).  
   - Run **create-and-plan** or **plan-ansible** and open the generated playbook.

3. **Check the generated playbook**  
   - **Modules:** Match the reference (e.g. Cisco VLAN → `cisco.ios.ios_vlans` with `config` list; Huawei → `community.network.ce_config` with `lines`/`parents`; no `community.network.network_config`).  
   - **Parameters:** Resource modules use `config:` list and `state: merged` where applicable.  
   - **Vendor scope:** Correct `when:` or separate plays so only the right vendor runs each block.  
   - **Deprecated:** No deprecated FQCNs (e.g. use `juniper.device`, not `junipernetworks.junos`).

4. **Optional: Check (dry-run)**  
   Run **Check** on the job; fix any module or connection errors and update the reference or prompt if the LLM keeps diverging.

When a generated playbook is wrong for a feature/vendor, improve the corresponding `reference.md` (or playbook snippet in it), run RAG Rebuild, and re-test.

---

## Maintaining references

- When you confirm a playbook for a vendor, set **Status:** in `reference.md` to “Confirmed” and sync the working play into `playbook.yml`.  
- For Huawei, always document: use `community.network.ce_config`; do **not** use `community.network.network_config` (module does not exist).  
- For Juniper, use `juniper.device` (e.g. `junos_vlans`, `junos_l2_interfaces`); avoid deprecated `junipernetworks.junos`.
