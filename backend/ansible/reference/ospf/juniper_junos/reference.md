# OSPF — Juniper Junos

- **Connection:** `netconf`.
- **Collection/module:** `juniper.device` / `juniper.device.junos_config`.
- **Required params:** `process_id`, `target_hosts`, `networks`.
- **`networks` shape:** list of `{ network, area, prefix_length }` (or `{network,wildcard,area}` where prefix can be derived).
- **CLI pattern (set-style):**
  - `set protocols ospf router-id <router_id>` (optional)
  - `set protocols ospf area <area> network <network>/<prefix_length>`
