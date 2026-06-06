# NetTopoView - ERD Implementation Guide

## Document Overview

This guide provides a complete roadmap for implementing the NetTopoView Entity Relationship Diagram (ERD) based on your current system architecture.

---

## Quick Start

### Files Generated
1. **[DATABASE_ERD.md](DATABASE_ERD.md)** - Complete ERD with all 28+ tables, attributes, and relationships
2. **[PROMPTS_TABLE_SCHEMA.md](PROMPTS_TABLE_SCHEMA.md)** - Detailed schema for prompt management system
3. **[create_database.sql](create_database.sql)** - SQLite initialization script
4. **[ERD_IMPLEMENTATION_GUIDE.md](ERD_IMPLEMENTATION_GUIDE.md)** - This file

### Initialize Database

```bash
# Using SQLite CLI
sqlite3 nettopotview.db < docs/create_database.sql

# Or using Python
import sqlite3
conn = sqlite3.connect('nettopotview.db')
cursor = conn.cursor()
with open('docs/create_database.sql', 'r') as f:
    cursor.executescript(f.read())
conn.commit()
```

---

## System Architecture Overview

### Domain Organization

#### 1. **Core Inventory** (Foundational)
- USERS — Authentication & Authorization
- DEVICES — Network device inventory
- INTERFACES — Device ports/connections
- LINKS — Topology edges (LLDP/CDP/manual)

**Purpose**: Maintain authoritative network topology and user access control

---

#### 2. **Configuration Management** (Core Operations)
- CONFIG_SNAPSHOTS — Configuration backups with metadata
- INTENTS — High-level user requests
- CHANGE_REQUESTS — Logical grouping of changes
- JOBS — Device-level deployment execution
- JOB_STEPS — Granular execution logs
- CONFIG_PROPOSALS — AI-generated configurations

**Purpose**: Track configuration changes from intent to deployment

**Workflow**:
```
USER REQUEST → INTENT → CHANGE_REQUEST → JOBS → JOB_STEPS
                                   ↓
                          CONFIG_PROPOSALS
```

---

#### 3. **Compliance & Drift** (Assurance)
- POLICIES — Compliance rules with scope
- COMPLIANCE_JOBS — Policy scan executions
- COMPLIANCE_RESULTS — Per-device compliance status
- DRIFT_EVENTS — Configuration change detection
- POLICY_AUDIT_LOG — Policy modification history

**Purpose**: Ensure network compliance and detect unauthorized changes

**Workflow**:
```
POLICIES → COMPLIANCE_JOBS → COMPLIANCE_RESULTS
                                    ↓
                            DRIFT_EVENTS (if drift detected)
```

---

#### 4. **Security** (Protection & Monitoring)
- SECURITY_AUDITS — CIS benchmarks & vulnerability scans
- AUDIT_FINDINGS — Individual security check results
- SECURITY_EVENTS — Security alerts (intrusions, anomalies)

**Purpose**: Security monitoring and incident detection

**Workflow**:
```
SECURITY_AUDITS → AUDIT_FINDINGS → SECURITY_EVENTS
```

---

#### 5. **Credentials & Secrets** (Protection)
- DEVICE_CREDENTIALS — Encrypted per-device credentials

**Purpose**: Secure credential storage with encryption

---

#### 6. **Onboarding & ZTP** (Provisioning)
- ZTP_PUBLISH_JOBS — Zero-touch provisioning job tracking
- ZTP_PUBLISH_ITEMS — Per-item ZTP publication
- ZTP_DHCP_MAPPINGS — MAC→boot file mappings

**Purpose**: Automated device onboarding

---

#### 7. **Agent & Advanced Features** (Intelligence)
- AGENT_PLANS — AI agent workflow state
- CONTEXT_EVENT_LOG — Context refresh events
- ANSIBLE_CHAT_SESSIONS — Chat history for automation
- SDN_CONTROLLERS — SDN controller integration
- COMMON_SERVICE_PROFILES — Shared service configurations

**Purpose**: AI-driven automation and intelligent operations

---

#### 8. **Prompts Management** (AI Operations)
- PROMPTS — AI prompt storage & versioning
- PROMPT_VERSIONS — Prompt history
- PROMPT_EXECUTION_HISTORY — Execution tracking
- PROMPT_TEMPLATES — Reusable templates
- PROMPT_CHAINS — Complex multi-step workflows
- PROMPT_CHAIN_EXECUTIONS — Execution history for chains
- PROMPT_FEEDBACK — User feedback & ratings
- PROMPT_PERFORMANCE_METRICS — Analytics

**Purpose**: Manage, version, and optimize AI prompts

---

## Table Dependency Graph

```
USERS
├── DEVICE_CREDENTIALS (n:1)
├── CHANGE_REQUESTS (n:1)
├── CONFIG_PROPOSALS (n:1 - approval)
├── POLICIES (n:1 - created_by)
├── INTENTS (n:1)
├── AGENT_PLANS (indirect)
├── ZTP_PUBLISH_JOBS (n:1)
└── PROMPTS (n:1 - created_by)

DEVICES
├── INTERFACES (1:n)
├── CONFIG_SNAPSHOTS (1:n)
├── CHANGE_REQUESTS (n:1)
├── DRIFT_EVENTS (1:n)
├── JOBS (1:n)
├── COMPLIANCE_RESULTS (1:n)
├── SECURITY_AUDITS (1:n)
├── DEVICE_CREDENTIALS (1:n)
└── COMMON_SERVICE_PROFILES (n:1)

INTERFACES
├── LINKS (participates_in)
└── CONFIG_SNAPSHOTS (may reference)

INTENTS
├── CHANGE_REQUESTS (1:n)
└── AGENT_PLANS (1:n)

CHANGE_REQUESTS
└── JOBS (1:n)

JOBS
├── JOB_STEPS (1:n)
└── CONFIG_PROPOSALS (1:n)

CONFIG_SNAPSHOTS
├── CONFIG_PROPOSALS (1:n)
├── DRIFT_EVENTS (comparison)
└── COMPLIANCE_RESULTS (evaluation)

POLICIES
├── COMPLIANCE_JOBS (1:n)
├── COMPLIANCE_RESULTS (1:n)
└── POLICY_AUDIT_LOG (1:n)

COMPLIANCE_JOBS
└── COMPLIANCE_RESULTS (1:n)

COMPLIANCE_RESULTS
└── DRIFT_EVENTS (may trigger)

SECURITY_AUDITS
└── AUDIT_FINDINGS (1:n)

AUDIT_FINDINGS
└── SECURITY_EVENTS (1:n)

AGENT_PLANS
├── CONTEXT_EVENT_LOG (1:n)
└── ANSIBLE_CHAT_SESSIONS (1:n)

ANSIBLE_CHAT_SESSIONS
└── ANSIBLE_CHAT_MESSAGES (1:n)

ZTP_PUBLISH_JOBS
└── ZTP_PUBLISH_ITEMS (1:n)

ZTP_PUBLISH_ITEMS
└── ZTP_DHCP_MAPPINGS (1:n)

PROMPTS
├── PROMPT_VERSIONS (1:n)
├── PROMPT_EXECUTION_HISTORY (1:n)
├── PROMPT_FEEDBACK (1:n)
└── PROMPT_PERFORMANCE_METRICS (1:n)

PROMPT_CHAINS
├── PROMPT_CHAIN_EXECUTIONS (1:n)
└── (references PROMPTS in JSON)
```

---

## Implementation Phases

### Phase 1: Core Infrastructure (Week 1-2)
**Priority**: CRITICAL

Implement these tables first as they form the foundation:

1. ✅ USERS
2. ✅ DEVICES
3. ✅ INTERFACES
4. ✅ LINKS
5. ✅ CONFIG_SNAPSHOTS

**Deliverables**:
- Database initialization script
- ORM models (SQLAlchemy)
- CRUD API endpoints
- Basic UI for device inventory

---

### Phase 2: Configuration Management (Week 3-4)
**Priority**: HIGH

Build change management workflow:

6. ✅ INTENTS
7. ✅ CHANGE_REQUESTS
8. ✅ JOBS
9. ✅ JOB_STEPS
10. ✅ CONFIG_PROPOSALS

**Deliverables**:
- Change request creation & approval workflow
- Job execution engine
- Configuration diff/proposal UI
- Deployment tracking dashboard

---

### Phase 3: Compliance & Drift (Week 5-6)
**Priority**: HIGH

Add compliance monitoring:

11. ✅ POLICIES
12. ✅ COMPLIANCE_JOBS
13. ✅ COMPLIANCE_RESULTS
14. ✅ DRIFT_EVENTS
15. ✅ POLICY_AUDIT_LOG

**Deliverables**:
- Policy definition & management UI
- Compliance scanning job scheduler
- Drift detection alerts
- Compliance dashboard with metrics

---

### Phase 4: Security (Week 7-8)
**Priority**: MEDIUM-HIGH

Implement security monitoring:

16. ✅ DEVICE_CREDENTIALS
17. ✅ SECURITY_AUDITS
18. ✅ AUDIT_FINDINGS
19. ✅ SECURITY_EVENTS

**Deliverables**:
- Credential encryption & rotation
- Security audit framework integration
- Security event alerting
- Audit findings dashboard

---

### Phase 5: Onboarding & ZTP (Week 9-10)
**Priority**: MEDIUM

Add provisioning capabilities:

20. ✅ ZTP_PUBLISH_JOBS
21. ✅ ZTP_PUBLISH_ITEMS
22. ✅ ZTP_DHCP_MAPPINGS

**Deliverables**:
- ZTP template management
- DHCP mapping UI
- Onboarding workflow automation
- Provisioning status tracking

---

### Phase 6: Advanced Features (Week 11-12)
**Priority**: MEDIUM

Implement AI & advanced features:

23. ✅ AGENT_PLANS
24. ✅ CONTEXT_EVENT_LOG
25. ✅ ANSIBLE_CHAT_SESSIONS
26. ✅ ANSIBLE_CHAT_MESSAGES
27. ✅ SDN_CONTROLLERS
28. ✅ COMMON_SERVICE_PROFILES

**Deliverables**:
- Agent plan execution & tracking
- Chat session UI
- SDN controller integration
- Service profile templates

---

### Phase 7: Prompts Management (Week 13-14)
**Priority**: MEDIUM

Add prompt management:

29. ✅ PROMPTS
30. ✅ PROMPT_VERSIONS
31. ✅ PROMPT_EXECUTION_HISTORY
32. ✅ PROMPT_TEMPLATES
33. ✅ PROMPT_CHAINS
34. ✅ PROMPT_CHAIN_EXECUTIONS
35. ✅ PROMPT_FEEDBACK
36. ✅ PROMPT_PERFORMANCE_METRICS

**Deliverables**:
- Prompt management UI
- Version control interface
- Execution history & analytics
- Performance optimization dashboard

---

## Technology Stack Recommendations

### Backend
```python
# ORM Layer
from sqlalchemy import create_engine, Column, ForeignKey, String, Integer, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

Base = declarative_base()

class Device(Base):
    __tablename__ = 'DEVICES'
    device_id = Column(String(36), primary_key=True)
    hostname = Column(String(255), unique=True, nullable=False)
    # ... other columns
    interfaces = relationship("Interface", back_populates="device")
    configs = relationship("ConfigSnapshot", back_populates="device")
```

### Database
- **SQLite** for development/testing
- **PostgreSQL** for production (better concurrency, JSONB support)

### Migration Tool
```bash
# Use Alembic for schema versioning
alembic init migrations
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

---

## Key Features by Table

### DEVICES Table
- **Unique tracking** by hostname
- **Status monitoring** (active, inactive, maintenance)
- **Last seen timestamp** for availability tracking
- **Indexes** on hostname, status, vendor for fast querying

### CONFIG_SNAPSHOTS Table
- **Deduplication** via config_hash
- **Configuration types** (running, startup, golden, baseline)
- **Compliance metadata** for policy evaluation
- **Full text search** capability

### JOBS Table
- **Multi-stage execution** (plan, apply, verify, commit)
- **Retry logic** support
- **Rollback capability** tracking
- **Per-device granularity** for parallel execution

### COMPLIANCE_RESULTS Table
- **Per-device status** per policy per execution
- **Violation tracking** with JSON details
- **Remediation workflow** support

### PROMPTS Table
- **Versioning** for prompt evolution
- **Template variables** for reusability
- **Model compatibility** matrix
- **Usage tracking** and analytics

---

## API Endpoint Patterns

### Device Management
```
GET    /api/devices                    - List all devices
GET    /api/devices/{device_id}        - Get device details
POST   /api/devices                    - Create device
PUT    /api/devices/{device_id}        - Update device
DELETE /api/devices/{device_id}        - Delete device

GET    /api/devices/{device_id}/interfaces
GET    /api/devices/{device_id}/configs
GET    /api/devices/{device_id}/compliance
GET    /api/devices/{device_id}/drift
```

### Configuration Management
```
POST   /api/change-requests            - Create change request
GET    /api/change-requests/{change_id} - Get details
PUT    /api/change-requests/{change_id} - Update status
GET    /api/change-requests/{change_id}/jobs - List jobs

POST   /api/jobs/{job_id}/execute      - Execute job
GET    /api/jobs/{job_id}/steps        - List execution steps
GET    /api/jobs/{job_id}/status       - Get job status
```

### Compliance
```
GET    /api/policies                   - List policies
POST   /api/policies                   - Create policy
POST   /api/compliance/scan            - Trigger compliance scan
GET    /api/compliance/results         - Get compliance results
GET    /api/drift-events               - List drift events
```

### Prompts
```
GET    /api/prompts                    - List prompts
POST   /api/prompts                    - Create prompt
PUT    /api/prompts/{prompt_id}        - Update prompt
GET    /api/prompts/{prompt_id}/versions - Version history
POST   /api/prompts/{prompt_id}/execute - Execute prompt
GET    /api/prompts/{prompt_id}/metrics - Performance metrics
```

---

## Data Validation Rules

### Device Fields
- `hostname`: 1-255 characters, alphanumeric + hyphens, unique
- `ip_address`: Valid IPv4 or IPv6 format
- `status`: One of ('active', 'inactive', 'maintenance')
- `device_type`: Predefined list ('router', 'switch', 'firewall', etc.)

### Config Snapshots
- `config_content`: Not null, max 10MB per SQLite limits
- `config_type`: One of ('running', 'startup', 'golden', 'baseline')
- `device_id`: Must exist in DEVICES

### Jobs
- `status`: One of ('pending', 'running', 'completed', 'failed', 'rolled_back')
- `priority`: 1-1000 (higher = more important)
- `execution_stage`: One of ('plan', 'apply', 'verify', 'commit')

### Compliance Results
- `compliance_status`: One of ('compliant', 'non-compliant', 'error', 'unknown')
- `violation_count`: >= 0
- `checked_at`: Recent timestamp (not future-dated)

---

## Performance Optimization

### Indexing Strategy

**Primary Indexes** (Always Create):
- Foreign key columns
- Status/state columns
- Timestamp columns for range queries
- Unique constraint columns

**Secondary Indexes** (Create as needed):
```sql
CREATE INDEX idx_devices_status_site ON DEVICES(status, site_location);
CREATE INDEX idx_jobs_device_status ON JOBS(device_id, status);
CREATE INDEX idx_compliance_policy_device ON COMPLIANCE_RESULTS(policy_id, device_id);
```

### Query Optimization

**Use pagination for large result sets:**
```sql
SELECT * FROM DEVICES 
ORDER BY created_at DESC 
LIMIT 50 OFFSET 0;
```

**Denormalize metrics for analytics:**
```sql
-- Create materialized view for dashboard
CREATE VIEW device_metrics AS
SELECT 
    d.device_id,
    d.hostname,
    COUNT(DISTINCT i.interface_id) as interface_count,
    COUNT(DISTINCT cs.snapshot_id) as config_count,
    COUNT(DISTINCT j.job_id) as job_count
FROM DEVICES d
LEFT JOIN INTERFACES i ON d.device_id = i.device_id
LEFT JOIN CONFIG_SNAPSHOTS cs ON d.device_id = cs.device_id
LEFT JOIN JOBS j ON d.device_id = j.device_id
GROUP BY d.device_id;
```

### Partitioning Strategy

For high-volume tables (optional for PostgreSQL):
```sql
-- Partition DRIFT_EVENTS by month
CREATE TABLE drift_events_2024_01 PARTITION OF DRIFT_EVENTS
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

---

## Backup & Recovery

### Backup Strategy

```bash
# Daily backup
sqlite3 nettopotview.db ".backup nettopotview_$(date +%Y%m%d).db"

# Or with pg_dump for PostgreSQL
pg_dump -U postgres nettopotview > backup_$(date +%Y%m%d).sql
```

### Point-in-Time Recovery

- Keep transaction logs
- Maintain WAL (Write-Ahead Logs) for PostgreSQL
- Test recovery procedures quarterly

---

## Monitoring & Maintenance

### Key Metrics to Track
- Database size growth
- Query performance (slow query logs)
- Connection pool utilization
- Table row counts by domain
- Index efficiency (index unused indexes)

### Regular Maintenance Tasks

```sql
-- Vacuum and analyze (SQLite)
VACUUM;
ANALYZE;

-- Or for PostgreSQL
VACUUM ANALYZE;
REINDEX;
```

---

## Security Considerations

### Access Control
- Role-based access via USERS.role column
- API middleware checks permissions before operations
- Audit trail via *_AUDIT_LOG tables

### Data Protection
- Encrypt DEVICE_CREDENTIALS at rest
- Encrypt in transit (HTTPS)
- Sanitize all user inputs
- Use parameterized queries (SQLAlchemy ORM handles this)

### Sensitive Data
- Password fields use SHA256 hashing
- API keys stored hashed
- SNMP/SSH credentials encrypted with AES-256
- Never log actual credentials

---

## Testing Strategy

### Unit Tests
```python
def test_device_creation():
    device = Device(device_id="123", hostname="router-1", device_type="router")
    db.session.add(device)
    db.session.commit()
    assert Device.query.filter_by(hostname="router-1").first() is not None

def test_invalid_status():
    device = Device(hostname="router-2", status="invalid")
    with pytest.raises(ValueError):
        device.validate()
```

### Integration Tests
```python
def test_change_request_workflow():
    # Create intent → change request → jobs → job steps
    intent = create_intent(...)
    change = create_change_request(intent_id=intent.intent_id)
    jobs = create_jobs(change_id=change.change_id)
    # Assert workflow state transitions
```

### Data Validation Tests
- Constraint validation
- Referential integrity
- Cascade delete behavior
- Unique constraint enforcement

---

## Next Steps

1. **Review ERD documents** for alignment with your architecture
2. **Create database** using `create_database.sql`
3. **Implement ORM models** for each table
4. **Build API layer** with CRUD operations
5. **Create UI components** for data management
6. **Add business logic** for workflows
7. **Implement security** (auth, encryption, audit)
8. **Test thoroughly** (unit, integration, performance)
9. **Monitor production** with metrics & alerts
10. **Optimize based on** usage patterns

---

## References

- [Database_ERD.md](DATABASE_ERD.md) - Full table definitions
- [PROMPTS_TABLE_SCHEMA.md](PROMPTS_TABLE_SCHEMA.md) - Prompt management details
- [create_database.sql](create_database.sql) - SQL initialization script
- SQLAlchemy Docs: https://docs.sqlalchemy.org/
- SQLite Docs: https://www.sqlite.org/docs.html
- PostgreSQL Docs: https://www.postgresql.org/docs/

---

## Support

For questions or clarifications on the ERD:
1. Check the specific table documentation in DATABASE_ERD.md
2. Review the schema visualization in the Mermaid diagram
3. Refer to query examples in the relevant section
4. Consult the implementation guide for your specific use case
