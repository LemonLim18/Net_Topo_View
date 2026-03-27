# QoS - Huawei VRP (official reference)

- Connection: network_cli.
- Collection: community.network.
- Preferred module pattern: community.network.ce_config with VRP traffic classifier / behavior / policy syntax.
- Intent-driven fields:
  - policy_name
  - classification_rules
  - marking_dscp
  - traffic_type
  - police_rate
  - interface_apply
  - direction
- Guidance:
  - Build classifier and behavior before binding traffic policy.
  - Attach policy on interface with inbound/outbound direction mapping.
  - Keep generated commands deterministic from intent parameters.
- Status: baseline template prepared for multi-vendor generation and iterative hardening.
