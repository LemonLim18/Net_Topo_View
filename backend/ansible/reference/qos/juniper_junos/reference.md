# QoS - Juniper Junos (official reference)

- Connection: netconf.
- Collection: juniper.device.
- Preferred module pattern: juniper.device.junos_config for class-of-service classifier/forwarding-class/policer and interface scheduler map attachment.
- Intent-driven fields:
  - policy_name
  - classification_rules
  - marking_dscp
  - traffic_type
  - police_rate
  - interface_apply
  - direction
- Guidance:
  - Keep class-of-service hierarchy deterministic and tied to policy_name.
  - For DSCP marking prompts, create concise classifier/forwarding updates.
  - For policing prompts, generate policer and apply where requested.
- Status: baseline template prepared for multi-vendor generation and iterative hardening.
