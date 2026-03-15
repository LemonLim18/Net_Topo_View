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

## Status (as of now)

| Feature | Cisco IOS | Huawei VRP | Juniper Junos |
|---------|-----------|-----------|----------------|
| vlan    | ✅ Confirmed | ✅ Confirmed (CLI) | ⚠️ Unverified |

When you confirm a playbook works for a vendor, update the corresponding `reference.md` (e.g. set status to "Confirmed") and copy or sync the working playbook into `playbook.yml`. When a template from a vendor is confirmed operational, add or update it here so the LLM RAG can retrieve it for plan-ansible.

## RAG ingestion

To have the LLM use these references:

1. **Option A — Knowledge base:** Copy each `reference.md` (or a single consolidated doc per feature/vendor that includes playbook + inventory snippets) into `backend/rag/knowledge_base/` with a name like `ansible_reference_vlan_cisco_ios.md`, and add a `.meta.json` or `curation_manifest.json` entry with `doc_type: "ansible_reference"`, `vendor: "cisco_ios"`, `applicable_feature: "vlan"`. Then run **RAG Rebuild** so the chunks are ingested.

2. **Option B — Auto-ingest:** The backend can be extended to ingest `reference/**/reference.md` (and optionally playbook/inventory content) during RAG rebuild, with metadata derived from the path (`reference/{feature}/{vendor}/`).

Plan-ansible retrieval passes **feature** and **vendor** (from intent and device inventory) into the retriever so the right reference is preferred when building playbooks.
