-- ----------------------------------------------------------------
-- Migration 002: Incidents table (Phase 2 Autonomous Agent APIs)
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS incidents (
    id                   TEXT    PRIMARY KEY,            -- UUID  e.g. INC-2026-0108-001
    anomaly_type         TEXT    NOT NULL,               -- bandwidth_drop | link_failure | …
    severity             TEXT    NOT NULL DEFAULT 'medium', -- critical | high | medium | low | info
    affected_devices     TEXT,                           -- JSON array of hostnames
    metrics_current      REAL,
    metrics_baseline     REAL,
    metrics_threshold    REAL,
    detected_at          TEXT    DEFAULT (datetime('now')),

    -- Collected context (written after root-cause collection)
    logs                 TEXT,                           -- JSON array of log lines
    configs              TEXT,                           -- JSON object device->config_snippet
    telemetry_snapshot   TEXT,                           -- JSON object of raw metrics

    -- Root-cause analysis (written by AI)
    root_cause_summary   TEXT,
    root_cause_confidence REAL    DEFAULT 0.0,          -- 0.0 – 1.0
    possible_causes      TEXT,                          -- JSON array of strings

    -- Proposed remediation (written by AI)
    proposed_description TEXT,
    proposed_playbook    TEXT,                          -- YAML content
    estimated_impact     TEXT,
    rollback_plan        TEXT,

    -- Lifecycle
    status               TEXT    DEFAULT 'open',        -- open | investigating | approved | dismissed | resolved
    approver             TEXT,
    resolved_at          TEXT,

    created_at           TEXT    DEFAULT (datetime('now')),
    updated_at           TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_incidents_status   ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_created  ON incidents(created_at);
