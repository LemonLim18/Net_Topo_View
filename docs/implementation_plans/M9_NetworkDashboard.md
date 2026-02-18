# M9: Network Topology Dashboard — Implementation Plan

Build the unified frontend dashboard and refactor the backend to integrate M2 (Crawler), M4 (Deployment), and M5 (Agent) into a single application.

## Goal
A fully functional web dashboard where the user can:
1.  **Visualize Topology**: See the network graph (M2).
2.  **Chat with AI**: Ask the agent to plan changes (M5).
3.  **Manage Deployments**: Approve/Rollback configuration jobs (M4).
4.  **View Inventory**: See device details (M14).

## User Review Required
> [!IMPORTANT]
> This refactor will replace the existing standalone `backend.py` with a modular FastAPI app that mounts M2, M4, and M5 routers.

## Proposed Changes

### Backend Refactor

#### [NEW] [backend/crawler/api.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/crawler/api.py)
Move topology endpoints from `backend.py` to a dedicated M2 router:
-   `GET /api/topology`: Returns current topology from `DatabaseManager`/`TopologyStore`.
-   `POST /api/crawl`: Triggers a manual crawl.
-   `WebSocket /ws`: Streams topology updates (moved from `backend.py`).

#### [MODIFY] [backend/backend.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/backend.py)
Refactor to be the main entry point:
-   Initialize `DatabaseManager` (SQLite).
-   Initialize `DeploymentPipeline` (M4).
-   Initialize `NetworkAgent` (M5).
-   Mount routers:
    -   `backend.crawler.api.router` -> `/api/v1/topology`
    -   `backend.deploy.api.router` -> `/api/v1/deploy`
    -   `backend.llm.api.router` -> `/api/v1/llm`
-   Start periodic crawler task.

---

### Frontend Implementation

#### [MODIFY] [frontend/src/config.ts](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/src/config.ts)
Update API base URLs to match new backend structure (`/api/v1/...`).

#### [NEW] [frontend/src/services/api.ts](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/src/services/api.ts)
Unified API client for all modules:
-   `topology`: fetch topology, trigger crawl.
-   `deploy`: list jobs, approve, rollback, get logs.
-   `llm`: chat, clear history.

#### [MODIFY] [frontend/views/Dashboard.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/Dashboard.tsx)
-   Update to use `api.topology.get()` instead of direct fetch.
-   Ensure `processTopologyData` handles M14 data structure correctly.

#### [MODIFY] [frontend/views/AIChat.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/AIChat.tsx)
-   **CRITICAL**: Remove `@google/genai` client-side usage.
-   Replace with calls to `api.llm.chat()` to use the backend M5 Agent (which has tools/context).
-   Remove `frontend/services/gemini.ts`.

#### [NEW] [frontend/views/Deployments.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/Deployments.tsx)
New dashboard view for M4:
-   Table of `jobs` (Status, Device, Created At).
-   Action buttons: `Approve`, `Rollback`.
-   Log viewer for `job_steps`.

#### [MODIFY] [frontend/src/App.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/src/App.tsx)
-   Add route for `/deployments`.
-   Add `Deployments` to sidebar.

---

## Verification Plan

### Manual Verification
1.  **Startup**: Run `python backend/backend.py` and `npm run dev`. Verify no errors.
2.  **Topology**: Check http://localhost:5173/ shows the graph. Trigger "Refresh" and verify backend logs show crawl.
3.  **Agent**: Open Chat. Ask "What devices are in the network?". Verify it hits `POST /api/v1/llm/chat` and returns correct answer using M5 tools.
4.  **Deployment**:
    -   Ask Agent: "Create a plan to change description on R1".
    -   Agent should call `create_change_request`.
    -   Go to "Deployments" view. See new job.
    -   Click "Approve" (mock/implement if time permits, or CLI verify).
