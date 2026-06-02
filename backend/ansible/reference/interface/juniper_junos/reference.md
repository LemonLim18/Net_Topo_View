# Interface — Juniper Junos (shutdown / enable)

- **Connection:** `netconf` (port 830). Use `juniper.device` collection (not deprecated `junipernetworks.junos`).
- **Module:** `juniper.device.junos_config` with explicit CLI `set` / `delete` lines.
- Reason: older Junos releases can fail when resource modules translate interface intent into unsupported XML tags such as `interface-mode`.
- **Shutdown:** `enabled: false`. **Enable:** `enabled: true`.
- **Do not** put `{{ item }}` in the task name; use `loop_control.label: "{{ item }}"`.
- **Vars (preferred):** `interfaces_config` = `[ { name: "ge-0/0/0", enabled: false }, ... ]` for explicit enable/disable per interface.
- **Vars (fallback):** `interfaces_to_shutdown` from `nettopo_interfaces | default([]) | map(attribute='name') | list` for shutdown-only.
- **When:** `inventory_hostname in groups.get('vendor_juniper_junos', [])` and length check on whichever var is used.
- **Status:** Confirmed operational (job_42). Use when intent is interface shutdown/enable and devices are Juniper Junos.
