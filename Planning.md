# NetAutoController — FYP Planning Notes

## Project Vision

An automation controller system that provides **full-network-topology visibility** and **LLM-powered network automation**. The system targets:

- **Legacy devices** that cannot integrate with SDN controllers (CLI/SSH-based)
- **Modern devices** that support SDN integration (NETCONF/RESTCONF/API)
- **Multi-vendor ecosystem**: Cisco, Huawei, Juniper (primary)
- **Centralized management** across all device types

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend (React + Vite)                  │
│  ┌──────────┐  ┌─────────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Topology │  │  LLM Chat   │  │  Config  │  │  Change  │ │
│  │   View   │  │  Interface  │  │ Diff/Push│  │ History  │ │
│  └──────────┘  └─────────────┘  └──────────┘  └──────────┘ │
├──────────────────────────────────────────────────────────────┤
│                    Backend (FastAPI + Python)                │
│  ┌──────────┐  ┌─────────────┐  ┌──────────────────────────┐│
│  │ Topology │  │  LLM Agent  │  │   Config Engine          ││
│  │ Crawler  │  │  (Gemini)   │  │  Template │ Diff │ Push  ││
│  │  (BFS)   │  │  Tool-Use   │  │  Rollback │ Audit│ Queue ││
│  └──────────┘  └─────────────┘  └──────────────────────────┘│
│  ┌──────────────────────────────────────────────────────────┐│
│  │           Multi-Vendor Driver Layer                      ││
│  │  CiscoIOS │ CiscoXE │ HuaweiVRP │ JuniperJunos │ SDN   ││
│  │  (SSH)    │(NETCONF)│  (SSH)    │  (NETCONF)   │ (API) ││
│  └──────────────────────────────────────────────────────────┘│
├──────────────────────────────────────────────────────────────┤
│                     Database (SQLite → PostgreSQL)           │
│    device_inventory │ config_snapshots │ change_log │ users  │
└──────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Discovery & Visibility

### Multi-Vendor Command Mapping

| Vendor | Legacy (CLI/SSH) | Modern |
|--------|-----------------|--------|
| **Cisco IOS/IOS-XE** | SSH + `show` commands (current approach) | NETCONF/RESTCONF, DNA Center API |
| **Huawei VRP** | SSH + `display` commands | NETCONF, iMaster NCE API |
| **Juniper Junos** | SSH + `show` commands | NETCONF (native), Apstra API |

### Driver Abstraction Layer

Each vendor gets a "driver" class that normalizes output into a common schema:

```python
class CiscoIOSDriver:
    def get_hostname(self, session) -> str: ...
    def get_interfaces(self, session) -> List[Interface]: ...
    def get_neighbors(self, session) -> List[Neighbor]: ...

class HuaweiVRPDriver:
    # Same interface, different parsing logic
    def get_hostname(self, session) -> str: ...
    # "display lldp neighbor" instead of "show lldp neighbor"
```

### Libraries to Consider

- **Netmiko** — Multi-vendor SSH abstraction
- **NAPALM** — Unified API for Cisco, Juniper, Huawei (community driver)
- **Nornir** — "Ansible but in Python" — handles inventory, parallel execution, integrates with Netmiko/NAPALM

---

## Layer 2: LLM Agent System

### Core Approach: Tool-Calling Agent

The LLM decides which backend tools to call based on the user's natural language intent.

### Agent Tool Definitions

```python
TOOLS = [
    # --- Read-only (Troubleshooting) ---
    {
        "name": "get_topology",
        "description": "Get current network topology (all devices, links, interfaces)",
    },
    {
        "name": "get_device_config",
        "description": "Get the running-config of a specific device",
        "parameters": {"device_id": "string"}
    },
    {
        "name": "get_interface_status",
        "description": "Get interface up/down status and counters for a device",
        "parameters": {"device_id": "string"}
    },
    {
        "name": "run_show_command",
        "description": "Run a safe read-only 'show' command on a device",
        "parameters": {"device_id": "string", "command": "string"}
    },
    {
        "name": "get_routing_table",
        "description": "Get the routing table of a device",
        "parameters": {"device_id": "string"}
    },

    # --- Write (Config Generation — requires approval) ---
    {
        "name": "generate_config_change",
        "description": "Generate a config change for a device. Returns a diff preview. Does NOT apply it.",
        "parameters": {
            "device_id": "string",
            "intent": "string",
            "vendor": "string"
        }
    },
    {
        "name": "propose_config_push",
        "description": "Create a change request to push config. Requires human approval.",
        "parameters": {
            "device_id": "string",
            "config_lines": "list[string]",
            "rollback_config": "list[string]",
            "reason": "string"
        }
    },
]
```

### Use Case A: Intent-Based Configuration

```
User: "Set up OSPF area 0 between R1 and R3 on their shared link"

LLM thinks:
  1. Call get_topology() → finds R1-R3 link is Et0/3 ↔ Et0/3
  2. Call get_device_config("R1") → checks if OSPF is already configured
  3. Call generate_config_change("R1", "enable OSPF area 0 on Et0/3", "cisco_ios")
  4. Call generate_config_change("R3", "enable OSPF area 0 on Et0/3", "cisco_ios")
  5. Returns diff preview to user

   ┌─────── R1 Config Diff ──────┐
   │ + router ospf 1             │
   │ +  network 192.168.5.0      │
   │ +   0.0.0.255 area 0        │
   │ + interface Ethernet0/3     │
   │ +  ip ospf 1 area 0         │
   └─────────────────────────────┘

   [Apply Both]  [Edit]  [Cancel]
```

### Use Case B: Conversational Troubleshooting

```
User: "R4 seems to be unreachable from R1, can you check what's wrong?"

LLM thinks:
  1. Call get_topology() → understand network layout
  2. Call get_interface_status("R4") → Et0/2 is down!
  3. Call run_show_command("R3", "show interface Et0/2") → line protocol down
  4. Call get_routing_table("R1") → no route to 192.168.4.x

LLM Response:
  "The link between R3 and R4 (Ethernet0/2) is down on both sides.
   R1 has no route to R4's subnet (192.168.4.0/24).

   Possible causes:
   1. Physical cable disconnected
   2. Interface administratively shut down

   Would you like me to bring up the interfaces?"
```

### Use Case C: Compliance & Audit

```
User: "Are all devices running the latest firmware?"
  → LLM queries device inventory, compares versions
  → "R2 is running IOS 15.4 — recommended minimum is 15.7"
```

---

## Layer 3: Config Diff, Preview & Rollback

### Config Snapshot System

Every time a device is polled, save its running-config as a "snapshot":

```
config_snapshots table:
┌────┬───────────┬─────────────────────┬──────────────────┐
│ id │ device_id │ timestamp           │ config_text      │
├────┼───────────┼─────────────────────┼──────────────────┤
│ 1  │ R1        │ 2026-02-15 23:00:00 │ hostname R1\n... │
│ 2  │ R1        │ 2026-02-15 23:20:00 │ hostname R1\n... │
│ 3  │ R2        │ 2026-02-15 23:00:00 │ hostname R2\n... │
└────┴───────────┴─────────────────────┴──────────────────┘
```

### Change Request Lifecycle

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ DRAFTED  │───→│ PENDING  │───→│ APPROVED │───→│ APPLIED  │
│          │    │ REVIEW   │    │          │    │          │
└──────────┘    └────┬─────┘    └──────────┘    └────┬─────┘
                     │                               │
                     ↓                               ↓
                ┌──────────┐                    ┌──────────┐
                │ REJECTED │                    │ ROLLED   │
                │          │                    │ BACK     │
                └──────────┘                    └──────────┘
```

### Rollback Strategy

For each config push, auto-generate the **undo commands**:

| Vendor | Config Command | Rollback Command |
|--------|---------------|-----------------|
| **Cisco IOS** | `router ospf 1` | `no router ospf 1` |
| **Huawei VRP** | `ospf 1` | `undo ospf 1` |
| **Juniper Junos** | `set protocols ospf area 0` | `delete protocols ospf area 0` |

Additionally, implement **full rollback** by diffing the current config against the last known-good snapshot and pushing the delta.

---

## Layer 4: SDN Controller Integration

For devices that support SDN controllers:

| Controller | API Type | Use Case |
|-----------|----------|----------|
| Cisco DNA Center / Catalyst Center | REST API | Enterprise campus |
| Cisco ACI | REST API | Data center |
| Huawei iMaster NCE | REST API | Huawei ecosystem |
| Juniper Apstra | REST API | Intent-based DC |
| OpenDaylight | REST (RESTCONF) | Open-source, multi-vendor |

The system acts as a **meta-controller** — pulling topology and state from SDN controllers via their APIs, and merging with legacy SSH-discovered topology into a **unified view**.

---

## Tech Stack (Finalized)

| Component | Technology | Reason |
|-----------|-----------|--------|
| **Backend** | Python (FastAPI) | Already in use; great network automation ecosystem |
| **Network Drivers** | Netmiko + Paramiko | Multi-vendor SSH; Netmiko for Huawei/Juniper, Paramiko for Cisco (existing) |
| **LLM** | Gemini API | Function calling support, good for structured outputs |
| **Config Generation** | Jinja2 templates | LLM fills parameters → template renders vendor-specific CLI |
| **Frontend** | React + Vite | Already in use; interactive topology + chat UI |
| **Topology Viz** | D3.js / Cytoscape.js | Dynamic network graph rendering |
| **Database** | SQLite → PostgreSQL | Device inventory, config history, audit logs |
| **Task Queue** | Celery or asyncio | Long-running config pushes, scheduled scans |

> **Decision: No Nornir.** The existing ThreadPoolExecutor + BFS hop-chain architecture handles parallel execution and dynamic discovery. Nornir assumes static, directly-reachable inventory which conflicts with the SSH-hopping model. Adding it would mean maintaining two inventory systems and learning an extra abstraction layer with little return.

---

## Design Decisions (Finalized)

### 1. Config Generation: Jinja2 Templates (not raw LLM output)

The LLM selects a template and provides **structured parameters**. The config engine renders vendor-specific CLI from validated templates.

**Why this is better than raw LLM generation:**

| | Raw LLM Generation | Jinja2 Templates |
|---|---|---|
| **Reliability** | LLM might hallucinate syntax | Templates are pre-validated |
| **Auditability** | Hard to verify what the LLM generated | Clear parameter → config mapping |
| **Vendor consistency** | LLM might mix up vendor syntax | Each vendor has its own template set |
| **Rollback generation** | LLM must also generate undo commands | Matching `undo_*.j2` templates |
| **Testability** | Can't unit-test LLM output | Can unit-test templates with known inputs |

#### Template Directory Structure

```
templates/
├── cisco_ios/
│   ├── ospf.j2
│   ├── bgp.j2
│   ├── interface.j2
│   ├── acl.j2
│   └── static_route.j2
├── huawei_vrp/
│   ├── ospf.j2
│   ├── bgp.j2
│   ├── interface.j2
│   ├── acl.j2
│   └── static_route.j2
└── juniper_junos/
    ├── ospf.j2
    ├── bgp.j2
    └── ...
```

#### Example: OSPF Template

**Cisco IOS** (`templates/cisco_ios/ospf.j2`):
```jinja
router ospf {{ process_id }}
{% for network in networks %}
 network {{ network.address }} {{ network.wildcard }} area {{ network.area }}
{% endfor %}
{% for interface in interfaces %}
interface {{ interface.name }}
 ip ospf {{ process_id }} area {{ interface.area }}
{% endfor %}
```

**Huawei VRP** (`templates/huawei_vrp/ospf.j2`):
```jinja
ospf {{ process_id }}
{% for network in networks %}
 area {{ network.area }}
  network {{ network.address }} {{ network.wildcard }}
{% endfor %}
{% for interface in interfaces %}
interface {{ interface.name }}
 ospf enable {{ process_id }} area {{ interface.area }}
{% endfor %}
```

#### LLM Output Format (Structured Parameters)

The LLM returns structured JSON, not raw CLI:

```json
{
    "template": "ospf",
    "device_id": "R1",
    "vendor": "cisco_ios",
    "parameters": {
        "process_id": 1,
        "networks": [
            {"address": "192.168.5.0", "wildcard": "0.0.0.255", "area": 0}
        ],
        "interfaces": [
            {"name": "Ethernet0/3", "area": 0}
        ]
    }
}
```

Then the config engine renders it:

```python
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader("templates"))
template = env.get_template(f"{vendor}/{template_name}.j2")
config = template.render(**parameters)
```

---

### 2. Multi-Vendor Priority: Cisco → Huawei → Juniper

| Phase | Vendor | Access Method | Timeline |
|-------|--------|--------------|----------|
| **Phase 1** (current) | Cisco IOS | SSH (Paramiko) | ✅ Done |
| **Phase 2** | Huawei VRP | SSH (Netmiko `huawei_vrp`) | Q1 |
| **Phase 3** | Juniper Junos | SSH → later NETCONF | Q3 |

#### Huawei VRP Key Differences from Cisco IOS

| Function | Cisco IOS | Huawei VRP |
|----------|----------|------------|
| Show neighbors | `show lldp neighbor` | `display lldp neighbor` |
| Show interfaces | `show ip interface brief` | `display ip interface brief` |
| Show ARP | `show ip arp` | `display arp` |
| Show hostname | `show running-config \| include hostname` | `display current-configuration \| include sysname` |
| Enter config mode | `configure terminal` | `system-view` |
| Exit config mode | `end` | `return` |
| Undo command | `no <command>` | `undo <command>` |
| Disable paging | `terminal length 0` | `screen-length 0 temporary` |

---

### 3. Execution Model: Custom ThreadPoolExecutor + Netmiko (No Nornir)

```
LLM Agent
    ↓ "configure OSPF on R1"
Config Engine
    ↓ renders Jinja2 template → CLI commands
Driver Layer (CiscoIOSDriver, HuaweiVRPDriver, JuniperJunosDriver)
    ↓ picks the right driver based on device vendor
Netmiko / Paramiko
    ↓ handles SSH connection + command execution (with hop-chain if needed)
Device
```

**Rationale for skipping Nornir:**
- The existing `ThreadPoolExecutor` + BFS hop-chain handles parallel execution with SSH hopping — Nornir's runner assumes all hosts are directly reachable
- Nornir requires a static inventory file; the topology is dynamically discovered
- Reduces dependency count and learning curve for a single-person FYP

---

## Lab Environment

- **Platform**: EVE-NG
- **Vendors available**: Cisco IOS, Huawei VRP, Juniper Junos
- **Current topology**: 4-router ring (R1 ↔ R2 ↔ R4 ↔ R3 ↔ R1, all Cisco IOS)

---

## Feature 2: AI-Assisted Network Script Generation (IDE-Style)

### The Vision

Build a **"GitHub Copilot for Network Engineering"** — an IDE-like experience where the LLM has deep context about the live network and assists engineers in generating, validating, and deploying configuration scripts.

### Network Context vs Software Context

| Software Dev (Copilot/Antigravity) | Network Engineering (This System) |
|-------------------------------------|----------------------------------|
| Source code files | Running configs, topology data |
| Language syntax | Vendor CLI syntax (IOS, VRP, Junos) |
| Project structure | Network architecture (zones, VRFs, segments) |
| Git history | Config change history / audit log |
| Test results | Ping/traceroute results, interface counters |
| Dependencies | Routing tables, OSPF/BGP adjacencies |

The system's "IDE" is a **web-based editor panel** where the engineer:
- Sees the topology map
- Selects target device(s)
- Describes intent or writes config directly
- Gets AI-assisted suggestions, auto-completion, and validation
- Previews diffs, approves, pushes, and monitors

---

## RAG (Retrieval-Augmented Generation) for Network Context

### What Is RAG Here?

Before the LLM generates a response, **retrieve relevant context from the network data** and inject it into the prompt. This makes the LLM aware of the actual network state.

### The Network Context Store

```text
Network Context Store
├── Static Context (rarely changes)
│   ├── Device inventory (hostname, vendor, model, OS version)
│   ├── Network design docs (if any)
│   ├── Standard templates (Jinja2 templates)
│   └── Vendor CLI reference (commands, syntax)
│
├── Live Context (changes frequently)
│   ├── Running configs (periodic snapshots)
│   ├── Topology graph (crawler output)
│   ├── Interface status (up/down, counters, errors)
│   ├── Routing tables (OSPF, BGP, static)
│   ├── ARP/MAC tables
│   └── LLDP/CDP neighbor data
│
└── Historical Context
    ├── Config change log (who changed what, when)
    ├── Previous configs (for diff/rollback)
    └── Incident history (past troubleshooting)
```

### RAG Flow

```text
User: "Configure BGP peering between R1 and R2 using AS 65001"
                    ↓
┌─────────── RAG Retrieval ────────────┐
│ 1. Query topology: R1-R2 link info   │
│ 2. Fetch R1 running-config           │
│ 3. Fetch R2 running-config           │
│ 4. Check: any existing BGP config?   │
│ 5. Get interface IPs on shared link  │
│ 6. Load BGP Jinja2 template schema   │
│ 7. Check vendor types (both Cisco?)  │
└──────────────────────────────────────┘
                    ↓
        Inject all context into LLM prompt:

        "You are a network automation assistant.
         Here is the current topology: {...}
         R1 running-config: {...}
         R2 running-config: {...}
         R1 is Cisco IOS, R2 is Cisco IOS.
         Their shared link is Et0/2 ↔ Et0/3.
         Available BGP template parameters: {...}

         Generate the Jinja2 parameters for BGP configuration."
                    ↓
        LLM returns structured parameters
                    ↓
        Jinja2 renders vendor-specific config
```

### Vector Database — Not Needed for FYP Scope

For a FYP with 4–20 devices, a vector DB (ChromaDB, Pinecone) is overkill. The context is small enough to:
- Directly query SQLite DB for device configs
- Load the full topology JSON into the prompt
- Use existing API endpoints as LLM tools

If scaling later (or to impress the supervisor), config snippets and vendor documentation could be embedded into a vector store. But for FYP scope, **tool-calling + direct DB queries** is simpler and more deterministic.

---

## Script Testing, Deployment, and Rollback Pipeline

### The 6-Stage Pipeline

```text
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ GENERATE │───→│ VALIDATE │───→│ DRY-RUN  │───→│  APPLY   │───→│ VERIFY   │
│          │    │          │    │ (Preview)│    │          │    │          │
└──────────┘    └──────────┘    └──────────┘    └────┬─────┘    └────┬─────┘
                                                     │               │
                                                     │          ┌────┴─────┐
                                                     │          │ ROLLBACK │
                                                     │          │(if fail) │
                                                     │          └──────────┘
                                                     ↓
                                               ┌──────────┐
                                               │  COMMIT  │
                                               │ (saved)  │
                                               └──────────┘
```

### Stage 1: Generate
LLM + Jinja2 generates vendor-specific config commands.

### Stage 2: Validate (Syntax Check)
- **Cisco IOS**: No built-in dry-run, but parse for syntax errors
- **Huawei VRP**: `check` mode in some versions
- **Juniper Junos**: `commit check` — validates before applying (best here)

### Stage 3: Dry-Run / Preview
Show the engineer exactly what will change:
- Display a **diff view** (current config vs. proposed config)
- Highlight additions (green), removals (red)
- Show rollback commands that would be generated

### Stage 4: Apply
Push config via SSH with safety:

```python
def apply_config(device, config_lines, rollback_lines):
    # 1. Snapshot current config BEFORE changes
    pre_snapshot = get_running_config(device)
    
    # 2. Apply the config
    result = push_config_lines(device, config_lines)
    
    # 3. Snapshot AFTER changes
    post_snapshot = get_running_config(device)
    
    # 4. Log the change
    save_change_record(device, pre_snapshot, post_snapshot,
                       config_lines, rollback_lines)
    
    return result
```

### Stage 5: Verify
After applying, automatically verify the change worked:
- Run relevant `show` commands (e.g., `show ip ospf neighbor` after OSPF config)
- Check interface status
- Verify connectivity (ping test)

### Stage 6: Rollback (if verification fails)
Two approaches:
1. **Command-level rollback**: Push pre-generated undo commands
2. **Full config rollback**: Diff pre-snapshot with current config and push the delta

---

## Ansible vs. Alternatives — Analysis

### Ansible

| Pros | Cons |
|------|------|
| Industry standard for network automation | YAML-based — rigid, not great for dynamic LLM integration |
| Huge module library (cisco.ios, huawei, juniper) | Runs as external process — harder to integrate into web app |
| Idempotent by design | Debugging is painful (verbose YAML error messages) |
| Good for scheduled/batch operations | Overkill for single-device, interactive config pushes |
| Impressive for FYP (industry relevance) | Adds significant complexity to architecture |

### Direct SSH (Netmiko/Paramiko — current approach)

| Pros | Cons |
|------|------|
| Full control, easy to integrate with FastAPI | Build everything yourself (inventory, error handling) |
| Real-time feedback in web UI | No built-in idempotency |
| Works perfectly with SSH hop-chain | Less "industry standard" for FYP paper |
| LLM can trigger directly via tool-calling | Need to handle vendor differences manually |

### NAPALM

| Pros | Cons |
|------|------|
| `compare_config()` gives diffs for free | Limited vendor support (Huawei is community-maintained) |
| `commit_config()` / `discard_config()` / `rollback()` built-in | Less control over SSH connection management |
| `get_facts()`, `get_interfaces()` etc. standardized | Doesn't support SSH hopping natively |

### Decision: Hybrid Approach

```text
Use Case                          → Tool
─────────────────────────────────────────────
Interactive single-device config  → Netmiko/Paramiko (direct SSH)
Batch multi-device deployment     → Ansible (called from Python)
Config diff/rollback              → NAPALM (or custom implementation)
Discovery/crawling                → Existing Paramiko + hop-chain
```

**For the FYP:**
1. **Primary**: Keep Netmiko/Paramiko for interactive LLM-driven config engine
2. **Secondary**: Integrate Ansible as optional execution backend for batch operations — LLM could generate Ansible playbooks as alternative to direct CLI
3. **Show both** in thesis: "The system supports both direct interactive deployment and Ansible-based batch deployment"

---

## MCP (Model Context Protocol) Integration

### What Is MCP?

MCP is a protocol (developed by Anthropic) that standardizes how LLMs connect to external data sources and tools. Think of it as **"USB-C for AI"** — a universal connector between LLMs and the outside world.

### Without MCP vs. With MCP

```text
Without MCP:
  Your App → custom API → LLM → custom tool calls → Your Backend
  (tightly coupled, different for every LLM provider)

With MCP:
  Your App → MCP Client → LLM → MCP Server → Your Network Tools
  (standardized, any MCP-compatible LLM can use your tools)
```

### MCP Server Design for Network Automation

```text
MCP Server: "network-automation"
│
├── Resources (read-only data the LLM can access):
│   ├── network://topology            → current topology JSON
│   ├── network://device/{id}/config  → running-config
│   ├── network://device/{id}/status  → interface status
│   └── network://templates/{vendor}  → available Jinja2 templates
│
├── Tools (actions the LLM can perform):
│   ├── run_show_command(device_id, command)
│   ├── generate_config(device_id, template, parameters)
│   ├── preview_diff(device_id, config_lines)
│   ├── apply_config(device_id, config_lines)   ← requires approval
│   └── rollback_config(device_id, change_id)   ← requires approval
│
└── Prompts (pre-built prompt templates):
    ├── troubleshoot(device_id, symptom)
    ├── configure(intent, target_devices)
    └── audit(compliance_standard)
```

### Why MCP Is Great for the FYP

1. **Academic novelty**: MCP is very new (2024/2025) — applying it to network automation is cutting-edge research material
2. **Interoperability**: The MCP server could work with Claude, Gemini, GPT — any MCP-compatible client
3. **The IDE angle**: Tools like VS Code, Cursor, and Antigravity already use MCP. Building an MCP server for network automation means someone could use the tools from inside their IDE
4. **Clean separation of concerns**: MCP server handles network operations; LLM client handles natural language understanding

### MCP vs. Plain Function Calling

| Aspect | Plain Function Calling | MCP |
|--------|----------------------|-----|
| Standardization | Custom per LLM provider | Universal protocol |
| Discoverability | Hardcoded tool list | Dynamic resource/tool discovery |
| Context management | Manual prompt injection | MCP Resources handle it |
| Multi-LLM support | Rewrite for each provider | Works with any MCP client |
| Industry relevance | Common but basic | Cutting-edge, impressive for thesis |

---

## Final Architecture (with MCP)

```text
User (Engineer)
    ↓ natural language intent
React Dashboard (Chat + Config Editor + Topology View)
    ↓
FastAPI Backend (MCP Client)
    ↓ MCP protocol
MCP Server (exposes network tools & context)
    ↓
┌────────────────────────────────────┐
│     Network Automation Engine      │
│                                    │
│  RAG Context:                      │
│    - Topology (live from crawler)  │
│    - Running configs (snapshots)   │
│    - Routing/ARP/LLDP tables       │
│    - Change history (DB)           │
│                                    │
│  Config Generation:                │
│    - Jinja2 templates              │
│    - LLM parameter extraction      │
│                                    │
│  Execution:                        │
│    - Interactive: Netmiko/Paramiko │
│    - Batch: Ansible (optional)     │
│                                    │
│  Safety:                           │
│    - Diff preview                  │
│    - Human approval gate           │
│    - Pre/post snapshots            │
│    - Rollback capability           │
└────────────────────────────────────┘
    ↓ SSH / NETCONF / REST API
Network Devices (Cisco, Huawei, Juniper)
```

---

## NetBox as Source of Truth (Planned State)

### Why NetBox

NetBox is the industry-standard **Network Source of Truth (NSoT)** — purpose-built for storing the planned/intended network state.

| Feature | How It Helps This Project |
|---------|--------------------------|
| **Device inventory** | Store hostname, vendor, model, OS version, serial number |
| **IP Address Management (IPAM)** | Track all IP prefixes, assignments, VLANs |
| **Interface management** | Map physical/logical interfaces per device |
| **Cable/connection tracking** | Document physical topology (which port connects where) |
| **REST API** | Backend can query NetBox for planned topology |
| **Webhooks** | NetBox notifies the system when planned topology changes |
| **Config contexts** | Attach structured data (Jinja2 variables) to devices |

### Alternatives Considered

| Tool | Verdict |
|------|---------|
| **NetBox** | ✅ Best choice — industry standard, rich API, huge community |
| **Nautobot** | Fork of NetBox — equally good but smaller community |
| **Custom SQLite/PostgreSQL** | Simpler but reinvents NetBox features |
| **YAML/JSON files in Git** | Works for small labs but no API, doesn't scale |
| **Cisco DNA Center** | Vendor-locked, not multi-vendor |

### Planned vs. Actual State — Drift Detection

```text
                    ┌──────────────────┐
                    │     NetBox       │
                    │ (Planned State)  │  ← "What the network SHOULD look like"
                    └────────┬─────────┘
                             │ REST API
                             ↓
┌────────────────────────────────────────────────┐
│              Your System                       │
│                                                │
│  ┌──────────────┐    ┌──────────────────────┐  │
│  │  Crawler      │    │  Compliance Engine   │  │
│  │ (Actual State)│───→│  Compare Planned vs  │  │
│  │              │    │  Actual State         │  │
│  └──────────────┘    └──────────────────────┘  │
│                              │                 │
│                    "R4 should have OSPF but     │
│                     it's not configured"        │
│                              ↓                 │
│                    ┌──────────────────────┐     │
│                    │  LLM + Config Engine │     │
│                    │  Generate fix config │     │
│                    └──────────────────────┘     │
└────────────────────────────────────────────────┘
```

This enables a **killer FYP feature**: automatic drift detection between planned state (NetBox) and actual state (crawled topology), with LLM-powered auto-remediation.

### Deployment

NetBox runs in Docker — can be hosted alongside EVE-NG lab or on the desktop PC.

---

## LLM Choice: Gemini API (Primary) + Self-Hosted (Stretch Goal)

### Hardware Reality: Quadro P4000 8GB VRAM

What 8GB VRAM can actually run:

| Model | Size (Q4) | Fits? | Quality for Network Tasks |
|-------|-----------|-------|--------------------------|
| **Llama 3.1 8B** | ~5GB | ✅ | Decent for simple tasks, struggles with complex multi-device configs |
| **Mistral 7B** | ~5GB | ✅ | Similar to Llama 8B |
| **Qwen2.5 7B** | ~5GB | ✅ | Good reasoning for its size |
| **DeepSeek-R1 7B** | ~5GB | ✅ | Good reasoning |
| **Llama 3.1 70B** | ~40GB | ❌ | Excellent but impossible on this hardware |
| **Gemini 2.0 Flash** | Cloud | N/A | Excellent quality, free tier |

### Gemini API vs. Self-Hosted Comparison

| Factor | Gemini Free Tier (API) | Self-Hosted 7-8B (Ollama) |
|--------|----------------------|---------------------------|
| **Quality** | ⭐⭐⭐⭐⭐ Superior reasoning and structured output | ⭐⭐⭐ Works but makes more mistakes |
| **Function calling** | ✅ Native, reliable | ⚠️ Requires careful prompting, inconsistent |
| **Structured JSON** | ✅ Native JSON mode | ⚠️ Often outputs malformed JSON |
| **Context window** | 1M tokens (fit entire network state) | 4K–8K tokens (tight for multiple configs) |
| **Speed** | Fast (cloud GPUs) | Slow on P4000 (~5–10 tokens/sec) |
| **Cost** | Free: 15 RPM, 1M tokens/day | Free (electricity only) |
| **Privacy** | Data goes to Google | Everything stays local |
| **Offline** | ❌ Needs internet | ✅ Works offline |
| **Rate limits** | 15 requests/min on free tier | No limits |

### Why Self-Hosted Is Problematic for This Use Case

1. **Reliable tool calling** — the LLM must consistently output valid JSON with correct template names and parameters. 7B models are unreliable at this.
2. **Large context needed** — RAG requires injecting multiple device configs (500–2000 lines each) + topology + templates into one prompt. 4K–8K context is too small.
3. **Multi-step reasoning** — "Configure BGP between R1 and R2" requires topology understanding → shared link detection → existing config check → template selection → parameter filling. 7B models struggle with this chain.

### Decision: Dual-Provider Architecture

**Phase 1 (Q1–Q3): Gemini API as primary**
- Free tier: 15 RPM, 1M tokens/day — more than enough for dev and demos
- Native function calling works perfectly with MCP/tool architecture
- Focus energy on building the automation system, not fighting LLM limitations

**Phase 2 (Q4 stretch goal): Add self-hosted option**
- Show in thesis: "The system supports both cloud and self-hosted LLMs"
- Covers air-gapped/offline enterprise requirement
- If a better small model emerges by then, leverage it

### LLM Abstraction Layer

```python
class LLMProvider:
    async def generate(self, prompt, tools, context) -> Response: ...

class GeminiProvider(LLMProvider):
    # google.generativeai SDK
    # Native function calling, JSON mode, 1M context
    ...

class OllamaProvider(LLMProvider):
    # Local Ollama API (localhost:11434)
    # Manual function calling via prompt engineering
    # Smaller context window
    ...

# Config toggle
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # or "ollama"
```

---

## Gemini Free Tier + RAG Feasibility

### Free Tier Limits vs. Actual Usage

| Resource | Gemini 2.0 Flash Free Limit | This Project's Usage |
|----------|----------------------------|---------------------|
| Requests/min | 15 RPM | ~1–3 per interaction |
| Tokens/day | 1,000,000 | ~50 full-context requests/day |
| Context window | 1,048,576 tokens | Fits entire network state |

### Token Budget Per RAG Request (Worst Case)

```text
Input tokens per LLM call:
├── System prompt + tool definitions:     ~2,000 tokens
├── Topology JSON (20 devices):           ~3,000 tokens
├── Running config (device 1, full):      ~2,000–5,000 tokens
├── Running config (device 2, full):      ~2,000–5,000 tokens
├── Jinja2 template schema:              ~500 tokens
├── Conversation history (5 turns):       ~2,000 tokens
├── NetBox planned state context:         ~1,000 tokens
└── Total input:                          ~12,500–18,500 tokens

Output tokens:
├── LLM reasoning + response:            ~500–1,000 tokens
└── Structured JSON parameters:          ~200–500 tokens

Total per request: ~15,000–20,000 tokens
```

**Daily capacity**: 1,000,000 ÷ 20,000 = **~50 full RAG requests/day** — more than enough for development and demos.

### Why 1M Context Eliminates the Need for Vector DB

With smaller models (7B, 8K context), you'd need to truncate configs, split requests, or use a vector DB for selective retrieval. With Gemini's 1M context, you can dump the **entire network state** into one prompt:

- All 4–20 device configs in full
- Complete topology JSON
- All Jinja2 template schemas
- Routing tables, ARP/LLDP data
- NetBox planned state

The LLM sees everything at once, enabling cross-device reasoning (e.g., "R1's OSPF config needs to match R3's area assignment").

---

## Configuration Drift Detection & Auto-Remediation

### The Drift Detection Loop

```text
Every N minutes (configurable):
  1. Crawl actual state from devices
  2. Fetch planned state from NetBox
  3. Compare: find all differences
  4. Classify each difference by severity
  5. Act based on severity level
```

### Three-Tier Severity Classification

| Severity | Action | Human Involvement |
|----------|--------|-------------------|
| 🟢 **LOW** (Auto-Fix) | System fixes automatically, logs the change | Notification only (after fix) |
| 🟡 **MEDIUM** (Alert) | System generates fix, waits for approval | Admin must approve before applying |
| 🔴 **HIGH** (Critical) | System alerts immediately, does NOT generate fix | Admin must investigate manually |

### 🟢 LOW — Auto-Fix Examples (Safe, Non-Disruptive)

These are safe to auto-remediate: non-disruptive, deterministic, and common "config hygiene" issues.

| Issue | Example | Auto-Fix |
|-------|---------|----------|
| **NTP server missing** | Device has no NTP config, NetBox says `ntp server 10.0.0.100` | Push `ntp server 10.0.0.100` |
| **SNMP community drift** | Community is `OLD_STRING`, should be `NEW_STRING` | Replace community string |
| **Logging server missing** | Missing `logging host 10.0.0.50` | Push logging config |
| **Banner mismatch** | Login banner doesn't match standard template | Push correct banner |
| **DNS server missing** | No `ip name-server`, should be `8.8.8.8` | Push DNS config |
| **Timezone wrong** | UTC instead of `clock timezone MYT 8` | Push timezone config |
| **Description missing** | Interface connected to R3 but no description | Push `description Link_to_R3` |

**Example auto-fix flow:**

```text
Drift detected: R3 missing NTP server configuration
  Planned (NetBox): ntp server 10.0.0.100
  Actual: (none)
  Severity: LOW (NTP is non-disruptive, deterministic fix)
  
  → Auto-generating config: "ntp server 10.0.0.100"
  → Pushing to R3... ✅ Success
  → Logging: "Auto-remediated NTP drift on R3 at 2026-02-16 00:15"
  → Admin notification: "ℹ️ Auto-fixed: NTP server added to R3"
```

### 🟡 MEDIUM — Alert + Approval Examples (Could Affect Traffic)

| Issue | Example | Generated Fix (Awaiting Approval) |
|-------|---------|----------------------------------|
| **OSPF area mismatch** | Et0/3 in area 0, NetBox says area 1 | Move interface to correct area |
| **ACL rule missing** | Planned ACL has 10 rules, device has 8 | Generate 2 missing entries |
| **BGP neighbor down** | Session in `Idle` state | Clear session or check config |
| **Interface MTU mismatch** | MTU 1500, NetBox says 9000 (jumbo) | Generate MTU change command |
| **Static route missing** | Planned route not present | Generate static route command |
| **STP priority drift** | Root bridge priority changed | Generate priority correction |

**Example alert flow:**

```text
Drift detected: R1 OSPF area mismatch on Et0/3
  Planned (NetBox): area 1
  Actual: area 0
  Severity: MEDIUM (routing change, could affect traffic)
  
  → Generated fix:
    interface Ethernet0/3
     no ip ospf 1 area 0
     ip ospf 1 area 1
  → Rollback prepared:
    interface Ethernet0/3
     no ip ospf 1 area 1
     ip ospf 1 area 0
  
  → ⚠️ Awaiting admin approval
  → [Apply] [Edit] [Dismiss]
```

### 🔴 HIGH — Critical Alert Examples (Needs Investigation)

| Issue | Example | Alert Action |
|-------|---------|-------------|
| **Device unreachable** | R4 was reachable last scan, now gone | 🚨 Possible hardware failure |
| **Unauthorized config change** | Config changed but no change request exists | 🚨 Potential unauthorized access |
| **Interface flapping** | Et0/2 up/down 15 times in 10 minutes | 🚨 Unstable link |
| **Unknown device** | LLDP shows neighbor not in NetBox | 🚨 Rogue device detected |
| **Route anomaly** | R1 suddenly has 500 routes, expected ~20 | 🚨 Route leak or BGP hijack |
| **Security config removed** | `enable secret` or SSH config deleted | 🚨 Security posture degraded |

### LLM-Powered Classification

The LLM classifies diffs using network context, not just pattern matching:

```python
prompt = f"""
You are a network compliance engine.

Planned state (NetBox): {planned_state}
Actual state (device): {actual_state}
Differences: {diffs}

For each difference, classify severity and generate fix if applicable.

LOW (auto-fix safe):
- Non-disruptive (NTP, DNS, logging, banners, descriptions)
- No data plane impact
- Deterministic fix (one correct solution)

MEDIUM (needs approval):
- Control plane changes (OSPF, BGP, STP)
- Could momentarily disrupt traffic
- Multiple possible fixes

HIGH (investigate):
- Device unreachable, unauthorized changes
- Security config removed, hardware symptoms
"""
```

### Dashboard UI Concept

```text
┌─────────────────────────────────────────────────────────┐
│  Configuration Compliance Dashboard                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  🟢 Auto-Fixed (last 24h): 3                           │
│  ├── R3: NTP server added (00:15)                      │
│  ├── R2: Logging host restored (08:30)                 │
│  └── R4: Interface description updated (12:45)         │
│                                                         │
│  🟡 Pending Approval: 2                                │
│  ├── R1: OSPF area mismatch on Et0/3    [Review]       │
│  └── R2: Missing ACL rule #9            [Review]       │
│                                                         │
│  🔴 Critical Alerts: 1                                  │
│  └── R4: Unauthorized config change     [Investigate]   │
│                                                         │
│  ✅ Compliant Devices: R1, R2                           │
│  ⚠️  Non-Compliant: R3, R4                             │
│                                                         │
│  Overall Compliance Score: 85%                          │
└─────────────────────────────────────────────────────────┘
```

---

## Feature 3: Security Posture Assessment (Automated Hardening Audit)

The LLM scans device configs against CIS Benchmarks and custom security baselines, then auto-generates hardening configs.

### Security Checks

| Check Category | What It Validates | Cisco IOS Check |
|---------------|-------------------|-----------------|
| **Authentication** | Enable secret, AAA, local users | `enable secret` present, `aaa new-model` |
| **Transport** | SSH v2 only, no Telnet | `transport input ssh`, `ip ssh version 2` |
| **Access Control** | VTY ACLs, console timeout | `access-class` on VTY, `exec-timeout` |
| **SNMP** | v3 preferred, no default communities | No `community public/private` |
| **Logging** | Syslog configured, timestamps | `logging host`, `service timestamps` |
| **Management Plane** | HTTP/HTTPS server disabled | `no ip http server` |
| **Password** | Encryption, minimum length | `service password-encryption`, `security passwords min-length` |
| **Unused Ports** | Shutdown unused interfaces | Unused interfaces administratively down |
| **Banner** | Login/MOTD banner present | `banner login` configured |
| **NTP** | NTP auth enabled, trusted source | `ntp authenticate`, `ntp trusted-key` |

### Example Audit Output

```text
┌─────────────────────────────────────────────────────────────┐
│  Security Posture Report — 2026-02-16                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🔴 CRITICAL                                                │
│  ├── R1: Telnet enabled (port 23 open)                      │
│  ├── R3: No enable secret configured                        │
│  └── R2: SNMP community = "public" (default!)               │
│                                                             │
│  🟡 WARNING                                                 │
│  ├── R1: No login banner                                    │
│  ├── R4: SSH v1 enabled (should be v2 only)                 │
│  ├── R2: No ACL on VTY lines                                │
│  └── R3: No logging to syslog server                        │
│                                                             │
│  🟢 PASS                                                    │
│  ├── All: Password encryption enabled                       │
│  ├── All: Console timeout configured                        │
│  └── R1, R2: AAA authentication configured                  │
│                                                             │
│  Overall Security Score: 62% (Poor)                         │
│  [Generate Hardening Configs]  [Export PDF Report]           │
└─────────────────────────────────────────────────────────────┘
```

The LLM auto-generates hardening configs using Jinja2 templates for each finding.

### Vulnerability Correlation (CVE Mapping)

Cross-reference device OS versions with known CVEs using a local lookup table:

```text
Device: R1 — Cisco IOS 15.4(3)M
  CVE-2023-20198 (CVSS 10.0) 🔴 Web UI privilege escalation
  CVE-2023-20273 (CVSS 7.2)  🟡 Local privilege escalation
  Fix: Upgrade to 15.7+ or disable HTTP server
  [Generate Mitigation Config]
```

Implementation: local JSON/SQLite table mapping `(vendor, os_version) → known CVEs`. No live CVE queries needed for FYP.

### Config Change Anomaly Detection

Extends existing config snapshot system to detect suspicious changes:

| Anomaly | Severity | Reason |
|---------|----------|--------|
| New `username` added | 🔴 | Could be backdoor account |
| ACL `deny` rule removed | 🔴 | Security posture weakened |
| SNMP community changed outside window | 🟡 | Verify authorization |
| Unknown static route injected | 🔴 | Possible traffic hijacking |
| `no logging host` appeared | 🔴 | Covering tracks? |
| SSH keys regenerated | 🟡 | Normal rotation or attack? |

### Network Access Path Analysis

Analyze ACLs across multi-hop paths to show who can reach what:

```text
User: "Show access path from VLAN 10 to server subnet 10.10.0.0/24"

VLAN 10 (192.168.10.0/24)
  → R1 Et0/1 [ACL 101: PERMIT tcp eq 443] ✅ HTTPS allowed
  → R1 Et0/1 [ACL 101: DENY tcp eq 22]   🚫 SSH blocked
  → R2 Et0/3 [No ACL] ⚠️ Unrestricted!
  → Server subnet 10.10.0.0/24

Finding: R2 has NO ingress ACL — unrestricted access to servers.
```

---

## Feature 4: Threat Detection & Automated Incident Response

### Security Monitoring Data (Extends Existing Polling)

```text
Existing polling:             New security polling:
├── Running configs           ├── Interface traffic counters
├── LLDP/CDP neighbors        ├── ACL deny hit counters
├── Routing tables            ├── Login attempt logs (syslog)
├── Interface status          ├── CPU/Memory utilization
└── ARP tables                ├── Connection counts per interface
                              └── NAT translation table
```

### Attack Scenario 1: DDoS Detection & Mitigation

```text
Normal:  R1 Et0/1 → 500 pps, CPU 15%, 200 connections
Attack:  R1 Et0/1 → 50,000 pps (100x), CPU 95%, 15,000 connections
         Source: 203.0.113.0/24 — SYN flood on port 443

🚨 ALERT: DDoS attack detected on R1
  → [AUTO] Apply rate-limiting ACL on Et0/1
  → [APPROVAL] Block source 203.0.113.0/24
  → [MANUAL] Contact upstream ISP for blackhole routing

Generated mitigation:
  ip access-list extended DDOS_MITIGATION
   deny ip 203.0.113.0 0.0.0.255 any log
   permit ip any any
  interface Ethernet0/1
   ip access-group DDOS_MITIGATION in
```

### Attack Scenario 2: Brute Force Login

```text
Syslog: 20 failed SSH logins in 30 seconds from 10.0.0.99
Usernames tried: admin, root, cisco, test → dictionary attack

🚨 ALERT: Brute force detected on R3
  → [AUTO] Block source IP on VTY lines
  → [AUTO] Enable login delay + max retries

Generated config:
  ip access-list extended BLOCK_BRUTEFORCE
   deny ip host 10.0.0.99 any log
   permit ip any any
  line vty 0 4
   access-class BLOCK_BRUTEFORCE in
   login block-for 120 attempts 5 within 60
```

### Attack Scenario 3: Rogue Device / Man-in-the-Middle

```text
Previous: R2 Et0/2 → R4 (MAC aabb.cc00.0400)
Current:  R2 Et0/2 → Unknown (MAC dead.beef.1234), not in NetBox

🚨 ALERT: Rogue device on R2 Ethernet0/2
  → [AUTO] Shutdown Et0/2 (isolate rogue device)
  → [ALERT] Notify security team
  → [MANUAL] Physical investigation required

Generated isolation:
  interface Ethernet0/2
   shutdown
   description *** ISOLATED: Rogue device detected ***
```

### Attack Scenario 4: Route Hijacking

```text
Previous: 10.10.0.0/16 via R2 (OSPF, cost 10)
Current:  10.10.0.0/16 via 192.168.99.1 (Static, AD 1)
  ↑ Static route injected, overriding OSPF!
  192.168.99.1 NOT in NetBox as valid next-hop

🚨 ALERT: Route hijacking — traffic to servers redirected
  Risk: Data exfiltration or man-in-the-middle
  → [Remove Rogue Route]  [Investigate]  [Block Next-Hop]
```

### Automated Incident Response Tiers

| Tier | Trigger | Auto-Response | Human Action |
|------|---------|--------------|--------------|
| 🟢 **Tier 0** | Failed login (1–3 attempts) | Log only | None |
| 🟡 **Tier 1** | Brute force (>10 fails/min) | Block source IP, enable login delay | Review 24h |
| 🟡 **Tier 2** | Traffic anomaly (>10x baseline) | Apply rate-limiting ACL | Review 1h |
| 🔴 **Tier 3** | Rogue device detected | Auto-shutdown port | Immediate investigate |
| 🔴 **Tier 4** | Route hijacking | Alert only (too risky to auto-fix) | Immediate response |
| 🔴 **Tier 5** | DDoS (sustained) | Apply edge ACL block | Contact ISP, escalate |

---

## Firewall & DMZ Integration

### Router ACLs as Distributed Firewall

In EVE-NG lab, router ACLs serve as firewall rules (common in enterprise networks):

```text
Internet → R1 (Edge Router + ACLs = "Firewall")
              ├── ACL inbound: block known bad IPs, rate-limit
              ├── ACL outbound: prevent data exfiltration
              └── Et0/1 → "DMZ" subnet
                     ├── Web server
                     └── ACL: only HTTP/HTTPS inbound

           R1 Et0/2 → R2 (Internal Router)
                         ├── ACL: block DMZ → Internal except specific ports
                         └── "Inside" zone
```

### LLM-Managed Firewall Rules

```text
User: "Block all traffic from 203.0.113.0/24 at the edge"

LLM:
  1. Identifies R1 as edge device (from topology + NetBox role)
  2. Checks existing ACLs on external interface
  3. Inserts deny rule BEFORE existing permits

Generated:
  ip access-list extended EDGE_INBOUND
   5 deny ip 203.0.113.0 0.0.0.255 any log     ← new
   10 permit tcp any any eq 443                  ← existing
   20 permit tcp any any eq 80                   ← existing
   100 deny ip any any log                       ← catch-all
```

### EVE-NG Security Demo Topology

```text
"Attacker" VM (Kali Linux)
     │
     ▼
R1 (Edge/Firewall) ──── R2 (Internal)
     │                       │
     ▼                       ▼
R3 (DMZ)              R4 (Server Zone)
└── Web Server VM      └── Database VM

Demo scenarios:
  1. Kali → brute force SSH on R1 → system detects, auto-blocks
  2. Kali → flood traffic on R1 → DDoS detected, rate-limited
  3. Rogue device on R2 → unknown MAC detected, port isolated
  4. Inject bad route → route hijack detected, admin alerted
```

---

## MCP Security Tools (Extensions)

```text
MCP Server (extended with security):
│
├── Existing Network Tools...
│
├── Security Audit Tools:
│   ├── run_security_audit(device_id)        → CIS benchmark checks
│   ├── check_vulnerabilities(device_id)     → CVE correlation
│   ├── analyze_acl_path(src, dst)           → Access path analysis
│   ├── generate_hardening_config(device_id) → Auto-generate fixes
│   └── get_security_score(device_id)        → Compliance scoring
│
└── Threat Response Tools:
    ├── detect_anomaly(device_id)            → Traffic/CPU/login anomalies
    ├── block_source(device_id, ip)          → Generate blocking ACL
    ├── isolate_port(device_id, intf)        → Shutdown suspicious port
    ├── rate_limit(device_id, intf, pps)     → Apply rate limiting
    ├── get_login_history(device_id)         → Parse auth logs
    ├── get_traffic_baseline(device_id)      → Historical stats
    └── incident_response(threat_type)       → Full incident playbook
```

---

## Unified System Architecture (Network + Security)

```text
                    LLM Agent (Gemini)
                         │
           ┌─────────────┼─────────────┐
           │             │             │
    Network Ops    Security Ops    Compliance
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ Config   │  │ Threat   │  │ Hardening│
    │ Generate │  │ Detect   │  │ Audit    │
    │ Deploy   │  │ Block    │  │ CVE Map  │
    │ Rollback │  │ Isolate  │  │ ACL Path │
    └──────────┘  └──────────┘  └──────────┘
           │             │             │
           └─────────────┼─────────────┘
                         │
              Same Infrastructure:
              - SSH/Netmiko drivers
              - Jinja2 templates
              - Config snapshots
              - Change approval workflow
              - MCP Server
```

The same LLM agent handles both networking and security — when user says "configure OSPF" it picks network tools; when user says "block this attacker" it picks security tools.

---

## Security Feature Implementation Priority

| Priority | Feature | Effort | Impact |
|----------|---------|--------|--------|
| **P0** | Security posture audit + hardening templates | Medium | Very High — showpiece |
| **P1** | Config change anomaly detection | Low (extends drift detection) | High |
| **P1** | Encrypted credential management | Low | High for thesis |
| **P1** | Brute force detection + auto-block | Medium | High for demo |
| **P2** | DDoS detection + rate limiting | Medium | High for demo |
| **P2** | CVE mapping (local DB) | Low | Medium |
| **P2** | ACL path analysis | Medium | Medium |
| **P2** | Rogue device detection | Low (extends LLDP/MAC check) | High |
| **P3** | Security compliance dashboard UI | Medium | High visual impact |

---

## Security Dashboard UI Concept

```text
┌──────────────────────────────────────────────────────────────┐
│  🛡️ Security & Compliance Dashboard                        │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │  Security   │ │ Compliance  │ │   Vuln      │           │
│  │  Score: 72% │ │ Score: 85%  │ │ Count: 4    │           │
│  │  ▲ +8%      │ │ ▲ +5%       │ │ ▼ -2        │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
│                                                              │
│  Recent Security Events                                      │
│  ├── 🔴 R1: Telnet still enabled (3 days overdue)           │
│  ├── 🟡 R3: SNMP community changed (pending review)         │
│  ├── 🟢 R2: SSH v1 disabled (auto-fixed 2h ago)            │
│  └── 🟢 R4: Login banner applied (auto-fixed 5h ago)       │
│                                                              │
│  Active Threats                                              │
│  ├── 🔴 Brute force from 10.0.0.99 → R3 (blocked)          │
│  └── 🟡 Traffic spike on R1 Et0/1 (monitoring)             │
│                                                              │
│  Hardening Progress          CVE Status                      │
│  R1 ████████░░ 80%           R1 🔴 2 critical               │
│  R2 ██████████ 100%          R2 ✅ patched                   │
│  R3 ██████░░░░ 60%           R3 🔴 2 critical               │
│  R4 ███████░░░ 70%           R4 🔴 2 critical               │
│                                                              │
│  [Run Full Audit]  [Generate Report]  [Hardening Wizard]     │
└──────────────────────────────────────────────────────────────┘
```

---

## Security Implementation Plan (Low-Level Detail)

### Technology Stack for Security Features

| Component | Technology | Why |
|-----------|-----------|-----|
| **Device data collection** | Netmiko + Paramiko (existing SSH) | Already working in `mini_nce.py`, reuse for security-relevant commands |
| **Config parsing** | Python `re` + `ciscoconfparse2` | Regex for simple checks, `ciscoconfparse2` for structured IOS config parsing (parent-child relationships) |
| **Syslog ingestion** | Python `socketserver.UDPServer` | Lightweight syslog receiver (UDP 514) — devices send logs to your system |
| **Traffic baseline** | SNMP via `pysnmp` or SSH `show interface` polling | Collect interface counters (packets in/out, errors, CRC) |
| **CVE database** | Local SQLite table | Pre-populated with known Cisco/Huawei CVEs, no external dependency |
| **Security rules engine** | Python dataclasses + JSON rule definitions | Extensible, vendor-agnostic rule format |
| **Alerting** | FastAPI WebSocket (existing) + optional email via `smtplib` | Real-time push to dashboard, email for critical alerts |
| **Credential storage** | `cryptography.fernet` (AES-128-CBC) | Encrypt device passwords at rest, decrypt only at SSH time |
| **Audit logging** | SQLite `audit_log` table | Immutable log of all config changes, security events, and admin actions |
| **Scheduling** | `APScheduler` (or `asyncio` periodic tasks) | Periodic security scans without heavy dependencies like Celery |

### Python Dependencies to Add

```text
# Security-specific additions to requirements.txt
ciscoconfparse2>=0.7.0    # Structured IOS config parsing
pysnmp>=6.0               # SNMP polling for traffic counters (optional)
cryptography>=42.0        # Credential encryption (Fernet)
apscheduler>=3.10         # Periodic scan scheduling
jinja2>=3.1               # Already needed for config templates
```

### Backend File Structure

```text
backend/
├── mini_nce.py                    # Existing crawler (unchanged)
├── backend.py                     # Existing FastAPI server
├── security/                      # NEW: Security module
│   ├── __init__.py
│   ├── engine.py                  # Main security engine (orchestrates all checks)
│   ├── rules.py                   # Security rule definitions (dataclasses)
│   ├── checks/                    # Individual check implementations
│   │   ├── __init__.py
│   │   ├── auth_checks.py         # enable secret, AAA, local users
│   │   ├── transport_checks.py    # SSH v2, no telnet, VTY ACLs
│   │   ├── snmp_checks.py         # community strings, SNMP v3
│   │   ├── logging_checks.py      # syslog, timestamps
│   │   ├── mgmt_plane_checks.py   # HTTP server, CDP, unused ports
│   │   └── banner_checks.py       # login/MOTD banners
│   ├── threat/                    # Threat detection
│   │   ├── __init__.py
│   │   ├── anomaly_detector.py    # Traffic/CPU/login anomaly detection
│   │   ├── syslog_receiver.py     # UDP syslog listener + parser
│   │   ├── login_monitor.py       # Brute force detection
│   │   └── baseline.py            # Traffic baseline calculation
│   ├── response/                  # Incident response
│   │   ├── __init__.py
│   │   ├── responder.py           # Auto-response actions (block, isolate, rate-limit)
│   │   └── playbooks.py           # Incident response playbook definitions
│   ├── cve/                       # Vulnerability correlation
│   │   ├── __init__.py
│   │   ├── cve_db.py              # SQLite CVE lookup
│   │   └── cve_data.json          # Pre-populated CVE entries
│   ├── credentials.py             # Fernet encryption for device passwords
│   └── audit.py                   # Audit logging
├── templates/                     # Jinja2 templates
│   ├── hardening/                 # Security hardening templates
│   │   ├── cisco_ios_harden.j2    # Full hardening template
│   │   ├── cisco_ssh_only.j2      # Disable telnet, enable SSH v2
│   │   ├── cisco_snmp_v3.j2       # SNMP v3 setup
│   │   ├── cisco_vty_acl.j2       # VTY access restriction
│   │   ├── cisco_logging.j2       # Syslog + timestamps
│   │   └── cisco_banner.j2        # Login banner
│   ├── incident/                  # Incident response templates
│   │   ├── block_source_ip.j2     # ACL to block attacker IP
│   │   ├── isolate_port.j2        # Shutdown + description
│   │   ├── rate_limit.j2          # Traffic rate limiting
│   │   └── login_hardening.j2     # login block-for config
│   └── network/                   # Existing network config templates
│       ├── cisco_ospf.j2
│       ├── cisco_bgp.j2
│       └── ...
└── db/
    ├── schema.sql                 # Database schema including security tables
    └── security.db                # SQLite database
```

### Database Schema for Security

```sql
-- Security audit results
CREATE TABLE security_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_hostname TEXT NOT NULL,
    device_ip TEXT NOT NULL,
    audit_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    overall_score REAL,              -- 0.0 to 100.0
    total_checks INTEGER,
    passed_checks INTEGER,
    failed_checks INTEGER,
    critical_count INTEGER,
    warning_count INTEGER
);

-- Individual check results per audit
CREATE TABLE audit_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER REFERENCES security_audits(id),
    check_category TEXT NOT NULL,     -- 'authentication', 'transport', etc.
    check_name TEXT NOT NULL,         -- 'enable_secret_present'
    severity TEXT NOT NULL,           -- 'critical', 'warning', 'pass'
    status TEXT NOT NULL,             -- 'pass', 'fail'
    description TEXT,                 -- Human-readable finding
    remediation_config TEXT,          -- Generated fix config (if applicable)
    evidence TEXT                     -- Relevant config snippet that triggered finding
);

-- Security events / threats
CREATE TABLE security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    device_hostname TEXT NOT NULL,
    device_ip TEXT NOT NULL,
    event_type TEXT NOT NULL,         -- 'brute_force', 'ddos', 'rogue_device', etc.
    severity TEXT NOT NULL,           -- 'tier_0' through 'tier_5'
    description TEXT,
    source_ip TEXT,                   -- Attacker IP if applicable
    auto_response_taken TEXT,         -- What the system did automatically
    admin_action_required BOOLEAN DEFAULT TRUE,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_by TEXT,
    resolved_at DATETIME
);

-- Config change tracking (for anomaly detection)
CREATE TABLE config_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_hostname TEXT NOT NULL,
    device_ip TEXT NOT NULL,
    snapshot_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    config_hash TEXT NOT NULL,        -- SHA-256 of running config
    running_config TEXT NOT NULL,     -- Full config text
    change_detected BOOLEAN DEFAULT FALSE,
    change_authorized BOOLEAN,       -- NULL = pending review
    diff_summary TEXT                 -- Human-readable diff
);

-- Traffic baselines for anomaly detection
CREATE TABLE traffic_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_hostname TEXT NOT NULL,
    interface_name TEXT NOT NULL,
    baseline_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    avg_input_pps REAL,              -- Average packets per second
    avg_output_pps REAL,
    avg_input_bps REAL,              -- Average bits per second
    avg_output_bps REAL,
    avg_cpu_percent REAL,
    avg_connection_count INTEGER,
    stddev_input_pps REAL,           -- Standard deviation for thresholds
    sample_count INTEGER             -- Number of samples in baseline
);

-- CVE lookup table
CREATE TABLE known_cves (
    cve_id TEXT PRIMARY KEY,         -- 'CVE-2023-20198'
    vendor TEXT NOT NULL,            -- 'cisco', 'huawei'
    affected_versions TEXT NOT NULL, -- JSON array: ['15.4', '15.5']
    cvss_score REAL,
    severity TEXT,                   -- 'critical', 'high', 'medium', 'low'
    description TEXT,
    mitigation TEXT,                 -- Config commands to mitigate
    fix_version TEXT                 -- Version that fixes the CVE
);

-- Immutable audit trail
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    actor TEXT NOT NULL,              -- 'system', 'admin', 'llm_agent'
    action TEXT NOT NULL,             -- 'config_push', 'port_isolate', etc.
    target_device TEXT,
    target_interface TEXT,
    config_applied TEXT,              -- Exact config pushed
    result TEXT,                      -- 'success', 'failed'
    rollback_config TEXT              -- Config to undo this action
);
```

### Step-by-Step Implementation

#### Step 1: Security Rules Engine (`security/rules.py`)

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable

class Severity(Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"

class CheckCategory(Enum):
    AUTHENTICATION = "authentication"
    TRANSPORT = "transport"
    ACCESS_CONTROL = "access_control"
    SNMP = "snmp"
    LOGGING = "logging"
    MANAGEMENT = "management"
    PASSWORD = "password"
    UNUSED_PORTS = "unused_ports"
    BANNER = "banner"
    NTP = "ntp"

@dataclass
class SecurityRule:
    """Defines a single security check."""
    name: str                        # 'enable_secret_present'
    category: CheckCategory
    severity: Severity
    description: str                 # 'Enable secret should be configured'
    check_fn: str                    # Name of check function to call
    remediation_template: Optional[str] = None  # Jinja2 template path
    vendor: str = "cisco_ios"        # Vendor this applies to
    auto_fixable: bool = False       # Can be auto-remediated?

# Example rules definition
CISCO_IOS_RULES = [
    SecurityRule(
        name="enable_secret_present",
        category=CheckCategory.AUTHENTICATION,
        severity=Severity.CRITICAL,
        description="Enable secret must be configured for privilege escalation protection",
        check_fn="check_enable_secret",
        remediation_template="hardening/cisco_ios_harden.j2",
        auto_fixable=False,  # Too risky to auto-set passwords
    ),
    SecurityRule(
        name="ssh_v2_only",
        category=CheckCategory.TRANSPORT,
        severity=Severity.CRITICAL,
        description="SSH version 2 must be enabled, SSH v1 disabled",
        check_fn="check_ssh_v2",
        remediation_template="hardening/cisco_ssh_only.j2",
        auto_fixable=True,
    ),
    SecurityRule(
        name="telnet_disabled",
        category=CheckCategory.TRANSPORT,
        severity=Severity.CRITICAL,
        description="Telnet must be disabled on all VTY lines",
        check_fn="check_no_telnet",
        remediation_template="hardening/cisco_ssh_only.j2",
        auto_fixable=True,
    ),
    SecurityRule(
        name="snmp_no_default_community",
        category=CheckCategory.SNMP,
        severity=Severity.CRITICAL,
        description="Default SNMP communities (public/private) must not be used",
        check_fn="check_snmp_community",
        remediation_template="hardening/cisco_snmp_v3.j2",
        auto_fixable=False,
    ),
    SecurityRule(
        name="vty_acl_applied",
        category=CheckCategory.ACCESS_CONTROL,
        severity=Severity.WARNING,
        description="VTY lines should have an access-class applied",
        check_fn="check_vty_acl",
        remediation_template="hardening/cisco_vty_acl.j2",
        auto_fixable=False,
    ),
    SecurityRule(
        name="logging_configured",
        category=CheckCategory.LOGGING,
        severity=Severity.WARNING,
        description="Syslog server should be configured",
        check_fn="check_logging",
        remediation_template="hardening/cisco_logging.j2",
        auto_fixable=True,
    ),
    SecurityRule(
        name="login_banner_present",
        category=CheckCategory.BANNER,
        severity=Severity.WARNING,
        description="Login banner must be configured for legal compliance",
        check_fn="check_banner",
        remediation_template="hardening/cisco_banner.j2",
        auto_fixable=True,
    ),
    SecurityRule(
        name="password_encryption",
        category=CheckCategory.PASSWORD,
        severity=Severity.WARNING,
        description="Password encryption service should be enabled",
        check_fn="check_password_encryption",
        auto_fixable=True,
    ),
    SecurityRule(
        name="http_server_disabled",
        category=CheckCategory.MANAGEMENT,
        severity=Severity.WARNING,
        description="HTTP management server should be disabled",
        check_fn="check_http_disabled",
        auto_fixable=True,
    ),
    SecurityRule(
        name="console_timeout",
        category=CheckCategory.ACCESS_CONTROL,
        severity=Severity.WARNING,
        description="Console line should have exec-timeout configured",
        check_fn="check_console_timeout",
        auto_fixable=True,
    ),
]
```

#### Step 2: Individual Security Checks (`security/checks/auth_checks.py`)

```python
import re
from typing import Dict, Any

def check_enable_secret(running_config: str, **kwargs) -> Dict[str, Any]:
    """Check if enable secret (type 5/8/9) is configured."""
    has_secret = bool(re.search(
        r"^enable secret \d+ \S+", running_config, re.MULTILINE
    ))
    has_password_only = bool(re.search(
        r"^enable password", running_config, re.MULTILINE
    ))

    if has_secret:
        return {"status": "pass", "evidence": "enable secret configured"}
    elif has_password_only:
        return {
            "status": "fail",
            "evidence": "Only 'enable password' found (type 7, easily reversible). Use 'enable secret' instead.",
        }
    else:
        return {
            "status": "fail",
            "evidence": "No enable secret or enable password configured",
        }

def check_aaa_enabled(running_config: str, **kwargs) -> Dict[str, Any]:
    """Check if AAA new-model is enabled."""
    if re.search(r"^aaa new-model", running_config, re.MULTILINE):
        return {"status": "pass", "evidence": "aaa new-model enabled"}
    return {
        "status": "fail",
        "evidence": "AAA not enabled — using legacy authentication",
    }
```

```python
# security/checks/transport_checks.py

import re
from typing import Dict, Any

def check_ssh_v2(running_config: str, **kwargs) -> Dict[str, Any]:
    """Check if SSH version 2 is enforced."""
    if re.search(r"^ip ssh version 2", running_config, re.MULTILINE):
        return {"status": "pass", "evidence": "SSH version 2 enforced"}
    return {
        "status": "fail",
        "evidence": "SSH version 2 not explicitly set — may allow SSHv1",
    }

def check_no_telnet(running_config: str, **kwargs) -> Dict[str, Any]:
    """Check that VTY lines only allow SSH, not telnet."""
    # Find all 'line vty' blocks
    vty_blocks = re.findall(
        r"^line vty.*?(?=^line |\Z)", running_config,
        re.MULTILINE | re.DOTALL
    )
    for block in vty_blocks:
        if "transport input ssh" in block:
            continue
        if "transport input all" in block or "transport input telnet" in block:
            return {
                "status": "fail",
                "evidence": f"Telnet enabled on VTY: {block.strip()[:100]}",
            }
        if "transport input" not in block:
            return {
                "status": "fail",
                "evidence": "No 'transport input' specified — telnet is default",
            }
    return {"status": "pass", "evidence": "All VTY lines restrict to SSH only"}
```

#### Step 3: Security Engine (`security/engine.py`)

```python
import hashlib
from typing import List, Dict
from datetime import datetime
from .rules import CISCO_IOS_RULES, SecurityRule
from .checks import auth_checks, transport_checks, snmp_checks  # etc.

# Map check function names to actual functions
CHECK_FUNCTIONS = {
    "check_enable_secret": auth_checks.check_enable_secret,
    "check_ssh_v2": transport_checks.check_ssh_v2,
    "check_no_telnet": transport_checks.check_no_telnet,
    # ... register all check functions
}

class SecurityEngine:
    def __init__(self, db_connection):
        self.db = db_connection

    def run_audit(self, device_hostname: str, device_ip: str,
                  running_config: str, vendor: str = "cisco_ios") -> Dict:
        """Run all security checks against a device's running config."""

        rules = [r for r in CISCO_IOS_RULES if r.vendor == vendor]
        findings = []

        for rule in rules:
            check_fn = CHECK_FUNCTIONS.get(rule.check_fn)
            if not check_fn:
                continue

            result = check_fn(running_config=running_config)
            findings.append({
                "check_name": rule.name,
                "category": rule.category.value,
                "severity": rule.severity.value,
                "description": rule.description,
                "status": result["status"],
                "evidence": result.get("evidence", ""),
                "auto_fixable": rule.auto_fixable,
                "remediation_template": rule.remediation_template,
            })

        # Calculate score
        total = len(findings)
        passed = sum(1 for f in findings if f["status"] == "pass")
        score = (passed / total * 100) if total > 0 else 0

        audit_result = {
            "device_hostname": device_hostname,
            "device_ip": device_ip,
            "timestamp": datetime.utcnow().isoformat(),
            "score": round(score, 1),
            "total_checks": total,
            "passed": passed,
            "failed": total - passed,
            "critical_count": sum(1 for f in findings
                                  if f["status"] == "fail" and f["severity"] == "critical"),
            "warning_count": sum(1 for f in findings
                                  if f["status"] == "fail" and f["severity"] == "warning"),
            "findings": findings,
        }

        # Save to database
        self._save_audit(audit_result)
        return audit_result

    def detect_config_change(self, device_hostname: str, device_ip: str,
                             current_config: str) -> Dict:
        """Compare current config against last snapshot, detect changes."""
        current_hash = hashlib.sha256(current_config.encode()).hexdigest()
        last_snapshot = self._get_last_snapshot(device_hostname)

        if last_snapshot and last_snapshot["config_hash"] != current_hash:
            diff = self._compute_diff(last_snapshot["running_config"], current_config)
            return {
                "changed": True,
                "device": device_hostname,
                "diff": diff,
                "previous_hash": last_snapshot["config_hash"],
                "current_hash": current_hash,
            }
        return {"changed": False, "device": device_hostname}
```

#### Step 4: Syslog Receiver for Threat Detection (`security/threat/syslog_receiver.py`)

```python
import socketserver
import re
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# Track login failures per source IP
login_failures = defaultdict(list)  # ip -> [timestamps]

# Syslog patterns for Cisco IOS
PATTERNS = {
    "login_failed": re.compile(
        r"%SEC_LOGIN-\d-LOGIN_FAILED.*from\s+(\d+\.\d+\.\d+\.\d+)"
    ),
    "login_success": re.compile(
        r"%SEC_LOGIN-\d-LOGIN_SUCCESS.*from\s+(\d+\.\d+\.\d+\.\d+)"
    ),
    "interface_down": re.compile(
        r"%LINK-\d-UPDOWN.*Interface\s+(\S+).*down"
    ),
    "interface_up": re.compile(
        r"%LINK-\d-UPDOWN.*Interface\s+(\S+).*up"
    ),
    "config_changed": re.compile(
        r"%SYS-\d-CONFIG_I"
    ),
}

BRUTE_FORCE_THRESHOLD = 10   # failures
BRUTE_FORCE_WINDOW = 60      # seconds
FLAP_THRESHOLD = 5           # up/down events
FLAP_WINDOW = 300            # seconds

class SyslogHandler(socketserver.BaseRequestHandler):
    """Handle incoming syslog messages (UDP 514)."""

    def handle(self):
        data = self.request[0].strip().decode("utf-8", errors="replace")
        source_ip = self.client_address[0]

        # Parse and classify
        for event_type, pattern in PATTERNS.items():
            match = pattern.search(data)
            if match:
                self._process_event(event_type, match, source_ip, data)
                break

    def _process_event(self, event_type, match, device_ip, raw_message):
        if event_type == "login_failed":
            attacker_ip = match.group(1)
            login_failures[attacker_ip].append(datetime.now())

            # Prune old entries
            cutoff = datetime.now().timestamp() - BRUTE_FORCE_WINDOW
            login_failures[attacker_ip] = [
                t for t in login_failures[attacker_ip]
                if t.timestamp() > cutoff
            ]

            if len(login_failures[attacker_ip]) >= BRUTE_FORCE_THRESHOLD:
                self._trigger_alert(
                    event_type="brute_force",
                    severity="tier_1",
                    device_ip=device_ip,
                    source_ip=attacker_ip,
                    description=(
                        f"Brute force: {len(login_failures[attacker_ip])} "
                        f"failed logins from {attacker_ip} in {BRUTE_FORCE_WINDOW}s"
                    ),
                )

def start_syslog_server(host="0.0.0.0", port=514):
    """Start UDP syslog listener in background thread."""
    server = socketserver.UDPServer((host, port), SyslogHandler)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Syslog receiver started on {host}:{port}")
    return server
```

#### Step 5: Traffic Anomaly Detection (`security/threat/anomaly_detector.py`)

```python
from typing import Dict, Optional
import re

def parse_interface_counters(show_interface_output: str) -> Dict:
    """Parse 'show interface' output for traffic counters."""
    counters = {}
    # Match: "5 minute input rate 1000 bits/sec, 2 packets/sec"
    rate_match = re.search(
        r"5 minute input rate (\d+) bits/sec, (\d+) packets/sec",
        show_interface_output
    )
    if rate_match:
        counters["input_bps"] = int(rate_match.group(1))
        counters["input_pps"] = int(rate_match.group(2))

    rate_match = re.search(
        r"5 minute output rate (\d+) bits/sec, (\d+) packets/sec",
        show_interface_output
    )
    if rate_match:
        counters["output_bps"] = int(rate_match.group(1))
        counters["output_pps"] = int(rate_match.group(2))

    return counters

def check_traffic_anomaly(
    current: Dict, baseline: Dict, threshold_multiplier: float = 10.0
) -> Optional[Dict]:
    """
    Compare current traffic counters against baseline.
    Trigger alert if current > baseline * threshold_multiplier.
    """
    alerts = []

    if baseline.get("avg_input_pps", 0) > 0:
        ratio = current.get("input_pps", 0) / baseline["avg_input_pps"]
        if ratio > threshold_multiplier:
            alerts.append({
                "metric": "input_pps",
                "current": current["input_pps"],
                "baseline": baseline["avg_input_pps"],
                "ratio": round(ratio, 1),
                "description": f"Input traffic {ratio:.0f}x above baseline",
            })

    # Similar checks for output_pps, input_bps, output_bps...
    return alerts if alerts else None

def parse_cpu_utilization(show_processes_cpu: str) -> Optional[float]:
    """Parse CPU utilization from 'show processes cpu'."""
    # "CPU utilization for five seconds: 95%/0%"
    match = re.search(r"five seconds:\s*(\d+)%", show_processes_cpu)
    return float(match.group(1)) if match else None
```

#### Step 6: Automated Incident Response (`security/response/responder.py`)

```python
from jinja2 import Environment, FileSystemLoader
from typing import Dict

# Load Jinja2 templates
jinja_env = Environment(
    loader=FileSystemLoader("templates/incident"),
    trim_blocks=True,
    lstrip_blocks=True,
)

class IncidentResponder:
    def __init__(self, ssh_executor, db, audit_logger):
        self.ssh = ssh_executor          # Your existing SSH functions
        self.db = db
        self.audit = audit_logger

    def block_source_ip(self, device_ip: str, attacker_ip: str,
                         hop_chain=None, auto_apply=False) -> Dict:
        """Generate ACL to block attacker IP. Optionally auto-apply."""
        template = jinja_env.get_template("block_source_ip.j2")
        config = template.render(
            attacker_ip=attacker_ip,
            acl_name=f"BLOCK_{attacker_ip.replace('.', '_')}",
        )

        result = {
            "action": "block_source_ip",
            "target_device": device_ip,
            "attacker_ip": attacker_ip,
            "generated_config": config,
            "applied": False,
        }

        if auto_apply:
            # Push config via existing SSH infrastructure
            ssh_result = self.ssh.ssh_invoke_via_chain(
                hop_chain=hop_chain or [],
                target_host=device_ip,
                user=self.ssh.user,
                password=self.ssh.password,
                key_file=None,
                commands=["conf t"] + config.split("\n") + ["end", "wr mem"],
            )
            result["applied"] = True
            result["ssh_result"] = ssh_result

            # Log to audit trail
            self.audit.log(
                actor="system",
                action="auto_block_ip",
                target_device=device_ip,
                config_applied=config,
                result="success" if ssh_result else "failed",
            )

        return result

    def isolate_port(self, device_ip: str, interface: str,
                      hop_chain=None, auto_apply=False) -> Dict:
        """Shutdown an interface to isolate a rogue device."""
        template = jinja_env.get_template("isolate_port.j2")
        config = template.render(interface=interface)

        result = {
            "action": "isolate_port",
            "target_device": device_ip,
            "interface": interface,
            "generated_config": config,
            "rollback_config": f"interface {interface}\n no shutdown",
            "applied": False,
        }

        if auto_apply:
            # Same SSH push pattern as above
            ...

        return result
```

#### Step 7: Jinja2 Hardening Templates

```jinja2
{# templates/hardening/cisco_ssh_only.j2 #}
! --- Disable Telnet, Enable SSH v2 Only ---
ip ssh version 2
ip ssh time-out 60
ip ssh authentication-retries 3
!
line vty 0 4
 transport input ssh
 exec-timeout 10 0
line vty 5 15
 transport input ssh
 exec-timeout 10 0
```

```jinja2
{# templates/hardening/cisco_logging.j2 #}
! --- Configure Syslog Logging ---
service timestamps log datetime msec localtime show-timezone
service timestamps debug datetime msec localtime show-timezone
logging buffered 16384 informational
{% if syslog_server is defined %}
logging host {{ syslog_server }}
{% endif %}
logging trap informational
```

```jinja2
{# templates/hardening/cisco_banner.j2 #}
! --- Login Banner ---
banner login ^
*************************************************************
* WARNING: Unauthorized access to this system is prohibited.*
* All activities on this system are logged and monitored.   *
* Violators will be prosecuted to the full extent of law.   *
*************************************************************
^
```

```jinja2
{# templates/incident/block_source_ip.j2 #}
! --- Block Attacker IP: {{ attacker_ip }} ---
ip access-list extended {{ acl_name }}
 deny ip host {{ attacker_ip }} any log
 permit ip any any
!
{% if interface is defined %}
interface {{ interface }}
 ip access-group {{ acl_name }} in
{% endif %}
```

```jinja2
{# templates/incident/isolate_port.j2 #}
! --- Isolate Rogue Device on {{ interface }} ---
interface {{ interface }}
 shutdown
 description *** ISOLATED: Rogue device detected {{ timestamp | default('') }} ***
```

#### Step 8: FastAPI Security Endpoints

```python
# Add to backend.py (or a new security_routes.py)

from fastapi import APIRouter
from security.engine import SecurityEngine
from security.threat.anomaly_detector import check_traffic_anomaly

security_router = APIRouter(prefix="/api/security", tags=["security"])

@security_router.post("/audit/{device_hostname}")
async def run_security_audit(device_hostname: str):
    """Run CIS-style security audit on a device."""
    # 1. Fetch running config (use existing crawl infrastructure)
    # 2. Run SecurityEngine.run_audit()
    # 3. Return findings + score
    ...

@security_router.get("/audit/latest/{device_hostname}")
async def get_latest_audit(device_hostname: str):
    """Get most recent audit results for a device."""
    ...

@security_router.get("/events")
async def get_security_events(severity: str = None, resolved: bool = None):
    """List security events, optionally filtered."""
    ...

@security_router.post("/respond/block")
async def block_attacker(device_ip: str, attacker_ip: str, auto_apply: bool = False):
    """Generate or apply an IP block ACL."""
    ...

@security_router.post("/respond/isolate")
async def isolate_port(device_ip: str, interface: str, auto_apply: bool = False):
    """Generate or apply port isolation config."""
    ...

@security_router.get("/cve/{device_hostname}")
async def check_cves(device_hostname: str):
    """Check device OS version against known CVE database."""
    ...

@security_router.get("/score/all")
async def get_all_security_scores():
    """Get security scores for all discovered devices."""
    ...

@security_router.get("/compliance/report")
async def generate_compliance_report(format: str = "json"):
    """Generate full compliance report (JSON or PDF)."""
    ...
```

#### Step 9: Credential Encryption (`security/credentials.py`)

```python
import os
import json
from cryptography.fernet import Fernet

CREDENTIAL_FILE = "db/credentials.enc"
KEY_FILE = "db/credential.key"

def _get_or_create_key() -> bytes:
    """Get encryption key from file, or generate one."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    return key

def encrypt_credentials(credentials: dict) -> None:
    """Encrypt and save device credentials."""
    fernet = Fernet(_get_or_create_key())
    data = json.dumps(credentials).encode()
    encrypted = fernet.encrypt(data)
    with open(CREDENTIAL_FILE, "wb") as f:
        f.write(encrypted)

def decrypt_credentials() -> dict:
    """Load and decrypt device credentials."""
    fernet = Fernet(_get_or_create_key())
    with open(CREDENTIAL_FILE, "rb") as f:
        encrypted = f.read()
    data = fernet.decrypt(encrypted)
    return json.loads(data.decode())
```

#### Step 10: Scheduling Periodic Scans

```python
# In backend.py startup or a dedicated scheduler module
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# Security audit every 6 hours
@scheduler.scheduled_job('interval', hours=6)
async def periodic_security_audit():
    """Run security audit on all known devices."""
    devices = load_topology()
    for device in devices["nodes"].values():
        config = fetch_running_config(device["mgmt_ip"])
        engine.run_audit(device["hostname"], device["mgmt_ip"], config)

# Config change detection every 30 minutes
@scheduler.scheduled_job('interval', minutes=30)
async def periodic_config_check():
    """Check for unauthorized config changes."""
    devices = load_topology()
    for device in devices["nodes"].values():
        config = fetch_running_config(device["mgmt_ip"])
        change = engine.detect_config_change(
            device["hostname"], device["mgmt_ip"], config
        )
        if change["changed"]:
            # Classify and alert
            await notify_admin(change)

# Traffic baseline update every hour
@scheduler.scheduled_job('interval', hours=1)
async def update_traffic_baselines():
    """Refresh traffic baselines for anomaly detection."""
    ...

scheduler.start()
```

### Implementation Order (Within the Timeline)

```text
Phase 1 — Q2 Month 5-6 (alongside LLM engine):
  [1] security/rules.py              — Define all check rules
  [2] security/checks/*.py           — Implement each check function
  [3] security/engine.py             — Orchestrate checks, compute score
  [4] templates/hardening/*.j2       — Hardening templates
  [5] Security API endpoints          — /api/security/audit/*
  [6] security/credentials.py        — Encrypted credential storage
  [7] Database schema                — security tables
  → Deliverable: "Run audit on R1" returns score + findings + fix configs

Phase 2 — Q3 Month 7-8 (alongside deployment pipeline):
  [8] security/threat/syslog_receiver.py — Syslog listener
  [9] security/threat/login_monitor.py   — Brute force detection
  [10] security/threat/anomaly_detector.py — Traffic anomaly
  [11] security/response/responder.py    — Auto-response actions
  [12] templates/incident/*.j2           — Incident response templates
  [13] APScheduler periodic scans        — Automated checks
  → Deliverable: Live demo — attack from Kali, system detects + responds

Phase 3 — Q4 Month 10 (polish):
  [14] security/cve/cve_db.py           — CVE lookup
  [15] Frontend security dashboard       — React components
  [16] PDF compliance report generation  — ReportLab or WeasyPrint
  → Deliverable: Security dashboard + PDF report for thesis
```

---

## Updated 1-Year Timeline (Final)

| Quarter | Focus | Deliverables |
|---------|-------|-------------|
| **Q1** (Month 1–3) | Foundation | Huawei VRP driver, NetBox setup + integration, unified topology discovery, database schema, config snapshot system, Jinja2 template library |
| **Q2** (Month 4–6) | LLM + Config Engine | MCP Server, Gemini-powered tool-calling agent, intent → Jinja2 parameter extraction, config diff preview UI, drift detection (NetBox vs actual), security posture audit engine + hardening templates |
| **Q3** (Month 7–9) | Deployment & Security | 6-stage deployment pipeline, Ansible integration, rollback system, Juniper driver, auto-remediation, syslog receiver, threat detection (brute force, DDoS, rogue device), incident response with auto-block/isolate |
| **Q4** (Month 10–12) | Polish & Thesis | Self-hosted LLM (Ollama), compliance + security dashboard UI, CVE mapping, ACL path analysis, PDF report generation, NETCONF, testing, documentation, thesis |

