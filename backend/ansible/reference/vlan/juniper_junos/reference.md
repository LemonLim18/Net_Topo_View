# VLAN — Juniper Junos (unverified)

- **Connection:** `netconf` (port 830). Requires ncclient on runner; device must have NETCONF enabled.
- **Collection:** `juniper.device` (replaces deprecated `junipernetworks.junos`).
- **Module:** `juniper.device.junos_config` with explicit CLI `set` lines for:
  - `set vlans <name> vlan-id <id>`
  - `set interfaces <if> unit 0 family ethernet-switching port-mode access|trunk`
  - `set interfaces <if> unit 0 family ethernet-switching vlan members ...`
- **Inventory groups:** `access_switches:&vendor_juniper_junos`.
- **Status:** Unverified. When you confirm this playbook works, update this file and mark status as confirmed so the LLM can rely on it.
