# Preview Modal Dialog

Generated at: 2026-05-29T01:56:30.886Z
Job ID: 332

## Intent Summary
Help me to turn on the interfaces of the Juniper Acc

## Parsed Highlights
- Categories: interface
- Target hosts: Juniper_Acc_4
- Policy approved: yes

## Reasoning Trace
- ### 1. Intent
- **Type:** configure · **Action:** configure_interface
- **Feature:** interface
- **Categories (for templates):** interface. You can add or remove above.
- ### 1a. Intent pipeline (refactored)
- **Split / summary:** 1 segment(s): Help me to turn on the interfaces of the Juniper Acc
- **Categories per segment:** `[['interface']]`
- **Catalog missing fields:** []

- ### 1b. Operations involved
- primary=`configure_interface`
- ### 2. Target devices
- **Resolved:** Juniper_Acc_4. You can change them in **Target inventory** below.
- **Why these devices:**
  - Juniper_Acc_4: User-selected target (Target inventory or API)

- ### 3. Vendor & templates
- **Vendor hint:** juniper_junos. Only vendor-specific reference templates are shown (multi-vendor hidden).
- ### 4. Reference templates (RAG)
- **Fast preview:** skipped vector/embedding search; **1** template path(s) from category + vendor. Playbook Studio runs full RAG when you change targets or after debounced refresh.

## Selected References
- playbook.yml (juniper_junos/interface) -> reference/interface/juniper_junos/playbook.yml

## Parsed Intent JSON
```json
{
  "id": "8bcbdb44",
  "intent_type": "configure",
  "action": "shutdown_interface",
  "feature": "interface",
  "vendor": "juniper_junos",
  "parameters": {
    "device_deduction": {
      "deduced": [
        "Test_Juniper_Device"
      ],
      "mapping": [
        {
          "hostname": "Test_Juniper_Device",
          "reason": "exact hostname match from scope.device_hostnames or topology inventory"
        }
      ],
      "why": "Only exact planned-inventory hostnames (or device_alias_hints) — no fuzzy or role/number guessing."
    },
    "_catalog_schema_source": "catalog",
    "_catalog_operation_key": "configure_interface",
    "target_hosts": [
      "Juniper_Acc_4"
    ],
    "interfaces_to_shutdown": [
      "ge-0/0/1"
    ],
    "interfaces_admin_enabled": true,
    "target_devices": [
      "Juniper_Acc_4"
    ]
  },
  "scope": {
    "device_hostnames": [
      "Test_Juniper_Device"
    ],
    "device_role": "access",
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low",
  "justification": "Turn on the interfaces of the Juniper Acc",
  "raw_user_input": "Help me to turn on the interfaces of the Juniper Acc",
  "requires_disambiguation": false,
  "disambiguation_message": "",
  "requires_resolution": false,
  "resolved_devices": [
    "Juniper_Acc_4"
  ],
  "created_at": "2026-05-29T01:49:08.412406+00:00",
  "secondary_intents": [],
  "inventory_unknown_device_tokens": [],
  "device_deduction": {
    "deduced": [
      "Juniper_Acc_4"
    ],
    "why": "Target device(s) were chosen explicitly in **Target inventory** or the device picker (not inferred only from chat text).",
    "clues": [],
    "mapping": [
      {
        "hostname": "Juniper_Acc_4",
        "reason": "User-selected target (Target inventory or API)"
      }
    ]
  },
  "intent_pipeline": {
    "segments": [
      "Help me to turn on the interfaces of the Juniper Acc"
    ],
    "segment_summary": "1 segment(s): Help me to turn on the interfaces of the Juniper Acc",
    "categories_by_segment": [
      [
        "interface"
      ]
    ],
    "catalog_validation_ok": true,
    "catalog_missing_fields": [],
    "catalog_forbidden_violations": [],
    "catalog_dependency_notes": [],
    "catalog_schema_source": "catalog",
    "catalog_operation_key": "shutdown_interface",
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
