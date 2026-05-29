# Preview Modal Dialog

Generated at: 2026-05-28T16:31:13.385Z
Job ID: 331

## Intent Summary
Can you turn the interfaces of the Juniper Acc 4 off?

## Parsed Highlights
- Categories: interface
- Target hosts: Juniper_Acc_4
- Policy approved: yes

## Reasoning Trace
- ### 1. Intent
- **Type:** configure · **Action:** shutdown_interface
- **Feature:** interface
- **Categories (for templates):** interface. You can add or remove above.
- ### 1a. Intent pipeline (refactored)
- **Split / summary:** 1 segment(s): Can you turn the interfaces of the Juniper Acc 4 off?
- **Categories per segment:** `[['interface']]`
- **Catalog missing fields:** []

- ### 1b. Operations involved
- primary=`shutdown_interface`
- ### 2. Target devices
- **Resolved:** Juniper_Acc_4. You can change them in **Target inventory** below.
- **Why these devices:**
  - Juniper_Acc_4: matched by inventory similarity

- ### 3. Vendor & templates
- **Vendor hint:** juniper_junos. Only vendor-specific reference templates are shown (multi-vendor hidden).
- ### 4. Reference templates (RAG)
- **Fast preview:** skipped vector/embedding search; **1** template path(s) from category + vendor. Playbook Studio runs full RAG when you change targets or after debounced refresh.
- ### 5. Planned interfaces (all-interfaces scope)
  - **Juniper_Acc_4**: **1** interface(s) — ge-0/0/1

## Selected References
- playbook.yml (juniper_junos/interface) -> reference/interface/juniper_junos/playbook.yml

## Parsed Intent JSON
```json
{
  "id": "8cb55e29",
  "intent_type": "configure",
  "action": "shutdown_interface",
  "feature": "interface",
  "vendor": "juniper_junos",
  "parameters": {
    "device_deduction": {
      "deduced": [
        "Juniper_Acc_4"
      ],
      "mapping": [
        {
          "hostname": "Juniper_Acc_4",
          "reason": "exact hostname match from scope.device_hostnames or topology inventory"
        }
      ],
      "why": "Only exact planned-inventory hostnames (or device_alias_hints) — no fuzzy or role/number guessing."
    },
    "_catalog_schema_source": "catalog",
    "_catalog_operation_key": "shutdown_interface",
    "_inventory_validation": {
      "invalid_devices": [],
      "invalid_interfaces": [
        {
          "hostname": "Juniper_Acc_4",
          "value": "Ethernet0/1",
          "field": "interfaces_to_shutdown",
          "message": "Interface 'Ethernet0/1' not found on Juniper_Acc_4 in planned inventory.",
          "suggestions": [
            "ge-0/0/0",
            "ge-0/0/1",
            "Management"
          ],
          "multi_select": true,
          "planned_network_context": true
        }
      ]
    },
    "interfaces_shutdown_use_nettopo": true
  },
  "scope": {
    "device_hostnames": [
      "Juniper_Acc_4"
    ],
    "device_role": "access",
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low",
  "justification": "Turn off interfaces of Juniper Acc 4",
  "raw_user_input": "Can you turn the interfaces of the Juniper Acc 4 off?",
  "requires_disambiguation": false,
  "disambiguation_message": "",
  "requires_resolution": false,
  "resolved_devices": [
    "Juniper_Acc_4"
  ],
  "created_at": "2026-05-28T16:25:58.331023+00:00",
  "secondary_intents": [],
  "inventory_unknown_device_tokens": [],
  "device_deduction": {
    "deduced": [
      "Juniper_Acc_4"
    ],
    "why": "The backend matched the device names/roles directly from inventory.",
    "clues": [],
    "mapping": [
      {
        "hostname": "Juniper_Acc_4",
        "reason": "matched by inventory similarity"
      }
    ]
  },
  "intent_pipeline": {
    "segments": [
      "Can you turn the interfaces of the Juniper Acc 4 off?"
    ],
    "segment_summary": "1 segment(s): Can you turn the interfaces of the Juniper Acc 4 off?",
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
      "invalid_interfaces": [
        {
          "hostname": "Juniper_Acc_4",
          "value": "Ethernet0/1",
          "field": "interfaces_to_shutdown",
          "message": "Interface 'Ethernet0/1' not found on Juniper_Acc_4 in planned inventory.",
          "suggestions": [
            "ge-0/0/0",
            "ge-0/0/1",
            "Management"
          ],
          "multi_select": true,
          "planned_network_context": true
        }
      ]
    },
    "interactive_asks": [
      {
        "kind": "invalid_interface",
        "field": "interfaces_to_shutdown",
        "hostname": "Juniper_Acc_4",
        "value": "Ethernet0/1",
        "question": "Interface 'Ethernet0/1' not found on Juniper_Acc_4 in planned inventory.",
        "suggestions": [
          "ge-0/0/0",
          "ge-0/0/1",
          "Management"
        ],
        "multi_select": true,
        "planned_network_context": true,
        "source": "planned_network_context"
      }
    ]
  },
  "disambiguation": null,
  "planned_all_interfaces_preview": {
    "devices": [
      {
        "hostname": "Juniper_Acc_4",
        "interface_count": 1,
        "interfaces": [
          {
            "name": "ge-0/0/1"
          }
        ]
      }
    ]
  }
}
```
