# VLAN — Cisco IOS (confirmed operational)

- **Connection:** `network_cli` (SSH port 22). No NETCONF.
- **Collections:** `cisco.ios`
- **Modules:** `cisco.ios.ios_vlans` (config list, state: merged), `cisco.ios.ios_l2_interfaces` (name, mode: access, access.vlan).
- **Do NOT use:** deprecated `cisco.ios.vlan` or `cisco.ios.vlan_interfaces`.
- **Inventory groups:** Use `access_switches:&vendor_cisco_ios` for access switches; ensure `vendor_cisco_ios` has vars above.
- **Status:** Confirmed operational. Use this pattern when intent is "configure VLAN on access switches" and devices are Cisco IOS.
