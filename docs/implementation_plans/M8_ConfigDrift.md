# M8: Configuration Drift Detection — Implementation Plan (Executed)

Implemented a drift detection engine that compares the **Actual Network State** (from M2/M14) against the **Intended Network State** (from M7 NetBox).

## Goal
Automatically identify when a device's configuration deviates from the source of truth, classify the severity of the drift, log it for auditing, and provide hooks for auto-remediation.

## User Review Required
> [!IMPORTANT]
> **Logic Strategy**: We use a **Hybrid Comparison**:
> 1.  **Intended State**: Derived from M7 NetBox via M3 Config Engine templates.
> 2.  **Actual State**: Latest `ConfigSnapshot` from M14 Database.
> 3.  **Persistence**: All drift events are stored in the `drift_events` table.

## Architecture
1.  **Intended State Generator**:
    -   Fetch device variables from **M7 (NetBox)**.
    -   Render "Golde Config" using **M3 (Jinja2 Templates)**.
2.  **Actual State Retriever**:
    -   Fetch latest `ConfigSnapshot` from **M14 (Database)**.
3.  **Comparator Engine**:
    -   Compute `difflib` unified diff.
    -   Classify severity (Low/Medium/High).
4.  **Drift Manager**:
    -   Store `DriftEvent` in DB (`drift_events` table).
    -   Stub for `AutoRemediator` (future auto-fix logic).

## Implementation Details

### Backend
-   **Models**: `backend/drift/models.py` (`DriftEvent`).
-   **Logic**: `backend/drift/comparator.py`.
    -   Core `check_drift()` function.
    -   Logic to fetch intended vs actual.
-   **Persistence**:
    -   `backend/db/schema.sql`: Added `drift_events` table.
    -   `backend/db/manager.py`: Added `save_drift_event()`, `get_drift_events()`.
-   **Auto-Fix**: `backend/drift/auto_fix.py` (Structure ready).
-   **API**: `backend/drift/api.py`.
    -   `POST /drift/check/{hostname}`
    -   `POST /drift/check/all`

### Integration
-   **Router**: Mounted in `backend.py` under `/api/v1/drift`.

---

## Verification Plan
1.  **Mock Test**: `tests/verify_m8.py` verify logic with mocked DB/NetBox.
2.  **Manual Test**: Trigger API and check DB for saved event.
