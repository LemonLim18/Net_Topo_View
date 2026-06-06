# Interface shutdown playbook — verification

## What’s wrong in the generated playbook you showed

| Issue | Generated playbook | Correct approach (from your reference) |
|-------|--------------------|----------------------------------------|
| **Backup task** | `ios_config` with `lines: '{{ item }}'`, `parents: 'show running-config'`, `backup: yes` in a loop | `parents` is for config context (e.g. `interface Gi0/1`), not show commands. Full backup is a single task: `ios_config: backup: yes` (no loop). |
| **Shutdown task** | `include_tasks: tasks/main.yml` with a loop | Reference uses a single play with `cisco.ios.ios_interfaces` and `config: - name: "{{ item }}", enabled: false` in a loop over `interfaces_to_shutdown`. |
| **Verification** | `ios_l2_interfaces` with `lines`, `parents`, `state: down` | `ios_l2_interfaces` takes `config` (list of interface dicts), not `lines`/`parents`. “Verify down” is done with a command/facts or `ios_interfaces` read, not by applying config again. |
| **Rescue / Check** | Same wrong `ios_config` / `ios_l2_interfaces` usage | Same as above; these blocks don’t match Ansible module parameters. |
| **Hosts** | `hosts: access_switches` | Reference uses `hosts: "{{ target_hosts \| default('all') }}"` or explicit host list so “access switch 1 and 2” can be targeted. |
| **Vars** | No `interfaces_to_shutdown` defined in play | Reference sets `interfaces_to_shutdown` in play `vars` (e.g. from `nettopo_interfaces` or an explicit list). |

## Reference pattern (from your templates)

- **Cisco IOS:** `cisco.ios.ios_interfaces` with `config: [ { name: "{{ item }}", enabled: false } ]`, `state: merged`, loop over `interfaces_to_shutdown`.
- **Huawei VRP:** `community.network.ce_config` with `lines: [ shutdown ]`, `parents: "interface {{ item }}"`, same loop.
- **Juniper Junos:** `juniper.device.junos_interfaces` with `config: [ { name: "{{ item }}", enabled: false } ]`, same loop.

No backup/verify/rescue blocks in the reference; those would need to be redesigned with correct modules and parameters if you want them.

---

## Corrected example (aligned with your reference)

For **“Turn off the interfaces of access switch 1 and 2”** (Cisco IOS), a playbook aligned with `reference/interface/cisco_ios/playbook.yml`:

```yaml
---
# Shutdown interfaces on access switch 1 and 2 (Cisco IOS — matches reference template)
- name: Shutdown interfaces (Cisco IOS)
  hosts: "{{ target_hosts | default('all') }}"   # set target_hosts to [Acc_Cisco_1, Acc_Cisco_2] or your hostnames
  gather_facts: no
  ignore_unreachable: true
  vars:
    # Either from inventory (nettopo_interfaces) or explicit list, e.g.:
    interfaces_to_shutdown: "{{ nettopo_interfaces | default([]) | map(attribute='name') | list }}"
  tasks:
    - name: Shutdown interface (Cisco IOS)
      cisco.ios.ios_interfaces:
        config:
          - name: "{{ item }}"
            enabled: false
        state: merged
      loop: "{{ interfaces_to_shutdown | default([]) }}"
      loop_control:
        label: "{{ item }}"
      when:
        - (interfaces_to_shutdown | default([])) | length > 0
```

- **Hosts:** Pass `target_hosts` as your access switch 1 and 2 (e.g. `Acc_Cisco_1`, `Acc_Cisco_2`) in extra vars or inventory group.
- **interfaces_to_shutdown:** From planning context (`nettopo_interfaces`) or set explicitly, e.g. `['GigabitEthernet0/1', 'GigabitEthernet0/2']`.
- No backup/verify/rescue in this snippet; add them separately with correct modules (e.g. one `ios_config: backup: yes` for backup) if needed.
