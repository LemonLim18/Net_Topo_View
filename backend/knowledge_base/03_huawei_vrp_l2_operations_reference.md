# Huawei VRP Layer 2 Operations Reference

This document is an internal operations reference for common Layer 2 checks on VRP-based switch estates.

## VLAN verification

- `display vlan`
  Confirm the VLAN exists and is active.
- `display port vlan`
  Confirm tagged and untagged membership on the affected interface.

## Spanning tree verification

- `display stp brief`
  Confirm root role and interface state.
- `display stp interface <interface>`
  Use this when a single port is suspected to be blocked or inconsistent.

## LACP verification

- `display eth-trunk`
  Confirm member state and selection.
- `display lacp statistics eth-trunk <id>`
  Use this to identify negotiation mismatch or one-way member behavior.

## Fast interpretation

- If the VLAN is present but the endpoint is unreachable, verify access versus trunk intent before deeper protocol checks.
- If STP role changes during a maintenance window, validate whether the expected root bridge changed.
- If an Eth-Trunk is degraded, compare member speed, duplex, and optical health before changing protocol settings.

## Operator notes

- Capture pre-change and post-change `display stp brief` output when modifying uplinks.
- For intermittent endpoint reachability, correlate VRP interface counters with incident timestamps before clearing counters.