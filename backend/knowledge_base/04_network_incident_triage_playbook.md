# Network Incident Triage Playbook

This playbook standardizes the first fifteen minutes of multi-vendor network incident handling.

## Objective

Reduce guesswork by separating scope discovery, source-of-truth verification, and device-level confirmation.

## Triage sequence

1. Confirm blast radius.
   Determine whether the issue affects a single device, a path, a site, or a service class.
2. Verify intended state.
   Compare the reported impact against planned inventory, approved changes, and recent incidents.
3. Verify live state.
   Confirm device reachability, interface health, and topology edges from current crawl data.
4. Check drift and configuration history.
   Identify whether the affected nodes changed recently or diverged from expected templates.
5. Decide action.
   Choose one of: rollback, isolate, continue investigation, or escalate.

## Minimum evidence before escalation

- Affected device list
- Last known-good timestamp
- Recent change window correlation
- One source-of-truth check and one live-state check
- Current severity assessment

## Guardrails

- Do not treat static documentation as proof of live state.
- Do not change routing policy before verifying whether the incident is localized to transport, adjacency, or inventory mismatch.
- When evidence conflicts, prefer live network state for current behavior and use static knowledge only to explain likely mechanisms.