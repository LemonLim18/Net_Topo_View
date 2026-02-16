# NetAutoController — Module Architecture

> **Open-source campus network controller** for universities, campuses, and SMEs who cannot afford Cisco DNA Center, Juniper Mist, or Huawei iMaster NCE.
>
> **What this document covers:** Every module in the system, what it does, what it depends on, which files it touches, and in what order to build it.

---

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        FRONTEND (React + Vite)                      │
│                                                                     │
│  M9: Dashboard │ M10: Topology │ M11: Config │ M12: Security │ Chat│
├─────────────────────────────────────────────────────────────────────┤
│                        BACKEND (FastAPI + Python)                    │
│                                                                     │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌───────┐ │
│  │  M1    │ │  M2    │ │  M3    │ │  M4    │ │  M5    │ │  M6   │ │
│  │ Driver │→│ Crawl  │→│ Config │→│ Deploy │→│  LLM   │→│  MCP  │ │
│  │ Layer  │ │  +BFS  │ │ Engine │ │Pipeline│ │ Agent  │ │Server │ │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └───────┘ │
│                                                                     │
│  ┌────────┐ ┌────────┐ ┌────────────────────────────────────┐       │
│  │  M7    │ │  M8    │ │  M13: Security Module              │       │
│  │NetBox  │ │ Drift  │ │  Audit │ Threat │ Response │ CVE   │       │
│  │  Sync  │ │Detect  │ │  Engine│ Detect │ Engine   │ Map   │       │
│  └────────┘ └────────┘ └────────────────────────────────────┘       │
├─────────────────────────────────────────────────────────────────────┤
│  M14: Database │ M15: Auth & Credentials │ M16: Scheduler           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Module Dependency Graph

```text
Build order (left to right, top to bottom):

M1 Driver Layer ─────────┐
                         ↓
M14 Database ──────→ M2 Crawler/BFS ──→ M3 Config Engine ──→ M4 Deploy Pipeline
                         │                    │                     │
M15 Auth/Creds ──────────┤                    ↓                     ↓
                         ↓               M5 LLM Agent ────────→ M6 MCP Server
M7 NetBox Sync ────→ M8 Drift Detect         │
                         │                    ↓
                         └──────────→ M13 Security Module
                                             │
M16 Scheduler ───────────────────────────────┘
                                             ↓
                              M9–M12 Frontend Modules
```

---

## Module Catalog

### M1: Multi-Vendor Driver Layer

**Purpose:** Normalize CLI differences across Cisco IOS, Huawei VRP, and Juniper Junos into a single Python interface. This is the foundation — every other module calls into drivers.

| Property | Detail |
|----------|--------|
| **Status** | ✅ Done (Full SSH/SDN Drivers + Factory) |
| **Priority** | P0 — everything depends on this |
| **Quarter** | Q1 |
| **Depends on** | Nothing (leaf module) |
| **Used by** | M2, M3, M4, M8, M13 |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Base driver interface | Abstract class all vendors implement | `backend/drivers/base.py` |
| Cisco IOS driver | Existing SSH logic (Paramiko) | `backend/drivers/cisco_ios.py` |
| Huawei VRP driver | `display` commands via Netmiko | `backend/drivers/huawei_vrp.py` |
| Juniper Junos driver | SSH + later NETCONF | `backend/drivers/juniper_junos.py` |
| Driver factory | Auto-detect vendor, return correct driver | `backend/drivers/factory.py` |
| Command map | Vendor → command lookup table | `backend/drivers/command_map.json` |

**Key interface (every driver must implement):**

```python
class BaseDriver(ABC):
    def get_hostname(self, session) -> str: ...
    def get_interfaces(self, session) -> List[Interface]: ...
    def get_neighbors(self, session) -> List[Neighbor]: ...
    def get_routing_table(self, session) -> List[Route]: ...
    def get_arp_table(self, session) -> List[ARPEntry]: ...
    def get_running_config(self, session) -> str: ...
    def get_version_info(self, session) -> DeviceInfo: ...
    def push_config(self, session, commands: List[str]) -> Result: ...
    def get_config_mode_commands(self) -> Tuple[str, str]: ...  # enter, exit
```

**Tech:** Paramiko (existing), Netmiko (new — for Huawei/Juniper), `ncclient` (Juniper NETCONF stretch goal)

---

### M2: Topology Crawler (BFS Discovery Engine)

**Purpose:** Auto-discover the entire network from seed devices using BFS traversal via LLDP/CDP. Builds the topology graph that the frontend visualizes and the LLM reasons about.

| Property | Detail |
|----------|--------|
| **Status** | ✅ Working (`mini_nce.py`) |
| **Priority** | P0 — already done, needs refactoring |
| **Quarter** | Q1 (refactor into module) |
| **Depends on** | M1 (Driver Layer), M14 (Database) |
| **Used by** | M5, M7, M8, M9, M10, M13 |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| BFS engine | Multi-hop SSH chain traversal | `backend/crawler/bfs_engine.py` |
| Topology builder | Build node/edge graph from crawl data | `backend/crawler/topology.py` |
| Parsers | Interface, LLDP, ARP, hostname parsers | `backend/crawler/parsers/` |
| Hop chain manager | SSH proxy chain for nested devices | `backend/crawler/hop_chain.py` |
| Topology store | Save/load topology JSON + history | `backend/crawler/store.py` |
| Scheduled crawl | Periodic re-crawl with change detection | Integrates with M16 |

**Tech:** Paramiko (SSH), ThreadPoolExecutor (parallel polling), SQLite (store results)

**Migration path:** Refactor `mini_nce.py` → split into the files above. No new logic needed, just cleaner structure.

---

### M3: Configuration Engine (Template + Render + Diff)

**Purpose:** Generate vendor-specific CLI configs from structured parameters using Jinja2 templates. Includes diff preview, rollback generation, and syntax validation.

| Property | Detail |
|----------|--------|
| **Status** | ✅ Done (Full renderer, differ, validator, schemas, and 30 templates) |
| **Priority** | P0 — core of the automation story |
| **Quarter** | Q2 |
| **Depends on** | M1 (Driver Layer) |
| **Used by** | M4, M5, M6, M8, M13 |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Template library | Jinja2 templates per vendor per feature | `backend/templates/{vendor}/{feature}.j2` |
| Undo templates | Matching rollback templates | `backend/templates/{vendor}/undo_{feature}.j2` |
| Template renderer | Load + render template with parameters | `backend/config_engine/renderer.py` |
| Config differ | Show additions/removals between configs | `backend/config_engine/differ.py` |
| Syntax validator | Basic syntax validation per vendor | `backend/config_engine/validator.py` |
| Parameter schema | JSON schemas for each template | `backend/config_engine/schemas/` |

**Supported templates (per vendor):**

| Template | Cisco IOS | Huawei VRP | Juniper Junos |
|----------|-----------|------------|---------------|
| OSPF | `ospf.j2` | `ospf.j2` | `ospf.j2` |
| BGP | `bgp.j2` | `bgp.j2` | `bgp.j2` |
| Interface | `interface.j2` | `interface.j2` | `interface.j2` |
| ACL | `acl.j2` | `acl.j2` | `firewall.j2` |
| Static route | `static_route.j2` | `static_route.j2` | `static_route.j2` |
| VLAN | `vlan.j2` | `vlan.j2` | `vlan.j2` |
| NTP | `ntp.j2` | `ntp.j2` | `ntp.j2` |
| Logging | `logging.j2` | `logging.j2` | `logging.j2` |
| Banner | `banner.j2` | `banner.j2` | `banner.j2` |
| SSH hardening | `ssh_harden.j2` | `ssh_harden.j2` | `ssh_harden.j2` |

**Tech:** Jinja2, Python `difflib`

---

### M4: Deployment Pipeline (Apply + Verify + Rollback)

**Purpose:** 6-stage pipeline for safely applying config changes to devices. Handles pre/post snapshots, approval gates, verification, and automatic rollback on failure.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P1 |
| **Quarter** | Q3 |
| **Depends on** | M1 (Driver), M3 (Config Engine), M14 (Database), M15 (Credentials) |
| **Used by** | M5, M6, M8, M13 |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Pipeline orchestrator | 6-stage: Generate → Validate → Preview → Apply → Verify → Commit/Rollback | `backend/deploy/pipeline.py` |
| Change request model | Draft → Pending → Approved → Applied → Rolled Back | `backend/deploy/change_request.py` |
| Snapshot manager | Take pre/post config snapshots | `backend/deploy/snapshots.py` |
| Verification engine | Post-apply checks (ping, show commands) | `backend/deploy/verifier.py` |
| Rollback engine | Command-level or full-config rollback | `backend/deploy/rollback.py` |
| Approval gate | REST endpoint for admin approval | `backend/deploy/approval.py` |
| Ansible bridge | Optional Ansible playbook generation for batch ops | `backend/deploy/ansible_bridge.py` |
| Audit logger | Log every change to `audit_log` table | `backend/deploy/audit.py` |

**Pipeline stages:**

```text
[1] GENERATE → Jinja2 + LLM params → CLI commands
[2] VALIDATE → Syntax check, Junos: commit check
[3] PREVIEW  → Diff view, rollback commands shown
[4] APPLY    → SSH push with pre/post snapshots
[5] VERIFY   → Run show commands, ping tests
[6] COMMIT   → Save to DB, or ROLLBACK if verify fails
```

**Tech:** Netmiko/Paramiko (SSH push), Ansible (optional batch), SQLite (change records)

---

### M5: LLM Agent (AI Brain)

**Purpose:** Natural language interface that understands user intent, gathers network context (RAG), and orchestrates tool calls to accomplish tasks. The "intelligence" of the system.

| Property | Detail |
|----------|--------|
| **Status** | 🟡 Partial (Gemini chat exists in frontend, no tool-calling yet) |
| **Priority** | P0 |
| **Quarter** | Q2 |
| **Depends on** | M2 (topology data), M3 (config generation), M14 (database) |
| **Used by** | M6 (MCP), M11 (frontend chat) |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| LLM provider interface | Abstract class for any LLM backend | `backend/llm/provider.py` |
| Gemini provider | Google Gemini API with function calling | `backend/llm/gemini_provider.py` |
| Ollama provider | Self-hosted LLM (stretch goal) | `backend/llm/ollama_provider.py` |
| Tool registry | Define all tools the LLM can call | `backend/llm/tools.py` |
| Context builder (RAG) | Assemble network state into LLM prompt | `backend/llm/context.py` |
| Conversation manager | Multi-turn chat with memory | `backend/llm/conversation.py` |
| Intent classifier | Classify: troubleshoot vs configure vs audit | `backend/llm/classifier.py` |

**Tool categories the LLM can call:**

| Category | Tools | Risk Level |
|----------|-------|------------|
| **Read-only** | `get_topology`, `get_config`, `get_routing_table`, `run_show_command` | Safe |
| **Generate** | `generate_config`, `preview_diff` | Safe (no changes) |
| **Write** | `apply_config`, `rollback_config` | Requires approval |
| **Security** | `run_audit`, `block_ip`, `isolate_port` | Tiered auto/manual |

**Tech:** `google-generativeai` SDK (Gemini), `httpx` (Ollama API), function calling / tool-use protocol

---

### M6: MCP Server (Model Context Protocol)

**Purpose:** Exposes all network automation capabilities as a standardized MCP server. Any MCP-compatible client (Claude, Gemini, VS Code extensions) can discover and use the tools.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P1 — impressive for thesis, enables multi-LLM support |
| **Quarter** | Q2 |
| **Depends on** | M2, M3, M4, M5, M13 |
| **Used by** | External MCP clients, M11 (frontend) |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| MCP server core | Protocol handler (JSON-RPC over stdio/HTTP) | `backend/mcp/server.py` |
| Resource definitions | Expose topology, configs, statuses as MCP resources | `backend/mcp/resources.py` |
| Tool definitions | Register all tools with schemas | `backend/mcp/tools.py` |
| Prompt templates | Pre-built prompts for common workflows | `backend/mcp/prompts.py` |

**MCP resources and tools:**

```text
Resources:                              Tools:
├── network://topology                  ├── run_show_command(device, cmd)
├── network://device/{id}/config        ├── generate_config(device, template, params)
├── network://device/{id}/status        ├── preview_diff(device, config)
├── network://device/{id}/routes        ├── apply_config(device, config)  [approval]
├── network://templates/{vendor}        ├── rollback_config(device, change_id)
├── security://audit/{device}           ├── run_security_audit(device)
└── security://events                   ├── block_source(device, ip)
                                        └── isolate_port(device, intf)
```

**Tech:** `mcp` Python SDK, JSON-RPC, FastAPI integration

---

### M7: NetBox Integration (Source of Truth Sync)

**Purpose:** Sync with NetBox to maintain the planned/intended network state. Enables drift detection by comparing NetBox's intended state with the actual crawled state.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P1 |
| **Quarter** | Q1–Q2 |
| **Depends on** | M14 (Database) |
| **Used by** | M8 (Drift Detection), M5 (LLM context), M13 (Rogue device check) |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| NetBox API client | REST client for NetBox DCIM, IPAM | `backend/netbox/client.py` |
| Device sync | Pull devices, interfaces, IPs from NetBox | `backend/netbox/sync_devices.py` |
| Topology sync | Pull cable/connection data for planned topology | `backend/netbox/sync_topology.py` |
| Config context loader | Pull config contexts (Jinja2 vars) for devices | `backend/netbox/config_context.py` |
| Webhook receiver | React to NetBox changes in real-time | `backend/netbox/webhook.py` |

**Tech:** `pynetbox` (official NetBox Python client), Docker (NetBox deployment)

---

### M8: Configuration Drift Detection

**Purpose:** Continuously compare actual device state (from crawler) against planned state (from NetBox). Classify differences by severity and either auto-fix, alert, or escalate.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P1 — key differentiator from commercial tools |
| **Quarter** | Q2–Q3 |
| **Depends on** | M2 (Crawler), M3 (Config Engine), M7 (NetBox), M14 (DB), M16 (Scheduler) |
| **Used by** | M4 (Deploy Pipeline for auto-fix), M5 (LLM), M9 (Dashboard) |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Drift comparator | Diff actual vs planned for each device | `backend/drift/comparator.py` |
| Severity classifier | LLM-powered LOW/MEDIUM/HIGH classification | `backend/drift/classifier.py` |
| Auto-remediator | Generate + apply fixes for LOW severity | `backend/drift/auto_fix.py` |
| Alert manager | Create alerts for MEDIUM/HIGH | `backend/drift/alerts.py` |
| Compliance scorer | Calculate per-device and network-wide compliance % | `backend/drift/scoring.py` |

**Three-tier actions:**

| Severity | Auto-action | Examples |
|----------|-------------|---------|
| 🟢 LOW | Auto-fix + log | NTP, DNS, logging, banners, descriptions |
| 🟡 MEDIUM | Generate fix, await approval | OSPF area, ACL rules, BGP, MTU |
| 🔴 HIGH | Alert only, no auto-fix | Device unreachable, unauthorized change, rogue device |

**Tech:** Python `difflib`, LLM (severity classification), APScheduler (periodic checks)

---

### M9: Frontend — Main Dashboard

**Purpose:** Landing page showing network health at a glance. Summary cards, recent events, device counts, compliance score, and quick-action buttons.

| Property | Detail |
|----------|--------|
| **Status** | 🟡 Partial (basic dashboard exists) |
| **Priority** | P1 |
| **Quarter** | Q2–Q4 (iterative) |
| **Depends on** | Backend APIs (M2, M8, M13) |

**What to build:**

| Component | Description | Files |
|-----------|-------------|-------|
| Health summary cards | Device count, up/down, compliance %, security score | `frontend/components/HealthCards.tsx` |
| Recent events feed | Last 20 events: config changes, alerts, auto-fixes | `frontend/components/EventFeed.tsx` |
| Quick actions | Run audit, crawl network, generate report | `frontend/components/QuickActions.tsx` |
| Notification center | Toast alerts for critical events (WebSocket) | `frontend/components/Notifications.tsx` |

**Tech:** React, TypeScript, Recharts (charts), WebSocket (real-time alerts)

---

### M10: Frontend — Topology Visualization

**Purpose:** Interactive network map showing devices, links, status, and drill-down capabilities. The visual centerpiece of the controller.

| Property | Detail |
|----------|--------|
| **Status** | 🟡 Partial (basic topology view exists) |
| **Priority** | P0 |
| **Quarter** | Q1–Q2 |
| **Depends on** | M2 (topology data API) |

**What to build:**

| Component | Description | Files |
|-----------|-------------|-------|
| Topology graph | Force-directed or hierarchical layout | `frontend/components/TopologyGraph.tsx` |
| Device node | Clickable node with vendor icon + status color | `frontend/components/DeviceNode.tsx` |
| Link edge | Line with bandwidth/status indicator | `frontend/components/LinkEdge.tsx` |
| Device detail panel | Slide-out panel: interfaces, config, routes | `frontend/components/DeviceDetail.tsx` |
| Topology controls | Zoom, filter by vendor/status, layout toggle | `frontend/components/TopologyControls.tsx` |

**Tech:** Cytoscape.js or D3.js (graph rendering), React

---

### M11: Frontend — LLM Chat + Config Editor

**Purpose:** Split-pane view: chat with the AI assistant on one side, config diff/preview on the other. The "Copilot for networking" interface.

| Property | Detail |
|----------|--------|
| **Status** | 🟡 Partial (basic chat exists) |
| **Priority** | P1 |
| **Quarter** | Q2 |
| **Depends on** | M5 (LLM Agent API), M3 (Config Engine API) |

**What to build:**

| Component | Description | Files |
|-----------|-------------|-------|
| Chat panel | Message bubbles, tool-call indicators, streaming | `frontend/components/ChatPanel.tsx` |
| Config diff viewer | Side-by-side or unified diff with syntax highlighting | `frontend/components/ConfigDiff.tsx` |
| Approval dialog | Review + approve/reject config changes | `frontend/components/ApprovalDialog.tsx` |
| Intent suggestions | Quick-action chips: "Configure OSPF", "Check status" | `frontend/components/IntentChips.tsx` |
| Change history | Timeline of all changes with rollback buttons | `frontend/components/ChangeHistory.tsx` |

**Tech:** React, Monaco Editor (syntax highlighting), `react-diff-viewer` or custom

---

### M12: Frontend — Security Dashboard

**Purpose:** Centralized security view: compliance scores, active threats, hardening progress, CVE status, and incident timeline.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P2 |
| **Quarter** | Q4 |
| **Depends on** | M13 (Security Module APIs) |

**What to build:**

| Component | Description | Files |
|-----------|-------------|-------|
| Security score cards | Security %, Compliance %, Vulnerability count | `frontend/components/SecurityCards.tsx` |
| Hardening progress | Per-device progress bars | `frontend/components/HardeningProgress.tsx` |
| Threat feed | Active threats with actions (block, isolate) | `frontend/components/ThreatFeed.tsx` |
| Audit report viewer | CIS benchmark results per device | `frontend/components/AuditReport.tsx` |
| CVE table | Known vulns mapped to devices | `frontend/components/CVETable.tsx` |
| Incident timeline | Chronological security event log | `frontend/components/IncidentTimeline.tsx` |

**Tech:** React, Recharts, WebSocket (live threat alerts)

---

### M13: Security Module (Backend)

**Purpose:** The entire security brain: audits device configs against CIS benchmarks, detects threats (brute force, DDoS, rogue devices, route hijacking), and auto-responds with ACL blocks, port isolation, and rate limiting.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P0–P2 (phased) |
| **Quarter** | Q2–Q4 |
| **Depends on** | M1 (Drivers), M2 (Topology), M3 (Templates), M4 (Deploy), M14 (DB), M16 (Scheduler) |
| **Used by** | M5 (LLM tools), M6 (MCP tools), M8 (Drift security checks), M12 (Frontend) |

**Sub-modules:**

#### M13a: Security Audit Engine (Q2)

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Rule definitions | Dataclass-based security rules per vendor | `backend/security/rules.py` |
| Check functions | Individual CIS-style checks (10 categories) | `backend/security/checks/*.py` |
| Audit orchestrator | Run all checks, compute score, save results | `backend/security/engine.py` |
| Hardening templates | Jinja2 templates that fix each finding | `backend/templates/hardening/*.j2` |

#### M13b: Threat Detection (Q3)

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Syslog receiver | UDP listener parsing IOS syslog messages | `backend/security/threat/syslog_receiver.py` |
| Login monitor | Track failed logins, detect brute force | `backend/security/threat/login_monitor.py` |
| Traffic anomaly detector | Compare counters against baselines | `backend/security/threat/anomaly_detector.py` |
| Rogue device detector | Compare LLDP/ARP against NetBox inventory | `backend/security/threat/rogue_detector.py` |
| Route monitor | Detect unexpected route changes / hijacking | `backend/security/threat/route_monitor.py` |
| Baseline calculator | Build traffic/CPU baselines from historical data | `backend/security/threat/baseline.py` |

#### M13c: Incident Response (Q3)

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Responder engine | Generate + optionally apply mitigation configs | `backend/security/response/responder.py` |
| Playbooks | Pre-defined response workflows per threat type | `backend/security/response/playbooks.py` |
| Incident templates | Jinja2 for block IP, isolate port, rate limit | `backend/templates/incident/*.j2` |

#### M13d: Vulnerability Correlation (Q4)

| Sub-component | Description | Files |
|--------------|-------------|-------|
| CVE database | Local SQLite lookup table | `backend/security/cve/cve_db.py` |
| CVE data | Pre-populated JSON of known Cisco/Huawei CVEs | `backend/security/cve/cve_data.json` |
| Version matcher | Map `(vendor, os_version)` → known CVEs | `backend/security/cve/matcher.py` |

**Tech:** `ciscoconfparse2`, `socketserver` (syslog), `cryptography` (credentials), Jinja2, SQLite

---

### M14: Database Layer

**Purpose:** Persistent storage for everything: topology, configs, change history, security events, audit results, traffic baselines, credentials.

| Property | Detail |
|----------|--------|
| **Status** | ✅ Done (Schema, Manager, Models, Repositories implemented) |
| **Priority** | P0 |
| **Quarter** | Q1 |
| **Depends on** | Nothing (leaf module) |
| **Used by** | Every other module |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Schema definition | All tables in SQL | `backend/db/schema.sql` |
| DB manager | Connection pool, migrations | `backend/db/manager.py` |
| Models | Python dataclasses/Pydantic models | `backend/db/models.py` |
| Repository layer | CRUD operations per table | `backend/db/repositories/` |

**Tables:**

| Table | Purpose | Module |
|-------|---------|--------|
| `devices` | Device inventory (hostname, vendor, IP, model) | M2 |
| `interfaces` | Interface details per device | M2 |
| `links` | Topology edges (device_a ↔ device_b) | M2 |
| `config_snapshots` | Running config history with hashes | M2, M8 |
| `change_requests` | Config change lifecycle (draft → applied) | M4 |
| `audit_log` | Immutable log of every action | M4, M13 |
| `security_audits` | CIS audit results per device | M13a |
| `audit_findings` | Individual check results | M13a |
| `security_events` | Threats detected (brute force, DDoS, etc.) | M13b |
| `traffic_baselines` | Per-interface traffic statistics | M13b |
| `known_cves` | CVE lookup table | M13d |
| `credentials` | Encrypted device passwords (see M15) | M15 |

**Tech:** SQLite (dev) → PostgreSQL (production), Python `sqlite3`/`asyncpg`

---

### M15: Authentication & Credential Management

**Purpose:** Encrypt device SSH credentials at rest using Fernet (AES), provide user authentication for the web UI, and maintain audit trails for who did what.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P1 |
| **Quarter** | Q1–Q2 |
| **Depends on** | M14 (Database) |
| **Used by** | M1 (Drivers need creds), M4 (Deploy needs creds) |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Credential encryption | Fernet encrypt/decrypt for device passwords | `backend/security/credentials.py` |
| Key management | Generate/load encryption key from file | `backend/security/keystore.py` |
| Web auth | JWT or session-based login for dashboard | `backend/auth/auth.py` |
| RBAC | Role-based access: viewer, operator, admin | `backend/auth/rbac.py` |

**Tech:** `cryptography` (Fernet), `python-jose` (JWT), FastAPI `Depends`

---

### M16: Task Scheduler

**Purpose:** Run periodic background tasks: re-crawl topology, check for drift, run security audits, update traffic baselines. No heavy dependency like Celery — use APScheduler or asyncio.

| Property | Detail |
|----------|--------|
| **Status** | 🔴 Not started |
| **Priority** | P1 |
| **Quarter** | Q2–Q3 |
| **Depends on** | M2, M8, M13 |
| **Used by** | Runs M2, M8, M13 on schedule |

**What to build:**

| Sub-component | Description | Files |
|--------------|-------------|-------|
| Scheduler core | APScheduler with configurable intervals | `backend/scheduler/core.py` |
| Job definitions | Registered periodic jobs | `backend/scheduler/jobs.py` |
| Job config | Configurable intervals via env/config | `backend/scheduler/config.py` |

**Scheduled jobs:**

| Job | Interval | Module |
|-----|----------|--------|
| Network crawl | Every 15 min | M2 |
| Config change check | Every 30 min | M8 |
| Security audit | Every 6 hours | M13a |
| Traffic baseline update | Every 1 hour | M13b |
| CVE re-check | Daily | M13d |

**Tech:** `APScheduler` (AsyncIOScheduler)

---

## Complete File Tree

```text
NetTopoView/
├── backend/
│   ├── drivers/                     # M1: Multi-Vendor Driver Layer
│   │   ├── __init__.py
│   │   ├── base.py                  # Abstract BaseDriver
│   │   ├── cisco_ios.py             # Cisco IOS SSH driver
│   │   ├── huawei_vrp.py            # Huawei VRP driver
│   │   ├── juniper_junos.py         # Juniper Junos driver
│   │   ├── factory.py               # Auto-detect vendor, return driver
│   │   └── command_map.json         # Vendor command lookup table
│   │
│   ├── crawler/                     # M2: Topology Crawler
│   │   ├── __init__.py
│   │   ├── bfs_engine.py            # BFS traversal + hop chain
│   │   ├── topology.py              # Build topology graph
│   │   ├── hop_chain.py             # SSH proxy chain manager
│   │   ├── store.py                 # Topology persistence
│   │   └── parsers/                 # CLI output parsers
│   │       ├── __init__.py
│   │       ├── cisco_parser.py
│   │       ├── huawei_parser.py
│   │       └── juniper_parser.py
│   │
│   ├── config_engine/               # M3: Configuration Engine
│   │   ├── __init__.py
│   │   ├── renderer.py              # Jinja2 template renderer
│   │   ├── differ.py                # Config diff generation
│   │   ├── validator.py             # Syntax validation
│   │   └── schemas/                 # JSON schemas per template
│   │
│   ├── deploy/                      # M4: Deployment Pipeline
│   │   ├── __init__.py
│   │   ├── pipeline.py              # 6-stage orchestrator
│   │   ├── change_request.py        # Change lifecycle model
│   │   ├── snapshots.py             # Pre/post config snapshots
│   │   ├── verifier.py              # Post-apply verification
│   │   ├── rollback.py              # Rollback engine
│   │   ├── approval.py              # Approval gate API
│   │   ├── ansible_bridge.py        # Optional Ansible integration
│   │   └── audit.py                 # Change audit logger
│   │
│   ├── llm/                         # M5: LLM Agent
│   │   ├── __init__.py
│   │   ├── provider.py              # Abstract LLMProvider
│   │   ├── gemini_provider.py       # Google Gemini API
│   │   ├── ollama_provider.py       # Self-hosted Ollama
│   │   ├── tools.py                 # Tool registry + definitions
│   │   ├── context.py               # RAG context builder
│   │   ├── conversation.py          # Chat memory manager
│   │   └── classifier.py            # Intent classification
│   │
│   ├── mcp/                         # M6: MCP Server
│   │   ├── __init__.py
│   │   ├── server.py                # MCP protocol handler
│   │   ├── resources.py             # MCP resource definitions
│   │   ├── tools.py                 # MCP tool definitions
│   │   └── prompts.py               # Pre-built prompt templates
│   │
│   ├── netbox/                      # M7: NetBox Integration
│   │   ├── __init__.py
│   │   ├── client.py                # NetBox REST API client
│   │   ├── sync_devices.py          # Sync device inventory
│   │   ├── sync_topology.py         # Sync planned topology
│   │   ├── config_context.py        # Pull config variables
│   │   └── webhook.py               # Webhook receiver
│   │
│   ├── drift/                       # M8: Drift Detection
│   │   ├── __init__.py
│   │   ├── comparator.py            # Actual vs planned diffing
│   │   ├── classifier.py            # LOW/MEDIUM/HIGH classification
│   │   ├── auto_fix.py              # Auto-remediation for LOW
│   │   ├── alerts.py                # Alert generation
│   │   └── scoring.py               # Compliance scoring
│   │
│   ├── security/                    # M13: Security Module
│   │   ├── __init__.py
│   │   ├── engine.py                # M13a: Audit orchestrator
│   │   ├── rules.py                 # Security rule definitions
│   │   ├── credentials.py           # M15: Credential encryption
│   │   ├── keystore.py              # Encryption key management
│   │   ├── checks/                  # M13a: Individual checks
│   │   │   ├── __init__.py
│   │   │   ├── auth_checks.py
│   │   │   ├── transport_checks.py
│   │   │   ├── snmp_checks.py
│   │   │   ├── logging_checks.py
│   │   │   ├── mgmt_plane_checks.py
│   │   │   └── banner_checks.py
│   │   ├── threat/                  # M13b: Threat Detection
│   │   │   ├── __init__.py
│   │   │   ├── syslog_receiver.py
│   │   │   ├── login_monitor.py
│   │   │   ├── anomaly_detector.py
│   │   │   ├── rogue_detector.py
│   │   │   ├── route_monitor.py
│   │   │   └── baseline.py
│   │   ├── response/                # M13c: Incident Response
│   │   │   ├── __init__.py
│   │   │   ├── responder.py
│   │   │   └── playbooks.py
│   │   └── cve/                     # M13d: CVE Mapping
│   │       ├── __init__.py
│   │       ├── cve_db.py
│   │       ├── cve_data.json
│   │       └── matcher.py
│   │
│   ├── auth/                        # M15: Web Authentication
│   │   ├── __init__.py
│   │   ├── auth.py                  # JWT auth
│   │   └── rbac.py                  # Role-based access
│   │
│   ├── scheduler/                   # M16: Background Scheduler
│   │   ├── __init__.py
│   │   ├── core.py                  # APScheduler setup
│   │   ├── jobs.py                  # Job definitions
│   │   └── config.py                # Interval configuration
│   │
│   ├── db/                          # M14: Database
│   │   ├── schema.sql
│   │   ├── manager.py
│   │   ├── models.py
│   │   └── repositories/
│   │
│   ├── templates/                   # Jinja2 Templates (M3, M13)
│   │   ├── cisco_ios/               # Network config templates
│   │   │   ├── ospf.j2
│   │   │   ├── bgp.j2
│   │   │   ├── interface.j2
│   │   │   ├── acl.j2
│   │   │   ├── static_route.j2
│   │   │   ├── vlan.j2
│   │   │   └── ntp.j2
│   │   ├── huawei_vrp/
│   │   ├── juniper_junos/
│   │   ├── hardening/               # Security hardening templates
│   │   │   ├── cisco_ssh_only.j2
│   │   │   ├── cisco_snmp_v3.j2
│   │   │   ├── cisco_logging.j2
│   │   │   ├── cisco_vty_acl.j2
│   │   │   └── cisco_banner.j2
│   │   └── incident/                # Incident response templates
│   │       ├── block_source_ip.j2
│   │       ├── isolate_port.j2
│   │       ├── rate_limit.j2
│   │       └── login_hardening.j2
│   │
│   ├── api/                         # FastAPI Routes
│   │   ├── __init__.py
│   │   ├── topology_routes.py       # /api/topology/*
│   │   ├── config_routes.py         # /api/config/*
│   │   ├── deploy_routes.py         # /api/deploy/*
│   │   ├── llm_routes.py            # /api/llm/*
│   │   ├── security_routes.py       # /api/security/*
│   │   ├── drift_routes.py          # /api/drift/*
│   │   └── netbox_routes.py         # /api/netbox/*
│   │
│   ├── backend.py                   # FastAPI app entry point
│   ├── mini_nce.py                  # Legacy crawler (to be refactored into M2)
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   └── App.tsx                  # Root with router
│   ├── views/                       # Page-level views
│   │   ├── Dashboard.tsx            # M9
│   │   ├── TopologyView.tsx         # M10
│   │   ├── ConfigEditor.tsx         # M11
│   │   ├── SecurityDashboard.tsx    # M12
│   │   ├── ChangeHistory.tsx        # M11
│   │   └── DeviceDetail.tsx         # M10
│   ├── components/                  # Reusable components
│   │   ├── TopologyGraph.tsx
│   │   ├── ChatPanel.tsx
│   │   ├── ConfigDiff.tsx
│   │   ├── HealthCards.tsx
│   │   ├── SecurityCards.tsx
│   │   ├── EventFeed.tsx
│   │   ├── ThreatFeed.tsx
│   │   ├── AuditReport.tsx
│   │   └── ApprovalDialog.tsx
│   ├── services/
│   │   └── api.ts                   # API client (Axios/Fetch)
│   └── types.ts                     # TypeScript interfaces
│
├── docker-compose.yml               # NetBox + DB containers
├── Planning.md                      # Architecture planning doc
└── module.md                        # This file
```

---

## Implementation Roadmap

### Q1 (Month 1–3): Foundation

| Order | Module | What to Do | Deliverable |
|-------|--------|-----------|-------------|
| 1 | **M14** | Create `schema.sql`, DB manager, models | Working database |
| 2 | **M1** | Create `BaseDriver`, refactor Cisco driver, add Huawei VRP | 2 working drivers |
| 3 | **M2** | Refactor `mini_nce.py` into modular crawler | Clean BFS engine |
| 4 | **M15** | Implement Fernet credential encryption | Encrypted password storage |
| 5 | **M7** | Deploy NetBox (Docker), build API client, sync devices | NetBox populated with lab devices |
| 6 | **M10** | Improve topology visualization | Interactive topology map |

### Q2 (Month 4–6): Intelligence Layer

| Order | Module | What to Do | Deliverable |
|-------|--------|-----------|-------------|
| 7 | **M3** | Build Jinja2 template library + renderer + differ | Config generation for OSPF, BGP, ACL |
| 8 | **M5** | Implement Gemini function-calling agent + RAG context | "Configure OSPF on R1" works end-to-end |
| 9 | **M6** | Build MCP server exposing network tools | Any MCP client can use your tools |
| 10 | **M8** | Implement drift comparator + severity classifier | Drift dashboard shows discrepancies |
| 11 | **M13a** | Security audit engine + CIS checks + hardening templates | "Run audit on R1" returns score + fixes |
| 12 | **M11** | Chat panel + config diff viewer + approval dialog | Full LLM → diff → approve → push flow |
| 13 | **M9** | Main dashboard with health cards + event feed | At-a-glance network status |

### Q3 (Month 7–9): Operations + Security

| Order | Module | What to Do | Deliverable |
|-------|--------|-----------|-------------|
| 14 | **M4** | 6-stage deployment pipeline + rollback + audit logging | Safe config deployment workflow |
| 15 | **M1** | Add Juniper Junos driver | 3-vendor support |
| 16 | **M8** | Add auto-remediation (LOW severity) | System auto-fixes NTP/banner drift |
| 17 | **M13b** | Syslog receiver + brute force + DDoS + rogue device detection | Live threat detection |
| 18 | **M13c** | Incident response engine + block/isolate templates | Auto-respond to attacks |
| 19 | **M16** | APScheduler for periodic crawl, drift check, audits | Background automation |

### Q4 (Month 10–12): Polish + Thesis

| Order | Module | What to Do | Deliverable |
|-------|--------|-----------|-------------|
| 20 | **M5** | Add Ollama provider (self-hosted LLM option) | Dual-provider architecture |
| 21 | **M13d** | CVE mapping database | Device → vulnerability correlation |
| 22 | **M12** | Security dashboard UI | Visual security posture |
| 23 | **M4** | Ansible bridge for batch operations | "Deploy to all routers" via Ansible |
| 24 | All | Testing, documentation, thesis writing | Submission-ready project |

---

## What to Do Next

**Start here → M14 (Database) + M1 (Drivers)**

These are leaf modules with no dependencies. Everything else builds on top of them.

1. Create `backend/db/schema.sql` with the device, interface, link, and config_snapshot tables
2. Create `backend/drivers/base.py` with the abstract `BaseDriver` interface
3. Refactor the Cisco-specific logic from `mini_nce.py` into `backend/drivers/cisco_ios.py`
4. Implement `backend/drivers/huawei_vrp.py` using Netmiko
5. Run both drivers against your EVE-NG lab to verify they work

Once M1 + M14 are done, proceed to M2 (refactor crawler) and M7 (NetBox setup).

---

## Comparison: NetAutoController vs Commercial Alternatives

| Capability | **NetAutoController** (This Project) | Cisco DNA Center | Juniper Mist | Huawei iMaster NCE |
|-----------|--------------------------------------|-----------------|-------------|-------------------|
| **Cost** | Free (open-source) | $$$$ (license + appliance) | $$$ (subscription) | $$$ (license) |
| **Multi-vendor** | ✅ Cisco + Huawei + Juniper | ❌ Cisco only | ❌ Juniper only | ❌ Huawei only |
| **Legacy device support** | ✅ SSH/CLI for any age device | ⚠️ Limited to DNAC-compatible | ❌ Mist-managed only | ⚠️ Limited |
| **AI/LLM integration** | ✅ Gemini + MCP + RAG | ⚠️ Basic assurance AI | ✅ Marvis AI | ⚠️ Basic |
| **Open protocol (MCP)** | ✅ Works with any MCP client | ❌ Proprietary | ❌ Proprietary | ❌ Proprietary |
| **Self-hosted** | ✅ Runs on single Linux server | ❌ Dedicated appliance | ❌ Cloud-only | ❌ Appliance |
| **Config drift detection** | ✅ NetBox-based, 3-tier | ⚠️ Compliance checks | ❌ Limited | ⚠️ Basic |
| **Threat detection** | ✅ Brute force, DDoS, rogue device | ⚠️ Separate product (ISE) | ⚠️ Separate | ⚠️ Separate |
| **Target audience** | Universities, campuses, SMEs | Large enterprise | Campus (Juniper) | Huawei enterprise |
