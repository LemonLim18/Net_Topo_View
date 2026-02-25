# Implementation Plan: Closed-Loop Incident Response Pipeline

> Saved: 2026-02-26

## TL;DR

Build an automated **detection → alert → AI analysis → playbook generation → user approval → deployment** pipeline that connects the existing but isolated modules: `AnomalyDetector`, `IncidentStore`, `NetworkAgent`/`PlaybookBuilder`, telemetry WebSocket, and the Ansible execution API. The core missing piece is glue code: a new **IncidentOrchestrator** service that bridges anomaly detection to incident creation to LLM analysis to real-time UI alerts. The frontend needs a new **IncidentDashboard** view and a telemetry-driven notification bridge.

## Data Flow

```
  Scheduler (5min)
       │
       ▼
  AnomalyDetector.analyze_events()
       │
       ▼
  IncidentOrchestrator.detect_and_ingest()
       │  ├─ IncidentStore.create()          → SQLite
       │  └─ broadcast("incident_created")   → WS /api/telemetry
       ▼
  IncidentOrchestrator.analyze_incident()
       │  ├─ NetworkAgent (topology + config context)
       │  ├─ PlaybookBuilder.build()          → YAML
       │  ├─ IncidentStore.update_analysis()  → SQLite
       │  └─ broadcast("incident_updated")    → WS /api/telemetry
       ▼
  Frontend: IncidentDashboard shows incident + playbook
       │
       ▼  (User clicks "Approve & Deploy")
  POST /incidents/{id}/approve
  POST /incidents/{id}/execute
       │  ├─ ansible.runner.run_playbook(check_mode=True)   → dry-run
       │  ├─ ansible.runner.run_playbook(check_mode=False)  → apply
       │  └─ IncidentStore.set_status("resolved")
       ▼
  Frontend: SSE stream shows live output → toast "Incident resolved"
```

## Steps

### Backend — Detection & Orchestration

1. **New: `backend/incidents/orchestrator.py`** — `IncidentOrchestrator` class:
   - `detect_and_ingest()` — Runs AnomalyDetector on recent security_events, creates incidents, deduplicates
   - `analyze_incident(incident_id)` — LLM RCA + PlaybookBuilder generates YAML, writes to incident
   - `broadcast_incident(incident_id, event_type)` — Pushes to telemetry WS clients
   - `execute_approved_playbook(incident_id)` — Creates Ansible job, runs --check then apply

2. **Update `backend/scheduler/jobs.py`** — Add `job_anomaly_detect()`

3. **Update `backend/scheduler/core.py`** — Register anomaly job (5 min interval)

4. **Update `backend/incidents/api.py`** — Add `/execute` and `/reanalyze` endpoints

5. **Update `backend/backend.py`** — Initialize orchestrator in lifespan, inject broadcast fn

6. **Enhance `backend/security/threat/anomaly_detector.py`** — Add network degradation patterns

### Frontend — Incident Dashboard & Notifications

7. **New: `frontend/views/IncidentDashboard.tsx`** — Full incident master-detail view

8. **New: `frontend/hooks/useTelemetry.ts`** — Shared telemetry WS hook

9. **Update `frontend/contexts/NotificationContext.tsx`** — Bridge WS events to toasts

10. **Update `frontend/types.ts`** — Add `'incidents'` to ViewId

11. **Update `frontend/src/App.tsx`** — Add incidents route

12. **Update `frontend/components/Sidebar.tsx`** — Add Incidents nav item

13. **Update `frontend/views/OverviewDashboard.tsx`** — Add incident KPI card

## Decisions

- Orchestrator as injectable class (testable) vs fire-and-forget background tasks
- 5-minute anomaly scan interval (configurable via SCHED_ANOMALY_MINUTES env var)
- Human-in-the-loop approval required before deployment
- Reuse telemetry WS for incidents (no new WebSocket connection)
- Separate IncidentDashboard view (complex lifecycle warrants full page)
