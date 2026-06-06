# NetTopoView Database Schema & Entity Overview

**Last Updated:** May 7, 2026  
**Database Type:** SQLite (netauto.db) with PostgreSQL support for production  
**ORM Pattern:** Plain dataclasses (M14) — not using SQLAlchemy ORM for flexibility

---

## 📊 Complete Entity Relationship Diagram (ERD)

### Core Inventory Entities
```
┌─────────────────────────────────────────────────────────────┐
│                      DEVICES (Core)                         │
├─────────────────────────────────────────────────────────────┤
│ PK: id                                                      │
│ hostname (UNIQUE)                                           │
│ management_ip, management_subnet_mask, management_interface│
│ vendor, model, os_version, serial_number                   │
│ status, role, source, last_polled                          │
│ ssh_user, ssh_password, ssh_port                           │
│ netbox_id (UNIQUE, FK to NetBox), created_at, updated_at   │
└──────┬──────────────────┬─────────────────┬────────────────┘
       │                  │                 │
       ├─ 1:M ──────┐     │ 1:M ─────┐     │ 1:M ─────┐
       │            ▼     │          ▼     │          ▼
    INTERFACES    LINKS (src)  CONFIG_SNAPSHOTS DRIFT_EVENTS
       │            │          │
       │            └─ 1:M ──┤ SECURITY_AUDITS
       │               (dst) │
       │                     │
       └─ References ────┐   │
                         ▼   │
                       Links ──┘
```

---

## 📋 CORE ENTITIES (Persistent)

### 1. **DEVICES** — Network Inventory
The foundational entity representing all network devices.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Unique device identifier |
| `hostname` | TEXT | NOT NULL, UNIQUE | Device name (FQDN recommended) |
| `management_ip` | TEXT | | IP address for SSH/SNMP access |
| `management_subnet_mask` | TEXT | | Netmask for management IP (e.g., /24) |
| `management_interface` | TEXT | | Interface on device (e.g., eth0) |
| `vendor` | TEXT | | Vendor: `cisco_ios`, `huawei_vrp`, `juniper_junos`, etc. |
| `model` | TEXT | | Device model (e.g., ASR 1001) |
| `os_version` | TEXT | | Operating system version |
| `serial_number` | TEXT | | Device serial number |
| `status` | TEXT | DEFAULT 'unknown' | `planned`, `registered`, `provisioning`, `active`, `down`, `unknown` |
| `role` | TEXT | DEFAULT 'network-device' | `core`, `distribution`, `access`, `network-device` |
| `source` | TEXT | DEFAULT 'manual' | Source: `manual`, `crawler`, `netbox` |
| `last_polled` | TEXT | ISO-8601 | Timestamp of last polling cycle |
| `ssh_user` | TEXT | | SSH username for device access |
| `ssh_password` | TEXT | | SSH password (plaintext—consider encryption in prod) |
| `ssh_port` | INTEGER | DEFAULT 22 | SSH port |
| `netbox_id` | INTEGER | UNIQUE, FK | External NetBox device ID |
| `created_at` | TEXT | DEFAULT now() | Record creation timestamp |
| `updated_at` | TEXT | DEFAULT now() | Record last update timestamp |

#### Indexes:
- `idx_devices_netbox_id` — Unique index on `netbox_id` (WHERE netbox_id IS NOT NULL)

#### Relationships:
- **1:M → INTERFACES** — A device has many interfaces
- **1:M → LINKS** (src) — A device can be source of many links
- **1:M → LINKS** (dst) — A device can be destination of many links
- **1:M → CONFIG_SNAPSHOTS** — A device has many configuration snapshots
- **1:M → DRIFT_EVENTS** — A device has many drift detections
- **1:M → SECURITY_AUDITS** — A device has many security audits
- **1:M → AUDIT_FINDINGS** (via SECURITY_AUDITS)
- **1:M → SECURITY_EVENTS** — A device may trigger security events
- **1:M → DEVICE_CREDENTIALS** — A device may have multiple credential sets
- **1:M → ZTP_DHCP_MAPPINGS** — One device per DHCP mapping
- **1:M → COMMON_SERVICE_PROFILES** (via scope)
- **1:M → COMPLIANCE_RESULTS** — A device has many compliance checks

---

### 2. **INTERFACES** — Device Interfaces
Individual ports/interfaces on devices.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), NOT NULL, CASCADE | Parent device |
| `name` | TEXT | NOT NULL | Interface name (e.g., `GigabitEthernet0/1`, `Gi0/0`) |
| `ip_address` | TEXT | | IP address assigned to interface |
| `subnet_mask` | TEXT | | Netmask (e.g., `255.255.255.0` or `/24` format) |
| `mac_address` | TEXT | | MAC address of interface |
| `speed` | TEXT | | Speed (e.g., `1000Mbps`, `10Gbps`) |
| `status` | TEXT | DEFAULT 'unknown' | `up`, `down`, `admin-down`, `unknown` |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Constraints:
- `UNIQUE(device_id, name)` — One interface name per device

#### Indexes:
- `idx_interfaces_device` — Fast lookup by `device_id`

#### Relationships:
- **M:1 ← DEVICES** — Many interfaces belong to one device
- **1:M → LINKS** (src) — Interface can be source of link
- **1:M → LINKS** (dst) — Interface can be destination of link

---

### 3. **LINKS** — Topology Edges
Adjacencies between devices/interfaces (discovered by LLDP/CDP or manually entered).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `src_device_id` | INTEGER | FK devices(id), NOT NULL, CASCADE | Source device |
| `src_interface_id` | INTEGER | FK interfaces(id), SET NULL | Source interface (optional) |
| `dst_device_id` | INTEGER | FK devices(id), NOT NULL, CASCADE | Destination device |
| `dst_interface_id` | INTEGER | FK interfaces(id), SET NULL | Destination interface (optional) |
| `discovery_protocol` | TEXT | | Protocol: `lldp`, `cdp`, `manual` |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Constraints:
- `UNIQUE(src_device_id, src_interface_id, dst_device_id, dst_interface_id)` — No duplicate adjacencies

#### Indexes:
- `idx_links_src` — Fast lookup by `src_device_id`
- `idx_links_dst` — Fast lookup by `dst_device_id`

#### Relationships:
- **M:1 ← DEVICES** (src) — Link originates from device
- **M:1 ← DEVICES** (dst) — Link terminates at device
- **M:1 ← INTERFACES** (src) — May reference source interface
- **M:1 ← INTERFACES** (dst) — May reference destination interface

---

### 4. **CONFIG_SNAPSHOTS** — Configuration Backup & Baseline
Point-in-time backups of device configurations with compliance metadata.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), NOT NULL, CASCADE | Device whose config this is |
| `config_text` | TEXT | NOT NULL | Full configuration (raw text) |
| `config_hash` | TEXT | | SHA-256 of `config_text` (deduplication) |
| `captured_at` | TEXT | DEFAULT now() | When snapshot was taken |
| **Compliance Metadata (M26):** | | | |
| `snapshot_type` | TEXT | DEFAULT 'snapshot' | `snapshot`, `baseline`, `rollback`, `golden`, `actual` |
| `source_type` | TEXT | | `deploy_pipeline`, `manual`, `import`, `scan` |
| `normalized_config` | TEXT | | Canonicalized form for diff/compliance (ignoring whitespace/comments) |
| `checksum` | TEXT | | SHA-256 of `normalized_config` (compliance diffing) |
| `policy_version` | INTEGER | | Policy version used when captured |
| `intent_id` | TEXT | FK intents(id) | Link to intent that created this snapshot |
| `execution_id` | INTEGER | FK jobs(id) | Link to job that created this snapshot |
| `approved_by` | TEXT | | User who approved the configuration |
| `verified_by` | TEXT | | User who verified the configuration |
| `verified_at` | TEXT | ISO-8601 | When verification was completed |

#### Indexes:
- `idx_snapshots_device` — Lookup by `device_id`
- `idx_snapshots_device_type` — Fast queries: `device_id`, `snapshot_type`, `captured_at DESC`

#### Relationships:
- **M:1 ← DEVICES** — Many snapshots per device
- **0..1 → INTENTS** — Optional link to originating intent
- **0..1 → JOBS** — Optional link to originating job
- **1:M ← COMPLIANCE_RESULTS** (baseline_snapshot_id)
- **1:M ← COMPLIANCE_RESULTS** (actual_snapshot_id)

---

### 5. **AUDIT_LOG** — Immutable Action Log
Records of user/system actions for auditing.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `user_id` | TEXT | | User identifier (future M15 auth integration) |
| `action` | TEXT | NOT NULL | Action name (e.g., `config_push`, `crawl_start`, `device_created`) |
| `target` | TEXT | | Target entity (e.g., device hostname, intent ID) |
| `details` | TEXT | | JSON blob with context |
| `created_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_audit_created` — Lookup by `created_at`

---

## 🔄 CHANGE MANAGEMENT ENTITIES (M4 Deployment Pipeline)

### 6. **CHANGE_REQUESTS** — Logical Change Grouping
Groups multiple device changes into a single logical request (e.g., "Apply OSPF config to core routers").

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `title` | TEXT | NOT NULL | User-friendly name |
| `description` | TEXT | | Detailed description |
| `status` | TEXT | DEFAULT 'draft' | Workflow state: `draft`, `pending_approval`, `approved`, `rejected`, `running`, `completed`, `failed`, `rolled_back` |
| `requester` | TEXT | | User or 'ai_agent' |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Relationships:
- **1:M → JOBS** — A request has many device-level jobs
- **1:M → CONFIG_PROPOSALS** (request_id)

---

### 7. **JOBS** — Device-Level Execution
Device-specific instances of a change request (one job per device per request).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `request_id` | INTEGER | FK change_requests(id), CASCADE | Parent change request |
| `device_id` | INTEGER | FK devices(id), CASCADE | Target device |
| `status` | TEXT | DEFAULT 'pending' | `pending`, `approved`, `rejected`, `running`, `success`, `failed`, `rolled_back` |
| `stage` | TEXT | | Current pipeline stage (e.g., `snapshot_pre`, `apply`, `verify`, `commit`) |
| `snapshot_pre_id` | INTEGER | FK config_snapshots(id) | Pre-change snapshot |
| `snapshot_post_id` | INTEGER | FK config_snapshots(id) | Post-change snapshot |
| `error_message` | TEXT | | Error details if failed |
| `generated_config` | TEXT | | Proposed configuration (from LLM/template) |
| `rollback_config` | TEXT | | Rollback configuration (if needed) |
| **M22 Ansible Execution:** | | | |
| `execution_engine` | TEXT | DEFAULT 'ssh_fallback' | `ansible_native`, `ansible_cli`, `ssh_fallback`, `no_op` |
| `ansible_playbook` | TEXT | | LLM-generated Ansible YAML for this job |
| `ansible_artifact_manifest` | TEXT | | JSON manifest for generated multi-file bundles |
| `ansible_entrypoint_path` | TEXT | | Relative path to entry playbook |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_jobs_request` — Lookup by `request_id`

#### Relationships:
- **M:1 ← CHANGE_REQUESTS** — Many jobs per request
- **M:1 ← DEVICES** — Each job targets one device
- **0..1 → CONFIG_SNAPSHOTS** (snapshot_pre_id)
- **0..1 → CONFIG_SNAPSHOTS** (snapshot_post_id)
- **1:M → JOB_STEPS** — Jobs have granular execution steps
- **0..1 → CONFIG_PROPOSALS** (via job_id)

---

### 8. **JOB_STEPS** — Granular Execution Logs
Detailed log of each stage within a job.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `job_id` | INTEGER | FK jobs(id), CASCADE | Parent job |
| `stage` | TEXT | NOT NULL | Pipeline stage: `plan`, `approve`, `snapshot_pre`, `apply`, `verify`, `commit`, `rollback` |
| `status` | TEXT | DEFAULT 'running' | `running`, `success`, `failed`, `skipped` |
| `details` | TEXT | | JSON or massive text output (logs, errors) |
| `created_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_steps_job` — Lookup by `job_id`

#### Relationships:
- **M:1 ← JOBS** — Many steps per job

---

## 🌐 DRIFT & COMPLIANCE ENTITIES (M8, M26)

### 9. **DRIFT_EVENTS** — Configuration Drift Detection
Records of detected configuration changes (expected vs. actual).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), CASCADE | Device with drift |
| `has_drift` | BOOLEAN | NOT NULL | Whether drift was detected |
| `hostname` | TEXT | | Device hostname (denormalized for performance) |
| `severity` | TEXT | | `low`, `medium`, `high` |
| `diff_text` | TEXT | | Unified diff of expected vs. actual |
| `timestamp` | TEXT | DEFAULT now() | When drift was detected |

#### Indexes:
- `idx_drift_device` — Lookup by `device_id`
- `idx_drift_timestamp` — Queries by time range

#### Relationships:
- **M:1 ← DEVICES** — Many drift events per device

---

### 10. **POLICIES** — Compliance Rules (M26)
Defines compliance policies with scope, rules, and thresholds.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Internal row ID |
| `policy_id` | TEXT | UNIQUE, NOT NULL | Stable external ID (slug/UUID) |
| `name` | TEXT | NOT NULL | Human-readable name |
| `description` | TEXT | | Policy description |
| `category` | TEXT | NOT NULL | `interface`, `vlan`, `ospf`, `static_route`, `acl`, `snmp`, `logging`, `ntp`, ... |
| `scope_json` | TEXT | NOT NULL | JSON: `{vendors: [...], device_roles: [...], devices: [...]}` |
| `rules_json` | TEXT | NOT NULL | JSON: Array of rule objects `[{action, target, severity, ...}]` |
| `normalization_json` | TEXT | | JSON: `{ignore_patterns: [...]}` |
| `thresholds_json` | TEXT | | JSON: `{auto_alert: true, alert_severity_min: 'high', ...}` |
| `enabled` | BOOLEAN | DEFAULT 1 | Policy is active |
| `version` | INTEGER | DEFAULT 1 | Policy version (for tracking changes) |
| `created_by` | TEXT | | User who created policy |
| `updated_by` | TEXT | | User who last updated policy |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_policies_category_enabled` — Fast queries by category and status
- `idx_policies_enabled` — Queries active policies

#### Relationships:
- **1:M → POLICY_AUDIT_LOG** — Policy change history
- **1:M ← COMPLIANCE_RESULTS** (violated_policies_json) — Referenced by results

---

### 11. **POLICY_AUDIT_LOG** — Policy Change History
Tracks all changes to compliance policies.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `policy_id` | INTEGER | FK policies(id), CASCADE | |
| `action` | TEXT | NOT NULL | `create`, `update`, `enable`, `disable`, `delete`, `clone` |
| `old_value_json` | TEXT | | Previous state (JSON) |
| `new_value_json` | TEXT | | New state (JSON) |
| `changed_by` | TEXT | | User who made change |
| `timestamp` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_policy_audit_policy_ts` — Fast lookup by policy and date

#### Relationships:
- **M:1 ← POLICIES** — Many audit entries per policy

---

### 12. **COMPLIANCE_JOBS** — Compliance Scan Execution
Records of compliance scan runs.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `run_mode` | TEXT | NOT NULL | `scheduled`, `manual`, `per_device` |
| `status` | TEXT | DEFAULT 'running' | `running`, `success`, `failed`, `partial` |
| `started_at` | TEXT | DEFAULT now() | |
| `finished_at` | TEXT | | When scan completed |
| `summary_json` | TEXT | | JSON: `{total_devices: X, passed: Y, failed: Z, ...}` |
| `error_text` | TEXT | | Error message if failed |

#### Indexes:
- `idx_compliance_jobs_started` — Lookup by `started_at`

#### Relationships:
- **1:M ← COMPLIANCE_RESULTS** — A job produces many results

---

### 13. **COMPLIANCE_RESULTS** — Per-Device Compliance Status
Result of compliance check for a device against policies.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), CASCADE | Device being checked |
| `baseline_snapshot_id` | INTEGER | FK config_snapshots(id) | Expected/baseline config |
| `actual_snapshot_id` | INTEGER | FK config_snapshots(id) | Current/actual config |
| `status` | TEXT | NOT NULL | `compliant`, `drifted`, `violated`, `no_baseline`, `no_actual`, `unreachable`, `partial` |
| `reason` | TEXT | | Reason for non-compliance |
| `compliance_score` | INTEGER | | 0–100 score |
| `severity` | TEXT | | `low`, `medium`, `high`, `critical` |
| `drift_pct` | REAL | | Percentage of config that differs |
| `missing_lines_json` | TEXT | | JSON array of lines missing from actual |
| `extra_lines_json` | TEXT | | JSON array of extra lines in actual |
| `changed_lines_json` | TEXT | | JSON array of changed lines |
| `violated_policies_json` | TEXT | | JSON array: `[{policy_id, rule_id, severity, message, evidence}]` |
| `checked_at` | TEXT | DEFAULT now() | When check was performed |

#### Indexes:
- `idx_compliance_results_device_checked` — Fast lookup by device and date
- `idx_compliance_results_status_checked` — Queries by status

#### Relationships:
- **M:1 ← DEVICES** — Many results per device
- **0..1 ← CONFIG_SNAPSHOTS** (baseline)
- **0..1 ← CONFIG_SNAPSHOTS** (actual)

---

## 🔒 SECURITY ENTITIES (M13)

### 14. **SECURITY_AUDITS** — Security Audit Results
Summary of security audits run on devices.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), CASCADE | |
| `score` | REAL | NOT NULL | 0.0–100.0 security score |
| `total_checks` | INTEGER | NOT NULL | Total rules checked |
| `passed` | INTEGER | NOT NULL | Number passed |
| `failed` | INTEGER | NOT NULL | Number failed |
| `critical` | INTEGER | DEFAULT 0 | Critical findings |
| `high` | INTEGER | DEFAULT 0 | High findings |
| `medium` | INTEGER | DEFAULT 0 | Medium findings |
| `low` | INTEGER | DEFAULT 0 | Low findings |
| `created_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_security_audits_device` — Lookup by `device_id`

#### Relationships:
- **M:1 ← DEVICES** — Many audits per device
- **1:M → AUDIT_FINDINGS** — Audit has many findings

---

### 15. **AUDIT_FINDINGS** — Individual Security Findings
Individual check results from security audits (CIS rules, etc.).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `audit_id` | INTEGER | FK security_audits(id), CASCADE | |
| `rule_id` | TEXT | NOT NULL | Rule identifier (e.g., `CIS-1.1.1`) |
| `category` | TEXT | NOT NULL | Rule category (e.g., `access_control`, `encryption`) |
| `severity` | TEXT | NOT NULL | `critical`, `high`, `medium`, `low` |
| `status` | TEXT | NOT NULL | `pass`, `fail`, `error`, `skip` |
| `description` | TEXT | | What the rule checks |
| `evidence` | TEXT | | Config snippet showing the issue |
| `remediation` | TEXT | | Suggested fix command |

#### Indexes:
- `idx_findings_audit` — Lookup by `audit_id`

#### Relationships:
- **M:1 ← SECURITY_AUDITS** — Many findings per audit

---

### 16. **SECURITY_EVENTS** — Security Alerts & Threats (M13b)
Records of security events (intrusions, anomalies, rogue devices, etc.).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `event_type` | TEXT | NOT NULL | `brute_force`, `port_scan`, `malware`, `anomaly`, `rogue_device`, `suricata` |
| `severity` | TEXT | NOT NULL | `critical`, `high`, `medium`, `low`, `info` |
| `source_ip` | TEXT | | IP address of source |
| `dest_ip` | TEXT | | IP address of destination |
| `device_id` | INTEGER | FK devices(id) | Device involved (if applicable) |
| `description` | TEXT | | Human-readable description |
| `raw_data` | TEXT | | JSON blob of original alert |
| `status` | TEXT | DEFAULT 'new' | `new`, `investigating`, `mitigated`, `false_positive` |
| `created_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_security_events_type` — Lookup by event type
- `idx_security_events_severity` — Lookup by severity
- `idx_security_events_created` — Queries by time range

#### Relationships:
- **0..1 ← DEVICES** — Event may involve a device

---

## 👤 AUTHENTICATION & CREDENTIALS (M15)

### 17. **USERS** — User Accounts
User accounts with roles and authentication.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `username` | TEXT | UNIQUE, NOT NULL | Login username |
| `password_hash` | TEXT | NOT NULL | Hashed password (bcrypt/argon2) |
| `role` | TEXT | DEFAULT 'engineer' | `admin`, `engineer`, `auditor` |
| `is_active` | BOOLEAN | DEFAULT 1 | Account active status |
| `created_at` | TEXT | DEFAULT now() | |
| `last_login` | TEXT | | Last login timestamp |

#### Indexes:
- `idx_users_username` — Fast login lookup

---

### 18. **DEVICE_CREDENTIALS** — Encrypted Credential Storage (M15)
Per-device credentials (SSH, SNMP, Enable) stored encrypted.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), CASCADE | |
| `label` | TEXT | | Credential type: `SSH`, `SNMP`, `Enable` |
| `username_enc` | TEXT | NOT NULL | Fernet-encrypted username |
| `password_enc` | TEXT | NOT NULL | Fernet-encrypted password |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Constraints:
- `UNIQUE(device_id, label)` — One credential per type per device

#### Indexes:
- `idx_credentials_device` — Lookup by `device_id`

#### Relationships:
- **M:1 ← DEVICES** — Many credentials per device

---

## 🚀 ONBOARDING & ZTP ENTITIES (M0)

### 19. **ZTP_PUBLISH_JOBS** — Zero-Touch Provisioning Job Tracking
Records of ZTP file publish operations.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `status` | TEXT | DEFAULT 'queued' | `queued`, `running`, `success`, `partial`, `failed` |
| `mode` | TEXT | NOT NULL | `single`, `batch` |
| `target_server_ip` | TEXT | NOT NULL | Server IP where files are published |
| `credential_label` | TEXT | DEFAULT 'SSH' | Credential set to use |
| `requested_by` | TEXT | | User who requested |
| `payload_json` | TEXT | | JSON payload |
| `summary_json` | TEXT | | Execution summary |
| `manifest_json` | TEXT | | Generated files manifest |
| `error_text` | TEXT | | Error message if failed |
| `total_items` | INTEGER | DEFAULT 0 | Total devices/items |
| `success_items` | INTEGER | DEFAULT 0 | Successfully processed |
| `failed_items` | INTEGER | DEFAULT 0 | Failed items |
| `created_at` | TEXT | DEFAULT now() | |
| `started_at` | TEXT | | When execution started |
| `finished_at` | TEXT | | When execution finished |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_ztp_publish_jobs_created` — Lookup by `created_at DESC`
- `idx_ztp_publish_jobs_status` — Fast status queries

#### Relationships:
- **1:M → ZTP_PUBLISH_ITEMS** — Job has many items

---

### 20. **ZTP_PUBLISH_ITEMS** — Individual ZTP Publication Items
Per-item tracking for ZTP file publication.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `job_id` | INTEGER | FK ztp_publish_jobs(id), CASCADE | |
| `device_id` | INTEGER | FK devices(id), SET NULL | Device being onboarded |
| `hostname` | TEXT | | Device hostname |
| `vendor` | TEXT | | Device vendor |
| `management_ip` | TEXT | | Device management IP |
| `management_mac` | TEXT | | Device MAC address |
| `filename` | TEXT | | File to publish |
| `remote_path` | TEXT | | Path on target server |
| `status` | TEXT | DEFAULT 'pending' | `pending`, `running`, `success`, `failed` |
| `stage` | TEXT | | Current stage |
| `error_text` | TEXT | | Error if failed |
| `details_json` | TEXT | | Additional metadata |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_ztp_publish_items_job` — Lookup by `job_id`
- `idx_ztp_publish_items_status` — Fast status queries

#### Relationships:
- **M:1 ← ZTP_PUBLISH_JOBS** — Many items per job
- **0..1 ← DEVICES** — Item may link to device

---

### 21. **ZTP_DHCP_MAPPINGS** — DHCP-to-Device Mappings
Maps MAC addresses to boot files for DHCP-based ZTP.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `device_id` | INTEGER | FK devices(id), CASCADE, UNIQUE | One mapping per device |
| `hostname` | TEXT | | Device hostname |
| `vendor` | TEXT | | Device vendor |
| `management_ip` | TEXT | | Device management IP |
| `management_mac` | TEXT | NOT NULL | MAC address to match |
| `filename` | TEXT | NOT NULL | Boot file to return |
| `enabled` | BOOLEAN | DEFAULT 1 | Mapping is active |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_ztp_dhcp_mappings_mac` — Lookup by `management_mac`
- `idx_ztp_dhcp_mappings_enable` — Queries active mappings

#### Relationships:
- **1:1 ← DEVICES** — One device per mapping

---

## 🔧 CONFIGURATION & INTENT ENTITIES

### 22. **CONFIG_PROPOSALS** — Generated Configuration Proposals (M19)
AI-generated configuration proposals awaiting approval.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | UUID |
| `status` | TEXT | DEFAULT 'pending' | `pending`, `approved`, `rejected`, `deployed`, `rolled_back` |
| `user_prompt` | TEXT | NOT NULL | Original user request |
| `target_device` | TEXT | NOT NULL | Device hostname |
| `target_device_id` | INTEGER | | Device ID (optional denormalization) |
| `vendor` | TEXT | NOT NULL | Device vendor |
| `feature` | TEXT | NOT NULL | Feature being configured |
| `params` | TEXT | | JSON parameters for the feature |
| `generated_config` | TEXT | NOT NULL | Proposed configuration text |
| `rollback_config` | TEXT | | Configuration to revert |
| `diff_text` | TEXT | | Unified diff to current config |
| `risk_level` | TEXT | DEFAULT 'medium' | `low`, `medium`, `high`, `critical` |
| `ai_explanation` | TEXT | | AI explanation of changes |
| `action_artifact_json` | TEXT | | JSON metadata about action (M19) |
| `job_id` | INTEGER | FK jobs(id) | If deployed, link to job |
| `request_id` | INTEGER | FK change_requests(id) | If from request, link to it |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_proposals_status` — Lookup by `status`

#### Relationships:
- **0..1 ← JOBS** — Proposal may become a job
- **0..1 ← CHANGE_REQUESTS** — Proposal may link to request
- **0..1 ← INTENTS** (proposal_id) — Referenced by intents

---

### 23. **INTENTS** — User Intents (Phase 1 — Intent-Based Pipeline)
High-level user intents that drive the entire workflow (UC02–UC05).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | Short UUID |
| `user_prompt` | TEXT | NOT NULL | Original user request |
| `intent_type` | TEXT | NOT NULL | `configure`, `troubleshoot`, `query`, `audit`, `rollback`, `deploy` |
| `action` | TEXT | | Specific action (e.g., `enable_ospf`, `patch_acl`) |
| `feature` | TEXT | | Feature being targeted |
| `vendor` | TEXT | | Vendor context |
| `parameters` | TEXT | | JSON blob of parameters |
| `scope` | TEXT | | JSON blob of scope (devices, sites, etc.) |
| `risk_level` | TEXT | DEFAULT 'medium' | `low`, `medium`, `high`, `critical` |
| `resolved_devices` | TEXT | | JSON array of resolved hostnames |
| `policy_approved` | BOOLEAN | | Policy check result |
| `policy_result` | TEXT | | JSON: violations, warnings, blast_radius |
| `blast_radius` | INTEGER | DEFAULT 0 | Number of devices affected |
| `status` | TEXT | DEFAULT 'parsed' | `parsed`, `validated`, `planned`, `approved`, `executed`, `verified`, `rejected` |
| `proposal_id` | TEXT | FK config_proposals(id) | Link to generated proposal |
| `created_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_intents_status` — Lookup by `status`
- `idx_intents_type` — Lookup by `intent_type`

#### Relationships:
- **0..1 → CONFIG_PROPOSALS** (proposal_id) — Intent may generate a proposal
- **1:M ← CONFIG_SNAPSHOTS** (intent_id) — May create snapshots

---

### 24. **AGENT_PLANS** — AI Agent Plans (M25)
Plans and execution state for autonomous agent workflows.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | UUID |
| `domain` | TEXT | DEFAULT 'network' | `network`, `security`, etc. |
| `message` | TEXT | NOT NULL | User message / request |
| `context_json` | TEXT | | JSON: `{view, selected_device}` |
| `plan_data_json` | TEXT | | JSON: `{proposed_actions, outcomes, risk}` |
| `affected_devices` | TEXT | | JSON array of hostnames |
| `risk_level` | TEXT | DEFAULT 'medium' | Risk assessment |
| `status` | TEXT | DEFAULT 'planned' | `planned`, `executing`, `completed`, `cancelled` |
| `playbook_yaml` | TEXT | | Generated Ansible playbook |
| `created_at` | TEXT | DEFAULT now() | |
| `executed_at` | TEXT | | When execution occurred |

#### Indexes:
- `idx_agent_plans_status` — Lookup by `status`

---

## 🌍 NETWORK MANAGEMENT ENTITIES

### 25. **COMMON_SERVICE_PROFILES** — Shared Service Configurations (M11)
Reusable SNMP, Syslog, NTP configurations across device scopes.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `scope_type` | TEXT | NOT NULL | `global`, `site`, `role`, `device` |
| `scope_value` | TEXT | NOT NULL | Scope value (site name, role, hostname, etc.) |
| `snmp_community` | TEXT | | SNMP community string |
| `snmp_location` | TEXT | | SNMP location |
| `snmp_contact` | TEXT | | SNMP contact |
| `snmp_host_ip` | TEXT | | SNMP trap server IP |
| `syslog_servers_json` | TEXT | | JSON array of syslog servers |
| `ntp_servers_json` | TEXT | | JSON array of NTP servers |
| `syslog_host_ip` | TEXT | | Primary syslog server |
| `ntp_server_ip` | TEXT | | Primary NTP server |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Constraints:
- `UNIQUE(scope_type, scope_value)` — One profile per scope

#### Indexes:
- `idx_common_service_scope` — Lookup by `scope_type` and `scope_value`

---

### 26. **SDN_CONTROLLERS** — SDN Controller Management (Phase 3)
Integration points for SDN controllers (OpenDaylight, ONOS, etc.).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `name` | TEXT | UNIQUE, NOT NULL | Human-friendly label |
| `controller_type` | TEXT | NOT NULL | `opendaylight`, `onos`, `others` |
| `url` | TEXT | NOT NULL | Controller URL (e.g., `http://192.168.1.100:8181`) |
| `username` | TEXT | | REST API username |
| `password_enc` | TEXT | | Fernet-encrypted password |
| `verify_ssl` | BOOLEAN | DEFAULT 0 | Verify SSL certificates |
| `status` | TEXT | DEFAULT 'offline' | `online`, `offline`, `error` |
| `managed_device_count` | INTEGER | DEFAULT 0 | Devices managed by controller |
| `last_synced` | TEXT | | Last sync timestamp |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_sdn_controllers_status` — Lookup by `status`

---

### 27. **CONTEXT_EVENT_LOG** — Context Refresh Event Trail (Phase 4 Step 14)
Records of context refresh events (topology changes, configuration updates, etc.).

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `event_id` | TEXT | UNIQUE, NOT NULL | Event identifier |
| `source` | TEXT | NOT NULL | Source system (e.g., `crawler`, `deploy`, `compliance`) |
| `action` | TEXT | NOT NULL | Action name (e.g., `topology_change`, `config_update`) |
| `summary` | TEXT | NOT NULL | Human-readable summary |
| `event_type` | TEXT | NOT NULL | Event category |
| `domains_json` | TEXT | NOT NULL | JSON: affected domains (inventory, topology, config, drift, security) |
| `versions_json` | TEXT | NOT NULL | JSON: version increments per domain |
| `metadata_json` | TEXT | | JSON: arbitrary context |
| `refresh_mode` | TEXT | | `none`, `context_only`, `full`, etc. |
| `refresh_targets_json` | TEXT | | JSON: targeted modules to refresh |
| `refresh_jobs_json` | TEXT | | JSON: background jobs spawned |
| `listener_results_json` | TEXT | | JSON: results from listeners |
| `downstream_targets_json` | TEXT | | JSON: systems notified downstream |
| `created_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_context_event_log_created` — Lookup by `created_at`
- `idx_context_event_log_source` — Lookup by `source`
- `idx_context_event_log_action` — Lookup by `action`

---

## 💬 COLLABORATION & CHAT ENTITIES

### 28. **ANSIBLE_CHAT_SESSIONS** — Ansible Automation Chat
Persistent chat sessions for Ansible Studio / Network Chat features.

#### Attributes:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | Session UUID |
| `owner_username` | TEXT | NOT NULL | User who owns session |
| `title` | TEXT | NOT NULL | Session title |
| `messages_json` | TEXT | NOT NULL | JSON array of messages |
| `created_at` | TEXT | DEFAULT now() | |
| `updated_at` | TEXT | DEFAULT now() | |

#### Indexes:
- `idx_ansible_chat_sessions_owner_updated` — Fast user session lookup

---

## 🏗️ IN-MEMORY TOPOLOGY (Real-Time Cache)

### **TopologyNode** — Real-Time Topology Cache (Not Persisted)
Python dataclass representing the live network state in memory.

```python
@dataclass
class TopologyNode:
    hostname: str
    mgmt_ip: Optional[str]
    interfaces: dict              # {interface_name: ip_address, ...}
    connected_nodes: list         # [hostname1, hostname2, ...]
    edge_links: dict              # {interface: {remote_node, remote_interface, remote_ip}}
    status: str                   # "up" or "down"
```

**Lifecycle:**
- Built by crawler (BFS engine) on every poll cycle
- Stored in `DatabaseManager._topo_cache` (in-memory)
- Serialized to `topology_store/current_topo.json` for persistence
- Read by frontend for live dashboard visualization
- **NOT** stored in SQLite (rebuilt from scratch each cycle)

---

## 📊 RELATIONSHIPS SUMMARY TABLE

| From Entity | Relationship | To Entity | Notes |
|-------------|--------------|-----------|-------|
| DEVICES | 1:M | INTERFACES | Cascading delete |
| DEVICES | 1:M | LINKS (src/dst) | Cascading delete |
| DEVICES | 1:M | CONFIG_SNAPSHOTS | Cascading delete |
| DEVICES | 1:M | DRIFT_EVENTS | Cascading delete |
| DEVICES | 1:M | SECURITY_AUDITS | Cascading delete |
| DEVICES | 1:M | DEVICE_CREDENTIALS | Cascading delete |
| DEVICES | 1:M | COMPLIANCE_RESULTS | Cascading delete |
| DEVICES | 0..1 | SDN_CONTROLLERS | Via managed devices |
| INTERFACES | M:1 | DEVICES | Required FK |
| INTERFACES | 1:M | LINKS (src/dst) | Set NULL on delete |
| LINKS | M:1 | DEVICES (src) | Cascading delete |
| LINKS | M:1 | DEVICES (dst) | Cascading delete |
| LINKS | M:1 | INTERFACES (src) | Set NULL on delete |
| LINKS | M:1 | INTERFACES (dst) | Set NULL on delete |
| CONFIG_SNAPSHOTS | M:1 | DEVICES | Cascading delete |
| CONFIG_SNAPSHOTS | 0..1 | INTENTS | Optional link |
| CONFIG_SNAPSHOTS | 0..1 | JOBS | Optional link |
| CONFIG_SNAPSHOTS | 1:M ← | COMPLIANCE_RESULTS | Referenced |
| CHANGE_REQUESTS | 1:M | JOBS | Cascading delete |
| CHANGE_REQUESTS | 1:M | CONFIG_PROPOSALS | Cascading delete |
| JOBS | M:1 | CHANGE_REQUESTS | Required FK |
| JOBS | M:1 | DEVICES | Cascading delete |
| JOBS | 1:M | JOB_STEPS | Cascading delete |
| JOBS | 0..1 ← | CONFIG_PROPOSALS | Referenced |
| JOB_STEPS | M:1 | JOBS | Cascading delete |
| SECURITY_AUDITS | M:1 | DEVICES | Cascading delete |
| SECURITY_AUDITS | 1:M | AUDIT_FINDINGS | Cascading delete |
| AUDIT_FINDINGS | M:1 | SECURITY_AUDITS | Cascading delete |
| SECURITY_EVENTS | 0..1 | DEVICES | Optional reference |
| POLICIES | 1:M | POLICY_AUDIT_LOG | Cascading delete |
| COMPLIANCE_RESULTS | M:1 | DEVICES | Cascading delete |
| COMPLIANCE_RESULTS | 0..1 | CONFIG_SNAPSHOTS (baseline) | Optional |
| COMPLIANCE_RESULTS | 0..1 | CONFIG_SNAPSHOTS (actual) | Optional |
| ZTP_PUBLISH_JOBS | 1:M | ZTP_PUBLISH_ITEMS | Cascading delete |
| ZTP_PUBLISH_ITEMS | M:1 | ZTP_PUBLISH_JOBS | Cascading delete |
| ZTP_PUBLISH_ITEMS | 0..1 | DEVICES | Set NULL on delete |
| ZTP_DHCP_MAPPINGS | 1:1 | DEVICES | Unique, cascading delete |
| DEVICE_CREDENTIALS | M:1 | DEVICES | Cascading delete |
| CONFIG_PROPOSALS | 0..1 | JOBS | Optional reference |
| CONFIG_PROPOSALS | 0..1 | CHANGE_REQUESTS | Optional reference |
| INTENTS | 0..1 | CONFIG_PROPOSALS | Optional reference |
| AGENT_PLANS | — | DEVICES | Via JSON affected_devices |

---

## 🔍 Key Entities by Domain

### **Network Inventory Domain**
- DEVICES, INTERFACES, LINKS, COMMON_SERVICE_PROFILES

### **Topology & Discovery**
- DEVICES, INTERFACES, LINKS (in DB)
- TopologyNode (in-memory cache)

### **Configuration Management**
- CONFIG_SNAPSHOTS, CHANGE_REQUESTS, JOBS, JOB_STEPS
- CONFIG_PROPOSALS, INTENTS

### **Compliance & Drift**
- POLICIES, POLICY_AUDIT_LOG, COMPLIANCE_JOBS, COMPLIANCE_RESULTS
- DRIFT_EVENTS, CONFIG_SNAPSHOTS (with snapshot_type)

### **Security**
- SECURITY_AUDITS, AUDIT_FINDINGS, SECURITY_EVENTS

### **Deployment Automation**
- CHANGE_REQUESTS, JOBS, JOB_STEPS
- INTENTS, AGENT_PLANS, CONFIG_PROPOSALS
- ZTP_PUBLISH_JOBS, ZTP_PUBLISH_ITEMS, ZTP_DHCP_MAPPINGS

### **User Management & Audit**
- USERS, DEVICE_CREDENTIALS, AUDIT_LOG, CONTEXT_EVENT_LOG

### **SDN & Integration**
- SDN_CONTROLLERS

---

## 🔐 Access Patterns & Indexes

### High-Traffic Queries
| Query Pattern | Table | Index |
|---------------|-------|-------|
| Get device by hostname | DEVICES | PK (implicit) |
| Get device by NetBox ID | DEVICES | idx_devices_netbox_id |
| Get all devices | DEVICES | — (full scan acceptable) |
| Get interfaces for device | INTERFACES | idx_interfaces_device |
| Get topology edges from device | LINKS | idx_links_src |
| Get latest config for device | CONFIG_SNAPSHOTS | idx_snapshots_device_type |
| Get compliance results for device | COMPLIANCE_RESULTS | idx_compliance_results_device_checked |
| Get active policies | POLICIES | idx_policies_enabled |
| Get recent security events | SECURITY_EVENTS | idx_security_events_created |
| Get user by username | USERS | idx_users_username |
| Get audit log by date | AUDIT_LOG | idx_audit_created |

---

## 📝 Notes on Data Consistency

1. **Cascading Deletes:** Deleting a device cascades to INTERFACES, LINKS, CONFIG_SNAPSHOTS, DRIFT_EVENTS, SECURITY_AUDITS, AUDIT_FINDINGS, DEVICE_CREDENTIALS, COMPLIANCE_RESULTS.

2. **Real-Time vs. Persistent:** TopologyNode (in-memory) is ephemeral; the crawler rebuilds it every cycle. DATABASE tables are persistent and archive-aware.

3. **Normalization:** Device fields like `hostname`, `status`, `role` are denormalized in some tables (DRIFT_EVENTS, etc.) for query performance.

4. **Timestamps:** All entities use ISO-8601 strings (UTC) for timestamps. Time zone aware code in manager.py uses UTC+8 for local logging.

5. **Encryption:** Sensitive fields (passwords, credentials) should be encrypted at rest. Currently passwords in DEVICES are plaintext—consider Fernet or similar for production.

6. **JSON Blobs:** Complex nested data (scope, rules, parameters, metadata) is stored as JSON strings. Application layer handles parsing.

---

## 🚀 Frontend Type Correspondences

Frontend TypeScript types (in `frontend/types.ts`, `frontend/types/intentSchema.ts`) mirror these entities:
- `TopologyData` ↔ TopologyNode (in-memory)
- `IntentSchema` ↔ INTENTS table
- `User` ↔ USERS table
- `D3Node` ↔ DEVICES (for visualization)
- `D3Link` ↔ LINKS (for visualization)

---

## 📌 Quick Reference: Entity Counts & Growth

For a typical deployment:
- **DEVICES:** 10–1000s
- **INTERFACES:** 10x DEVICES
- **LINKS:** 1–10x DEVICES
- **CONFIG_SNAPSHOTS:** 100s–1000s (historical)
- **COMPLIANCE_RESULTS:** 100s–10,000s (per scan)
- **SECURITY_EVENTS:** Growth depends on threat level
- **INTENTS:** 100s–1000s (user-driven)
- **AUDIT_LOG:** Grows continuously; archive quarterly

---

This schema represents a **production-grade network infrastructure automation platform** with support for inventory, topology, configuration management, compliance, security, and intent-driven deployment workflows.
