# NetBox Integration Verification Plan

This document provides a step-by-step procedure and sample data to test the end-to-end integration between the NetTopoView backend and a remote NetBox instance.

## Prerequisites
1.  **NetBox Instance**: Accessible at the URL specified in your `.env` file (e.g., `NETBOX_URL=http://<virtual-machine-ip>`).
2.  **API Token**: A valid NetBox API token configured in your `.env` file (`NETBOX_TOKEN`).
3.  **Backend Running**: NetTopoView FastAPI backend running (`uvicorn backend:app`).
4.  **Base NetBox Configuration**: The following must exist in NetBox:
    *   A Site with the slug `default`
    *   A Device Type with the slug `generic`
    *   A Role (e.g., `router` or `switch`)

---

## Test Procedure 1: Outbound Sync (NetTopoView -> NetBox)

This tests the ability to push planned devices from the Device Inventory to NetBox.

**Step 1.1: Create a Planned Device in SQLite**
Use the NetTopoView "Device Inventory" UI (or an API call) to create a new "Planned" device:
*   **Hostname**: `R1-TEST`
*   **Management IP**: `192.168.100.1`
*   **Vendor**: `cisco_ios`
*   **Status**: `planned`

**Step 1.2: Push to NetBox**
*   Click the "Push to NetBox" button for `R1-TEST` in the UI.
*   **Expected Result**:
    *   UI shows success.
    *   Device status changes from `planned` to `registered`.
    *   Device appears in the NetBox web UI.

---

## Test Procedure 2: Inbound Poll (NetBox -> NetTopoView)

This tests the `sync_devices.py` script that pulls the authoritative inventory from NetBox on a schedule.

**Step 2.1: Create Test Data in NetBox UI**
Manually create a device in the NetBox web UI:
*   **Name**: `SW1-NETBOX`
*   **Device Role**: `switch`
*   **Device Type**: `generic`
*   **Site**: `default`
*   (Optional) Add a Management Interface and IP (`192.168.100.2/24`).

**Step 2.2: Trigger the Sync Script**
Run the sync script manually from the backend directory:
```bash
python -m netbox.sync_devices
```

**Step 2.3: Verify in NetTopoView**
*   Open the NetTopoView "Device Inventory".
*   **Expected Result**: `SW1-NETBOX` appears in the list with:
    *   Source badge: `NetBox`
    *   Status badge: `Registered` (or `Planned`)

---

## Test Procedure 3: Inbound Push (NetBox Webhooks)

This tests real-time updates from NetBox to NetTopoView using the configured Webhook.

**Step 3.1: Configure Webhook in NetBox**
Ensure the webhook is configured as discussed previously, pointing to your NetTopoView backend IP (e.g., `http://192.168.1.116:8000/netbox/webhook`) with the correct secret.

**Step 3.2: Trigger Webhook (Device Creation)**
*   Create a new device in NetBox UI:
    *   **Name**: `R2-WEBHOOK`
    *   **Role**: `router`
    *   **Site**: `default`

**Step 3.3: Verify Webhook Reception**
*   Check the console where your `uvicorn` backend is running.
*   **Expected Result**: You should see log lines indicating the webhook was received and processed (e.g., `INFO: netbox.webhook: Received webhook for device R2-WEBHOOK`).
*   Check the NetTopoView UI. `R2-WEBHOOK` should appear in the Device Inventory almost instantly.

**Step 3.4: Trigger Webhook (Device Update)**
*   In NetBox UI, rename `R2-WEBHOOK` to `R2-WEBHOOK-UPDATED`.
*   **Expected Result**: The webhook logs fire again, and the UI updates the hostname.

---

## Manual Webhook Simulation (cURL or Postman)

If you want to test the NetTopoView webhook receiver *without* triggering NetBox, you can send these synthetic JSON payloads to your backend using `curl` or Postman.

### Test Payload 1: Device Created
```bash
curl -X POST http://127.0.0.1:8000/netbox/webhook \
  -H "Content-Type: application/json" \
  -H "X-Hook-Signature: [CALCULATED_HMAC]" \
  -d '{
    "event": "created",
    "timestamp": "2026-03-04 16:35:00",
    "model": "device",
    "username": "admin",
    "data": {
        "name": "SIM-DEVICE-01",
        "primary_ip": {"address": "10.0.0.1/24"},
        "device_type": {"slug": "generic"},
        "platform": {"slug": "cisco-ios"},
        "status": {"value": "active"}
    }
}'
```
*(Note: If your backend strictly enforces the HMAC signature `X-Hook-Signature` using your secret, raw curl commands will fail with a 401/403. You may need to temporarily disable signature verification in `webhook.py` for raw testing, or generate a valid HMAC).*

### Test Payload 2: Cable/Link Created
```bash
curl -X POST http://127.0.0.1:8000/netbox/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "event": "created",
    "model": "cable",
    "data": {
        "a_terminations": [
            {
                "object": {
                    "device": {"name": "R1-TEST"},
                    "name": "GigabitEthernet0/0"
                }
            }
        ],
        "b_terminations": [
            {
                "object": {
                    "device": {"name": "SW1-NETBOX"},
                    "name": "FastEthernet0/1"
                }
            }
        ]
    }
}'
```
