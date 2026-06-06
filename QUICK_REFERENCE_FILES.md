# Quick Reference: File Locations & Dependencies

A quick lookup guide for where features are implemented.

---

## FRONTEND FILES

### Views (Page Components)

```
frontend/views/
├── SecurityEvents.tsx          ← SIEM & security event list view
├── SecurityAnalysis.tsx        ← AI-powered threat analysis
├── SecurityDashboard.tsx       ← Security metrics dashboard
├── ComplianceHubView.tsx       ← Main compliance management interface
├── ComplianceDeviceDetail.tsx  ← Per-device compliance deep-dive
├── PolicyBuilder.tsx           ← Policy creation & editing
├── IncidentDashboard.tsx       ← Incident lifecycle management
├── OverviewDashboard.tsx       ← System-wide summary dashboard
├── DriftView.tsx               ← Configuration drift detection
└── Dashboard.tsx               ← Network topology main view
```

### Reusable Components

```
frontend/components/
├── compliance/
│   ├── ComplianceOverviewCards.tsx      ← Summary metrics
│   ├── ComplianceAlertsPanel.tsx        ← Alert list
│   ├── PolicyRuleEditor.tsx             ← Rule definition form
│   ├── PolicyScopeSelector.tsx          ← Device scope multi-select
│   └── PolicyVersionHistory.tsx         ← Policy audit log
├── Sidebar.tsx                          ← Main navigation (includes Security Domain menu)
├── Header.tsx                           ← Top nav & theme switcher
├── NotificationToast.tsx                ← Alert notifications
└── ...other components
```

### API Service Layer

```
frontend/src/services/
├── api.ts                               ← Main API client with all endpoints
│   ├── export const securityApi = {     ← Security module endpoints
│   ├── export const complianceApi = {   ← Compliance module endpoints  
│   ├── export const policyApi = {       ← Policy management endpoints
│   ├── export const incidentApi = {     ← Incident management endpoints
│   ├── export const dashboardApi = {    ← Dashboard summary endpoint
│   └── export interface types...        ← TypeScript interfaces
└── config.ts                            ← API_CONFIG with BASE_URL
```

### Type Definitions

```
frontend/
├── types.ts                    ← AppDomain, ViewId, User types
├── typesLLM.tsx               ← LLM-specific types
├── constants.ts               ← App constants
└── utils/
    └── complianceNavigationKeys.ts  ← Compliance tab routing keys
```

---

## BACKEND FILES

### Main Entry Point

```
backend/backend.py             ← FastAPI app initialization
                               ← Imports all routers
                               ← Sets up lifespan context
                               ← Includes:
                                  - compliance_router
                                  - security_router
                                  - incidents_router
                                  - dashboard routes
```

### Compliance Module (M26)

```
backend/compliance/
├── api.py                     ← @router endpoints
│   ├── GET    /policies
│   ├── POST   /policies/{policy_id}/clone
│   ├── POST   /compliance/scan
│   ├── GET    /compliance/results
│   └── ...more endpoints
├── evaluator.py               ← ComplianceEvaluator class
│   └── evaluate(policy, device_id, config) → ComplianceResult
├── policy_service.py          ← PolicyService validation
├── scope_resolver.py          ← NetBoxScopeResolver - device targeting
├── normalizer.py              ← Config normalization for diff
├── parser.py                  ← Policy parsing
└── schemas/                   ← Policy schema definitions
```

### Security Module (M13)

```
backend/security/
├── api.py                     ← @router endpoints
│   ├── POST   /security/audit/{device_id}
│   ├── GET    /security/events
│   ├── POST   /security/events (ingest)
│   └── ...more endpoints
├── engine.py                  ← SecurityAuditEngine class
│   └── run_audit(device_id, config, vendor) → SecurityAudit
├── models.py                  ← @dataclass definitions
│   ├── SecurityRule
│   ├── SecurityAudit
│   ├── SecurityEvent
│   └── AuditResult
├── rules.py                   ← Rule definitions & registry
│   └── SECURITY_RULES = [...]
├── checks/                    ← Vendor-specific check implementations
│   ├── cisco_ios.py
│   ├── huawei_vrp.py
│   ├── juniper_junos.py
│   ├── auth_checks.py
│   ├── mgmt_plane_checks.py
│   ├── banner_checks.py
│   ├── logging_checks.py
│   ├── snmp_checks.py
│   ├── transport_checks.py
│   └── ...more checks
├── threat/                    ← Threat detection engines
│   ├── syslog_receiver.py     ← Parse syslog messages
│   ├── suricata.py            ← Parse IDS alerts
│   ├── anomaly_detector.py    ← Behavioral anomaly detection
│   ├── rogue_detector.py      ← Rogue device detection
│   ├── login_monitor.py       ← SSH/login monitoring
│   └── route_monitor.py       ← Routing change detection
├── response/                  ← Incident response automation
│   ├── responder.py           ← IncidentResponder class
│   │   └── generate_response(event) → ResponseAction
│   └── playbooks.py           ← PlaybookEngine for execution
├── cve/                       ← CVE database integration
│   └── matcher.py             ← CVEMatcher class
└── keystore.py                ← Credential storage
```

### Incidents Module (M25)

```
backend/incidents/
├── api.py                     ← @router endpoints
│   ├── GET    /incidents
│   ├── GET    /incidents/{incident_id}
│   ├── POST   /incidents/{incident_id}/approve
│   ├── POST   /incidents/{incident_id}/dismiss
│   ├── POST   /incidents/{incident_id}/resolve
│   ├── POST   /incidents/ingest
│   └── ...more endpoints
├── models.py                  ← Pydantic models
│   ├── class IncidentReport
│   ├── class IncidentSummary
│   ├── class DetectedAnomaly
│   ├── class AnomalyMetrics
│   ├── class CollectedContext
│   ├── class RootCauseAnalysis
│   ├── class ProposedSolution
│   └── class PlaybookSummary
├── store.py                   ← IncidentStore class
│   ├── get(incident_id)
│   ├── list_all(status, limit)
│   ├── create(incident_data)
│   └── set_status(incident_id, status, approver)
└── orchestrator.py            ← IncidentOrchestrator class
    ├── detect_anomaly()
    ├── analyze_incident()
    └── execute_remediation()
```

### Dashboard Module

```
backend/dashboard/
├── api.py                     ← @router endpoint
│   └── GET /dashboard/summary → DashboardSummary
└── (logic spread across imported modules)
```

### Database & Storage

```
backend/db/
├── manager.py                 ← DatabaseManager class
│   ├── get_policy_by_policy_id()
│   ├── get_device_by_id()
│   ├── list_policies()
│   ├── save_compliance_result()
│   ├── save_audit()
│   └── ...more methods
├── models.py                  ← ORM-like dataclasses (ConfigSnapshot, Device, etc)
└── netauto.db                 ← SQLite database file

docs/
├── create_database.sql        ← Full schema with all tables
└── DATABASE_SCHEMA_OVERVIEW.md ← Schema documentation
```

### Scheduler & Jobs

```
backend/scheduler/
├── jobs.py                    ← Scheduled tasks
│   ├── job_compliance_scan()  ← Runs compliance scan on all devices
│   └── job_security_audit()   ← Runs security audits on all devices
└── (timing configured in backend.py lifespan)
```

### Integration Points

```
backend/monitor/
├── threshold_engine.py        ← Uses incidents.store.IncidentStore
└── Can trigger incident creation on threshold violation

backend/llm/
├── network_tools.py           ← Has functions to query incidents, security events
├── change_intelligence.py     ← Correlates incidents with changes
└── RAG context bundle uses incidents for context

backend/rag/
├── context_bundle.py          ← Fetches incidents for RAG context
└── digital_twin.py            ← Uses incidents for twin state

backend/drift/
├── alerts.py                  ← Creates drift alerts from security events
└── Triggers incident creation on drift detection
```

---

## DATABASE SCHEMA

```
docs/create_database.sql       ← Full schema

Key Tables:
  POLICIES                     ← Policy definitions
  POLICY_AUDIT_LOG             ← Policy change history
  COMPLIANCE_JOBS              ← Scan job executions
  COMPLIANCE_RESULTS           ← Per-device compliance results

  SECURITY_AUDITS              ← Audit executions
  AUDIT_FINDINGS               ← Individual finding results
  SECURITY_EVENTS              ← Threat/anomaly events
  SECURITY_SCORE               ← Device scores (if separate table)

  INCIDENTS                    ← Incident records
  DRIFT_EVENTS                 ← Configuration change events

  DEVICES                      ← Inventory
  CONFIG_SNAPSHOTS             ← Configuration baselines
```

---

## CONFIGURATION FILES

```
frontend/
├── vite.config.ts             ← Vite build config
├── tsconfig.json              ← TypeScript config
├── tsconfig.app.json          ← App-specific TS config
├── package.json               ← Dependencies
└── eslint.config.js           ← Linting rules

backend/
├── requirements.txt           ← Python dependencies
├── run_backend.ps1            ← Start script
└── .env (not in repo)         ← Environment variables
```

---

## TYPE DEFINITIONS & INTERFACES

### Frontend Types (TypeScript)

```
frontend/src/services/api.ts

Compliance:
  - interface PolicyRecord
  - interface ComplianceResultRecord
  - interface PolicyRule

Security:
  - (inline in securityApi)
  - AuditSummaryResponse
  - FindingResponse
  - AuditDetailResponse
  - EventResponse
  - IngestAlertRequest

Incidents:
  - interface IncidentSummary
  - interface IncidentReport
  - interface AnomalyMetrics
  - interface AutoRemediationResult
  - interface AutoRemediationTimeline

Dashboard:
  - interface DashboardSummary
```

### Backend Types (Python)

```
backend/compliance/ — PolicyRecord (dict-based)
backend/security/models.py:
  - @dataclass SecurityRule
  - @dataclass SecurityAudit
  - @dataclass SecurityEvent
  - @dataclass AuditResult

backend/incidents/models.py:
  - class IncidentReport (Pydantic)
  - class IncidentSummary (Pydantic)
  - class DetectedAnomaly (Pydantic)
  - class AnomalyMetrics (Pydantic)
  - class CollectedContext (Pydantic)
  - class RootCauseAnalysis (Pydantic)
  - class ProposedSolution (Pydantic)
  - class PlaybookSummary (Pydantic)
```

---

## IMPORT/USAGE PATTERNS

### Compliance Feature Used In:

```
Frontend:
  - ComplianceHubView imports complianceApi, policyApi
  - ComplianceDeviceDetail imports complianceApi
  - OverviewDashboard imports dashboardApi (has compliance_pct)
  - DriftView imports complianceApi
  - PolicyBuilder imports policyApi, complianceApi

Backend:
  - backend.py imports: from compliance.api import router as compliance_router
  - scheduler/jobs.py imports: from compliance.evaluator import ComplianceEvaluator
  - monitor/threshold_engine.py — can trigger alerts
  - llm/network_tools.py — can query compliance status
```

### Security Feature Used In:

```
Frontend:
  - SecurityEvents imports securityApi
  - SecurityAnalysis imports securityApi, llmApi
  - SecurityDashboard imports securityApi
  - OverviewDashboard imports dashboardApi (has security.avg_score, critical_events)

Backend:
  - backend.py imports: from security.api import router as security_router
  - scheduler/jobs.py imports: from security.engine import SecurityAuditEngine
  - incidents/orchestrator.py imports: from security.models import SecurityEvent
  - security/response/ — creates incidents from events
```

### Incidents Feature Used In:

```
Frontend:
  - IncidentDashboard imports incidentApi
  - OverviewDashboard imports incidentApi (open_incidents count)
  - Views may show incident counts/alerts

Backend:
  - backend.py imports: from incidents.api import router as incidents_router, setup_incidents
  - monitor/threshold_engine.py — creates incidents
  - security/threat detectors — create incidents
  - llm/network_tools.py — can query incidents
  - rag/context_bundle.py — fetches incidents for context
  - incidents/orchestrator.py — main orchestration engine
```

---

## API ENDPOINT SUMMARY

### Base: `/api/v1`

#### Compliance Routes
```
/compliance/*                  ← All compliance endpoints
/policies                      ← Policy CRUD
/devices/{id}/baseline         ← Baseline management
/devices/{id}/snapshots        ← Snapshot management
```

#### Security Routes
```
/security/*                    ← All security endpoints
/security/audit/{device_id}    ← Run device audit
/security/events               ← Event list/ingest
/security/score/{device_id}    ← Device security score
```

#### Incident Routes
```
/incidents/*                   ← All incident endpoints
/incidents                     ← List incidents
/incidents/{id}                ← Get incident report
/incidents/{id}/approve        ← Approve remediation
/incidents/{id}/dismiss        ← Dismiss (false positive)
/incidents/{id}/resolve        ← Mark resolved
```

#### Dashboard Routes
```
/dashboard/summary             ← Overall system summary
```

---

## HOW TO FIND SPECIFIC FEATURES

### "I want to add a new compliance check"
→ `backend/compliance/rules.py` — Register rule
→ `backend/security/checks/*.py` — Implement check function
→ `frontend/components/compliance/` — Display result (if UI needed)

### "I want to modify the compliance scoring algorithm"
→ `backend/compliance/evaluator.py` — ComplianceEvaluator.evaluate()
→ `backend/compliance/api.py` — /compliance/results endpoint

### "I want to add a new threat detection engine"
→ `backend/security/threat/*.py` — Create detector class
→ `backend/security/api.py` — Add endpoint to expose detector
→ `frontend/views/SecurityEvents.tsx` — Display new event type

### "I want to customize incident remediation playbooks"
→ `backend/incidents/orchestrator.py` — Analyze incident flow
→ `backend/security/response/playbooks.py` — Playbook templates
→ `frontend/views/IncidentDashboard.tsx` — Display & approval UI

### "I want to add metrics to the dashboard"
→ `backend/dashboard/api.py` — getDashboard/summary endpoint
→ `frontend/views/OverviewDashboard.tsx` — Display component
→ `frontend/src/services/api.ts` — dashboardApi.getSummary()

---

## STATE FLOW DIAGRAM

```
Frontend                    Backend                     Database
─────────────────────────────────────────────────────────────────
User Action                 
    │
    ├→ GET /compliance/results  ──→ complianceApi.listResults()
    │                              ├→ db.list_compliance_results()
    │                              └→ return JSON
    └← Display in ComplianceHub

User Action
    │
    ├→ POST /compliance/scan    ──→ complianceApi.scanAll()
    │                              ├→ job_compliance_scan() [async]
    │                              ├→ For each device:
    │                              │  ├→ evaluator.evaluate()
    │                              │  ├→ db.save_compliance_result()
    │                              │  └→ alert if violation
    │                              └→ return { status, job_id }
    └← Show progress (polling or WebSocket)

User Action
    │
    ├→ POST /security/audit/{device_id}
    │                              ──→ securityApi.runAudit()
    │                              ├→ engine.run_audit()
    │                              ├→ db.save_audit()
    │                              └→ return findings
    └← Display SecurityDashboard

Scheduled Task
    │
    ├→ job_compliance_scan()   ──→ Every day at 2 AM
    │                              ├→ Scan all policies
    │                              ├→ Save results
    │                              └→ Alert on violations
    │
    └→ job_security_audit()    ──→ Every day at 3 AM
                                  ├→ Audit all devices
                                  ├→ Save audits
                                  └→ Alert on low scores

External Event
    │
    ├→ POST /security/events   ──→ securityApi.ingestAlert()
    │                              ├→ db.save_security_event()
    │                              ├→ Threat detectors trigger
    │                              ├→ If anomaly: create incident
    │                              └→ Alert system
    │
    └← SecurityEvents view updates real-time
       (via polling or WebSocket)

Incident Creation
    │
    ├→ POST /incidents/ingest   ──→ incidentApi.ingestIncident()
    │                              ├→ store.create(incident)
    │                              ├→ orchestrator.analyze()
    │                              │  └→ AI generates RCA + playbook
    │                              └→ return { incident_id }
    │
    └← IncidentDashboard shows new incident

User Approval
    │
    ├→ POST /incidents/{id}/approve
    │                              ──→ store.set_status("approved")
    │                              └→ Playbook ready
    │
    ├→ POST /incidents/{id}/execute
    │                              ──→ ansible.executeJob()
    │                              ├→ Monitor progress
    │                              └→ Update status
    │
    └← Show execution result
```

---

## COMMON QUERIES

### Get all non-compliant devices
```
SELECT d.hostname, cr.compliance_status, cr.compliance_score
FROM COMPLIANCE_RESULTS cr
JOIN DEVICES d ON cr.device_id = d.id
WHERE cr.compliance_status != 'compliant'
ORDER BY cr.compliance_score ASC
```

### Get devices with critical security findings
```
SELECT DISTINCT d.hostname, sa.device_id
FROM SECURITY_AUDITS sa
JOIN AUDIT_FINDINGS af ON sa.id = af.audit_id
JOIN DEVICES d ON sa.device_id = d.id
WHERE af.severity = 'critical'
AND sa.created_at > datetime('now', '-7 days')
```

### Get open incidents
```
SELECT id, anomaly_type, severity, status, created_at
FROM INCIDENTS
WHERE status IN ('open', 'investigating')
ORDER BY created_at DESC
```

### Dashboard summary stats
```
SELECT 
  (SELECT COUNT(*) FROM DEVICES) as device_count,
  (SELECT AVG(score) FROM SECURITY_AUDITS 
   WHERE created_at > datetime('now', '-1 day')) as avg_security_score,
  (SELECT COUNT(*) FROM INCIDENTS WHERE status='open') as open_incidents,
  (SELECT COUNT(DISTINCT device_id) FROM COMPLIANCE_RESULTS 
   WHERE compliance_status='compliant') as compliant_devices
```

---

**Last Updated**: 2024-11-20  
**Purpose**: Quick file reference & navigation guide
