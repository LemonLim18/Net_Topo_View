# Junos BGP Triage Reference

This internal reference is optimized for fast incident handling when a Junos BGP session is unstable or unexpectedly down.

## Primary checks

1. `show bgp summary`
   Use this first to confirm neighbor state, flap count, and prefix activity.
2. `show bgp neighbor <peer-address>`
   Use this to inspect negotiated capabilities, hold timers, and last error.
3. `show route receive-protocol bgp <peer-address>`
   Use this to confirm whether routes are arriving before policy evaluation.
4. `show route advertising-protocol bgp <peer-address>`
   Use this to confirm what the device is actually sending.

## Fast interpretation

- `Active` usually indicates transport reachability or TCP session failure.
- `Connect` often means the transport path exists but the session has not completed.
- Repeated state churn with low prefix counts points to policy rejection or timer mismatch.
- A healthy session with zero received routes is often a policy or RIB-group issue, not a transport problem.

## Decision path

1. Confirm the peer IP is reachable with the expected source address.
2. Compare local and remote AS numbers against the planned inventory.
3. Check import and export policies for explicit rejects.
4. Confirm TTL and multi-hop expectations for loopback-based peers.
5. Review recent commits for neighbor, group, or policy changes.

## Escalate when

- The peer repeatedly resets within one hold-time interval after configuration rollback.
- Multiple peers in the same routing instance fail simultaneously.
- The session is up but route counts differ materially from the last known-good state.