# OSPF — Huawei VRP

- **Connection:** `network_cli`.
- **Collection/module:** `community.network` / `community.network.ce_config`.
- **Required params:** `process_id`, `target_hosts`, `networks`.
- **`networks` shape:** list of `{ network, wildcard, area }`.
- **CLI pattern:**
  - `ospf <process_id>`
  - `router-id <router_id>` (optional)
  - `area <area>`
  - `network <network> <wildcard>`
  - `return`
