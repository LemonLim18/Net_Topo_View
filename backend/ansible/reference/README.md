# Ansible reference library (confirmed operational)

This folder holds **confirmed operational** Ansible playbook and inventory snippets per **feature** and **vendor**. The LLM uses them (via RAG) to generate playbooks that match your device inventory and intent.

## Layout

```
reference/
  {feature}/           # e.g. vlan, acl, interface, ospf, qos, static_route
    {vendor}/          # e.g. cisco_ios, huawei_vrp, juniper_junos
      playbook.yml     # Reference play(s) for this feature + vendor
      inventory.yml    # Minimal inventory pattern (groups, vars)
      reference.md     # Notes for LLM: modules, connection, caveats, status
```

---

## Current reference scenarios (as of now)

| Feature | Cisco IOS | Huawei VRP | Juniper Junos |
|---------|-----------|------------|----------------|
| **vlan** | ✅ Confirmed | ✅ Confirmed (CLI) | ⚠️ Unverified |

**What’s ingested for RAG:** Each `reference/{feature}/{vendor}/reference.md` is auto-ingested on **RAG Rebuild** with `doc_type=ansible_reference`, `vendor`, and `applicable_feature` from the path. Plan-ansible uses **feature** and **vendor** (from intent and device inventory) to retrieve the right reference.

**Scenarios covered today:** “Configure VLAN on access switches” with targets resolved from NetBox → Cisco or Huawei get the correct module pattern (e.g. `ios_vlans` / `ios_l2_interfaces`, or `ce_config`). Juniper has a reference but is marked unverified.

---

## Gaps: what else for a full-rounded reference library

- **More features (prompt already mentions them, no reference yet):**
  - **interface** – L2/L3 interface config per vendor (e.g. `ios_interfaces`, `ce_config`, Junos).
  - **acl** – ACL create/bind (Cisco, Huawei, Juniper).
  - **ospf** – OSPF areas, router-id, interface enable (vendor modules).
  - **qos** – Queues, policies, interface attachment.
  - **static_route** – Static routes (vendor-specific modules).
- **Richer RAG content:** Ingest **playbook.yml** (and optionally **inventory.yml**) per feature/vendor with the same metadata, or add a short “reference play snippet” block into each `reference.md`, so the LLM sees concrete task structure, not only prose.
- **Juniper VLAN:** Run and confirm the existing `reference/vlan/juniper_junos/` playbook; then set status to Confirmed in `reference.md`.
- **Mixed-vendor runs:** Ensure retrieval and prompts handle one playbook targeting multiple vendors (e.g. access_switches with both Cisco and Huawei) using the right snippet per host.

When you confirm a playbook works for a vendor, update the corresponding `reference.md` (set status to “Confirmed”) and sync the working playbook into `playbook.yml`. Add new feature/vendor folders as you validate them.

---

## How to test LLM–RAG accuracy after consuming references

1. **Ingest references**
   - Run **RAG Rebuild** (e.g. from the RAG admin UI or `POST /api/v1/rag/documents/rebuild`) so all `reference/**/reference.md` (and any other ingested refs) are in the vector store.

2. **Smoke test retrieval**
   - Create a job with intent that matches a reference (e.g. “Configure VLAN 200 on access switches”, devices = Cisco or Huawei).
   - Call **plan-ansible** and inspect the request: the backend builds a query like `"{intent} Ansible playbook reference feature vlan vendor cisco_ios"` and retrieves with `doc_type_filter=ansible_reference`. Check logs or add a temporary debug response to see that `kb_snippets` contain the expected reference text (e.g. “ios_vlans”, “ios_l2_interfaces”, “Do NOT use deprecated…”).

3. **Output accuracy**
   - **Correct modules:** Generated playbook uses the reference pattern (e.g. Cisco: `cisco.ios.ios_vlans` / `ios_l2_interfaces`; Huawei: `community.network.ce_config` with the right `lines`/`parents`).
   - **No deprecated modules:** No `cisco.ios.vlan` or `vlan_interfaces` for Cisco.
   - **Inventory/vars:** Groups and connection vars align with reference (e.g. `ansible_network_os`, `network_cli`, vault vars).
   - **Idempotency:** Uses `state: merged` or equivalent; no append-only or destructive commands unless intended.

4. **Structured regression (optional)**
   - Add a pytest that: (a) mocks or uses a test vector store with known ansible_reference chunks, (b) calls the plan-ansible builder with a fixed intent (e.g. VLAN + cisco_ios), (c) parses the generated YAML and asserts presence of expected task modules and absence of deprecated ones. Run after RAG rebuild or with seeded test data to validate “after consuming references” behavior.
   - Alternatively, maintain a small **golden set**: 2–3 intents (e.g. “VLAN on Cisco”, “VLAN on Huawei”), run plan-ansible, and diff or assert key lines (module names, play hosts) against expected snippets.

5. **End-to-end**
   - Run **Check** (dry-run) on a real or lab device after plan-ansible; confirm no module or connection errors and that the diff matches intent. That validates both RAG retrieval and LLM output in a real scenario.
