# M7: NetBox Integration — Implementation Plan (Executed)

Implemented a bi-directional sync (focusing on Source-of-Truth pull) between NetBox and the Network Controller. This establishes the "Intended State" of the network, which is critical for Drift Detection (M8).

## Goal
Use NetBox as the authoritative Source of Truth (SoT) for device inventory, IP addressing, and physical connections.

## User Review Required
> [!IMPORTANT]
> **Prerequisite**: This module requires a running NetBox instance.
> **Configuration**: Set `NETBOX_URL` and `NETBOX_TOKEN` in your environment.
> **Webhooks**: Configure NetBox to send webhooks to `http://controller:8000/api/v1/netbox/webhook` with the secret `NETBOX_WEBHOOK_SECRET`.

## Architecture
1.  **NetBox Client**: Wrapper around `pynetbox` SDK.
2.  **Sync Engine**:
    -   **Devices**: Hostname, Platform, Management IP.
    -   **Interfaces**: Names, types, IP addresses.
    -   **Topology**: Cables (connections between interfaces).
3.  **Webhook Listener**: FastAPI endpoint to receive "Object Created/Updated" events from NetBox for real-time sync.

## Implementation Details

### Backend
-   **Client**: `backend/netbox/client.py` (Singleton `NetBoxClient`).
-   **Device Sync**: `backend/netbox/sync_devices.py` (`DeviceSyncer`).
    -   Maps NetBox platforms (e.g., `cisco-ios`) to internal drivers.
    -   Upserts devices to `net_topo.db`.
-   **Topology Sync**: `backend/netbox/sync_topology.py` (`TopologySyncer`).
    -   Fetches cables to build intended topology (placeholder logic for full link mapping).
-   **Webhooks**: `backend/netbox/webhook.py`.
    -   Verifies HMAC signature.
    -   Triggers sync on `device` or `cable` events.
-   **API**: `backend/netbox/api.py`.
    -   `POST /netbox/sync/devices`
    -   `POST /netbox/sync/topology`

### Integration
-   **Routers**: Mounted in `backend.py` under `/api/v1/netbox`.
-   **Dependencies**: Added `pynetbox`.

---

## Verification Plan
1.  **Mock Test**: `tests/verify_m7.py` verifies `DeviceSyncer` logic using mocked `pynetbox`.
2.  **Manual Sync**: Trigger `POST /netbox/sync/devices` and check logs.
