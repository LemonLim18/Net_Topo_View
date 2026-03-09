# Network Change Execution SOP

This SOP defines the minimum execution standard for planned network changes.

## Preconditions

1. Approved change record exists.
2. Affected devices and rollback targets are identified.
3. Validation commands are defined before implementation starts.
4. The operator has console or out-of-band recovery for critical nodes.

## Execution sequence

1. Capture pre-change evidence.
   Record topology health, interface state, and routing or switching state relevant to the change.
2. Apply the smallest reversible unit.
   Prefer staged configuration over large atomic edits when the platform supports safe checkpoints.
3. Run immediate validation.
   Confirm adjacency, forwarding, and service health against the plan.
4. Decide go or rollback.
   If validation fails or evidence is ambiguous, execute rollback before expanding scope.
5. Capture post-change evidence.
   Store both success and failure evidence so later incidents can correlate behavior.

## Rollback triggers

- Loss of management reachability beyond the defined grace period
- Unexpected protocol adjacency churn
- Packet loss or latency beyond the approved threshold
- Validation mismatch against the approved expected state