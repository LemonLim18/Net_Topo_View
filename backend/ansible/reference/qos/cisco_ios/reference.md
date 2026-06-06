# QoS - Cisco IOS (official reference)

- Connection: network_cli.
- Collection: cisco.ios.
- Preferred module pattern: cisco.ios.ios_config for class-map, policy-map, and interface service-policy binding.
- Intent-driven fields:
  - policy_name
  - qos_type
  - classification_rules
  - marking_dscp
  - traffic_type
  - police_rate
  - interface_apply
  - direction
- Guidance:
  - Use policy-map/class-map blocks for classification and actions.
  - Apply service-policy at interface scope with explicit direction (input/output).
  - For DSCP marking and policing-only prompts, keep tasks minimal and avoid unrelated class definitions.
- Status: baseline template prepared for multi-vendor generation and iterative hardening.
