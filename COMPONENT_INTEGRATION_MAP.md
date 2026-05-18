# Component Integration Map: Visual Reference

A visual guide to how compliance, security, incidents, and dashboard components interact.

---

## 1. FRONTEND COMPONENT HIERARCHY

```
App.tsx (Router)
│
├── Header.tsx (Theme, User Info)
├── Sidebar.tsx (Navigation Menu)
│   ├── Network Domain
│   │   ├── Topology → Dashboard.tsx
│   │   ├── Configuration → Configuration.tsx
│   │   ├── Performance → NetworkPerformance.tsx
│   │   └── Network Copilot → GeminiChat (domain="network")
│   │
│   └── Security Domain
│       ├── SIEM & Events → SecurityEvents.tsx
│       ├── Threat Analysis → SecurityAnalysis.tsx
│       └── Security Copilot → GeminiChat (domain="security")
│
└── Main Content Area (renderView())
    ├── [Dashboard Views]
    │   ├── OverviewDashboard.tsx ←── MAIN ENTRY POINT
    │   │   ├── uses dashboardApi.getSummary()
    │   │   ├── shows compliance_pct
    │   │   ├── shows security.avg_score
    │   │   ├── shows open_incidents count
    │   │   ├── shows recent_security_events
    │   │   └── shows recent_audit_log
    │   │
    │   ├── ComplianceHubView.tsx (Tabs: Overview|Policies|Results|Alerts)
    │   │   ├── ComplianceOverviewCards.tsx (compliant, violated, drifted counts)
    │   │   ├── ComplianceAlertsPanel.tsx (alerts list)
    │   │   ├── uses complianceApi.summary()
    │   │   ├── uses complianceApi.listResults()
    │   │   └── uses complianceApi.listAlerts()
    │   │
    │   ├── ComplianceDeviceDetail.tsx (Per-device deep-dive)
    │   │   ├── DeviceCard (hostname, vendor, role)
    │   │   ├── ComplianceStatus (current status, score)
    │   │   ├── BaselineComparison (baseline vs actual)
    │   │   ├── ComplianceTimeline (history)
    │   │   └── uses complianceApi.deviceResults()
    │   │
    │   ├── PolicyBuilder.tsx (Policy CRUD)
    │   │   ├── PolicyRuleEditor.tsx
    │   │   ├── PolicyScopeSelector.tsx
    │   │   ├── PolicyVersionHistory.tsx
    │   │   ├── uses policyApi.list(), create(), update(), delete()
    │   │   └── uses complianceApi.scopeOptions()
    │   │
    │   ├── DriftView.tsx (Configuration drift)
    │   │   ├── DeviceList (devices with drift)
    │   │   ├── ConfigDiff (baseline vs actual diff)
    │   │   ├── Timeline (drift events)
    │   │   └── uses complianceApi.deviceResults()
    │   │
    │   ├── SecurityDashboard.tsx (Security metrics)
    │   │   ├── AuditScoreCard
    │   │   ├── RecentAuditsChart
    │   │   ├── FindingsPanel
    │   │   └── uses securityApi.listAudits(), getFindings()
    │   │
    │   ├── SecurityEvents.tsx (SIEM event list)
    │   │   ├── ThreatsOverTimeChart (bar chart)
    │   │   ├── SeverityDistributionPie (pie chart)
    │   │   ├── EventsTable (filterable, paginated)
    │   │   └── uses securityApi.listEvents()
    │   │
    │   ├── SecurityAnalysis.tsx (AI threat analysis)
    │   │   ├── LogInput (textarea for logs)
    │   │   ├── AnalysisPanel (summary, containment, remediation, escalation)
    │   │   ├── uses GoogleGenAI API
    │   │   └── uses useNotification() for alerts
    │   │
    │   └── IncidentDashboard.tsx (Incident management)
    │       ├── IncidentList (by status: open, investigating, resolved)
    │       ├── IncidentDetail (full report)
    │       │   ├── AnomalyInfo (type, severity, devices)
    │       │   ├── CollectedContext (logs, configs, telemetry)
    │       │   ├── RootCauseAnalysis (summary, confidence, causes)
    │       │   ├── ProposedSolution (description, playbook)
    │       │   └── Actions (Approve, Dismiss, Resolve, Execute)
    │       ├── StatusTimeline
    │       └── uses incidentApi (list, get, approve, dismiss, resolve, execute)
    │
    └── [Other Views - not detailed]
        ├── Configuration.tsx
        ├── AnsibleStudioView.tsx
        ├── IntentParserView.tsx
        ├── etc.
```

---

## 2. API SERVICE LAYER INTERACTION

```
Frontend Views                  API Service                Backend Routes          Database
──────────────────────────────────────────────────────────────────────────────────────────

ComplianceHubView  ───┐
ComplianceDeviceDetail│
DriftView             │    ──→ complianceApi ──→ /api/v1/compliance/* ──→ compliance/api.py ──→ db
PolicyBuilder         │
OverviewDashboard     │                           │
                      │                           ├─→ /compliance/summary
                      │                           ├─→ /compliance/results
                      │                           ├─→ /compliance/results/{device_id}
                      │                           ├─→ /compliance/scan
                      │                           └─→ /policies/*
                      │
SecurityDashboard   ──┐
SecurityEvents       │
SecurityAnalysis     │    ──→ securityApi ──→ /api/v1/security/* ──→ security/api.py ──→ db
OverviewDashboard    │                        │
                     │                        ├─→ /security/audit/{device_id}
                     │                        ├─→ /security/audits
                     │                        ├─→ /security/events
                     │                        └─→ /security/score/{device_id}
                     │
IncidentDashboard  ──┐
OverviewDashboard   │    ──→ incidentApi ──→ /api/v1/incidents/* ──→ incidents/api.py ──→ db
                     │                       │
                     │                       ├─→ /incidents
                     │                       ├─→ /incidents/{id}
                     │                       ├─→ /incidents/{id}/approve
                     │                       ├─→ /incidents/{id}/dismiss
                     │                       ├─→ /incidents/{id}/resolve
                     │                       └─→ /incidents/ingest
                     │
OverviewDashboard  ──────→ dashboardApi ──→ /api/v1/dashboard/summary ──→ dashboard/api.py ──→ (queries all)
```

---

## 3. DATABASE TABLE RELATIONSHIPS

```
DEVICES (Inventory)
├─1:M──> COMPLIANCE_RESULTS
│        ├─ device_id
│        ├─ compliance_status: compliant|non-compliant|error|unknown
│        ├─ compliance_score: 0-100
│        ├─ baseline_snapshot_id (FK)
│        └─ actual_snapshot_id (FK)
│
├─1:M──> SECURITY_AUDITS
│        ├─ device_id
│        ├─ score: 0.0-100.0
│        ├─ total_checks, passed, failed, critical, high, medium, low
│        └─ created_at
│            └─1:M──> AUDIT_FINDINGS
│                     ├─ rule_id, severity, status
│                     ├─ description, evidence, remediation
│                     └─ created_at
│
├─1:M──> CONFIG_SNAPSHOTS
│        ├─ snapshot_id
│        ├─ config_text, normalized_config, checksum
│        ├─ created_at
│        └─ (referenced by COMPLIANCE_RESULTS as baseline/actual)
│
└─1:M──> DRIFT_EVENTS
         ├─ event_id
         ├─ event_type: compliance_drift|drift_detected
         ├─ severity, triggered_by
         └─ old_config, new_config


POLICIES
├─1:M──> COMPLIANCE_JOBS
│        ├─ job_id
│        ├─ status: pending|running|completed|failed
│        ├─ started_at, completed_at
│        └─1:M──> COMPLIANCE_RESULTS
│                 └─ (job produces results)
│
└─1:M──> POLICY_AUDIT_LOG
         ├─ policy_id, action
         ├─ changed_by, changed_at
         └─ old_value, new_value


SECURITY_EVENTS (External Sources: Syslog, IDS, Anomaly)
├─ event_id, event_type: ssh_brute_force|port_scan|malware_callback|anomaly|rogue_device
├─ severity: critical|high|medium|low|info
├─ source_ip, dest_ip, device_id (optional)
├─ status: new|investigating|mitigated|false_positive
└─ created_at
   └─ (Can trigger INCIDENTS)


INCIDENTS (Anomaly-based)
├─ incident_id
├─ anomaly_type: bandwidth_drop|link_failure|cpu_high|memory_high|etc
├─ severity, affected_devices (JSON array)
├─ status: open|investigating|approved|dismissed|resolved
├─ root_cause_summary, root_cause_confidence
├─ possible_causes (JSON array)
├─ proposed_playbook (YAML)
├─ estimated_impact, rollback_plan
├─ approver, resolved_at
└─ metrics: current, baseline, threshold
```

---

## 4. API ENDPOINT TREE

```
/api/v1/

├── /compliance
│   ├── GET    /policies
│   ├── POST   /policies
│   ├── GET    /policies/{policy_id}
│   ├── PUT    /policies/{policy_id}
│   ├── DELETE /policies/{policy_id}
│   ├── POST   /policies/{policy_id}/clone
│   ├── POST   /policies/{policy_id}/test
│   ├── GET    /policies/{policy_id}/audit
│   ├── GET    /compliance/summary
│   ├── GET    /compliance/results
│   ├── GET    /compliance/results/{device_id}
│   ├── POST   /compliance/scan
│   ├── POST   /compliance/scan/{device_id}
│   ├── GET    /compliance/alerts
│   ├── POST   /compliance/alerts/{alert_id}/ack
│   ├── GET    /devices/{device_id}/baseline
│   ├── POST   /devices/{device_id}/baseline/set
│   ├── GET    /devices/{device_id}/snapshots
│   ├── POST   /devices/{device_id}/snapshots/collect
│   └── GET    /compliance/scope/options
│
├── /security
│   ├── POST   /security/audit/{device_id}
│   ├── POST   /security/audit/all
│   ├── GET    /security/audits
│   ├── GET    /security/audits/{device_id}/findings
│   ├── GET    /security/score/{device_id}
│   ├── GET    /security/events
│   ├── POST   /security/events
│   ├── POST   /security/respond/{event_id}
│   ├── POST   /security/playbook/{event_id}
│   ├── GET    /security/playbooks
│   ├── GET    /security/cve/{device_id}
│   └── GET    /security/cve
│
├── /incidents
│   ├── GET    /incidents
│   ├── POST   /incidents (ingest)
│   ├── GET    /incidents/{id}
│   ├── POST   /incidents/{id}/approve
│   ├── POST   /incidents/{id}/dismiss
│   ├── POST   /incidents/{id}/resolve
│   ├── POST   /incidents/{id}/auto-remediate
│   ├── POST   /incidents/{id}/execute
│   └── POST   /incidents/auto-remediate/process-pending
│
├── /dashboard
│   └── GET    /dashboard/summary
│
└── ... (other modules)
```

---

## 5. DATA FLOW: Compliance Scan Lifecycle

```
User Action (Frontend)
    │
    └─→ Click "Scan All Devices"
        │
        └─→ POST /api/v1/compliance/scan
            │
            └─→ Backend: complianceApi.scanAll()
                │
                └─→ scheduler: job_compliance_scan() [Background Task]
                    │
                    ├─1. Load all enabled policies from POLICIES
                    │   │
                    │   └─2. For each policy:
                    │       │
                    │       ├─a. Resolve target devices via NetBoxScopeResolver
                    │       │   │   (vendors, device_roles, device_groups)
                    │       │
                    │       └─b. For each target device:
                    │           │
                    │           ├─i. Fetch latest CONFIG_SNAPSHOT
                    │           │
                    │           ├─ii. ComplianceEvaluator.evaluate(
                    │           │     policy=policy,
                    │           │     device_config=config_text,
                    │           │     vendor=device.vendor
                    │           │    )
                    │           │    Returns: ComplianceResult {
                    │           │      status: compliant|non-compliant,
                    │           │      reason: str,
                    │           │      score: 0-100,
                    │           │      violations: [...],
                    │           │      missing_lines: [...],
                    │           │      changed_lines: [...]
                    │           │    }
                    │           │
                    │           ├─iii. Save to COMPLIANCE_RESULTS:
                    │           │     INSERT INTO COMPLIANCE_RESULTS (
                    │           │       device_id, compliance_job_id,
                    │           │       compliance_status,
                    │           │       compliance_score, reason, etc
                    │           │     )
                    │           │
                    │           ├─iv. If non-compliant:
                    │           │    └─ Create DRIFT_EVENT
                    │           │    └─ Trigger alert if severity high
                    │           │
                    │           └─v. Continue to next device
                    │
                    └─3. Return job_id with status
                        │
                        └─→ Frontend polls /api/v1/compliance/results
                            │
                            └─→ Display updated results in ComplianceHubView:
                                ├─ ComplianceOverviewCards (counts)
                                ├─ Results table (per-device status)
                                ├─ Alerts panel (violations)
                                └─ Compliance timeline
```

---

## 6. DATA FLOW: Security Event → Incident → Remediation

```
External Threat Detection (Syslog, IDS, Firewall)
    │
    └─→ POST /api/v1/security/events
        │ (IngestAlertRequest)
        │
        └─→ Backend: securityApi.ingestAlert()
            │
            ├─1. Save to SECURITY_EVENTS table
            │
            ├─2. Trigger Threat Detectors:
            │   ├─ SyslogReceiver (parse syslog)
            │   ├─ SuricataParser (parse IDS alerts)
            │   ├─ AnomalyDetector (behavioral analysis)
            │   ├─ RogueDetector (unknown devices)
            │   └─ LoginMonitor (brute force detection)
            │
            ├─3. If threat detected:
            │   │
            │   └─→ POST /api/v1/incidents/ingest
            │       │ (IngestRequest with anomaly_type, severity, affected_devices)
            │       │
            │       └─→ IncidentStore.create(incident_data)
            │           │
            │           └─→ INSERT INTO INCIDENTS (
            │               id, anomaly_type, severity,
            │               affected_devices, status='open',
            │               created_at
            │           )
            │
            └─4. Orchestrator.analyze_incident():
                │
                ├─a. Fetch Collected Context:
                │   ├─ Recent logs
                │   ├─ Device configs
                │   └─ Telemetry snapshot
                │
                ├─b. Call LLM for RCA:
                │   └─ Generate: summary, confidence, possible_causes
                │
                ├─c. Call LLM for Remediation:
                │   └─ Generate: description, playbook (YAML), impact, rollback
                │
                └─d. UPDATE INCIDENTS:
                    INSERT proposed_playbook, root_cause_summary, etc


User Interaction (Frontend)
    │
    ├─→ IncidentDashboard.listIncidents()
    │   │
    │   └─→ GET /api/v1/incidents?status=open
    │       │
    │       └─→ Display incident in list with status
    │
    └─→ User clicks incident → GET /api/v1/incidents/{id}
        │
        └─→ Display full IncidentReport with all context & playbook
            │
            ├─ User reviews proposed remediation
            │
            ├─→ POST /api/v1/incidents/{id}/approve
            │   │
            │   └─→ UPDATE status = 'approved'
            │
            └─→ POST /api/v1/incidents/{id}/execute
                │
                └─→ IncidentOrchestrator.execute_remediation()
                    │
                    ├─ Deploy proposed_playbook via Ansible
                    ├─ Monitor execution progress
                    ├─ Update INCIDENTS with result
                    │
                    └─ After success:
                        └─→ POST /api/v1/incidents/{id}/resolve
                            │
                            └─→ UPDATE status = 'resolved'
                                └─ Set resolved_at timestamp
```

---

## 7. SCHEDULED JOBS

```
Scheduler (APScheduler or similar)
│
├─ Daily @ 2:00 AM
│  │
│  └─→ job_compliance_scan()
│      │
│      └─→ Load all enabled POLICIES
│          │
│          └─→ For each policy:
│              │
│              └─→ Evaluate against target devices
│                  │
│                  ├─ Save COMPLIANCE_RESULTS
│                  ├─ Create DRIFT_EVENTS
│                  └─ Alert if violation
│
└─ Daily @ 3:00 AM
   │
   └─→ job_security_audit()
       │
       └─→ For all DEVICES:
           │
           ├─ SecurityAuditEngine.run_audit()
           ├─ Save to SECURITY_AUDITS + AUDIT_FINDINGS
           └─ Alert if score < threshold
```

---

## 8. REAL-TIME UPDATES (WebSocket/Polling)

```
Frontend
    │
    └─→ Polling (currently implemented)
        │
        ├─ setInterval(() => {
        │   GET /api/v1/compliance/results
        │   GET /api/v1/security/events
        │   GET /api/v1/incidents
        │   GET /api/v1/dashboard/summary
        │ }, 5000) [Every 5 seconds]
        │
        └─→ Update React state → Re-render components
```

---

## 9. NOTIFICATION SYSTEM

```
Backend
    │
    ├─→ Alert triggers:
    │   ├─ Compliance violation (low score)
    │   ├─ Security finding (critical severity)
    │   ├─ Incident created
    │   └─ Device down
    │
    └─→ (Notification delivery method not explicitly shown)
        └─→ Possible: Database table, Message queue, WebSocket


Frontend
    │
    └─→ NotificationContext hook:
        │
        ├─ useNotification() returns {addNotification}
        │
        └─ addNotification(title, message, type)
            │
            └─→ NotificationToast.tsx displays:
                ├─ Type-based color (success, warning, error, info)
                ├─ Title + message
                └─ Auto-dismiss after 5 seconds
```

---

## 10. FEATURE INTERACTION MATRIX

| Feature | Compliance | Security | Incidents | Dashboard |
|---------|:----------:|:--------:|:---------:|:---------:|
| **Compliance** | PRIMARY | ✓ Alerts from violations | ✓ Can trigger | ✓ Percentage |
| **Security** | ✓ Score in results | PRIMARY | ✓ Events trigger | ✓ Avg score |
| **Incidents** | ✓ Reference policies | ✓ Root cause from events | PRIMARY | ✓ Open count |
| **Dashboard** | ✓ Summary | ✓ Summary | ✓ Summary | PRIMARY |

- PRIMARY = Feature is the main focus
- ✓ = Feature provides/uses data
- (empty) = No direct interaction

---

## 11. UI COMPONENT COMPOSITION

```
ComplianceHubView
├── Tabs: ["Overview", "Policies", "Results", "Alerts"]
│
├── Tab: "Overview"
│   └── ComplianceOverviewCards
│       ├── Card: "Compliant" (count + %)
│       ├── Card: "Drifted" (count + %)
│       ├── Card: "Violated" (count + %)
│       └── Card: "Error" (count)
│
├── Tab: "Policies"
│   ├── PolicyList (CRUD buttons)
│   ├── PolicyDetail (on select)
│   └── PolicyBuilder (edit/create)
│       ├── PolicyRuleEditor
│       │   └── Rule form (action, target, path, expected, severity)
│       │
│       └── PolicyScopeSelector
│           ├── Vendors multi-select
│           ├── Device Roles multi-select
│           ├── Device Groups multi-select
│           └── Devices multi-select
│
├── Tab: "Results"
│   └── ComplianceResultsTable
│       ├── device_id, hostname, vendor, role
│       ├── status (badge), score (bar)
│       ├── checked_at (timestamp)
│       ├── Expandable: violations, drift_pct, drift lines
│       └── Action: Set Baseline, Compare, View History
│
└── Tab: "Alerts"
    └── ComplianceAlertsPanel
        ├── AlertItem (policy_id, severity, device, message)
        └── Action: Acknowledge
```

---

## 12. STATE MANAGEMENT REFERENCES

### Frontend State Holders

```
OverviewDashboard
├── useState: selectedTab, selectedView
├── useWebSocket: topology data
└── useMemo: computed graph data

ComplianceHubView
├── useState: activeTab (using complianceNavigationKeys)
├── useEffect: fetch summary, results, alerts
└── localStorage: COMPLIANCE_HUB_TAB_KEY for persistence

IncidentDashboard
├── useState: selectedIncidentId, filters
├── useEffect: polling /incidents
└── useMemo: group by status

NotificationContext (Global)
├── useState: notifications (array)
└── useCallback: addNotification, removeNotification
```

### Backend State Holders

```
IncidentStore
├── SQLite database (INCIDENTS table)
├── Methods: get(), list_all(), create(), set_status()
└── Persistence: Always on disk

ComplianceEvaluator
├── In-memory: Rule definitions
├── Method: evaluate() returns result (not persisted here)
└── Caller: api.py saves result to DB

SecurityAuditEngine
├── In-memory: Security rules registry
├── Method: run_audit() returns SecurityAudit
└── Caller: api.py saves to SECURITY_AUDITS + AUDIT_FINDINGS
```

---

## 13. DEPLOYMENT/EXECUTION PATHS

```
Frontend Build
    │
    └─→ npm run build
        │
        ├─→ tsc (TypeScript compile)
        ├─→ vite build
        └─→ dist/ (static files)

Frontend Dev
    │
    └─→ npm run dev
        │
        └─→ vite (dev server on http://localhost:5173)
            │
            └─→ Proxies /api/* to backend

Backend Start
    │
    └─→ python backend.py
        │
        ├─→ FastAPI app initialization
        ├─→ Include compliance_router, security_router, incidents_router
        ├─→ Setup lifespan context (scheduler, orchestrator)
        └─→ Listen on 0.0.0.0:8000 (or configured port)
```

---

**Last Updated**: 2024-11-20  
**Purpose**: Visual reference for component interactions & data flows
