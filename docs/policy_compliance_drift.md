Yes — this is the right direction. The feature should be a **policy-driven compliance scanner** backed by **NetBox as source of truth**, with a UI that lets an admin define rules by filling forms instead of writing raw policy text. That fits the report’s design: NetBox is the authoritative inventory, the validation layer checks governance rules, drift detection runs periodically, and violations are surfaced with risk/impact context rather than trying to auto-fix everything.

## What this module should do

The module has one job: **compare live device configuration against a policy-backed intended baseline, then alert when a device drifts**. It should not replace topology diff. Topology diff remains its own module. This one is strictly **configuration compliance + drift detection + policy violation reporting**. The report already separates planned-vs-live topology comparison from playbook validation, policy checks, dry-run, execution, rollback, and alerting.

## Core design

Use these three layers:

### 1 Inventory layer

NetBox stores:

* devices
* roles
* interfaces
* VLANs
* IPs
* OSPF/static route intent data if you keep that there
* device categories and group membership

The report explicitly says NetBox is the system of record for intended network design and inventory.

### 2) Policy layer

Policy defines:

* what is allowed
* what is forbidden
* what is required
* what counts as severity
* what targets the policy applies to

The report’s validation module already includes vendor-specific checks, reserved resource checks, and governance policy enforcement.

### 3) Compliance layer

Compliance jobs:

* collect live config
* normalize it
* compare against baseline
* evaluate policy
* compute drift severity
* store result
* alert if needed

The report’s drift detection use case says the system periodically checks topology and configuration state, evaluates drift thresholds, and generates incidents/notifications.

---

# A. Policy UI: what the admin should be able to create

This UI should feel like a form builder, not raw code. The admin should be able to create a policy by choosing:

* target devices or device roles
* target vendor/platform
* target config category
* match method
* rule operator
* expected value
* enforcement level
* severity
* explanation

## Policy categories

Support at least:

* interface
* vlan
* ospf
* static route
* acl
* snmp
* logging
* ntp
* and other common config groups you want later

These categories are natural extensions of the validator/policy rules described in the report, which already mentions VLAN overlap, reserved resources, and IP conflict checks.

## Rule types the UI should support

Each rule should be built from a small set of primitives. Do not force admins to write raw regex unless they want advanced mode.

### Example primitives

* `must_exist`
* `must_not_exist`
* `must_equal`
* `must_contain`
* `must_match_regex`
* `must_be_in_range`
* `must_be_in_set`
* `must_be_absent`
* `must_be_on_device_role`
* `must_not_be_on_device_role`

### Example UI fields

For each rule:

* Policy name
* Description
* Scope
* Category
* CLI object path
* Match method
* Expected value
* Severity
* Remediation note
* Enabled/disabled toggle

## Example: interface policy

Admin selects:

* category: interface
* scope: access switches
* rule: `must_exist`
* object: `switchport mode access`
* severity: medium

## Example: VLAN policy

Admin selects:

* category: vlan
* scope: core/distribution
* rule: `must_not_exist`
* object: VLAN 1002–1005
* severity: high

## Example: OSPF policy

Admin selects:

* category: ospf
* rule: `must_exist`
* object: `router-id`
* expected: set
* severity: high

## Example: ACL policy

Admin selects:

* category: acl
* rule: `must_not_exist`
* object: `deny ip any any`
* severity: high

That matches the report’s policy-validation and governance flow, which is intended to catch forbidden commands, reserved resources, and restricted changes before execution or during drift review.

---

# B. Policy storage model

Use a real policy object, not just free text.

## Recommended structure

```json
{
  "policy_id": "campus_access_ssh_001",
  "name": "Access switches must allow SSH and block Telnet",
  "enabled": true,
  "scope": {
    "vendors": ["cisco_ios", "huawei_vrp"],
    "device_roles": ["access_switch"]
  },
  "category": "logging",
  "rules": [
    {
      "action": "must_exist",
      "target": "ip ssh version 2",
      "severity": "high"
    },
    {
      "action": "must_not_exist",
      "target": "transport input telnet",
      "severity": "high"
    }
  ],
  "normalization": {
    "ignore_patterns": [
      "^! Last configuration was updated.*",
      "^! Last configuration was saved.*"
    ]
  },
  "thresholds": {
    "auto_alert": true,
    "alert_severity_min": "medium"
  }
}
```

## Minimum stored fields

* policy ID
* name
* description
* created_by
* scope
* category
* enabled
* rules JSON
* severity mapping
* normalization rules
* audit timestamps
* version number
* last modified by

---

# C. Baseline model: how the system knows “good”

The baseline is **not the policy**. It is the **last approved known-good configuration snapshot** for a device. The report’s flow supports validation, dry-run, approval, execution, post-check verification, and pre-change snapshots for rollback. That means the baseline should be captured only after a change is approved and verified, while pre-change snapshots remain for rollback.

## Baseline lifecycle

1. capture pre-change snapshot
2. run validation and policy checks
3. run dry-run
4. approve
5. execute
6. verify
7. save resulting config as new baseline snapshot

## Baseline metadata

* device ID
* snapshot type = `baseline`
* source intent/playbook ID
* policy version used
* timestamp
* raw config
* normalized config
* checksum
* approval record
* verification result

## Fallback order

Use this priority:

1. latest approved post-execution snapshot
2. manually curated golden snapshot
3. imported seed snapshot from NetBox/admin

If none exists, return **no baseline** and do not compute compliance. That is the same discipline the report uses when a required topology snapshot is missing.

---

# D. Compliance engine: how to run periodic checks

This should be a scheduler-driven service.

## Scan workflow

For each device:

1. resolve device from NetBox
2. load policy set for that device/role/vendor
3. load baseline snapshot
4. collect actual running config
5. normalize both
6. compare baseline vs actual
7. evaluate policy violations
8. compute drift severity and compliance score
9. write result to DB
10. if severity threshold exceeded, raise alert

The report supports periodic drift detection and alert generation when thresholds are exceeded.

## Scheduled cadence

You should support:

* every 5 minutes for critical lab devices
* every 15–60 minutes for general devices
* manual “scan now” button in UI

The report already frames the platform as monitoring-driven and event-driven, so scheduled compliance scans are a natural extension.

---

# E. Normalization engine: mandatory, not optional

Raw configs will contain noise. If you do not normalize, the diff will be garbage.

## Normalize by:

* removing timestamps
* stripping banners and version noise where appropriate
* collapsing blank lines
* canonicalizing spacing
* removing ephemeral counters
* standardizing section order where safe
* vendor-specific formatting differences

This is necessary because the report’s validator and diff flow assumes structured, reviewable comparisons rather than raw noisy text.

---

# F. Drift classification: what the module should report

Each device result should tell the truth in plain terms:

* `compliant`
* `drifted`
* `violated`
* `no_baseline`
* `no_actual`
* `unreachable`
* `partial`

## What to show for drifted devices

* drift score
* severity
* missing lines
* extra lines
* changed lines
* violated policy names
* affected category

## Example output

* Missing baseline: `line vty 0 15`
* Extra live command: `no aaa new-model`
* Policy violated: `Require AAA on access switches`
* Severity: `high`

That kind of “what changed and why it matters” aligns with the report’s structured validation reports and diff presentation.

---

# G. Compliance score: how to compute it

Do not fake the number.

## A simple useful formula

Start at 100 and subtract:

* missing required commands
* forbidden commands present
* mismatched values
* major policy violations
* repeated violations by criticality weight

## Example weights

* high severity violation: -30
* medium severity drift: -15
* low severity cosmetic drift: -5

## Output

* compliance score
* drift percentage
* risk level
* reason text

The report already uses risk scoring and policy-based blocking, so the score should feed alerting and prioritization, not just a dashboard number.

---

# H. UI design: what the admin should see

## Policy builder page

Should include:

* policy list
* create policy
* edit policy
* clone policy
* disable policy
* delete policy
* version history
* test policy against a device snapshot

## Policy editor form

Should include:

* policy name
* scope selector
* vendor selector
* device role selector
* category selector
* rule rows
* severity per rule
* note field
* save/validate/test buttons

## Compliance dashboard

Should include:

* total devices checked
* compliant devices
* drifted devices
* violations
* no baseline devices
* unreachable devices
* current compliance rate

## Device drill-down

Should show:

* baseline snapshot version
* actual snapshot version
* diff
* violated policies
* timestamps
* action history

The report describes structured UI views for validator reports, diffs, and audit trails, so this UX is aligned with the broader design.

---

# I. API endpoints you need

## Policy management

* `GET /policies`
* `POST /policies`
* `GET /policies/{id}`
* `PUT /policies/{id}`
* `DELETE /policies/{id}`
* `POST /policies/{id}/clone`
* `POST /policies/{id}/test`

## Baselines and snapshots

* `GET /devices/{id}/baseline`
* `POST /devices/{id}/baseline/set`
* `GET /devices/{id}/snapshots`
* `POST /devices/{id}/snapshots/collect`

## Compliance

* `GET /compliance/results`
* `GET /compliance/results/{device_id}`
* `POST /compliance/scan`
* `POST /compliance/scan/{device_id}`
* `GET /compliance/summary`

## Alerts

* `GET /compliance/alerts`
* `POST /compliance/alerts/ack`

This structure fits the report’s requirement for auditability, structured reports, and linked validation artifacts.

---

# J. Database tables you need

## `policies`

* id
* name
* description
* scope JSON
* rules JSON
* category
* enabled
* version
* created_by
* updated_by
* timestamps

## `config_snapshots`

* id
* device_id
* snapshot_type
* source_type
* raw_config
* normalized_config
* checksum
* policy_version
* intent_id
* execution_id
* created_at

## `compliance_results`

* id
* device_id
* baseline_snapshot_id
* actual_snapshot_id
* status
* compliance_score
* severity
* missing_lines JSON
* extra_lines JSON
* changed_lines JSON
* violated_policies JSON
* checked_at

## `compliance_jobs`

* id
* run_mode
* started_at
* finished_at
* status
* summary
* error_text

## `policy_audit_log`

* id
* policy_id
* action
* old_value
* new_value
* changed_by
* timestamp

The report already uses persistent records for intents, playbooks, executions, approvals, audit logs, diffs, and snapshots, so adding these tables fits the architecture.

---

# K. Integration points with the rest of the system

## With NetBox

* pull authoritative device metadata
* use roles/groups for policy scope
* use planned inventory as target set
* use IP/VLAN references for validations

NetBox is the system of record in the report, so the policy module should read from it, not duplicate it.

## With playbook validation

* a policy can be tested against a candidate playbook before deployment
* a failed policy blocks approval or auto-apply

That mirrors UC19–UC23 in the report.

## With playbook execution

* after a successful execution, create the new baseline snapshot
* keep the pre-change snapshot for rollback
* link both to the execution record

That matches UC30–UC33.

## With alerting

* high-severity drift becomes an incident
* lower severity drift is logged
* optional notifications go to the GUI or alert channel

That matches the alert/remediation module’s periodic monitoring and incident record behavior.

---

# L. Operational rules the AI must follow

If you hand this to an AI to implement, it must obey these rules:

1. Never compute compliance without a baseline.
2. Never show diff without baseline + actual.
3. Never auto-create a baseline from live config unless the execution was approved and verified.
4. Never merge topology drift into config drift.
5. Never let policy replace the baseline.
6. Never let “Unknown” appear without a reason.
7. Always store snapshots and policy versions.
8. Always normalize before diffing.
9. Always use NetBox as the source of truth for scope.
10. Always preserve audit history.

These rules are fully consistent with the report’s emphasis on validation, audit trail, and source-of-truth-driven workflows.

---

# M. Build order

## Phase 1

Build the policy data model and CRUD UI.

## Phase 2

Build snapshot collection and baseline storage.

## Phase 3

Build normalization and diff engine.

## Phase 4

Build compliance scoring and drift classification.

## Phase 5

Build scheduled scans and alerts.

## Phase 6

Build device drill-down and policy violation views.

## Phase 7

Connect everything to playbook execution so approved changes can refresh baselines automatically.

This order matches the project’s layered structure in the report: validation first, then dry-run, then approval/execution, then rollback and monitoring.

---

# N. Final implementation prompt you can give your AI

```text
Refactor the Compliance & Drift module into a policy-driven snapshot comparison engine.

The system must:
1. Use NetBox as the source of truth for device scope, roles, and inventory.
2. Allow admins to create, edit, clone, disable, and delete policies using a form-based UI.
3. Support policy categories: interface, vlan, ospf, static route, acl, snmp, logging, ntp, and additional config groups.
4. Let each policy contain rules built from structured fields:
   - must_exist
   - must_not_exist
   - must_equal
   - must_contain
   - must_match_regex
   - must_be_in_range
   - must_be_in_set
5. Allow policy scope by vendor, device role, device group, or individual device.
6. Store policy version history and audit logs.
7. Periodically scan all devices for compliance.
8. For each device, collect the live running configuration.
9. Load the latest approved baseline snapshot for that device.
10. If no baseline exists, mark the device as no_baseline and do not compute compliance.
11. Normalize baseline and live configs before comparison.
12. Compare normalized configs and classify drift into missing lines, extra lines, and changed lines.
13. Evaluate policy violations separately from raw drift.
14. Compute a compliance score and severity.
15. Show the diff only when both baseline and actual snapshots exist.
16. Replace all unknown states with reasoned statuses such as no_baseline, no_actual, unreachable, compliant, drifted, or violated.
17. Use the baseline snapshot as the last approved, verified post-change configuration.
18. Keep pre-change snapshots for rollback only.
19. After successful execution and verification of an approved playbook, save the new post-change config as the new baseline snapshot.
20. Provide APIs for policy CRUD, compliance scan, device drift detail, baseline snapshot access, and manual rescans.
21. Build a dashboard that shows compliance rate, drift count, violations, unreachable devices, and missing baseline count.
22. Build a device detail page that shows baseline, actual config, diff, violated policies, severity, and scan history.
23. Keep topology drift separate and do not mix it into this module.
```

---

If you want, I can turn this into a **database schema + endpoint spec + UI wireframe checklist** next.
