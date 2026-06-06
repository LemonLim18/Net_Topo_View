# Preview Modal Dialog

Generated at: 2026-06-02T01:43:47.382Z
Job ID: 334

## Intent Summary
Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on Core_Switch

## Parsed Highlights
- Categories: vlan, interface
- Target hosts: Test_Core_Switch
- Policy approved: yes

## Reasoning Trace
- ### 1. Intent
- **Type:** configure · **Action:** configure_interface
- **Feature:** interface
- **Categories (for templates):** vlan, interface. You can add or remove above.
- ### 1a. Intent pipeline (refactored)
- **Split / summary:** 1 segment(s): Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, …
- **Categories per segment:** `[['vlan', 'interface']]`
- **Catalog missing fields:** []

- ### 1b. Operations involved
- primary=`configure_interface`
- - **Interfaces to configure:**
  - **GigabitEthernet1/0/5**: trunk, allowed VLANs 10, 20, 67
- ### 2. Target devices
- **Resolved:** Test_Core_Switch. You can change them in **Target inventory** below.
- **Why these devices:**
  - Test_Core_Switch: User-selected target (Target inventory or API)

- ### 3. Vendor & templates
- **Vendor hint:** cisco_ios. Only vendor-specific reference templates are shown (multi-vendor hidden).
- ### 4. Reference templates (RAG)
- **Fast preview:** skipped vector/embedding search; **2** template path(s) from category + vendor. Playbook Studio runs full RAG when you change targets or after debounced refresh.

## Selected References
- playbook.yml (cisco_ios/vlan) -> reference/vlan/cisco_ios/playbook.yml
- playbook.yml (cisco_ios/interface) -> reference/interface/cisco_ios/playbook.yml

## Parsed Intent JSON
```json
{
  "id": "6eb7cec6",
  "intent_type": "configure",
  "action": "configure_interface",
  "feature": "interface",
  "vendor": "cisco_ios",
  "parameters": {
    "interfaces": [
      {
        "name": "Test_Core_Switch:GigabitEthernet3",
        "mode": "trunk"
      }
    ],
    "_canonical_operation": "assign_vlan_to_trunk_port",
    "device_deduction": {
      "deduced": [
        "Test_Core_Switch"
      ],
      "mapping": [
        {
          "hostname": "Test_Core_Switch",
          "reason": "exact hostname match from scope.device_hostnames or topology inventory"
        }
      ],
      "why": "Only exact planned-inventory hostnames (or device_alias_hints) — no fuzzy or role/number guessing."
    },
    "_catalog_schema_source": "catalog",
    "_catalog_operation_key": "assign_vlan_to_trunk_port",
    "_inventory_validation": {
      "invalid_devices": [],
      "invalid_interfaces": [
        {
          "hostname": "Test_Core_Switch",
          "value": "Test_Core_Switch:GigabitEthernet3",
          "field": "interfaces",
          "message": "Interface 'Test_Core_Switch:GigabitEthernet3' was not found on 'Test_Core_Switch' in the planned network context. Select the correct interface from the list below.",
          "suggestions": [
            "GigabitEthernet1",
            "GigabitEthernet2",
            "GigabitEthernet3"
          ],
          "multi_select": true,
          "planned_network_context": true
        }
      ]
    },
    "target_hosts": [
      "Test_Core_Switch"
    ],
    "target_devices": [
      "Test_Core_Switch"
    ]
  },
  "scope": {
    "device_hostnames": [
      "Test_Core_Switch"
    ],
    "device_role": "core",
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low",
  "justification": "Configure trunk port on Core_Switch",
  "raw_user_input": "Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on Core_Switch",
  "requires_disambiguation": false,
  "disambiguation_message": "",
  "requires_resolution": false,
  "resolved_devices": [
    "Test_Core_Switch"
  ],
  "created_at": "2026-06-02T01:42:34.029142+00:00",
  "secondary_intents": [],
  "interfaces_summary": [
    {
      "interface": "GigabitEthernet1/0/5",
      "mode": "trunk",
      "vlans": [
        10,
        20,
        67
      ]
    }
  ],
  "vlan_validation_errors": [],
  "inventory_unknown_device_tokens": [],
  "device_deduction": {
    "deduced": [
      "Test_Core_Switch"
    ],
    "why": "Target device(s) were chosen explicitly in **Target inventory** or the device picker (not inferred only from chat text). Role keywords were matched against inventory roles and hostname prefixes.",
    "clues": [
      "role=core"
    ],
    "mapping": [
      {
        "hostname": "Test_Core_Switch",
        "reason": "User-selected target (Target inventory or API)"
      }
    ]
  },
  "intent_pipeline": {
    "segments": [
      "Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on Core_Switch"
    ],
    "segment_summary": "1 segment(s): Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, …",
    "categories_by_segment": [
      [
        "vlan",
        "interface"
      ]
    ],
    "catalog_validation_ok": true,
    "catalog_missing_fields": [],
    "catalog_forbidden_violations": [],
    "catalog_dependency_notes": [],
    "catalog_schema_source": "catalog",
    "catalog_operation_key": "assign_vlan_to_trunk_port",
    "cross_check": {
      "invalid_devices": [],
      "invalid_interfaces": [
        {
          "hostname": "Test_Core_Switch",
          "value": "Test_Core_Switch:GigabitEthernet3",
          "field": "interfaces",
          "message": "Interface 'Test_Core_Switch:GigabitEthernet3' was not found on 'Test_Core_Switch' in the planned network context. Select the correct interface from the list below.",
          "suggestions": [
            "GigabitEthernet1",
            "GigabitEthernet2",
            "GigabitEthernet3"
          ],
          "multi_select": true,
          "planned_network_context": true
        }
      ]
    },
    "interactive_asks": [
      {
        "kind": "invalid_interface",
        "field": "interfaces",
        "hostname": "Test_Core_Switch",
        "value": "Test_Core_Switch:GigabitEthernet3",
        "question": "Interface 'Test_Core_Switch:GigabitEthernet3' was not found on 'Test_Core_Switch' in the planned network context. Select the correct interface from the list below.",
        "suggestions": [
          "GigabitEthernet1",
          "GigabitEthernet2",
          "GigabitEthernet3"
        ],
        "multi_select": true,
        "planned_network_context": true,
        "source": "planned_network_context"
      }
    ]
  },
  "disambiguation": null,
  "interfaces_inventory_validation": {
    "ok": true,
    "validation_notice": "",
    "invalid_entities": []
  }
}
```
