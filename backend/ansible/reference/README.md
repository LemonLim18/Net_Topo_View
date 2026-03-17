# Ansible reference library (official — consumed by RAG)

This folder holds the **official** Ansible reference per **feature** and **vendor**. Every `reference/{feature}/{vendor}/reference.md` is ingested by RAG on **RAG Rebuild** and used by the LLM to generate playbooks. Test each feature by running plan-ansible with a matching intent and checking the generated playbook against the reference.

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
  (acl, ospf, qos, static_route are in Test_Reference/ — not ingested by RAG)
```

Each `{feature}/{vendor}/` has `reference.md`, `playbook.yml`, `inventory.yml`.

---

## Reference scenarios (ingested by RAG)

| Feature     | Cisco IOS | Huawei VRP | Juniper Junos | Multi-vendor template        |
|-------------|-----------|------------|---------------|-----------------------------|
| **vlan**    | ✅        | ✅         | ✅            | ✅ `vlan/multi_vendor/`     |
| **interface** | ✅        | ✅         | ✅            | ✅ `interface/multi_vendor/` (shutdown/enable) |

**Other features (acl, ospf, qos, static_route)** live in `Test_Reference/`; copy into `reference/` when you want them ingested by RAG.

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
