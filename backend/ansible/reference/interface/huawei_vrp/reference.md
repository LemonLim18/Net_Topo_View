# Interface — Huawei VRP / CloudEngine (shutdown / enable)

- **Connection:** `ansible.network.network_cli` (SSH). Do **not** use `community.network.network_config` — that module does not exist.
- **Module:** `community.network.ce_config` with `lines` and `parents`. **Shutdown:** `lines: [ shutdown ]`, `parents: "interface {{ item }}"`. **Enable:** `lines: [ undo shutdown ]`.
- **Do not** put `{{ item }}` in the task name; use `loop_control.label: "{{ item }}"`.
- **Vars:** `interfaces_to_shutdown` from `nettopo_interfaces | default([]) | map(attribute='name') | list`.
- **When:** `inventory_hostname in groups.get('vendor_huawei_vrp', [])` and length check on `interfaces_to_shutdown`.
- **Status:** Confirmed operational (job_42). Use when intent is interface shutdown/enable and devices are Huawei VRP/CE.
