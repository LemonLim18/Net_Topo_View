# VLAN — Multi-vendor single-play template (canonical for RAG/LLM)

**This is the canonical reference for "configure VLAN on access switches" with mixed vendors.** Use this pattern (and the accompanying `playbook_template.yml`) for RAG retrieval and for LLM-generated playbooks so the result matches the job_15-style, confirmed-operational playbook.

Use this pattern when one playbook targets **all access switches** (Cisco, Huawei, Juniper) in a single play. Connection and `ansible_network_os` come from the pipeline inventory per host; do not use `community.network.network_config` (module does not exist).

## Play structure

- **Hosts:** `access_switches`
- **Options:** `gather_facts: no` (required — do not use `gather_facts: yes`; fact gathering on all hosts can fail on unreachable or NETCONF hosts and fail the whole run), `ignore_unreachable: true` (so unreachable hosts do not fail the run).
- **Tasks:** One task block per vendor; each task has `when: inventory_hostname in groups.get('vendor_cisco_ios', [])` (or `vendor_huawei_vrp`, `vendor_juniper_junos`) so only the right devices run each block.

## Module usage (state: merged → use `config` list, not dict)

- **Cisco IOS:** `cisco.ios.ios_vlans` with `config:` as a **list** (e.g. `config: [ { vlan_id: '200', name: 'TEST', state: active } ]`). Do NOT use `vlans:` dict; with `state: merged` the module requires `config` or the task fails. Each task must have `when: inventory_hostname in groups.get('vendor_cisco_ios', [])` so it runs only on Cisco hosts (never run Cisco modules on Juniper/Huawei). `cisco.ios.ios_l2_interfaces` with `config: [ { name: GigabitEthernet0/1, mode: access, access: { vlan: '200' } }, ... ]`.
- **Huawei VRP:** `community.network.ce_config` with `lines` and `parents` (e.g. `vlan batch 200`, `vlan 200`, `name TEST`; then per-interface `parents: interface GigabitEthernet1/0/1`, `lines: port link-type access`, `port default vlan 200`). Do NOT use `community.network.network_config`.
- **Juniper Junos:** `juniper.device.junos_vlans` with `config: [ { name: TEST, vlan_id: '200' } ]`. `juniper.device.junos_l2_interfaces` with `config: [ { name: ge-0/0/0, access: { vlan: '200' } }, ... ]`. Use `juniper.device` (not deprecated `junipernetworks.junos`).

## Inventory

Pipeline builds inventory with per-host `ansible_connection`, `ansible_network_os`, and vendor groups (`vendor_cisco_ios`, `vendor_huawei_vrp`, `vendor_juniper_junos`). No need to set `connection:` in the play when using this inventory.

## Status

Confirmed operational (dry-run and apply on reachable hosts). Use for "configure VLAN on all access switches" with mixed vendors.
