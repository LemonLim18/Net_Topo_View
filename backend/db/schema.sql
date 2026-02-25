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
    vendor          TEXT,                       -- cisco_ios | huawei_vrp | juniper_junos
    model           TEXT,
    os_version      TEXT,
    serial_number   TEXT,
    status          TEXT    DEFAULT 'unknown',  -- up | down | unknown
    last_polled     TEXT,                       -- ISO-8601 timestamp
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
    captured_at     TEXT    DEFAULT (datetime('now'))
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
CREATE INDEX IF NOT EXISTS idx_audit_created       ON audit_log(created_at);

-- -----------------------------------------------------------
-- 6. Change Management (M4 Deployment Pipeline)
-- -----------------------------------------------------------
-- Groups multiple device changes into a single logical request.
CREATE TABLE IF NOT EXISTS change_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    description     TEXT,
    status          TEXT    DEFAULT 'draft',    -- draft | pending_approval | approved | running | completed | failed | rolled_back
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
    status          TEXT    DEFAULT 'pending',  -- pending | running | success | failed | rolled_back
    stage           TEXT,                       -- current pipeline stage (e.g. 'snapshot_pre')
    snapshot_pre_id INTEGER REFERENCES config_snapshots(id),
    snapshot_post_id INTEGER REFERENCES config_snapshots(id),
    error_message   TEXT,
    generated_config TEXT,
    rollback_config TEXT,
    -- M22: Ansible-first execution ----------------------------------------
    execution_engine TEXT    DEFAULT 'ssh_fallback',  -- ansible_native | ansible_cli | ssh_fallback | no_op
    ansible_playbook TEXT,                            -- LLM-generated YAML for this job
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
    role          TEXT    DEFAULT 'auditor',   -- admin | operator | auditor
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
-- 15. Config Proposals (M19 Agentic Workflow)
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
    job_id          INTEGER REFERENCES jobs(id),
    request_id      INTEGER REFERENCES change_requests(id),
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON config_proposals(status);

