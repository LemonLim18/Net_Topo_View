# Security Advisory Assessment Playbook

This playbook provides a repeatable way to assess operational impact when a network security advisory is published.

## Intake questions

1. Which vendors and platform families are affected?
2. Which software trains or feature combinations are in scope?
3. Does the exploit require adjacency, credentials, management-plane access, or malformed traffic?
4. Are there compensating controls already present?

## Assessment sequence

1. Match the advisory scope against inventory vendor, model, and software version data.
2. Identify whether the affected feature is enabled in production.
3. Separate exposure from exploitability.
4. Decide mitigation priority based on blast radius and ease of exploitation.
5. Track patch, workaround, or control decisions in the change system.

## Output expectations

- impacted devices
- affected feature tags
- mitigation path
- patch urgency
- verification owner

## Guardrails

- Do not recommend a patch-only answer without checking feature enablement and maintenance constraints.
- Do not assume all devices from the same vendor are affected; verify platform family and feature use.