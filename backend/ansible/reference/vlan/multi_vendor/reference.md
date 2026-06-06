# VLAN — Multi-vendor single-play template (canonical for RAG/LLM)

**This is the canonical reference for "configure VLAN on access switches" with mixed vendors.** Use this pattern (and the accompanying `playbook_template.yml`) for RAG retrieval and for LLM-generated playbooks so the result matches the job_15-style, confirmed-operational playbook.

**Intent rule:** The LLM outputs structured intent only (vlan id/name, target_hosts, interfaces). The template does **not** invent intent: if the user did not specify interfaces, intent has `interfaces: []` and the playbook only runs "Ensure VLAN exists" tasks. If the user specifies interfaces (e.g. "assign Gi0/1 as access VLAN"), intent has `interfaces: [{ name, mode, access.vlan }]` and the same template runs interface config. One template, data-driven.

Use this pattern when one playbook targets **all access switches** (Cisco, Huawei, Juniper) in a single play. Connection and `ansible_network_os` come from the pipeline inventory per host; do not use `community.network.network_config` (module does not exist).

## Play structure

- **Hosts:** `access_switches`
- **Options:** `gather_facts: no` (required — do not use `gather_facts: yes`; fact gathering on all hosts can fail on unreachable or NETCONF hosts and fail the whole run), `ignore_unreachable: true` (so unreachable hosts do not fail the run).
- **Tasks:** One task block per vendor; each task has `when: inventory_hostname in groups.get('vendor_cisco_ios', [])` (or `vendor_huawei_vrp`, `vendor_juniper_junos`) so only the right devices run each block.

## Module usage (state: merged → use `config` list, not dict)

- **Cisco IOS:** `cisco.ios.ios_vlans` with `config:` as a **list** (e.g. `config: [ { vlan_id: '200', name: 'TEST', state: active } ]`). Do NOT use `vlans:` dict; with `state: merged` the module requires `config` or the task fails. Each task must have `when: inventory_hostname in groups.get('vendor_cisco_ios', [])` so it runs only on Cisco hosts (never run Cisco modules on Juniper/Huawei). `cisco.ios.ios_l2_interfaces` with `config: [ { name: GigabitEthernet0/1, mode: access, access: { vlan: '200' } }, ... ]`.
- **Huawei VRP:** `community.network.ce_config` with `lines` and `parents` (e.g. `vlan batch 200`, `vlan 200`, `name TEST`; then per-interface `parents: interface GigabitEthernet1/0/1`, `lines: port link-type access`, `port default vlan 200`). Do NOT use `community.network.network_config`.
- **Juniper Junos:** `juniper.device.junos_config` with CLI `set` lines for VLAN creation and interface binding. Prefer direct CLI rendering over `junos_vlans` / `junos_l2_interfaces` on older Junos releases.

## Inventory

Pipeline builds inventory with per-host `ansible_connection`, `ansible_network_os`, and vendor groups (`vendor_cisco_ios`, `vendor_huawei_vrp`, `vendor_juniper_junos`). No need to set `connection:` in the play when using this inventory.

## Intent schema and validation

Structured intent (LLM/API) is validated against `reference/vlan/intent_schema.json`:

- **vlan:** `{ id: 1–4094, name?: string }` (required)
- **target_hosts:** array of hostnames (required)
- **interfaces:** array of `{ name, mode: "access"|"trunk", access_vlan?, allowed_vlans?, native_vlan? }` (optional; use `[]` when user did not request specific ports)

On validation failure the API returns 422 with `issues` listing schema errors.

## Role-based playbook (optional)

`playbook_with_role.yml` (generate_and_apply style) is a single play that passes only **vlan** and **interfaces** to `role: vlan`. The role is vendor-aware via **ansible_network_os** (inventory host vars), not via a `vendors` list:

- **When conditions:** `'cisco' in ansible_network_os or ansible_network_os is search('ios')`, same for huawei/ce and junos/juniper.
- **Interfaces:** Configured only when `interfaces | length > 0`; the role builds module-specific L2 config from the schema-shaped `interfaces` (name, mode, access_vlan, allowed_vlans, native_vlan).

Pipeline inventory must set `ansible_network_os` per host (e.g. `ios`, `ce`, `junos`). Set `ANSIBLE_ROLES_PATH` or copy the role so `role: vlan` is found at run time.

## Guardrails

- **Validation:** Intent validated against JSON schema; invalid intent returns 422 (no playbook generated).
- **Conservative defaults:** No interface config unless the user provided `interfaces`; template does not invent ports (e.g. no default Gi0/1, Gi0/2).
- **Dry-run first:** Pipeline should run the playbook with `--check` before apply.
- **Approval:** Show generated playbook (diff) to operator to accept/reject before apply.
- **Lint/tests:** Use ansible-lint, yamllint, and role tests (e.g. Molecule) in CI.

## Status

Confirmed operational (dry-run and apply on reachable hosts). Use for "configure VLAN on all access switches" with mixed vendors.
