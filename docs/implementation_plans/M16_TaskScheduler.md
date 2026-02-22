# M16: Task Scheduler

## Goal

Replace the raw `asyncio.sleep` loop in `backend.py` with APScheduler's `AsyncIOScheduler`, and register periodic background jobs for network crawling, drift detection, security audits, and CVE re-checks.

## Existing State

| Item | Current | After M16 |
|------|---------|-----------|
| Network crawl | `periodic_crawler()` via `asyncio.sleep(900)` in `backend.py:215` | APScheduler interval job (15 min) |
| Drift check | Manual only (`drift/api.py:run_network_drift_check`) | APScheduler interval job (30 min) |
| Security audit | Manual only (`security/engine.py:run_audit_all`) | APScheduler interval job (6 hrs) |
| CVE re-check | Manual only (`security/cve/matcher.py:scan_all_devices`) | APScheduler interval job (24 hrs) |

## Proposed Changes

### Dependencies

#### [MODIFY] [requirements.txt](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/requirements.txt)
- Add `apscheduler>=3.10.4`

---

### Scheduler Module

#### [NEW] [\_\_init\_\_.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/scheduler/__init__.py)
- Empty init

#### [NEW] [core.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/scheduler/core.py)
- Create `AsyncIOScheduler` instance
- `start_scheduler()` — registers all jobs with configurable intervals, starts the scheduler
- `stop_scheduler()` — shuts down gracefully
- Intervals configurable via environment variables with sane defaults

#### [NEW] [jobs.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/scheduler/jobs.py)
- `job_crawl()` — calls existing `run_crawl_and_broadcast("periodic")`
- `job_drift_check()` — calls `run_network_drift_check()` from drift module
- `job_security_audit()` — calls `SecurityEngine.run_audit_all(db)`
- `job_cve_rescan()` — calls `CveMatcher.scan_all_devices(db)`
- Each job wrapped in try/except with logging

---

### Backend Integration

#### [MODIFY] [backend.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/backend.py)
- Remove `periodic_crawler()` function and its `asyncio.create_task` in lifespan
- Import and call `start_scheduler(run_crawl_and_broadcast)` in lifespan startup
- Call `stop_scheduler()` in lifespan shutdown

---

### Documentation

#### [NEW] [M16_TaskScheduler.md](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/docs/implementation_plans/M16_TaskScheduler.md)
- Copy of this plan

#### [MODIFY] [module.md](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/module.md)
- Update M16 status to ✅ Done

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHED_CRAWL_MINUTES` | `15` | Network crawl interval |
| `SCHED_DRIFT_MINUTES` | `30` | Drift check interval |
| `SCHED_AUDIT_HOURS` | `6` | Security audit interval |
| `SCHED_CVE_HOURS` | `24` | CVE rescan interval |

## Verification Plan

### Automated Tests
- `cd backend && venv\Scripts\activate && python -c "from backend.scheduler.core import start_scheduler, stop_scheduler; print('OK')"` — import check
- `cd frontend && npx tsc --noEmit` — frontend unaffected

### Manual Verification
- Start the backend with `python -m uvicorn backend.backend:app`
- Observe logs for: `"Scheduler started with N jobs"` and individual job registrations
- After 1–2 minutes, verify the crawl job fires (log: `"[Scheduler] Crawl job triggered"`)
