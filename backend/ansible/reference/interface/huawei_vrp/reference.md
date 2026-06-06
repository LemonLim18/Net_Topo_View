# Interface — Huawei VRP / CloudEngine (multi-scenario)

- **Connection:** `ansible.network.network_cli` (SSH). **Module:** `community.network.ce_config` with `lines` and `parents`.
- **Reference template (per interface):**
  - `interface {{ item.name }}`
  - If `item.description` defined → `description {{ item.description }}`
  - If `item.enabled == false` → `shutdown`; else → `undo shutdown`
  - If `item.mode == 'access'` and `item.access` → `port link-type access`, `port default vlan {{ item.access.vlan }}`
  - If `item.mode == 'trunk'` and `item.trunk` → `port link-type trunk`, `port trunk allow-pass vlan {{ item.trunk.allowed_vlans | join(' ') }}`
  - If `item.ip` defined → `ip address {{ item.ip.address }} {{ item.ip.mask }}`
- **Vars:** `interfaces_config` = list of `{ name, description?, enabled?, mode?, access?: { vlan }, trunk?: { allowed_vlans }, ip?: { address, mask } }`. Or `interfaces_to_shutdown` = list of names (shutdown-only).
- **When:** `inventory_hostname in groups.get('vendor_huawei_vrp', [])` and length check.
