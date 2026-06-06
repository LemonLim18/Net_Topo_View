# Cisco IOS XE OSPF Design Baseline

This internal guide captures a conservative OSPF deployment baseline for IOS XE networks that prioritize operational stability over feature density.

## Scope

- Campus and branch routing domains with a small number of areas
- Catalyst 9300 and ISR 4000 families
- OSPFv2 unicast routing for IPv4 reachability

## Baseline rules

1. Keep area 0 contiguous. If a remote site cannot connect directly to area 0, use a virtual link only as a temporary recovery measure.
2. Mark all user-facing access interfaces as passive by default. Only transit and routed uplinks should form adjacencies.
3. Pin router IDs to stable loopback addresses so adjacencies and LSDB entries do not drift after interface changes.
4. Use point-to-point network type on routed uplinks when the link is truly point-to-point. Avoid broadcast unless multi-access behavior is required.
5. Summarize only at clear area boundaries and document the summary owner.

## Validation sequence

1. Verify neighbors with `show ip ospf neighbor` and confirm each adjacency reaches FULL state.
2. Verify interface intent with `show ip ospf interface brief` and confirm passive interfaces stay passive.
3. Inspect LSDB scale with `show ip ospf database` before and after the change.
4. Confirm routing outcomes with `show ip route ospf` from at least one branch and one core node.

## Common failure patterns

- MTU mismatch causes neighbors to stall in EXSTART or EXCHANGE.
- Missing passive-interface controls create unintended adjacencies on access segments.
- Duplicate router IDs lead to intermittent adjacency resets and route churn.
- Area mismatch keeps neighbors in INIT or DOWN.

## Operator notes

- If a planned change touches area design, capture pre-change LSDB size and post-change convergence time.
- Treat abrupt increases in external LSAs as a possible redistribution regression.