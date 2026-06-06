# VLAN — Huawei VRP / CloudEngine (confirmed operational, CLI)

- **Connection:** `ansible.netcommon.network_cli` (SSH port 22). Do **not** use NETCONF for VLAN in this project.
- **Network OS:** `community.network.ce`
- **Module:** `community.network.ce_config` with `lines` and `parents` (e.g. `parents: interface GigabitEthernet1/0/1`). Do **not** use `community.network.network_config` — that module does not exist.
- **VLAN creation:** `vlan batch {{ vlan_id }}`, then `vlan {{ vlan_id }}`, `name {{ vlan_name }}`.
- **Interface access:** under `interface GigabitEthernet1/0/X`, use `port link-type access`, `port default vlan {{ vlan_id }}`.
- **Inventory groups:** `access_switches:&vendor_huawei_vrp`.
- **Status:** Confirmed operational. Use this pattern when devices are Huawei VRP/CE and intent is VLAN on access switches.
