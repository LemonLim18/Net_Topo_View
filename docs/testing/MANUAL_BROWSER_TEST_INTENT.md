# Manual browser testing — Intent parsing & validation

Use this to test the intent parsing implementation (safe_mode, validation, from_inventory, schema params, Playbook Studio) in the browser.

## Prerequisites

1. **Backend** running, e.g.:
   ```bash
   uvicorn backend:app --host 0.0.0.0 --port 8000 --reload
   ```
2. **Frontend** running, e.g.:
   ```bash
   npm run dev
   ```
3. **Logged in** so the Ansible API is allowed.
4. (Optional) **DB has at least one device** so inventory resolution can be tested.

---

## 1. Open Ansible Studio

- In the sidebar, click **Ansible Studio** (or use the link that switches the main view to Ansible Studio).
- You should see: file tree, chat panel, and possibly an editor area.

---

## 2. Test params disambiguation (missing required params)

1. In the chat input, type something that clearly asks for a VLAN but **does not** give VLAN ID or name, e.g.:
   - **"Create a VLAN on access switch 1"**
2. Press **Enter** (or click Send).
3. **Expected:** The backend runs preview; if the schema says `vlan_id` / `vlan_name` are required and they’re missing, the response should show a **params disambiguation** form (e.g. “Please provide the following” with fields like VLAN ID, VLAN name).
4. Fill in the fields (e.g. VLAN ID `100`, VLAN name `DATA`) and click **Continue** (or the submit button for that form).
5. **Expected:** Either the **Playbook Studio overlay** opens with your intent and the params you provided, or you see a next step (e.g. device or interface disambiguation).

---

## 3. Test Playbook Studio overlay and schema-driven params

1. Trigger the overlay either:
   - By sending an intent that **does not** need disambiguation (e.g. **"Create VLAN 100 named DATA on SW1"** if SW1 is known), or
   - After completing params disambiguation (step 2).
2. When the **Playbook Studio** modal opens, check:
   - **“Parameters (schema)”** section is visible when the backend returned `schema_editable_fields` (e.g. for VLAN: VLAN ID, VLAN name, target hosts).
   - You can **edit** those fields (e.g. change VLAN ID to `200`).
3. Ensure **Target inventory** (devices) and, if shown, **Target interfaces** are set as you want.
4. Click **Confirm & generate**.
5. **Expected:** A playbook is generated and the overlay shows the result (or closes and the chat shows the generated playbook). The request sent to the backend should include the params you set in the schema form (e.g. `vlan_id`, `vlan_name`).

---

## 4. Test validation errors (VLAN interface rules)

This checks the JSON-Schema rule: **access_vlan required when mode=access**, **native_vlan when mode=trunk**.

- Easiest is to call the API directly from the browser (DevTools → Console or a REST client) so you can send a custom payload.
- **Option A — Browser console (fetch):**
  ```javascript
  const token = localStorage.getItem('auth_token');
  fetch('/api/v1/ansible/preview-intent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
    body: JSON.stringify({
      intent_description: 'Create VLAN 10 and put it on Gi0/1 as access',
      feature: 'vlan',
      params: {
        target_hosts: ['SW1'],
        vlan_id: 10,
        vlan_name: 'DATA',
        interfaces: [{ name: 'Gi0/1', mode: 'access' }]  // no access_vlan
      }
    })
  }).then(r => r.json()).then(console.log);
  ```
- **Expected:** Response includes **validation_errors** (e.g. that `access_vlan` is required when `mode` is access). In the UI, if the same situation is reached via the chat, you should see that error instead of a successful preview.

---

## 5. Test safe_mode (config default)

1. **Default (safe_mode = true):**
   - Send an intent that is missing required params and **cannot** be fully resolved from inventory/topology.
   - **Expected:** You are asked for the missing params (params disambiguation or validation error), not silently filled from inventory.
2. **Override via env (optional):**
   - Stop the backend, set:
     - Windows: `set ANSIBLE_SAFE_MODE_DEFAULT=false`
     - Linux/Mac: `export ANSIBLE_SAFE_MODE_DEFAULT=false`
   - Start the backend again, then repeat. With `false`, the backend may infer from inventory when the schema allows it; you should see different behavior (e.g. no params disambiguation when inventory can fill).

---

## 6. Test from_inventory resolution

1. Ensure the **database** has at least one **device** (and ideally **interfaces** for that device).
2. Send an intent that is missing **target_devices** or **interfaces** and that the schema resolves with **from_inventory** (e.g. “Shutdown interfaces on the access switch” without naming the switch or interfaces).
3. **Expected:** If the backend can resolve from the DB:
   - Preview (or plan) response contains filled **target_hosts** and/or **interfaces_to_shutdown**.
   - In the response, **reasoning_trace** or **resolution_trace** (if exposed in the UI or in the API response) includes an entry with **source: "inventory"** and a short detail.

Checking the **Network** tab for the `preview-intent` or `create-and-plan` response body is the most reliable way to see `resolution_trace` and filled params.

---

## 7. Quick checklist (browser only)

| What to check | How |
|----------------|-----|
| Params disambiguation | Send “Create a VLAN on access switch 1” → form asking for VLAN ID / name (or similar). |
| Schema params in overlay | After reaching Playbook Studio, see “Parameters (schema)” and edit VLAN ID / name, then generate. |
| Validation errors | Use DevTools fetch (or REST client) to send intent with `interfaces: [{ mode: 'access' }]` and no `access_vlan` → validation_errors in response. |
| Safe mode | With default config, missing params lead to “ask user”; with `ANSIBLE_SAFE_MODE_DEFAULT=false`, may infer from inventory. |
| From inventory | DB has devices/interfaces; send intent missing targets/interfaces → response has them filled and resolution_trace shows "inventory". |

---

## 8. Category regression prompts (strict validation trees)

Run each in Ansible AI chat. **Expected** before Playbook Studio: either `params_disambiguation` (wizard), `interfaces_disambiguation` (interface picker), or `parsed_intent.validation_errors` surfaced in chat — **not** an empty studio with guessed values.

| Category | Example prompt | Expect |
|----------|----------------|--------|
| VLAN | Configure VLAN on access switch 1 and 3 (no ID/name in text) | Wizard asks VLAN ID; wizard asks VLAN name if not in prompt; devices resolved or asked. |
| Interface shutdown | Shutdown the interfaces | `shutdown_interface`; interface picker and/or device questions; not `query/clarify` only. |
| Interface configure | Set Gi0/1 on access switch 1 to access VLAN 20 | `configure_interface` or explicit interfaces; if names missing, picker. |
| ACL | Create ACL WEB_FILTER on core router 1 | Asks for `acl_type`, `rules` if missing; devices. |
| OSPF | Enable OSPF on distribution switches | Asks `process_id`, `networks` if missing. |
| Static route | Add default route via 10.0.0.1 | Asks `destination`, `next_hop`, devices if any missing. |
| QoS | Apply QoS policy VOICE on access switch 1 | Asks `policy_name`, `qos_type`, `classification_rules` if missing. |

Verify in **Network** → `preview-intent` → `params_disambiguation.fields` matches schema `required` (minus resolved `target_devices` when skipped by backend).

- **`schema_action`**: top-level field on the preview response (e.g. `shutdown_interface`, `configure_interface`). The UI uses it so the interface picker submits `interfaces_to_shutdown` vs `interfaces` correctly.
- **Multi-intent (secondary_intents)**: For prompts that combine two config tasks (e.g. *"Create VLAN 10 on access switch 1 and enable OSPF"*), the LLM may return a primary intent plus `secondary_intents`. The preview response includes **`secondary_intent_previews`** (per-secondary validation: missing_fields, validation_errors). Categories merge primary + secondary features; reasoning and overlay show "Also detected (secondary)". Only the **primary** intent is generated in create-and-plan; secondaries can be run in a follow-up.

---

## 9. Inspecting API responses in the browser

1. Open **DevTools** (F12) → **Network**.
2. Filter by **Fetch/XHR**.
3. Send an intent or click Continue / Confirm in the UI.
4. Click the **preview-intent** or **create-and-plan** request → **Response** (or **Payload** for the request body).
5. Check:
   - **params_disambiguation** (when the backend asks for params).
   - **schema_editable_fields** (list of fields for the confirm step).
   - **validation_errors** (when type/rule validation fails; under `parsed_intent.validation_errors`).
   - **schema_action** (intent schema key for interface param mapping).
   - **parsed_intent.resolved_devices** and **parsed_intent.parameters** (after resolution).
   - **resolution_trace** (e.g. in the response or under a manifest) for `"source": "inventory"` or `"topology"`.

This gives you a full manual browser-based test of the new behavior without writing code.
