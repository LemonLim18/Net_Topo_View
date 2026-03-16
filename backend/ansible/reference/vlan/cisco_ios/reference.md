# VLAN — Cisco IOS (confirmed operational)

- **Connection:** `network_cli` (SSH port 22). No NETCONF.
- **Collections:** `cisco.ios`
- **Modules:** `cisco.ios.ios_vlans` (config list, state: merged), `cisco.ios.ios_l2_interfaces` (name, mode: access|trunk, access.vlan or trunk.trunk_vlans).
- **cisco.ios.ios_vlans:** Use `config:` with a **list** of dicts (e.g. `config: [ { vlan_id: '200', name: 'TEST', state: active } ]`). Do NOT use `vlans:` dict form; with `state: merged` the module requires `config` and will fail with "all of the following are missing: config".
- **Do NOT use:** deprecated `cisco.ios.vlan` or `cisco.ios.vlan_interfaces`.
- **Inventory groups:** Use `access_switches:&vendor_cisco_ios` for access switches; ensure `vendor_cisco_ios` has vars above.
- **Scenario-based:** The playbook is filled from intent: any VLAN id/name, and any interface list + mode (access or trunk) from the user prompt. Parameters: `vlans` (id, name), `interfaces` (list of interface names), `mode` (access|trunk), optional `trunk_vlans`.
- **Status:** Confirmed operational. Use this pattern when intent is "configure VLAN on access switches" and devices are Cisco IOS.
