# M12: Frontend — Security Dashboard

## Goal

Create a centralized **Security Dashboard** view that provides an at-a-glance overview of the organization's security posture. This consolidates data from existing M13 APIs (audits, events, CVEs) into a single dashboard with score cards, hardening progress, threat feed, and incident timeline.

## Gap Analysis

| M12 Component | Status | Notes |
|---------------|--------|-------|
| Security Score Cards | ❌ Missing | Overall score %, compliance %, vuln count |
| Hardening Progress | ❌ Missing | Per-device progress bars from audit scores |
| Threat Feed (snapshot) | ⚠️ Partial | Full table in `SecurityEvents.tsx`, need summary widget |
| Audit Report viewer | ⚠️ Partial | Audit data fetched in `SecurityEvents.tsx`, need summary cards |
| CVE Summary | ⚠️ Partial | Stats in `SecurityAnalysis.tsx`, need dashboard widget |
| Incident Timeline | ❌ Missing | Chronological security event log with icons |

## Proposed Changes

### Frontend Views

#### [NEW] [SecurityDashboard.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/SecurityDashboard.tsx)

A grid-based dashboard view with:
1. **Score Cards Row** — 4 stat cards: Security Score, Compliance %, Critical Threats, CVE Count
2. **Hardening Progress** — Per-device radial/bar showing audit score with color coding
3. **Recent Threats** — Last 5 critical/high events as a compact list with severity badges
4. **Audit Summary** — Donut chart of pass/fail findings across all devices
5. **CVE Overview** — Bar chart of CVEs by severity, sourced from `securityApi.getCveStats()`
6. **Incident Timeline** — Vertical timeline of recent security events with icons and timestamps

Data sources: `securityApi.listEvents()`, `securityApi.listAudits()`, `securityApi.getCveStats()`

---

### Navigation & Routing

#### [MODIFY] [types.ts](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/types.ts)
- Add `'sec_dashboard'` to the `ViewId` union type

#### [MODIFY] [App.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/src/App.tsx)
- Import `SecurityDashboard` and add `case 'sec_dashboard'` to `renderView()`

#### [MODIFY] [Sidebar.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/components/Sidebar.tsx)
- Add `sec_dashboard` menu item (icon: `LayoutDashboard`) as the first item under Security Domain

---

### Documentation

#### [NEW] [M12_SecurityDashboard.md](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/docs/implementation_plans/M12_SecurityDashboard.md)
- This plan file, saved to docs/

#### [MODIFY] [module.md](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/module.md)
- Update M12 status from 🔴 to ✅, M15 status from 🔴 to ✅

---

## Verification Plan

### Automated Tests
- `cd frontend && npx tsc --noEmit` — TypeScript build must pass

### Manual Verification
- Start dev server → Navigate to Security Domain → "Security Dashboard" should appear as first item
- Dashboard shows score cards, hardening bars, recent threats, audit donut, CVE chart, timeline
