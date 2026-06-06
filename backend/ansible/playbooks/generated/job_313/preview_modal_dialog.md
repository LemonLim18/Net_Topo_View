# Preview Modal Dialog

Generated at: 2026-04-03T10:57:15.872Z
Job ID: 313

## Intent Summary
Add network 192.168.1.0/24 to OSPF area 0 on router 2

## Parsed Highlights
- Categories: ospf
- Target hosts: Acc_Cisco_2
- Policy approved: yes

## Reasoning Trace
- ### 1. Intent
- **Type:** configure · **Action:** configure_ospf
- **Feature:** ospf
- **Categories (for templates):** ospf. You can add or remove above.
- ### 1a. Intent pipeline (refactored)
- **Split / summary:** 1 segment(s): Add network 192.168.1.0/24 to OSPF area 0 on router 2
- **Categories per segment:** `[['ospf']]`
- **Catalog missing fields:** []

- ### 1b. Operations involved
- primary=`configure_ospf`
- ### 2. Target devices
- **Resolved:** Acc_Cisco_2. You can change them in **Target inventory** below.
- **Why these devices:**
  - Acc_Cisco_2: User-selected target (Target inventory or API)

- ### 3. Vendor & templates
- **Vendor hint:** cisco_ios. Only vendor-specific reference templates are shown (multi-vendor hidden).
- ### 4. Reference templates (RAG)
- **Fast preview:** skipped vector/embedding search; **1** template path(s) from category + vendor. Playbook Studio runs full RAG when you change targets or after debounced refresh.

## Selected References
- playbook.yml (cisco_ios/ospf) -> reference/ospf/cisco_ios/playbook.yml

## Parsed Intent JSON
```json
{
  "id": "be1a3dd4",
  "intent_type": "configure",
  "action": "configure_ospf",
  "feature": "ospf",
  "vendor": "cisco_ios",
  "parameters": {
    "process_id": 1,
    "networks": [
      {
        "network": "192.168.1.0",
        "wildcard": "0.0.0.255",
        "area": 0
      }
    ],
    "device_deduction": {
      "deduced": [
        "R23"
      ],
      "mapping": [
        {
          "hostname": "R23",
          "reason": "exact hostname match from scope.device_hostnames or topology inventory"
        }
      ],
      "why": "Only exact planned-inventory hostnames (or device_alias_hints) — no fuzzy or role/number guessing."
    },
    "_catalog_dependency_notes": [
      "Prefer enable_ospf_process, advertise_network, configure_passive_interface, configure_ospf_redistribute when the sentence is specific."
    ],
    "_catalog_schema_source": "catalog",
    "_catalog_operation_key": "configure_ospf",
    "network_address": "192.168.1.0",
    "prefix_length": 24,
    "area": 0,
    "target_hosts": [
      "Acc_Cisco_2"
    ],
    "target_devices": [
      "Acc_Cisco_2"
    ]
  },
  "scope": {
    "device_hostnames": [
      "R23"
    ],
    "device_role": "null",
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low",
  "justification": "Add network 192.168.1.0/24 to OSPF area 0 on router 2",
  "raw_user_input": "Add network 192.168.1.0/24 to OSPF area 0 on router 2",
  "requires_disambiguation": false,
  "disambiguation_message": "",
  "requires_resolution": false,
  "resolved_devices": [
    "Acc_Cisco_2"
  ],
  "created_at": "2026-04-03T10:54:25.805822+00:00",
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
      "Add network 192.168.1.0/24 to OSPF area 0 on router 2"
    ],
    "segment_summary": "1 segment(s): Add network 192.168.1.0/24 to OSPF area 0 on router 2",
    "categories_by_segment": [
      [
        "ospf"
      ]
    ],
    "catalog_validation_ok": true,
    "catalog_missing_fields": [],
    "catalog_forbidden_violations": [],
    "catalog_dependency_notes": [
      "Prefer enable_ospf_process, advertise_network, configure_passive_interface, configure_ospf_redistribute when the sentence is specific."
    ],
    "catalog_schema_source": "catalog",
    "catalog_operation_key": "configure_ospf",
    "cross_check": {
      "invalid_devices": [],
      "invalid_interfaces": []
    },
    "interactive_asks": [
      {
        "kind": "schema_dependency_note",
        "field": "dependencies",
        "question": "Prefer enable_ospf_process, advertise_network, configure_passive_interface, configure_ospf_redistribute when the sentence is specific.",
        "source": "catalog_schema"
      }
    ]
  },
  "disambiguation": null
}
```
