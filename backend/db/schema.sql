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
