# Interface — Juniper Junos (shutdown / enable)

- **Connection:** `netconf` (port 830). Use `juniper.device` collection (not deprecated `junipernetworks.junos`).
- **Module:** `juniper.device.junos_interfaces` with `config:` as a list (e.g. `config: [ { name: "{{ item }}", enabled: false } ]`), `state: merged`.
- **Shutdown:** `enabled: false`. **Enable:** `enabled: true`.
- **Do not** put `{{ item }}` in the task name; use `loop_control.label: "{{ item }}"`.
- **Vars:** `interfaces_to_shutdown` from `nettopo_interfaces | default([]) | map(attribute='name') | list`.
- **When:** `inventory_hostname in groups.get('vendor_juniper_junos', [])` and length check.
- **Status:** Confirmed operational (job_42). Use when intent is interface shutdown/enable and devices are Juniper Junos.
