# M6: MCP Server — Implementation Plan

Implement a **Model Context Protocol (MCP)** server to expose the Network Controller's capabilities (Topology, Config, Deployment) to external AI clients (e.g., Claude Desktop, IDEs, or other LLM agents).

## Goal
Make the "NetTopoView" controller an agentic tool provider. External agents can discover and use our network tools without knowing the internal API details.

## User Review Required
> [!IMPORTANT]
> **Transport Mode**: We will implement this as a **Stdio** server by default (best for Claude Desktop integration). We can also expose an SSE (Server-Sent Events) endpoint in FastAPI if remote access is needed later.
> **Dependency**: Requires `mcp` Python package (`pip install mcp`).

## Architecture
The MCP Server acts as a bridge:
`External Agent` <-> `MCP Protocol (JSON-RPC)` <-> `M6 Server` <-> `Backend Modules (M2, M3, M4)`

1.  **Resources (`network://...`)**: Read-only data access.
    -   `network://topology`: Current graph (JSON).
    -   `network://device/{hostname}/config`: Running config.
    -   `network://device/{hostname}/logs`: Recent audit logs.
2.  **Tools (Function Calls)**: Actions the agent can take.
    -   `get_device(hostname)`: Detailed info.
    -   `run_show_command(hostname, command)`: Live execution (M1).
    -   `generate_config(intent)`: Use M3 engine.
    -   `deploy_change(request)`: Trigger M4 pipeline.
3.  **Prompts**: Pre-defined workflows.
    -   `troubleshoot_device`: Context + logs + show commands.

## Proposed Changes

### Backend Implementation

#### [NEW] [backend/mcp/server.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/server.py)
-   Initialize `FastMCP` server ("NetAutoController").
-   Initialize `DatabaseManager` and `NetworkAgent`.
-   Define entry point `run_mcp_server()`.

#### [NEW] [backend/mcp/resources.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/resources.py)
-   `@mcp.resource("network://topology")`: Returns `db.get_topology()`.
-   `@mcp.resource("network://device/{hostname}/config")`: Returns latest snapshot.

#### [NEW] [backend/mcp/tools.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/tools.py)
-   `@mcp.tool()` wrappers around existing backend logic:
    -   `list_devices()` -> M14 DB.
    -   `get_interfaces(hostname)` -> M14 DB.
    -   `run_command(hostname, cmd)` -> M1 Driver.
    -   `plan_deployment(intent)` -> M5 Agent logic.

#### [MODIFY] [backend/backend.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/backend.py)
-   Add CLI command to run MCP mode (e.g., `python backend.py mcp`).
-   Or verify if we want it running alongside the API (SSE mode). *Per plan: Stdio script is simpler for now.*

### Integration
-   **Claude Desktop Config**: Generate a sample `claude_desktop_config.json` snippet for the user.

---

## Verification Plan
1.  **Install**: `pip install mcp`.
2.  **Run**: `python -m backend.mcp.server`.
3.  **Inspect**: Use "MCP Inspector" (npx @modelcontextprotocol/inspector) to connect to the python script and verify Tools/Resources list appear.
4.  **Test Tool**: Call `list_devices` from Inspector and verify JSON output.
