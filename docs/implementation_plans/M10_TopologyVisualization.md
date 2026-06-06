# M10: Topology Visualization — Implementation Plan

Enable versatile network visualization with "Real-Time", "Intended", and "Diff" views, togglable between D3.js and Cytoscape.js engines.

## Goal
Provide a clear, interactive visual representation of the network state, highlighting discrepancies between the actual implementation (M2) and the source of truth (M7), using two complementary visualization libraries.

## User Review Required
> [!IMPORTANT]
> **New Dependency**: This layout requires installing `cytoscape` and `react-cytoscapejs` via npm.
> **Comparison Logic**: The Diff view currently assumes `constants.ts` mock data is the "source of truth". We should eventually hook this up to the real M7 backend.

## Proposed Changes

### 1. Dependencies
-   `npm install cytoscape react-cytoscapejs` (Frontend)

### 2. Frontend Components

#### [NEW] `frontend/components/NetworkGraphCytoscape.tsx`
-   **Purpose**: Alternative visualization engine using Cytoscape.js.
-   **Features**:
    -   Force-directed layout (e.g., `fcose` or `cose`).
    -   Support `topologyView` prop ('actual' | 'planned' | 'diff').
    -   Style elements based on Diff status (Green/Red/Orange).

#### [MODIFY] `frontend/components/NetworkGraph.tsx` (D3)
-   **Refinement**: Ensure the "Diff" view clearly distinguishes:
    -   **Unplanned** (Green/Warning): Present in Real-Time, missing in Planned. (Previously "Added")
    -   **Missing** (Red/Error): Present in Planned, missing in Real-Time.
    -   **Matching** (Neutral): Present in both.

#### [MODIFY] `frontend/views/Dashboard.tsx`
-   **State**: Add `vizEngine` state ('d3' | 'cytoscape').
-   **UI**: Add toggle button group to switch engines.
-   **Rendering**: Conditionally render `NetworkGraph` or `NetworkGraphCytoscape`.

### 3. Logic Refinement (`Dashboard.tsx`)
-   Enhance `processTopologyData` to ensure stable node IDs and correct link matching for the diff.

## Verification Plan
1.  **Toggle Test**: Switch between D3 and Cytoscape. Ensure graph renders without crashing.
2.  **View Test**: Cycle through Planned/Actual/Diff in both engines.
3.  **Interaction**: Verify zooming, panning, and node clicking works in both engines.
