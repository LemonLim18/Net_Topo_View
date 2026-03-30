# Preview Modal Dialog

Generated at: 2026-03-30T02:13:39.175Z
Job ID: 307

## Intent Summary
Add a default route on router 2

## Parsed Highlights
- Categories: static_route
- Target hosts: Acc_Cisco_2
- Policy approved: yes

## Reasoning Trace
- ### 1. Intent
- **Type:** configure · **Action:** configure_static_route
- **Feature:** static_route
- **Categories (for templates):** static_route. You can add or remove above.
- ### 1a. Intent pipeline (refactored)
- **Split / summary:** 1 segment(s): Add a default route on router 2
- **Categories per segment:** `[['static_route']]`
- **Catalog missing fields:** []

- ### 1b. Operations involved
- primary=`configure_static_route`
- ### 2. Target devices
- **Resolved:** Acc_Cisco_2. You can change them in **Target inventory** below.
- **Why these devices:**
  - Acc_Cisco_2: User-selected target (Target inventory or API)

- ### 3. Vendor & templates
- **Vendor hint:** cisco_ios. Only vendor-specific reference templates are shown (multi-vendor hidden).
- ### 4. Reference templates (RAG)
- **Fast preview:** skipped vector/embedding search; **1** template path(s) from category + vendor. Playbook Studio runs full RAG when you change targets or after debounced refresh.

## Selected References
- playbook.yml (cisco_ios/static_route) -> reference/static_route/cisco_ios/playbook.yml

## Parsed Intent JSON
```json
{
  "id": "32054d52",
  "intent_type": "configure",
  "action": "configure_static_route",
  "feature": "static_route",
  "vendor": "cisco_ios",
  "parameters": {
    "next_hop": "Acc_Cisco_2:Ethernet0/2",
    "target_devices": [
      "Acc_Cisco_2"
    ],
    "_canonical_operation": "add_default_route",
    "device_deduction": {
      "deduced": [
        "Acc_Cisco_2"
      ],
      "mapping": [
        {
          "hostname": "Acc_Cisco_2",
          "reason": "exact hostname match from scope.device_hostnames or topology inventory"
        }
      ],
      "why": "Only exact planned-inventory hostnames (or device_alias_hints) — no fuzzy or role/number guessing."
    },
    "_catalog_schema_source": "catalog",
    "_catalog_operation_key": "add_default_route",
    "target_hosts": [
      "Acc_Cisco_2"
    ],
    "destination": "0.0.0.0/0"
  },
  "scope": {
    "device_hostnames": [
      "Acc_Cisco_2"
    ],
    "device_role": null,
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low",
  "justification": "Add default route to router 2",
  "raw_user_input": "Add a default route on router 2",
  "requires_disambiguation": false,
  "disambiguation_message": "",
  "requires_resolution": false,
  "resolved_devices": [
    "Acc_Cisco_2"
  ],
  "created_at": "2026-03-30T02:12:38.114679+00:00",
  "secondary_intents": [],
  "inventory_unknown_device_tokens": [],
  "device_deduction": {
    "deduced": [
      "Acc_Cisco_2"
    ],
    "why": "Target device(s) were chosen explicitly in **Target inventory** or the device picker (not inferred only from chat text).",
    "clues": [],
    "mapping": [
      {
        "hostname": "Acc_Cisco_2",
        "reason": "User-selected target (Target inventory or API)"
      }
    ]
  },
  "intent_pipeline": {
    "segments": [
      "Add a default route on router 2"
    ],
    "segment_summary": "1 segment(s): Add a default route on router 2",
    "categories_by_segment": [
      [
        "static_route"
      ]
    ],
    "catalog_validation_ok": true,
    "catalog_missing_fields": [],
    "catalog_forbidden_violations": [],
    "catalog_dependency_notes": [],
    "catalog_schema_source": "catalog",
    "catalog_operation_key": "add_default_route",
    "cross_check": {
      "invalid_devices": [],
      "invalid_interfaces": []
    },
    "interactive_asks": []
  },
  "disambiguation": null,
  "interfaces_inventory_validation": {
    "ok": true,
    "validation_notice": "",
    "invalid_entities": []
  }
}
```
