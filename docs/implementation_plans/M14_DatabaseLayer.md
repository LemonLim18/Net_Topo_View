# M14: Database Layer — Implementation Plan

## Goal
The goal of Module 14 is to provide a robust persistence layer for the NetAutoController. This involves setting up integrated SQLite storage (with a path to PostgreSQL for production) to save topology data, interface details, links, configuration history, and security events.

Currently, the system uses loose JSON files for topology; M14 will formalize this into a structured relational database.

**DB Choice:** We will use **SQLite** for development (zero-config, easy) but architect the system to switch to **PostgreSQL** for production. PostgreSQL is better suited for high-concurrency environments with frequent writes from multiple crawlers and users.

## Proposed Changes

### [Database Foundation]

#### [NEW] [schema.sql](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/db/schema.sql)
Define the core tables:
- `devices`: `id`, `hostname`, `vendor`, `management_ip`, `model`, `os_version`, `serial`, `uptime`, `last_polled`
- `interfaces`: `id`, `device_id`, `name`, `status`, `ip_address`, `subnet_mask`, `speed`, `mac_address`
- `links`: `id`, `src_device_id`, `src_interface_id`, `dst_device_id`, `dst_interface_id`, `discovery_protocol`
- `config_snapshots`: `id`, `device_id`, `config_text`, `config_hash`, `timestamp`
- `audit_log`: `id`, `user_id`, `action`, `target`, `timestamp`, `details`

#### [NEW] [manager.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/db/manager.py)
A `DatabaseManager` class to handle:
- SQLite connection pooling (Persistent Storage).
- **In-Memory Cache (Redis/Dict)**: For high-frequency, transient topology state that updates every second.
- Table initialization from `schema.sql`.
- Migration support (basic versioning).

#### [NEW] [models.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/db/models.py)
Pydantic/Dataclass models mirroring the database schema for clean type-hinting across the backend.

### [Repository Layer]

#### [NEW] [repositories/base.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/db/repositories/base.py)
Base repository with common CRUD operations.

#### [NEW] [repositories/device_repo.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/db/repositories/device_repo.py)
Methods to save/load devices and their interfaces.

#### [NEW] [repositories/topology_repo.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/db/repositories/topology_repo.py)
Methods to manage links and adjacency data.

## Verification Plan

### Automated Tests
- Run a test script to initialize the DB and perform CRUD operations on each table.
- Verify that data types (Enums, IP addresses) are correctly handled.

### Manual Verification
- Manually inspect the `network.db` file using `sqlite3` CLI.
- Ensure foreign key constraints (e.g., deleting a device deletes its interfaces) are working as expected.
