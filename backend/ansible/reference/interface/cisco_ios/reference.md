# Interface — Cisco IOS (shutdown / enable)

- **Connection:** `network_cli` (SSH). No NETCONF.
- **Collection:** `cisco.ios`
- **Module:** `cisco.ios.ios_interfaces` with `config:` as a **list** (e.g. one item per loop: `config: [ { name: "{{ item }}", enabled: false } ]`), `state: merged`.
- **Shutdown:** `enabled: false`. **Enable (no shutdown):** `enabled: true`.
- **Do not** put `{{ item }}` in the task name; use `loop_control.label: "{{ item }}"`.
- **Vars:** `interfaces_to_shutdown: "{{ nettopo_interfaces | default([]) | map(attribute='name') | list }}"` from pipeline host vars.
- **When:** `inventory_hostname in groups.get('vendor_cisco_ios', [])` and `(interfaces_to_shutdown | default([])) | length > 0`.
- **Status:** Confirmed operational (job_42). Use when intent is interface shutdown/enable and devices are Cisco IOS.
