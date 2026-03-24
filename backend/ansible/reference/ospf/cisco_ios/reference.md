# OSPF — Cisco IOS

- **Connection:** `network_cli` (SSH).
- **Collection/module:** `cisco.ios` / `cisco.ios.ios_config`.
- **Intent keys expected:**
  - `process_id` (required)
  - `networks` as list of `{ network, wildcard, area }` (required)
  - `router_id` (optional)
- **Generated CLI shape:**
  - `router ospf <process_id>`
  - `router-id <router_id>` (when provided)
  - `network <network> <wildcard> area <area>` (for each advertised network)
- **When:** `inventory_hostname in groups.get('vendor_cisco_ios', [])`.
