# Interface — Multi-vendor single-play (shutdown / enable)

**This is the canonical reference for "shutdown interfaces" or "enable/disable interfaces" with mixed vendors.** Use this pattern for RAG retrieval and for LLM-generated interface playbooks so the result matches the confirmed-operational style (e.g. job_42).

Use when one playbook targets one or more switches (single host or a group). Connection and `ansible_network_os` come from the pipeline inventory; interface list comes from host vars (`nettopo_interfaces`).

## Play structure

- **Hosts:** Single host (e.g. `Acc_Cisco_1`) or a group (e.g. `access_switches`). Do not rewrite a single-host play to a full job host list.
- **Options:** `gather_facts: no`, `ignore_unreachable: true`.
- **Vars:** Define `interfaces_to_shutdown` (or similar) from pipeline-provided host vars:
  - `interfaces_to_shutdown: "{{ nettopo_interfaces | default([]) | map(attribute='name') | list }}"`
- **Tasks:** All tasks **inline** in the playbook (no role). One task block per vendor; each task loops over the interface list and uses `when:` on the correct vendor group so only the right devices run each block.

## Task naming and looping

- **Do not** put `{{ item }}` in the task name (causes "item undefined" at parse time). Use a static name, e.g. "Shutdown interface (Cisco IOS)".
- Use `loop_control.label: "{{ item }}"` so the run output still shows the interface name.

## Module usage (shutdown = disabled interface)

- **Cisco IOS:** `cisco.ios.ios_interfaces` with `config:` as a list of one item per loop: `config: [ { name: "{{ item }}", enabled: false } ]`, `state: merged`. `when:` must include `inventory_hostname in groups.get('vendor_cisco_ios', [])` and `(interfaces_to_shutdown | default([])) | length > 0`.
- **Huawei VRP:** Multi-scenario via `community.network.ce_config`: per-interface lines from reference template (description, shutdown/undo shutdown, port link-type access/trunk, ip address). Use `interfaces_config` = list of `{ name, description?, enabled?, mode?, access?: { vlan }, trunk?: { allowed_vlans }, ip?: { address, mask } }`, or `interfaces_to_shutdown` for shutdown-only. `when:` must include `inventory_hostname in groups.get('vendor_huawei_vrp', [])` and length check.
- **Juniper Junos:** `juniper.device.junos_interfaces` with `config: [ { name: "{{ item }}", enabled: false } ]`, `state: merged`. Use `juniper.device` (not deprecated `junipernetworks.junos`). `when:` must include `inventory_hostname in groups.get('vendor_juniper_junos', [])` and length check.

## Example task block (Cisco)

```yaml
- name: Shutdown interface (Cisco IOS)
  cisco.ios.ios_interfaces:
    config:
      - name: "{{ item }}"
        enabled: false
    state: merged
  loop: "{{ interfaces_to_shutdown | default([]) }}"
  loop_control:
    label: "{{ item }}"
  when:
    - inventory_hostname in groups.get('vendor_cisco_ios', [])
    - (interfaces_to_shutdown | default([])) | length > 0
```

## Inventory

Pipeline builds inventory with per-host `ansible_connection`, `ansible_network_os`, and vendor groups (`vendor_cisco_ios`, `vendor_huawei_vrp`, `vendor_juniper_junos`). No need to set `connection:` in the play.

## Status

Confirmed operational (job_42). Use for "shutdown interfaces on [host or group]" with mixed vendors. For enable (bring up), use `enabled: true` and the same modules; for Huawei use `lines: [ undo shutdown ]`.
