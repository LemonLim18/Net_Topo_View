# M17: Network Performance Monitoring — Implementation Plan

Implement a real-time network performance monitoring stack using **SNMP**, **Telegraf**, **InfluxDB**, and **Grafana**, integrated into the NetTopoView dashboard.

## Goal
Enable the user to view live statistics (CPU, Memory, Interface Traffic) for network devices directly within the application.

## User Review Required
> [!IMPORTANT]
> This module introduces significant new infrastructure dependencies (InfluxDB, Telegraf, Grafana). These should ideally be run as Docker containers, but for this local environment, we will need to install/configure them or mock the data if full installation is too heavy.
> **Assumed Strategy**: We will implement the *configuration generation* and *frontend integration* logic. We will assume the user (or a script we provide) sets up the TIG stack.

## Architecture
1.  **Data Collection (Telegraf)**:
    -   Telegraf behaves as a Universal Collector.
    -   We dynamically generate `telegraf.conf` based on the Inventory (M14).
    -   **Protocols**: SNMP, Syslog, NetFlow, gNMI, MDT, NETCONF, ICMP.
2.  **Storage (InfluxDB)**:
    -   Time-series database for metrics.
3.  **Visualization (Grafana)**:
    -   Dashboards for "Device Details" and "Network Overview".
    -   Frontend embeds Grafana panels via `<iframe>`.
4.  **Backend Integration**:
    -   `M17 API`: Endpoint to regenerate Telegraf config when inventory changes.
    -   `M17 API`: Proxy endpoint for Grafana (optional, to handle auth).

## Proposed Changes

### Backend Implementation

#### [NEW] [backend/monitor/telegraf_gen.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/monitor/telegraf_gen.py)
Logic to generate `telegraf.conf` from the database.
**Global Listeners**:
-   `[[inputs.syslog]]`: UDP/TCP port 6514.
-   `[[inputs.netflow]]`: UDP port 2055.
-   `[[inputs.cisco_telemetry_mdt]]`: GRPC port 57000.
**Per-Device Inputs**:
-   `[[inputs.snmp]]`: For all enabled devices.
-   `[[inputs.ping]]`: Reachability check for all devices.
-   `[[inputs.gnmi]]`: For devices with matching vendor/capability.
-   `[[inputs.netconf]]`: Basic session check.

#### [NEW] [backend/monitor/api.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/monitor/api.py)
FastAPI Router for M17:
-   `POST /monitor/update-config`: Regenerates `telegraf.conf` and reloads Telegraf service.
-   `GET /monitor/status`: Checks if InfluxDB/Telegraf are responding.

#### [MODIFY] [backend/backend.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/backend.py)
-   Mount `monitor` router.

---

### Frontend Implementation

#### [NEW] [frontend/views/NetworkPerformance.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/NetworkPerformance.tsx)
-   **Dashboard Mode**: displays high-level Grafana dashboard (e.g., "Top Talkers").
-   **Device Mode**: when clicking a device in Topology (M2), show specific stats (CPU/Mem).
-   Use `<iframe>` pointing to `http://localhost:3000/d-solo/...` (Grafana).

#### [MODIFY] [frontend/src/App.tsx](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/src/App.tsx)
-   Add route `/net_perf`.

---

## Verification Plan
1.  **Config Generation**: Run `backend/monitor/telegraf_gen.py`, verify `telegraf.conf` contains valid blocks for all protocols.
2.  **API**: Trigger `POST /api/v1/monitor/update-config`.
3.  **Visualization**: Open `/net_perf` in frontend, verify Grafana iframe loads (requires running Grafana locally).
