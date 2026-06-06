# M4: Deployment Pipeline — Implementation Plan

## Goal
Implement a safe, auditable, and reversible pipeline for applying configuration changes to network devices. This bridges the gap between *generating* a config (M3/M5) and *applying* it (M1).

## Scope
1.  **Pipeline Orchestrator**: 6-stage process (Plan -> Approve -> Apply -> Verify -> Commit/Rollback).
2.  **Change Requests**: Persistent tracking of every change intent.
3.  **Verification Engine**: Automated pre/post-checks (ping, show commands).
4.  **Rollback Engine**: Automatic restoration of previous config on failure.

## Architecture

```text
backend/deploy/
├── __init__.py
├── models.py             # Pydantic/DB models (ChangeRequest, Job)
├── pipeline.py           # Core logic: execute_stage()
├── verifier.py           # Pre/Post-check runner
├── rollback.py           # Rollback logic (full replace vs undo commands)
└── api.py                # REST API for frontend/scheduler
```

## The 6 Stages
1.  **PLAN**: Generate config (M3) + generate rollback (M3) + syntax check.
2.  **APPROVE**: Pause for manual approval (or auto-approve if low risk).
3.  **SNAPSHOT (PRE)**: Backup current running config (M1).
4.  **APPLY**: Push config to device (M1).
5.  **VERIFY (POST)**: Run connectivity/state checks.
    *   *If FAIL*: Trigger immediate ROLLBACK.
6.  **COMMIT**: Mark job success + save post-snapshot.

## Data Schema (M14 Integration)
*   **ChangeRequest**: `id, title, status, requester, created_at`
*   **Job**: `id, request_id, device_id, stage, logs, snapshot_pre_id, snapshot_post_id`

## Integration
*   **M5 Agent** will submit Change Requests to M4 instead of calling M1 directly.
*   **M16 Scheduler** can trigger M4 jobs.

## Risks
*   **Loss of Connectivity**: Applying a bad config acts as a "self-lockout".
    *   *Mitigation*: "Reload in 10" safety timer (reload trigger before apply, cancel after verify).
