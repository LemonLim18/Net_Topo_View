# Feature Integration Overview: Compliance, Security, Incidents & Dashboard

A comprehensive mapping of compliance checks, security scores, incidents, and related features across the NetTopoView codebase.

---

## 1. HIGH-LEVEL ARCHITECTURE

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                      FRONTEND (React/TypeScript)                │
│  SecurityEvents | SecurityAnalysis | SecurityDashboard         │
│  ComplianceHub | ComplianceDeviceDetail | PolicyBuilder         │
│  IncidentDashboard | OverviewDashboard                          │
└────────────────┬────────────────────────────────────────────────┘
                 │ REST API Calls via api.ts
                 │
┌────────────────┴────────────────────────────────────────────────┐
│                    BACKEND (FastAPI + Python)                   │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  │  Compliance     │  │   Security       │  │    Incidents     │
│  │  Module (M26)   │  │  Module (M13)    │  │  Module (M25)    │
│  │                 │  │                  │  │                  │
│  │ /compliance/*   │  │ /security/*      │  │ /incidents/*     │
│  │ Evaluator       │  │ Engine           │  │ Store            │
│  │ Policies        │  │ Audits/Events    │  │ Orchestrator     │
│  │ Results         │  │ Response/Rules   │  │ Models           │
│  └─────────────────┘  └──────────────────┘  └──────────────────┘
└────────────────┬────────────────────────────────────────────────┘
                 │ Database Queries
                 │
┌────────────────┴────────────────────────────────────────────────┐
│                    DATABASE (SQLite)                            │
│  COMPLIANCE_JOBS | COMPLIANCE_RESULTS | POLICIES               │
│  SECURITY_AUDITS | AUDIT_FINDINGS | SECURITY_EVENTS            │
│  INCIDENTS | DRIFT_EVENTS | SECURITY_SCORE                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. DATABASE SCHEMA & MODELS

### A. Compliance Tables

#### **POLICIES** — Compliance Rules Definition
```
policy_id (PK)       : TEXT UNIQUE
name                 : TEXT
description          : TEXT
category             : TEXT
enabled              : BOOLEAN
scope                : JSON (vendors, device_roles, device_groups, devices)
rules                : JSON array of PolicyRule
version              : INTEGER
created_at, updated_at : TIMESTAMP
```

#### **COMPLIANCE_JOBS** — Scan Execution Records
```
job_id (PK)      : TEXT UNIQUE
policy_id (FK)   : TEXT
status           : TEXT (pending|running|completed|failed)
started_at       : TIMESTAMP
completed_at     : TIMESTAMP
error            : TEXT
```

#### **COMPLIANCE_RESULTS** — Per-Device Results
```
id (PK)                    : INTEGER
device_id (FK)             : INTEGER
hostname                   : TEXT
compliance_job_id (FK)     : TEXT
compliance_status          : TEXT (compliant|non-compliant|error|unknown)
reason                     : TEXT
compliance_score           : INTEGER (0-100)
severity                   : TEXT
drift_pct                  : FLOAT
checked_at                 : TIMESTAMP
baseline_snapshot_id (FK)  : INTEGER (optional)
actual_snapshot_id (FK)    : INTEGER (optional)
missing_lines              : JSON array
extra_lines                : JSON array
changed_lines              : JSON array
violated_policies          : JSON array
```

**Indices:**
- `idx_compliance_results_device_checked` — Fast lookup by device & date
- `idx_compliance_results_status_checked` — Query by compliance status

### B. Security Tables

#### **SECURITY_AUDITS** — Audit Execution Records
```
id (PK)         : INTEGER
device_id (FK)  : INTEGER
score           : REAL (0.0-100.0)
total_checks    : INTEGER
passed          : INTEGER
failed          : INTEGER
critical        : INTEGER
high            : INTEGER
medium          : INTEGER
low             : INTEGER
created_at      : TIMESTAMP
```

#### **AUDIT_FINDINGS** — Per-Device Findings
```
id (PK)         : INTEGER
audit_id (FK)   : INTEGER
rule_id         : TEXT
category        : TEXT
severity        : TEXT
status          : TEXT (pass|fail|error|skip)
description     : TEXT
evidence        : TEXT (config snippet)
remediation     : TEXT
created_at      : TIMESTAMP
```

#### **SECURITY_EVENTS** — Detected Threats/Anomalies
```
id (PK)         : INTEGER
event_type      : TEXT (ssh_brute_force|port_scan|malware_callback|etc)
severity        : TEXT (critical|high|medium|low|info)
source_ip       : TEXT
dest_ip         : TEXT
device_id (FK)  : INTEGER (optional)
description     : TEXT
status          : TEXT (new|investigating|mitigated|false_positive)
created_at      : TIMESTAMP
raw_data        : JSON (optional)
source          : TEXT (e.g., crawler, deploy, compliance, security)
```

**Indices:**
- `idx_security_events_created` — Recent events lookup

### C. Incidents Tables

#### **INCIDENTS** — Incident Records
```
id (PK)                    : TEXT UNIQUE
anomaly_type               : TEXT (bandwidth_drop|link_failure|cpu_high|etc)
severity                   : TEXT (critical|high|medium|low|info)
affected_devices           : JSON array of hostnames
detected_at                : TIMESTAMP
status                     : TEXT (open|investigating|approved|dismissed|resolved)
root_cause_summary         : TEXT
root_cause_confidence      : FLOAT
possible_causes            : JSON array
proposed_description       : TEXT
proposed_playbook          : TEXT (YAML content)
estimated_impact           : TEXT
rollback_plan              : TEXT
logs                       : JSON array
configs                    : JSON (hostname → config snippet)
telemetry_snapshot         : JSON
metrics_current            : FLOAT
metrics_baseline           : FLOAT
metrics_threshold          : FLOAT
approver                   : TEXT (username)
resolved_at                : TIMESTAMP
created_at, updated_at     : TIMESTAMP
```

#### **DRIFT_EVENTS** — Configuration Change Events
```
id (PK)           : INTEGER
device_id (FK)    : INTEGER
event_type        : TEXT (compliance_drift, drift_detected, etc)
severity          : TEXT
timestamp         : TIMESTAMP
triggered_by      : TEXT (which policy/check)
details           : JSON
old_config        : TEXT
new_config        : TEXT
created_at        : TIMESTAMP
```

---

## 3. BACKEND API ENDPOINTS

### A. Compliance API (`/api/v1/compliance/...`)

```python
# File: backend/compliance/api.py

# ─── POLICIES CRUD ───
GET    /policies                          → List all policies
GET    /policies/{policy_id}              → Get single policy
POST   /policies                          → Create policy
PUT    /policies/{policy_id}              → Update policy
DELETE /policies/{policy_id}              → Delete policy
POST   /policies/{policy_id}/clone        → Clone policy
POST   /policies/{policy_id}/test         → Test policy on snapshot
GET    /policies/{policy_id}/audit        → Policy change audit log

# ─── COMPLIANCE RESULTS ───
GET    /compliance/summary                → Overall compliance stats
GET    /compliance/results?limit=200      → List all compliance results
GET    /compliance/results/{device_id}    → Device-specific results
POST   /compliance/scan                   → Scan all devices (background)
POST   /compliance/scan/{device_id}       → Scan single device
GET    /compliance/alerts?limit=50        → Compliance alerts
POST   /compliance/alerts/{alert_id}/ack  → Acknowledge alert

# ─── BASELINES ───
GET    /devices/{device_id}/baseline      → Get baseline snapshot
POST   /devices/{device_id}/baseline/set  → Set baseline
GET    /devices/{device_id}/snapshots     → List snapshots
POST   /devices/{device_id}/snapshots/collect → Collect new snapshot

# ─── SCOPE HELPERS ───
GET    /compliance/scope/options          → Available vendors, roles, tags
```

### B. Security API (`/api/v1/security/...`)

```python
# File: backend/security/api.py

# ─── AUDITS ───
POST   /security/audit/{device_id}        → Run audit on single device
POST   /security/audit/all                → Run network-wide audit (background)
GET    /security/audits                   → List audit summaries
GET    /security/audits/{device_id}/findings → Latest findings for device
GET    /security/score/{device_id}        → Latest security score

# ─── EVENTS / THREATS ───
GET    /security/events?severity=HIGH     → List security events (filterable)
POST   /security/events                   → Ingest new alert
GET    /security/events/{event_id}        → Get event details

# ─── INCIDENT RESPONSE ───
POST   /security/respond/{event_id}       → Generate response recommendation
POST   /security/playbook/{event_id}      → Execute remediation playbook
GET    /security/playbooks                → List available playbooks

# ─── CVE CORRELATION ───
GET    /security/cve/{device_id}          → Device CVEs
GET    /security/cve                      → CVE statistics
```

### C. Incidents API (`/api/v1/incidents/...`)

```python
# File: backend/incidents/api.py

# ─── LIST & GET ───
GET    /incidents                         → List incidents (filterable by status)
GET    /incidents/{incident_id}           → Get full incident report

# ─── LIFECYCLE ───
POST   /incidents/{incident_id}/approve   → Approve remediation
POST   /incidents/{incident_id}/dismiss   → Mark as false-positive
POST   /incidents/{incident_id}/resolve   → Mark as resolved

# ─── INGEST & AUTO-REMEDIATION ───
POST   /incidents/ingest                  → Create incident from anomaly
POST   /incidents/{incident_id}/auto-remediate  → Start auto-remediation
POST   /incidents/auto-remediate/process-pending → Process pending remediations
POST   /incidents/{incident_id}/execute   → Execute approved playbook
```

### D. Dashboard API (`/api/v1/dashboard/...`)

```python
# File: backend/dashboard/api.py

GET    /dashboard/summary                 → Overall system summary including:
                                          → devices (total, up, down)
                                          → security (avg_score, critical_events)
                                          → drift (compliance_pct)
                                          → recent_events, recent_audit_log
```

---

## 4. FRONTEND API SERVICE LAYER

### File: `frontend/src/services/api.ts`

#### **Security API Client**
```typescript
export const securityApi = {
  // Audits
  runAudit: (deviceId: number) → AuditDetailResponse
  runAuditAll: () → { status, message }
  listAudits: (deviceId?: number) → AuditSummaryResponse[]
  getFindings: (deviceId: number) → FindingResponse[]
  getScore: (deviceId: number) → { device_id, score, as_of }

  // Events
  listEvents: (limit?, severity?) → EventResponse[]
  ingestAlert: (alert) → EventResponse
  respondToEvent: (eventId, vendor?) → ResponseAction
  executePlaybook: (eventId, vendor?) → PlaybookExecution
  listPlaybooks: () → PlaybookInfo[]

  // CVE
  getDeviceCves: (deviceId: number) → DeviceCveReport
  getCveStats: () → CveStatistics
}
```

#### **Compliance API Client**
```typescript
export const complianceApi = {
  // Summary & Results
  summary: () → { counts, severity_breakdown, compliance_pct }
  listResults: (limit = 200) → { items: ComplianceResultRecord[], total }
  deviceResults: (deviceId, limit = 20) → ComplianceResultRecord[]
  listAlerts: (limit = 50) → AlertItem[]
  ackAlert: (alertId) → { status, alert_id }

  // Scanning
  scanAll: () → { status, job_id }
  scanDevice: (deviceId) → { result: ComplianceResultRecord }

  // Baselines & Snapshots
  getBaseline: (deviceId) → { baseline }
  setBaseline: (deviceId, snapshotId) → { status, baseline_snapshot_id }
  listSnapshots: (deviceId, limit = 50) → SnapshotItem[]
  collectSnapshot: (deviceId) → { status, snapshot_id }

  // Utilities
  scopeOptions: () → { roles, tags, sites, tenants }
}

export interface ComplianceResultRecord {
  id?: number;
  device_id: number;
  hostname?: string;
  vendor?: string | null;
  status: 'compliant' | 'drifted' | 'violated' | 'no_baseline' | ...;
  reason?: string;
  compliance_score?: number;
  severity?: string;
  drift_pct?: number;
  missing_lines?: string[];
  extra_lines?: string[];
  changed_lines?: Array<{ key?, baseline, actual }>;
  violated_policies?: Array<{ policy_id, policy_name, severity, message }>;
  checked_at?: string;
}
```

#### **Policy API Client**
```typescript
export const policyApi = {
  list: (includeDisabled = true) → { items: PolicyRecord[], total }
  get: (policyId) → { policy: PolicyRecord }
  create: (policy) → { policy: PolicyRecord }
  update: (policyId, patch) → { policy: PolicyRecord }
  remove: (policyId) → { deleted: boolean, policy_id }
  clone: (policyId, suffix) → { policy: PolicyRecord }
  test: (policyId, body) → { status, violations }
  audit: (policyId, limit) → { items, total }
}

export interface PolicyRecord {
  policy_id: string;
  name: string;
  description?: string;
  category: string;
  enabled: boolean;
  version?: number;
  scope: {
    vendors?: string[];
    device_roles?: string[];
    device_groups?: string[];
    devices?: string[];
  };
  rules: PolicyRule[];
  normalization?: { ignore_patterns?: string[] };
  thresholds?: { auto_alert?: boolean; alert_severity_min?: string };
  created_at?: string;
  updated_at?: string;
}
```

#### **Incidents API Client**
```typescript
export const incidentApi = {
  // List & Get
  listIncidents: (status?, limit = 100) → IncidentSummary[]
  getIncident: (id) → IncidentReport

  // Lifecycle
  approveIncident: (id) → { status, incident_id, playbook }
  dismissIncident: (id) → { status, incident_id }
  resolveIncident: (id) → { status, incident_id, resolved_at }

  // Ingestion & Auto-Remediation
  ingestIncident: (data) → { status, incident_id, message }
  autoRemediate: (id, opts?) → AutoRemediationResult
  processPendingRemediations: () → { processed, succeeded, failed }
  executePlaybook: (id) → { status, incident_id, result }
}

export interface IncidentReport {
  id: string;
  anomaly: {
    id: string;
    type: string;  // bandwidth_drop | link_failure | interface_error | latency_spike | packet_loss | cpu_high | memory_high
    severity: string;
    affected_devices: string[];
    detected_at: string;
    metrics: AnomalyMetrics;
  };
  collected_context: {
    logs: string[];
    configs: Record<string, string>;
    telemetry_snapshot: Record<string, any>;
  };
  root_cause_analysis: {
    summary: string;
    confidence: number;
    possible_causes: string[];
  };
  proposed_solution: {
    description: string;
    playbook: { name, filename, content, hosts, tasks };
    estimated_impact: string;
    rollback_plan: string;
  };
  status: string;  // open | investigating | approved | dismissed | resolved
  approver?: string | null;
  resolved_at?: string | null;
  created_at: string;
  updated_at: string;
}
```

#### **Dashboard API Client**
```typescript
export const dashboardApi = {
  getSummary: () → DashboardSummary
}

export interface DashboardSummary {
  devices: { total, up, down, unknown };
  interfaces: { total, up, down };
  security: { avg_score, total_audits, critical_events };
  drift: { total_events, devices_with_drift, compliance_pct };
  deployments: { total_jobs, recent_7d, last_status };
  recent_events: Array<{ id, event_type, severity, description, created_at }>;
  recent_audit_log: Array<{ id, action, target, details, created_at }>;
}
```

---

## 5. FRONTEND COMPONENTS & VIEWS

### A. View Components (Pages)

#### **SecurityEvents** (`frontend/views/SecurityEvents.tsx`)
- **Purpose**: SIEM/Security Operations Center display
- **Features**:
  - Security event table with severity badges
  - Threats over time (bar chart)
  - Severity distribution (pie chart)
  - Event filtering and pagination
- **Data**: Uses `securityApi.listEvents()`
- **Status Display**: Blocked, Quarantined, Alerted, Logged

#### **SecurityAnalysis** (`frontend/views/SecurityAnalysis.tsx`)
- **Purpose**: Incident threat assessment using AI
- **Features**:
  - Paste SIEM logs/JSON for AI analysis
  - AI generates: summary, containment, remediation, escalation
  - Severity-based notifications
  - Uses Google Gemini API
- **Data**: Mock SIEM log analysis
- **Output**: HTML rendering of analysis results

#### **SecurityDashboard** (`frontend/views/SecurityDashboard.tsx`)
- **Purpose**: Overall security posture overview
- **Features**: Dashboard panels, metrics, alerts
- **Data**: Uses `securityApi.getScore()`, `securityApi.listAudits()`

#### **ComplianceHubView** (`frontend/views/ComplianceHubView.tsx`)
- **Purpose**: Main compliance management interface
- **Tabs**: Overview, Policies, Results, Alerts
- **Components**:
  - `ComplianceOverviewCards` — Summary statistics
  - `ComplianceAlertsPanel` — Alerts list
- **Data**: Uses `complianceApi.summary()`, `complianceApi.listResults()`
- **Navigation**: Uses `complianceNavigationKeys` for tab routing

#### **ComplianceDeviceDetail** (`frontend/views/ComplianceDeviceDetail.tsx`)
- **Purpose**: Per-device compliance deep-dive
- **Features**:
  - Device info, current compliance status
  - Baseline comparison
  - Compliance history timeline
  - Drift visualization
- **Data**: Uses `complianceApi.deviceResults()`, `complianceApi.getBaseline()`

#### **PolicyBuilder** (`frontend/views/PolicyBuilder.tsx`)
- **Purpose**: Create/edit compliance policies
- **Components**:
  - `PolicyRuleEditor` — Define rules
  - `PolicyScopeSelector` — Set target devices
  - `PolicyVersionHistory` — View changes
- **Data**: Uses `policyApi`, `complianceApi`

#### **DriftView** (`frontend/views/DriftView.tsx`)
- **Purpose**: Configuration drift detection
- **Features**:
  - Compare baseline vs actual config
  - Visual diff highlighting
  - Drift events timeline
- **Data**: Uses `complianceApi.deviceResults()`, `netboxApi`

#### **IncidentDashboard** (`frontend/views/IncidentDashboard.tsx`)
- **Purpose**: Incident management interface
- **Features**:
  - List incidents by status
  - Full incident details with RCA
  - Approve/dismiss/resolve actions
  - Auto-remediation workflow
- **Data**: Uses `incidentApi.listIncidents()`, `incidentApi.getIncident()`

#### **OverviewDashboard** (`frontend/views/OverviewDashboard.tsx`)
- **Purpose**: System-wide summary dashboard
- **Features**:
  - Device statistics
  - Compliance percentage
  - Open incidents count
  - Recent security events
- **Data**: Uses `dashboardApi.getSummary()`

### B. Reusable Components

#### **Compliance Components** (`frontend/components/compliance/`)
- **ComplianceOverviewCards** — Summary metric cards (compliant, violated, drifted, error)
- **ComplianceAlertsPanel** — Alert list with severity coloring
- **PolicyScopeSelector** — Multi-select device scope (vendors, roles, groups)
- **PolicyRuleEditor** — Form for defining policy rules
- **PolicyVersionHistory** — Timeline of policy changes

---

## 6. BACKEND MODULES STRUCTURE

### A. Compliance Module (`backend/compliance/`)

**Core Files:**
- `api.py` — FastAPI router with all compliance endpoints
- `evaluator.py` — ComplianceEvaluator class: runs policies against devices
- `policy_service.py` — PolicyService: validates and normalizes policies
- `scope_resolver.py` — NetBoxScopeResolver: resolves device target sets
- `normalizer.py` — Config normalization for diff/compliance
- `parser.py` — Parse policy definitions
- `schemas/` — Policy schema definitions

**Key Classes:**
```python
class ComplianceEvaluator:
  def evaluate(policy, device_config, vendor) → ComplianceResult
  def evaluate_all(policy, devices) → List[ComplianceResult]

class PolicyService:
  def validate_and_normalize(policy_dict) → ValidationResult

class NetBoxScopeResolver:
  def resolve(scope_dict) → List[DeviceId]
```

### B. Security Module (`backend/security/`)

**Core Files:**
- `api.py` — FastAPI router: audit, events, response endpoints
- `engine.py` — SecurityAuditEngine: runs security checks
- `models.py` — Data models: SecurityRule, SecurityAudit, SecurityEvent, AuditResult
- `rules.py` — Rule definitions (CIS-like checks)
- `checks/` — Vendor-specific check implementations
  - `cisco_ios.py`, `huawei_vrp.py`, `juniper_junos.py`
  - `auth_checks.py`, `mgmt_plane_checks.py`, `banner_checks.py`, etc.
- `threat/` — Threat detection engines
  - `syslog_receiver.py` — Parse syslog messages
  - `suricata.py` — Parse Suricata IDS alerts
  - `anomaly_detector.py` — Detect behavioral anomalies
  - `rogue_detector.py` — Detect rogue devices
  - `login_monitor.py` — Monitor login failures
  - `route_monitor.py` — Monitor routing changes
- `response/` — Incident response automation
  - `responder.py` — IncidentResponder: generate remediation
  - `playbooks.py` — PlaybookEngine: execute actions
- `cve/` — CVE database integration
  - `matcher.py` — CVEMatcher: correlate device info with CVE database

**Key Classes:**
```python
class SecurityAuditEngine:
  def run_audit(device_id, config_text, vendor) → SecurityAudit

class AnomalyDetector:
  def detect(metric_name, current, baseline, threshold) → bool

class IncidentResponder:
  def generate_response(event) → ResponseAction
```

### C. Incidents Module (`backend/incidents/`)

**Core Files:**
- `api.py` — FastAPI router: list, get, approve, dismiss, resolve, ingest, auto-remediate
- `models.py` — Pydantic models: IncidentReport, IncidentSummary, DetectedAnomaly, etc.
- `store.py` — IncidentStore: CRUD operations on incidents table
- `orchestrator.py` — IncidentOrchestrator: orchestrates detection, analysis, remediation flow

**Key Classes:**
```python
class IncidentStore:
  def get(incident_id) → dict
  def list_all(status?, limit?) → List[dict]
  def set_status(incident_id, status, approver?) → None
  def create(incident_data) → str (incident_id)

class IncidentOrchestrator:
  def run_analytics_cycle() → List[IncidentReport]
  def analyze_incident(incident) → AnalysisUpdate
  def execute_remediation(incident) → RemediationResult
```

---

## 7. DATA FLOW EXAMPLES

### A. Compliance Scan Flow

```
Frontend: User clicks "Scan All"
    ↓
POST /api/v1/compliance/scan
    ↓
Backend: complianceApi.scanAll()
    ↓
scheduler: job_compliance_scan() [background task]
    ↓
For each device:
  1. Fetch latest config snapshot
  2. Load applicable policies (via NetBoxScopeResolver)
  3. ComplianceEvaluator.evaluate(policy, config, vendor)
  4. Save result to COMPLIANCE_RESULTS table
  5. If violation → create DRIFT_EVENT
  6. Trigger alert if severity threshold exceeded
    ↓
Frontend polls: GET /api/v1/compliance/results?limit=200
    ↓
Display: ComplianceHubView with updated compliance scores
```

### B. Security Event Detection & Response Flow

```
External Source (Syslog, Suricata, Network Monitor)
    ↓
SecurityEvent ingested via:
  POST /api/v1/security/events
    ↓
Backend: ingestAlert()
    ↓
Save to SECURITY_EVENTS table
    ↓
Trigger threat detection engines:
  - AnomalyDetector (latency spikes, bandwidth drops)
  - RogueDetector (unknown devices)
  - SyslogReceiver (parse syslog)
  - SuricataParser (parse IDS alerts)
    ↓
If anomaly detected:
  Create INCIDENT via IncidentOrchestrator
    ↓
Frontend: SecurityEvents view updates in real-time
  - New event visible in event table
  - Severity badge colored (Critical=red, High=orange, etc)
  - Sparkline updated
    ↓
User clicks "Analyze" → SecurityAnalysis view
  - AI generates RCA & remediation
  - User can approve/dismiss
```

### C. Incident Lifecycle Flow

```
1. DETECTION
   Anomaly detected (bandwidth drop, CPU spike, etc)
      ↓
   IncidentOrchestrator.detect_anomaly()
      ↓
   Create INCIDENTS record with status='open'
      ↓
   POST /api/v1/incidents/ingest

2. INVESTIGATION
   Frontend: IncidentDashboard shows new incident
      ↓
   User clicks to view full report
      ↓
   GET /api/v1/incidents/{incident_id}
      ↓
   Display: Anomaly, Collected Context, RCA, Proposed Solution
      ↓
   Update status to 'investigating'

3. APPROVAL
   User reviews AI-generated remediation playbook
      ↓
   User clicks "Approve"
      ↓
   POST /api/v1/incidents/{incident_id}/approve
      ↓
   Update status to 'approved'
      ↓
   Playbook ready for execution

4. EXECUTION
   User executes approved playbook
      ↓
   POST /api/v1/incidents/{incident_id}/execute
      ↓
   IncidentOrchestrator.execute_remediation()
      ↓
   Deploy Ansible playbook to affected devices
      ↓
   Monitor progress

5. RESOLUTION
   After successful remediation:
      ↓
   POST /api/v1/incidents/{incident_id}/resolve
      ↓
   Update status to 'resolved'
      ↓
   Set resolved_at timestamp
      ↓
   Incident removed from "Open" view
```

---

## 8. KEY INTEGRATION POINTS

### A. Frontend Views That Reference Features

| View | Compliance | Security | Incidents | Dashboard |
|------|-----------|----------|-----------|-----------|
| **OverviewDashboard** | ✓ (summary) | ✓ (avg score) | ✓ (open count) | ✓ (main) |
| **ComplianceHubView** | ✓ (main) | - | - | - |
| **SecurityDashboard** | - | ✓ (main) | - | - |
| **SecurityEvents** | - | ✓ (events) | - | - |
| **SecurityAnalysis** | - | ✓ (analysis) | - | - |
| **IncidentDashboard** | - | - | ✓ (main) | - |
| **DriftView** | ✓ (drift) | - | - | - |
| **PolicyBuilder** | ✓ (policies) | - | - | - |

### B. Backend Integration Points

| Component | Compliance | Security | Incidents |
|-----------|-----------|----------|-----------|
| **Database** | ✓ | ✓ | ✓ |
| **Scheduler** | ✓ (job_compliance_scan) | ✓ (job_security_audit) | ✓ (via orchestrator) |
| **RAG/Context** | ✓ | ✓ | ✓ |
| **Network Tools** | ✓ | ✓ | ✓ |
| **Monitor/Threshold** | - | ✓ | ✓ (triggers incidents) |
| **Ansible** | - | - | ✓ (playbook execution) |

### C. Navigation & Routing

**Sidebar Menu Structure:**
```
Network Domain
  ├── Topology
  ├── Configuration
  ├── Performance
  └── Network Copilot

Security Domain
  ├── SIEM & Events       [SecurityEvents view]
  ├── Threat Analysis     [SecurityAnalysis view]
  └── Security Copilot    [GeminiChat domain="security"]

Main Menu
  ├── Dashboard           [OverviewDashboard]
  ├── Compliance Hub      [ComplianceHubView]
  ├── Incidents           [IncidentDashboard]
  ├── Drift Detection     [DriftView]
  ├── Policies            [PolicyBuilder]
  └── ...
```

---

## 9. SCHEDULER JOBS

### File: `backend/scheduler/jobs.py`

```python
@scheduler.task("0 2 * * *")  # Daily at 2 AM
async def job_compliance_scan():
  """Run compliance scan on all devices."""
  db = DatabaseManager()
  policies = db.list_policies(enabled=True)
  
  for policy in policies:
    targets = scope_resolver.resolve(policy['scope'])
    for device_id in targets:
      result = evaluator.evaluate(policy, device_id)
      db.save_compliance_result(result)
      if result.non_compliant:
        alert_system(f"Device {device_id} violates policy {policy['name']}")

@scheduler.task("0 3 * * *")  # Daily at 3 AM
def job_security_audit():
  """Run security audits on all devices."""
  db = DatabaseManager()
  devices = db.list_devices()
  
  for device in devices:
    config = db.get_config(device['id'])
    audit = engine.run_audit(device['id'], config, device['vendor'])
    db.save_audit(audit)
    if audit.score < 70:
      alert_system(f"Device {device['hostname']} has low security score: {audit.score}")
```

---

## 10. DASHBOARD SUMMARY COMPOSITION

### Backend: `backend/dashboard/api.py` — `GET /dashboard/summary`

Returns:
```json
{
  "devices": {
    "total": 42,
    "up": 38,
    "down": 2,
    "unknown": 2
  },
  "security": {
    "avg_score": 82.5,
    "total_audits": 42,
    "critical_events": 3
  },
  "drift": {
    "total_events": 18,
    "devices_with_drift": 7,
    "compliance_pct": 78.3
  },
  "deployments": {
    "total_jobs": 156,
    "recent_7d": 12,
    "last_status": "success"
  },
  "recent_events": [
    {
      "id": 1,
      "event_type": "port_scan",
      "severity": "high",
      "description": "Port scan from external IP",
      "created_at": "2024-11-20T10:30:00Z"
    }
  ],
  "recent_audit_log": [
    {
      "id": 1,
      "action": "policy_updated",
      "target": "CIS-Baseline-v2.1",
      "details": "Added new rule for NTP",
      "created_at": "2024-11-20T09:15:00Z"
    }
  ]
}
```

---

## 11. DATABASE QUERY PATTERNS

### Common Queries Used

```sql
-- Recent security events
SELECT * FROM security_events 
ORDER BY created_at DESC 
LIMIT 10

-- Compliance status by device
SELECT DISTINCT 
  cr.device_id, 
  d.hostname,
  cr.compliance_status,
  cr.compliance_score
FROM COMPLIANCE_RESULTS cr
JOIN DEVICES d ON cr.device_id = d.id
ORDER BY cr.checked_at DESC

-- Open incidents
SELECT COUNT(*) as n 
FROM incidents 
WHERE status='open'

-- Security audit trends
SELECT 
  DATE(created_at) as date,
  AVG(score) as avg_score,
  MIN(score) as min_score,
  MAX(score) as max_score
FROM security_audits
GROUP BY DATE(created_at)
ORDER BY date DESC

-- Devices with drift
SELECT 
  d.hostname,
  cr.drift_pct,
  cr.compliance_score
FROM COMPLIANCE_RESULTS cr
JOIN DEVICES d ON cr.device_id = d.id
WHERE cr.compliance_status = 'drifted'
ORDER BY cr.drift_pct DESC
```

---

## 12. STATE MANAGEMENT (Frontend)

### Context & Hooks Used

```typescript
// NotificationContext
useNotification() → { addNotification }

// Navigation
- complianceNavigationKeys — Tab routing in ComplianceHub
- COMPLIANCE_HUB_TAB_KEY — localStorage key for current tab

// Local State
- useMemo/useState for filtering, sorting
- localStorage for persistence (currentView, theme, etc)
```

---

## 13. THIRD-PARTY INTEGRATIONS

### External Systems Referenced

1. **NetBox** (Device Inventory)
   - `netboxApi.getDevices()`
   - Used for device metadata in compliance/security results

2. **Elasticsearch/SIEM** (Security Events)
   - Referenced in SecurityEvents view: "Elasticsearch / SIEM Integration"
   - Lucene syntax search for logs

3. **Google Gemini API** (AI Analysis)
   - SecurityAnalysis uses `GoogleGenAI` for threat analysis
   - Generates structured JSON: summary, containment, remediation, escalation

4. **Ansible** (Playbook Execution)
   - Incidents can trigger Ansible playbook execution
   - `ansibleApi.executeJob(jobId)`

5. **Suricata IDS**
   - `security/threat/suricata.py` — Parse Suricata alerts
   - Referenced in threat detection

---

## 14. FILE REFERENCE GUIDE

### Frontend

| Feature | Main File | Supporting Components | API Service |
|---------|-----------|----------------------|-------------|
| **Compliance** | `ComplianceHubView.tsx` | `compliance/ComplianceOverviewCards.tsx`, `PolicyBuilder.tsx`, `ComplianceDeviceDetail.tsx` | `complianceApi`, `policyApi` |
| **Security Events** | `SecurityEvents.tsx` | `Sidebar.tsx` (menu), `Header.tsx` | `securityApi` |
| **Security Analysis** | `SecurityAnalysis.tsx` | `NotificationContext.tsx` | `securityApi`, `llmApi` |
| **Incidents** | `IncidentDashboard.tsx` | `IncidentDashboard.tsx` | `incidentApi` |
| **Dashboard** | `OverviewDashboard.tsx` | `Dashboard.tsx` | `dashboardApi` |
| **Drift Detection** | `DriftView.tsx` | `ComplianceDeviceDetail.tsx` | `complianceApi`, `netboxApi` |

### Backend

| Feature | Main File | Core Classes | Database Tables |
|---------|-----------|----------------|-----------------|
| **Compliance** | `backend/compliance/api.py` | `ComplianceEvaluator`, `PolicyService`, `NetBoxScopeResolver` | `POLICIES`, `COMPLIANCE_JOBS`, `COMPLIANCE_RESULTS` |
| **Security** | `backend/security/api.py` | `SecurityAuditEngine`, `AnomalyDetector`, `IncidentResponder` | `SECURITY_AUDITS`, `AUDIT_FINDINGS`, `SECURITY_EVENTS` |
| **Incidents** | `backend/incidents/api.py` | `IncidentStore`, `IncidentOrchestrator`, (models) | `INCIDENTS`, `DRIFT_EVENTS` |
| **Scheduler** | `backend/scheduler/jobs.py` | Async task runners | All tables |

---

## 15. SUMMARY: KEY TAKEAWAYS

### What's Integrated

✅ **Compliance Module (M26)**
- Policy definition, evaluation, and compliance scanning
- Results tracking with baseline comparisons
- Device scope resolution (vendors, roles, groups)
- Compliance alerts with acknowledgment workflow

✅ **Security Module (M13)**
- Device security audits with CIS-like checks
- Security event ingestion (syslog, IDS, anomaly detectors)
- Incident response playbook generation
- CVE correlation with device inventory

✅ **Incidents Module (M25)**
- Anomaly detection → incident creation
- Root cause analysis via AI
- Proposed remediation playbooks
- Lifecycle management: open → investigating → approved → resolved/dismissed
- Auto-remediation with Ansible integration

✅ **Dashboard Integration**
- System-wide summary with key metrics
- Recent events & audit log display
- Cross-domain visibility (network, security, compliance)

### How to Extend

1. **Add New Security Check**: Create check function in `backend/security/checks/`, register in `rules.py`
2. **Create New Policy**: Use PolicyBuilder UI → POST `/api/v1/policies`
3. **Ingest Custom Events**: POST `/api/v1/security/events` from external system
4. **Trigger Incident**: POST `/api/v1/incidents/ingest` with anomaly data
5. **Add Dashboard Metric**: Update `backend/dashboard/api.py` → `DashboardSummary` response

---

**Last Updated**: 2024-11-20  
**Scope**: Complete feature integration analysis for Compliance, Security, Incidents, and Dashboard
