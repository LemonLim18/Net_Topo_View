# NetTopoView ERD - Quick Reference Summary

## Overview

Complete Entity Relationship Diagram (ERD) for NetTopoView based on your current implementation, including all 36+ tables with proper attributes, primary/foreign keys, and relationships aligned with SQLite.

---

## Document Catalog

| Document | Purpose | Key Content |
|----------|---------|-------------|
| [DATABASE_ERD.md](DATABASE_ERD.md) | Complete Schema Reference | All 28 core tables with full SQL definitions, attributes, constraints, indexes |
| [PROMPTS_TABLE_SCHEMA.md](PROMPTS_TABLE_SCHEMA.md) | Prompt Management System | 8 prompt-related tables for AI prompt versioning & execution tracking |
| [create_database.sql](create_database.sql) | Database Initialization | Complete SQLite SQL script - ready to run |
| [ERD_IMPLEMENTATION_GUIDE.md](ERD_IMPLEMENTATION_GUIDE.md) | Implementation Roadmap | 7-phase implementation plan, architecture, API patterns, best practices |
| [ERD_QUICK_REFERENCE.md](ERD_QUICK_REFERENCE.md) | This File | Summary table counts, domains, and key relationships |

---

## Table Count by Domain

| Domain | Count | Tables |
|--------|-------|--------|
| **Authentication** | 1 | USERS |
| **Inventory** | 4 | DEVICES, INTERFACES, LINKS, (COMMON_SERVICE_PROFILES) |
| **Configuration** | 6 | CONFIG_SNAPSHOTS, INTENTS, CHANGE_REQUESTS, JOBS, JOB_STEPS, CONFIG_PROPOSALS |
| **Compliance** | 5 | POLICIES, COMPLIANCE_JOBS, COMPLIANCE_RESULTS, DRIFT_EVENTS, POLICY_AUDIT_LOG |
| **Security** | 3 | SECURITY_AUDITS, AUDIT_FINDINGS, SECURITY_EVENTS |
| **Credentials** | 1 | DEVICE_CREDENTIALS |
| **Onboarding** | 3 | ZTP_PUBLISH_JOBS, ZTP_PUBLISH_ITEMS, ZTP_DHCP_MAPPINGS |
| **Agent & Advanced** | 5 | AGENT_PLANS, CONTEXT_EVENT_LOG, ANSIBLE_CHAT_SESSIONS, ANSIBLE_CHAT_MESSAGES, SDN_CONTROLLERS |
| **Prompts** | 8 | PROMPTS, PROMPT_VERSIONS, PROMPT_EXECUTION_HISTORY, PROMPT_TEMPLATES, PROMPT_CHAINS, PROMPT_CHAIN_EXECUTIONS, PROMPT_FEEDBACK, PROMPT_PERFORMANCE_METRICS |
| **TOTAL** | **36+** | **All tables across all domains** |

---

## Core Table Relationships

### Master-Detail Relationships (1:n)

```
USERS
  → DEVICE_CREDENTIALS (manage device secrets)
  → CHANGE_REQUESTS (create changes)
  → INTENTS (request operations)
  → PROMPTS (create prompts)

DEVICES
  → INTERFACES (has ports)
  → CONFIG_SNAPSHOTS (stores configurations)
  → JOBS (scheduled for changes)
  → COMPLIANCE_RESULTS (evaluated against policies)
  → DRIFT_EVENTS (detected on)
  → SECURITY_AUDITS (scanned for vulnerabilities)

INTENTS
  → CHANGE_REQUESTS (drives)
  → AGENT_PLANS (creates execution plan)

CHANGE_REQUESTS
  → JOBS (groups device-level work)

JOBS
  → JOB_STEPS (contains execution steps)
  → CONFIG_PROPOSALS (generates)

POLICIES
  → COMPLIANCE_JOBS (evaluated via)
  → COMPLIANCE_RESULTS (associated with)
  → POLICY_AUDIT_LOG (modification history)

SECURITY_AUDITS
  → AUDIT_FINDINGS (contains security checks)

AUDIT_FINDINGS
  → SECURITY_EVENTS (may trigger alerts)

ZTP_PUBLISH_JOBS
  → ZTP_PUBLISH_ITEMS (contains items)

ZTP_PUBLISH_ITEMS
  → ZTP_DHCP_MAPPINGS (maps MAC addresses)

PROMPTS
  → PROMPT_VERSIONS (tracks history)
  → PROMPT_EXECUTION_HISTORY (tracks executions)
  → PROMPT_FEEDBACK (user feedback)
  → PROMPT_PERFORMANCE_METRICS (analytics)

PROMPT_CHAINS
  → PROMPT_CHAIN_EXECUTIONS (execution history)
```

---

## Key Attributes by Table Type

### Primary Key Strategy
- **All tables**: UUID v4 (TEXT in SQLite) for distributed compatibility
- **No auto-increment**: Ensures consistency across replicas

### Foreign Key Strategy
- **ON DELETE CASCADE**: Child tables (INTERFACES, JOB_STEPS, etc.)
- **ON DELETE SET NULL**: Optional references (approved_by_user_id, finding_id)
- **No direct delete**: For integrity, use soft-delete pattern where needed

### Status/State Tracking
- All major tables have **status** field with CHECK constraints
- Examples:
  - DEVICES: `'active'|'inactive'|'maintenance'`
  - JOBS: `'pending'|'running'|'completed'|'failed'|'rolled_back'`
  - COMPLIANCE_RESULTS: `'compliant'|'non-compliant'|'error'|'unknown'`
  - PROMPTS: `is_active BOOLEAN` (soft delete)

### Timestamp Tracking
- **created_at**: When record created (DEFAULT CURRENT_TIMESTAMP)
- **updated_at**: Last modification (DEFAULT CURRENT_TIMESTAMP, update on change)
- **started_at/completed_at**: For time-bound operations
- **last_activity_at**: For session-like entities

### Indexing Standards

**Always Indexed**:
- Primary keys
- Foreign keys
- status/state columns
- created_at/updated_at (for sorting)
- Unique columns

**Conditional Indexes**:
- High-cardinality columns in WHERE clauses
- Multi-column indexes for frequent filter combinations
- Example: `CREATE INDEX idx_jobs_device_status ON JOBS(device_id, status);`

---

## Implementation Checklist

### Phase 1: Foundation (Critical Path)
- [ ] Create USERS table
- [ ] Create DEVICES, INTERFACES, LINKS
- [ ] Create CONFIG_SNAPSHOTS
- [ ] Build ORM models
- [ ] Create CRUD API endpoints
- [ ] Verify referential integrity

### Phase 2: Change Management
- [ ] Create INTENTS, CHANGE_REQUESTS
- [ ] Create JOBS, JOB_STEPS
- [ ] Create CONFIG_PROPOSALS
- [ ] Implement workflow state machine
- [ ] Build deployment tracking UI

### Phase 3: Compliance
- [ ] Create POLICIES, COMPLIANCE_JOBS
- [ ] Create COMPLIANCE_RESULTS, DRIFT_EVENTS
- [ ] Create POLICY_AUDIT_LOG
- [ ] Implement compliance scanner
- [ ] Build compliance dashboard

### Phase 4: Security
- [ ] Create DEVICE_CREDENTIALS (with encryption)
- [ ] Create SECURITY_AUDITS, AUDIT_FINDINGS
- [ ] Create SECURITY_EVENTS
- [ ] Integrate CIS framework
- [ ] Build security alerting

### Phase 5: Provisioning
- [ ] Create ZTP_PUBLISH_JOBS, ZTP_PUBLISH_ITEMS
- [ ] Create ZTP_DHCP_MAPPINGS
- [ ] Build ZTP workflow
- [ ] Integrate with DHCP server

### Phase 6: Advanced Features
- [ ] Create AGENT_PLANS, CONTEXT_EVENT_LOG
- [ ] Create ANSIBLE_CHAT_SESSIONS, ANSIBLE_CHAT_MESSAGES
- [ ] Create SDN_CONTROLLERS
- [ ] Create COMMON_SERVICE_PROFILES
- [ ] Implement agent execution engine

### Phase 7: Prompts System
- [ ] Create all PROMPTS_* tables
- [ ] Implement prompt versioning
- [ ] Build prompt execution engine
- [ ] Create prompt analytics dashboard
- [ ] Integrate with LLM pipeline

---

## Database Size Estimates

| Table | Estimated Rows (1 Year) | Size (SQLite) |
|-------|--------------------------|---------------|
| DEVICES | 500 | ~100KB |
| INTERFACES | 5,000 | ~800KB |
| CONFIG_SNAPSHOTS | 50,000 | ~500MB |
| JOBS | 100,000 | ~20MB |
| JOB_STEPS | 500,000 | ~50MB |
| COMPLIANCE_RESULTS | 50,000 | ~5MB |
| DRIFT_EVENTS | 10,000 | ~5MB |
| SECURITY_EVENTS | 20,000 | ~10MB |
| PROMPT_EXECUTION_HISTORY | 50,000 | ~50MB |
| **TOTAL** | **~800K rows** | **~650MB** |

*Note: Adjust based on your actual environment size. Consider PostgreSQL for 10GB+*

---

## SQL Quick Queries

### Inventory Status
```sql
SELECT 
    COUNT(*) as total_devices,
    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
    SUM(CASE WHEN status = 'inactive' THEN 1 ELSE 0 END) as inactive,
    SUM(CASE WHEN reachable = 1 THEN 1 ELSE 0 END) as reachable
FROM DEVICES;
```

### Recent Changes
```sql
SELECT 
    cr.change_id,
    cr.title,
    cr.status,
    COUNT(j.job_id) as job_count,
    cr.requested_at
FROM CHANGE_REQUESTS cr
LEFT JOIN JOBS j ON cr.change_id = j.change_id
WHERE cr.requested_at >= datetime('now', '-7 days')
GROUP BY cr.change_id
ORDER BY cr.requested_at DESC;
```

### Compliance Summary
```sql
SELECT 
    p.policy_name,
    COUNT(cr.result_id) as total_checks,
    SUM(CASE WHEN cr.compliance_status = 'compliant' THEN 1 ELSE 0 END) as compliant,
    ROUND(100.0 * SUM(CASE WHEN cr.compliance_status = 'compliant' THEN 1 ELSE 0 END) / 
          COUNT(cr.result_id), 2) as compliance_percent
FROM POLICIES p
LEFT JOIN COMPLIANCE_RESULTS cr ON p.policy_id = cr.policy_id
WHERE cr.checked_at >= datetime('now', '-30 days')
GROUP BY p.policy_id
ORDER BY compliance_percent;
```

### Security Alerts
```sql
SELECT 
    se.event_id,
    d.hostname,
    se.event_type,
    se.severity,
    se.detected_at
FROM SECURITY_EVENTS se
JOIN DEVICES d ON se.device_id = d.device_id
WHERE se.severity IN ('warning', 'critical')
  AND se.acknowledged = 0
  AND se.detected_at >= datetime('now', '-24 hours')
ORDER BY se.severity DESC, se.detected_at DESC;
```

---

## ORM Model Template (SQLAlchemy)

```python
from sqlalchemy import create_engine, Column, String, Text, Boolean, Integer, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()

class Device(Base):
    __tablename__ = 'DEVICES'
    
    device_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    hostname = Column(String(255), unique=True, nullable=False)
    device_type = Column(String(100))
    vendor = Column(String(100))
    model = Column(String(100))
    ip_address = Column(String(45))
    serial_number = Column(String(100))
    site_location = Column(String(255))
    status = Column(String(50), default='active')
    last_seen = Column(DateTime)
    reachable = Column(Boolean, default=False)
    os_version = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    interfaces = relationship("Interface", back_populates="device", cascade="all, delete-orphan")
    configs = relationship("ConfigSnapshot", back_populates="device", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="device")
    
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive', 'maintenance')"),
    )
```

---

## Performance Tuning Tips

### Query Optimization
1. **Always use indexes** on WHERE/JOIN/ORDER BY columns
2. **Prefer JOINs** over multiple queries
3. **Use LIMIT** for large result sets
4. **Avoid SELECT *** - name specific columns needed

### Connection Pooling
```python
from sqlalchemy import create_engine
engine = create_engine('sqlite:///nettopotview.db', 
    pool_pre_ping=True,  # Test connections before using
    pool_size=10,
    max_overflow=20)
```

### Batch Operations
```python
# Insert 1000s of records efficiently
devices = [Device(...) for _ in range(1000)]
session.bulk_insert_mappings(Device, devices)
session.commit()
```

---

## Database Migration Strategy

### Using Alembic
```bash
# Initialize migrations
alembic init migrations

# Create migration from current models
alembic revision --autogenerate -m "Add PROMPTS table"

# Apply migration
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Version Control
- Store `alembic/` directory in Git
- Track schema changes as commits
- Enable rollback/forward capability
- Document breaking changes

---

## Backup & Disaster Recovery

### Daily Backups
```bash
# SQLite backup
sqlite3 nettopotview.db ".backup backup_$(date +%Y%m%d).db"

# PostgreSQL backup
pg_dump -U postgres -Fc nettopotview > backup_$(date +%Y%m%d).dump
```

### Retention Policy
- Daily backups: Keep 7 days
- Weekly backups: Keep 4 weeks
- Monthly backups: Keep 12 months
- Test restoration quarterly

---

## Support & Documentation

### Related Documents
- [Full Database Schema](DATABASE_ERD.md)
- [Prompt System Details](PROMPTS_TABLE_SCHEMA.md)
- [SQL Initialization Script](create_database.sql)
- [Implementation Roadmap](ERD_IMPLEMENTATION_GUIDE.md)

### External Resources
- SQLAlchemy: https://docs.sqlalchemy.org/
- SQLite: https://www.sqlite.org/
- PostgreSQL: https://www.postgresql.org/docs/
- Database Design Best Practices: https://en.wikipedia.org/wiki/Database_normalization

---

## Alignment with Your Implementation

### Verified Against
✅ Backend module structure  
✅ Current data models  
✅ Frontend type definitions  
✅ API endpoint patterns  
✅ Existing database usage  
✅ Configuration management workflows  
✅ Compliance/drift detection logic  
✅ Security audit patterns  

### Ready for
✅ SQLite development database  
✅ PostgreSQL production database  
✅ ORM implementation (SQLAlchemy)  
✅ API development (FastAPI/Flask)  
✅ Migration scripts (Alembic)  
✅ Monitoring & analytics  

---

## Next Action Items

**Immediate** (This Week)
1. Review DATABASE_ERD.md for table definitions
2. Run create_database.sql to initialize database
3. Create ORM models based on schema

**Short-term** (Next 2 Weeks)
1. Implement Phase 1 tables & CRUD APIs
2. Build database initialization scripts
3. Create database monitoring dashboard

**Medium-term** (Next Month)
1. Complete Phase 2-7 implementation
2. Add comprehensive testing
3. Deploy to production with backups

---

**Last Updated**: May 7, 2026  
**Status**: ✅ Complete & Aligned  
**Version**: 1.0
