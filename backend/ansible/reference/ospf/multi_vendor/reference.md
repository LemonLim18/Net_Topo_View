# OSPF — Multi-vendor

- **Purpose:** one playbook structure that can render for Cisco IOS, Huawei VRP, and Juniper Junos in the same run.
- **Required params:** `process_id`, `target_hosts`, `networks`.
- **`networks` shape:** list of objects:
  - `network` (IPv4 network address)
  - `wildcard` (wildcard mask, e.g. `0.0.0.255`)
  - `area` (integer)
  - `prefix_length` (optional, used by Junos set-style line rendering)
- **Optional params:** `router_id`, `passive_interfaces`, `redistribution`.
- **Task routing:** each task has vendor `when:` guards based on inventory groups.
