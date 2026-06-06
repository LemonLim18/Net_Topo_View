# QoS - Multi-vendor single-play template (official)

Use this template when target devices span Cisco IOS, Huawei VRP, and Juniper Junos.

- Hosts: access_switches or resolved target_hosts.
- Pattern: single playbook, vendor-guarded task blocks with when group checks.
- Input variables (from intent):
  - policy_name
  - qos_type
  - classification_rules
  - interface_apply
  - direction
  - marking_dscp
  - traffic_type
  - police_rate
  - conform_action
  - exceed_action
- Behavior:
  - Create/update policy objects when policy/classification/mark/police fields are present.
  - Attach QoS policy to interfaces only when interface_apply is provided.
  - User-provided interface and direction selections are authoritative.
- Module guidance:
  - Cisco IOS: cisco.ios.ios_config.
  - Huawei VRP: community.network.ce_config.
  - Juniper Junos: juniper.device.junos_config.
