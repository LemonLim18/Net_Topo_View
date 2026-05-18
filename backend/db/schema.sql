-- ============================================================
-- M14: NetTopoView Database Schema
-- ============================================================
-- Persistent storage for device inventory, interface details,
-- link topology, configuration snapshots, and audit logs.
--
-- Real-time topology state is NOT stored here; it lives in the
-- in-memory cache (see manager.py).  This schema is for data
-- that must survive restarts.
-- ============================================================

PRAGMA journal_mode = WAL;           -- better concurrency for SQLite
PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------
-- 1. Devices  (network inventory)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname        TEXT    NOT NULL UNIQUE,
    management_ip   TEXT,
    management_subnet_mask TEXT,
    management_interface TEXT,
    vendor          TEXT,                       -- cisco_ios | huawei_vrp | juniper_junos
    model           TEXT,
    os_version      TEXT,
    serial_number   TEXT,
    netbox_id       INTEGER UNIQUE,
    status          TEXT    DEFAULT 'unknown',  -- planned | registered | provisioning | active | down | unknown
    role            TEXT    DEFAULT 'network-device', -- core | distribution | access | network-device
    source          TEXT    DEFAULT 'manual',   -- manual | crawler | netbox
    last_polled     TEXT,                       -- ISO-8601 timestamp
    ssh_user        TEXT,                       -- SSH login username
    ssh_password    TEXT,                       -- SSH login password (plain text)
    ssh_port        INTEGER DEFAULT 22,         -- SSH port
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

-- -----------------------------------------------------------
-- 2. Interfaces
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS interfaces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,           -- e.g. GigabitEthernet0/1
    ip_address      TEXT,
    subnet_mask     TEXT,
    mac_address     TEXT,
    speed           TEXT,
    status          TEXT    DEFAULT 'unknown',  -- up | down | admin-down | unknown
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(device_id, name)
);

-- -----------------------------------------------------------
-- 3. Links  (adjacency / topology edges)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS links (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    src_device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    src_interface_id    INTEGER REFERENCES interfaces(id) ON DELETE SET NULL,
    dst_device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    dst_interface_id    INTEGER REFERENCES interfaces(id) ON DELETE SET NULL,
    discovery_protocol  TEXT,                   -- lldp | cdp | manual
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE(src_device_id, src_interface_id, dst_device_id, dst_interface_id)
);

-- -----------------------------------------------------------
-- 4. Configuration Snapshots  (historical backups)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    config_text     TEXT    NOT NULL,
    config_hash     TEXT,                       -- SHA-256 for deduplication
    captured_at     TEXT    DEFAULT (datetime('now')),
    -- M26: Compliance & baseline metadata (added via manager migration; kept here for new DBs)
    snapshot_type   TEXT    DEFAULT 'snapshot', -- snapshot | baseline | rollback | golden | actual
    source_type     TEXT,                       -- deploy_pipeline | manual | import | scan
    normalized_config TEXT,                     -- normalized form used for diff/compliance
    checksum        TEXT,                       -- checksum of normalized_config (or config_text)
    policy_version  INTEGER,                    -- policy version used when captured/promoted
    intent_id       TEXT,                       -- link to intents.id when applicable
    execution_id    INTEGER,                    -- link to jobs.id when applicable
    approved_by     TEXT,
    verified_by     TEXT,
    verified_at     TEXT
);

-- -----------------------------------------------------------
-- 5. Audit Log  (who did what, when)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT,                       -- future M15 auth integration
    action          TEXT    NOT NULL,           -- e.g. config_push, crawl_start
    target          TEXT,                       -- e.g. device hostname
    details         TEXT,                       -- JSON blob with extra context
    created_at      TEXT    DEFAULT (datetime('now'))
);

-- -----------------------------------------------------------
-- Useful indexes
-- -----------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_interfaces_device   ON interfaces(device_id);
CREATE INDEX IF NOT EXISTS idx_links_src           ON links(src_device_id);
CREATE INDEX IF NOT EXISTS idx_links_dst           ON links(dst_device_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_device    ON config_snapshots(device_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_device_type ON config_snapshots(device_id, snapshot_type, captured_at);
CREATE INDEX IF NOT EXISTS idx_audit_created       ON audit_log(created_at);

-- -----------------------------------------------------------
-- 6. Change Management (M4 Deployment Pipeline)
-- -----------------------------------------------------------
-- Groups multiple device changes into a single logical request.
CREATE TABLE IF NOT EXISTS change_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    description     TEXT,
    status          TEXT    DEFAULT 'draft',    -- draft | pending_approval | approved | rejected | running | completed | failed | rolled_back
    requester       TEXT,                       -- user or 'ai_agent'
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

-- -----------------------------------------------------------
-- 7. Jobs (Device-level execution)
-- -----------------------------------------------------------
-- Link a Change Request to a specific Device.
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status          TEXT    DEFAULT 'pending',  -- pending | approved | rejected | running | success | failed | rolled_back
    stage           TEXT,                       -- current pipeline stage (e.g. 'snapshot_pre')
    snapshot_pre_id INTEGER REFERENCES config_snapshots(id),
    snapshot_post_id INTEGER REFERENCES config_snapshots(id),
    error_message   TEXT,
    generated_config TEXT,
    rollback_config TEXT,
    -- M22: Ansible-first execution ----------------------------------------
    execution_engine TEXT    DEFAULT 'ssh_fallback',  -- ansible_native | ansible_cli | ssh_fallback | no_op
    ansible_playbook TEXT,                            -- LLM-generated YAML for this job
    ansible_artifact_manifest TEXT,                   -- JSON manifest for generated multi-file bundles
    ansible_entrypoint_path TEXT,                     -- Relative path to generated entry playbook
    -- ---------------------------------------------------------------------
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

-- M22 migration: add columns to existing databases (safe – IF NOT EXISTS via PRAGMA)
-- SQLite does not support ADD COLUMN IF NOT EXISTS, so we guard with a trigger table trick.
-- These will silently fail if the columns already exist (which is fine).
CREATE TABLE IF NOT EXISTS _m22_migration (applied INTEGER DEFAULT 0);
INSERT OR IGNORE INTO _m22_migration (rowid, applied) VALUES (1, 0);

-- -----------------------------------------------------------
-- 8. Job Steps (Granular Logs)
-- -----------------------------------------------------------
-- Detailed log of every step taken during a job (e.g. "Generating config", "Connecting SSH").
CREATE TABLE IF NOT EXISTS job_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage           TEXT    NOT NULL,           -- plan | approve | snapshot_pre | apply | verify | commit | rollback
    status          TEXT    DEFAULT 'running',  -- running | success | failed | skipped
    details         TEXT,                       -- JSON / massive text output
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_request        ON jobs(request_id);
CREATE INDEX IF NOT EXISTS idx_steps_job           ON job_steps(job_id);

-- -----------------------------------------------------------
-- 9. Drift Events (M8)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS drift_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    has_drift       BOOLEAN NOT NULL,
    severity        TEXT,                       -- low | medium | high
    diff_text       TEXT,
    timestamp       TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_drift_device        ON drift_events(device_id);
CREATE INDEX IF NOT EXISTS idx_drift_timestamp     ON drift_events(timestamp);

-- -----------------------------------------------------------
-- 9b. Policy Compliance (M26)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS policies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id       TEXT    NOT NULL UNIQUE,        -- stable external id (slug / uuid)
    name            TEXT    NOT NULL,
    description     TEXT,
    category        TEXT    NOT NULL,               -- interface | vlan | ospf | static_route | acl | snmp | logging | ntp | ...
    scope_json      TEXT    NOT NULL,               -- JSON: vendors, device_roles, device_groups, devices
    rules_json      TEXT    NOT NULL,               -- JSON: array of rules
    normalization_json TEXT,                        -- JSON: ignore_patterns, etc.
    thresholds_json TEXT,                           -- JSON: auto_alert, alert_severity_min, score thresholds
    enabled         BOOLEAN DEFAULT 1,
    version         INTEGER DEFAULT 1,
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_policies_category_enabled ON policies(category, enabled);
CREATE INDEX IF NOT EXISTS idx_policies_enabled ON policies(enabled);

CREATE TABLE IF NOT EXISTS policy_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id       INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    action          TEXT    NOT NULL,               -- create | update | enable | disable | delete | clone
    old_value_json  TEXT,
    new_value_json  TEXT,
    changed_by      TEXT,
    timestamp       TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_policy_audit_policy_ts ON policy_audit_log(policy_id, timestamp);

CREATE TABLE IF NOT EXISTS compliance_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_mode        TEXT    NOT NULL,               -- scheduled | manual | per_device
    status          TEXT    DEFAULT 'running',      -- running | success | failed | partial
    started_at      TEXT    DEFAULT (datetime('now')),
    finished_at     TEXT,
    summary_json    TEXT,
    error_text      TEXT
);

CREATE INDEX IF NOT EXISTS idx_compliance_jobs_started ON compliance_jobs(started_at);

CREATE TABLE IF NOT EXISTS compliance_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id           INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    baseline_snapshot_id INTEGER REFERENCES config_snapshots(id),
    actual_snapshot_id  INTEGER REFERENCES config_snapshots(id),
    status              TEXT    NOT NULL,           -- compliant | drifted | violated | no_baseline | no_actual | unreachable | partial
    reason              TEXT,                       -- reason for no_baseline/no_actual/unreachable/partial
    compliance_score    INTEGER,
    severity            TEXT,                       -- low | medium | high | critical
    drift_pct           REAL,
    missing_lines_json  TEXT,
    extra_lines_json    TEXT,
    changed_lines_json  TEXT,
    violated_policies_json TEXT,                    -- array of {policy_id,name,rule_id,severity,message,evidence}
    checked_at          TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_compliance_results_device_checked ON compliance_results(device_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_results_status_checked ON compliance_results(status, checked_at DESC);

-- -----------------------------------------------------------
-- 10. Security Audits (M13a)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS security_audits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    score           REAL    NOT NULL,           -- 0.0–100.0
    total_checks    INTEGER NOT NULL,
    passed          INTEGER NOT NULL,
    failed          INTEGER NOT NULL,
    critical        INTEGER DEFAULT 0,
    high            INTEGER DEFAULT 0,
    medium          INTEGER DEFAULT 0,
    low             INTEGER DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_security_audits_device ON security_audits(device_id);

-- -----------------------------------------------------------
-- 11. Audit Findings (M13a — individual check results)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id        INTEGER NOT NULL REFERENCES security_audits(id) ON DELETE CASCADE,
    rule_id         TEXT    NOT NULL,           -- e.g. 'CIS-1.1.1'
    category        TEXT    NOT NULL,           -- e.g. 'access_control'
    severity        TEXT    NOT NULL,           -- critical | high | medium | low
    status          TEXT    NOT NULL,           -- pass | fail | error | skip
    description     TEXT,
    evidence        TEXT,                       -- config snippet showing the issue
    remediation     TEXT                        -- suggested fix command
);

CREATE INDEX IF NOT EXISTS idx_findings_audit ON audit_findings(audit_id);

-- -----------------------------------------------------------
-- 11b. Common Services Profiles
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS common_service_profiles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type          TEXT NOT NULL,            -- global | site | role | device
    scope_value         TEXT NOT NULL,
    snmp_community      TEXT,
    snmp_location       TEXT,
    snmp_contact        TEXT,
    snmp_host_ip        TEXT,
    syslog_port         INTEGER,
    syslog_servers_json TEXT,
    ntp_servers_json    TEXT,
    syslog_host_ip      TEXT,
    ntp_server_ip       TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(scope_type, scope_value)
);

CREATE INDEX IF NOT EXISTS idx_common_service_scope ON common_service_profiles(scope_type, scope_value);

-- -----------------------------------------------------------
-- 12. Security Events (M13b — threats / alerts)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS security_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,           -- brute_force | port_scan | malware | anomaly | rogue_device | suricata
    severity        TEXT    NOT NULL,           -- critical | high | medium | low | info
    source_ip       TEXT,
    dest_ip         TEXT,
    device_id       INTEGER REFERENCES devices(id),
    description     TEXT,
    raw_data        TEXT,                       -- JSON blob of original alert
    status          TEXT    DEFAULT 'new',      -- new | investigating | mitigated | false_positive
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_security_events_type     ON security_events(event_type);
CREATE INDEX IF NOT EXISTS idx_security_events_severity ON security_events(severity);
CREATE INDEX IF NOT EXISTS idx_security_events_created  ON security_events(created_at);

-- -----------------------------------------------------------
-- 13. Users (M15 Authentication)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    DEFAULT 'engineer',  -- admin | engineer | auditor
    is_active     BOOLEAN DEFAULT 1,
    created_at    TEXT    DEFAULT (datetime('now')),
    last_login    TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- -----------------------------------------------------------
-- 14. Device Credentials (M15 Credential Vault)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_credentials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    label         TEXT,                          -- e.g. "SSH", "SNMP", "Enable"
    username_enc  TEXT    NOT NULL,              -- Fernet-encrypted
    password_enc  TEXT    NOT NULL,              -- Fernet-encrypted
    created_at    TEXT    DEFAULT (datetime('now')),
    updated_at    TEXT    DEFAULT (datetime('now')),
    UNIQUE(device_id, label)
);

CREATE INDEX IF NOT EXISTS idx_credentials_device ON device_credentials(device_id);

-- -----------------------------------------------------------
-- 15. Onboarding ZTP Publish Jobs (M0 extension)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS ztp_publish_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT    DEFAULT 'queued',       -- queued | running | success | partial | failed
    mode            TEXT    NOT NULL,               -- single | batch
    target_server_ip TEXT   NOT NULL,
    credential_label TEXT   DEFAULT 'SSH',
    requested_by    TEXT,
    payload_json    TEXT,
    summary_json    TEXT,
    manifest_json   TEXT,
    error_text      TEXT,
    total_items     INTEGER DEFAULT 0,
    success_items   INTEGER DEFAULT 0,
    failed_items    INTEGER DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT,
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ztp_publish_jobs_created ON ztp_publish_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ztp_publish_jobs_status  ON ztp_publish_jobs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS ztp_publish_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES ztp_publish_jobs(id) ON DELETE CASCADE,
    device_id       INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    hostname        TEXT,
    vendor          TEXT,
    management_ip   TEXT,
    management_mac  TEXT,
    filename        TEXT,
    remote_path     TEXT,
    status          TEXT    DEFAULT 'pending',      -- pending | running | success | failed
    stage           TEXT,
    error_text      TEXT,
    details_json    TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ztp_publish_items_job    ON ztp_publish_items(job_id);
CREATE INDEX IF NOT EXISTS idx_ztp_publish_items_status ON ztp_publish_items(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ztp_dhcp_mappings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       INTEGER NOT NULL UNIQUE REFERENCES devices(id) ON DELETE CASCADE,
    hostname        TEXT,
    vendor          TEXT,
    management_ip   TEXT,
    management_mac  TEXT    NOT NULL,
    filename        TEXT    NOT NULL,
    enabled         BOOLEAN DEFAULT 1,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ztp_dhcp_mappings_mac    ON ztp_dhcp_mappings(management_mac);
CREATE INDEX IF NOT EXISTS idx_ztp_dhcp_mappings_enable ON ztp_dhcp_mappings(enabled, updated_at DESC);

-- -----------------------------------------------------------
-- 16. Config Proposals (M19 Agentic Workflow)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_proposals (
    id              TEXT PRIMARY KEY,              -- UUID
    status          TEXT DEFAULT 'pending',         -- pending|approved|rejected|deployed|rolled_back
    user_prompt     TEXT NOT NULL,
    target_device   TEXT NOT NULL,
    target_device_id INTEGER,
    vendor          TEXT NOT NULL,
    feature         TEXT NOT NULL,
    params          TEXT,                           -- JSON blob
    generated_config TEXT NOT NULL,
    rollback_config  TEXT,
    diff_text       TEXT,
    risk_level      TEXT DEFAULT 'medium',
    ai_explanation  TEXT,
    action_artifact_json TEXT,
    job_id          INTEGER REFERENCES jobs(id),
    request_id      INTEGER REFERENCES change_requests(id),
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON config_proposals(status);

-- -----------------------------------------------------------
-- 16. Intents (Intent-Based Pipeline — audit trail)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS intents (
    id              TEXT PRIMARY KEY,              -- short UUID
    user_prompt     TEXT NOT NULL,
    intent_type     TEXT NOT NULL,                  -- configure|troubleshoot|query|audit|rollback|deploy
    action          TEXT,
    feature         TEXT,
    vendor          TEXT,
    parameters      TEXT,                          -- JSON blob
    scope           TEXT,                          -- JSON blob
    risk_level      TEXT DEFAULT 'medium',
    resolved_devices TEXT,                         -- JSON array of hostnames
    policy_approved BOOLEAN,
    policy_result   TEXT,                          -- JSON blob (violations, warnings, blast_radius)
    blast_radius    INTEGER DEFAULT 0,             -- number of devices affected
    status          TEXT DEFAULT 'parsed',         -- parsed|validated|planned|approved|executed|verified|rejected
    proposal_id     TEXT REFERENCES config_proposals(id),
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);
CREATE INDEX IF NOT EXISTS idx_intents_type   ON intents(intent_type);


-- -----------------------------------------------------------
-- 16b. Context Event Log (Phase 4 Step 14)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_event_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT    NOT NULL UNIQUE,
    source              TEXT    NOT NULL,
    action              TEXT    NOT NULL,
    summary             TEXT    NOT NULL,
    event_type          TEXT    NOT NULL,
    domains_json        TEXT    NOT NULL,
    versions_json       TEXT    NOT NULL,
    metadata_json       TEXT,
    refresh_mode        TEXT,
    refresh_targets_json TEXT,
    refresh_jobs_json   TEXT,
    listener_results_json TEXT,
    downstream_targets_json TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_context_event_log_created ON context_event_log(created_at);
CREATE INDEX IF NOT EXISTS idx_context_event_log_source  ON context_event_log(source);
CREATE INDEX IF NOT EXISTS idx_context_event_log_action  ON context_event_log(action);

-- -----------------------------------------------------------
-- 16c. Ansible Chat Sessions
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS ansible_chat_sessions (
    id              TEXT PRIMARY KEY,
    owner_username  TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    messages_json   TEXT    NOT NULL,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ansible_chat_sessions_owner_updated
    ON ansible_chat_sessions(owner_username, updated_at DESC);


-- -----------------------------------------------------------
-- 17. Agent Plans (M25 — Persist plan/execute workflow state)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_plans (
    id              TEXT PRIMARY KEY,              -- UUID
    domain          TEXT NOT NULL DEFAULT 'network',
    message         TEXT NOT NULL,
    context_json    TEXT,                           -- JSON (view, selected_device)
    plan_data_json  TEXT,                           -- JSON (proposed_actions, outcomes, risk)
    affected_devices TEXT,                          -- JSON array of hostnames
    risk_level      TEXT DEFAULT 'medium',
    status          TEXT DEFAULT 'planned',         -- planned | executing | completed | cancelled
    playbook_yaml   TEXT,                           -- generated YAML (after execute)
    created_at      TEXT DEFAULT (datetime('now')),
    executed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_plans_status ON agent_plans(status);

-- -----------------------------------------------------------
-- 18. SDN Controllers (Phase 3 — SDN Controller Management)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS sdn_controllers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,        -- human-friendly label
    controller_type TEXT    NOT NULL,               -- opendaylight | onos
    url             TEXT    NOT NULL,               -- e.g. http://192.168.1.100:8181
    username        TEXT,
    password_enc    TEXT,                           -- Fernet-encrypted (via CredentialVault)
    verify_ssl      BOOLEAN DEFAULT 0,
    status          TEXT    DEFAULT 'offline',      -- online | offline | error
    managed_device_count INTEGER DEFAULT 0,
    last_synced     TEXT,                           -- ISO-8601
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sdn_controllers_status ON sdn_controllers(status);

-- -----------------------------------------------------------
-- Migration: add 'source' column to devices (safe for existing DBs)
-- -----------------------------------------------------------
-- SQLite ALTER TABLE ADD COLUMN is a no-op if column already exists
-- (raises error caught by application init).
-- We use a separate statement so the schema file remains idempotent.
