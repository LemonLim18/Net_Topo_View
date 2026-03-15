# VLAN — Juniper Junos (unverified)

- **Connection:** `netconf` (port 830). Requires ncclient on runner; device must have NETCONF enabled.
- **Collection:** `juniper.device` (replaces deprecated `junipernetworks.junos`).
- **Module:** `juniper.device.junos_vlans` (config with name, vlan_id; state: merged).
- **Inventory groups:** `access_switches:&vendor_juniper_junos`.
- **Status:** Unverified. When you confirm this playbook works, update this file and mark status as confirmed so the LLM can rely on it.
