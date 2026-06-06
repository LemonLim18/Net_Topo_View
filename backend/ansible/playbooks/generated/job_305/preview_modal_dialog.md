# Preview Modal Dialog

Generated at: 2026-03-30T01:28:23.153Z
Job ID: 305

## Intent Summary
Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1

## Parsed Highlights
- Categories: vlan, interface
- Target hosts: Acc_Cisco_1
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
- **Resolved:** Acc_Cisco_1. You can change them in **Target inventory** below.
- **Why these devices:**
  - Acc_Cisco_1: User-selected target (Target inventory or API)

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
  "id": "b670a13d",
  "intent_type": "configure",
  "action": "configure_interface",
  "feature": "interface",
  "vendor": "cisco_ios",
  "parameters": {
    "interfaces": [
      {
        "name": "Acc_Cisco_1:Ethernet0/3",
        "mode": "trunk",
        "trunk_vlans": [
          10,
          20,
          67
        ],
        "native_vlan": 10
      }
    ],
    "_canonical_operation": "assign_vlan_to_trunk_port",
    "device_deduction": {
      "deduced": [
        "Acc_Cisco_1"
      ],
      "mapping": [
        {
          "hostname": "Acc_Cisco_1",
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
          "hostname": "Acc_Cisco_1",
          "value": "Acc_Cisco_1:Ethernet0/3",
          "field": "interfaces",
          "message": "Interface 'Acc_Cisco_1:Ethernet0/3' was not found on 'Acc_Cisco_1' in the planned network context. Select the correct interface from the list below.",
          "suggestions": [
            "Ethernet0/1",
            "Ethernet0/2",
            "Ethernet0/3",
            "Ethernet1/0",
            "Management",
            "Vlan100"
          ],
          "multi_select": true,
          "planned_network_context": true
        }
      ]
    },
    "target_hosts": [
      "Acc_Cisco_1"
    ],
    "native_vlan": 10,
    "trunk_vlans": "10,20, 67",
    "target_devices": [
      "Acc_Cisco_1"
    ]
  },
  "scope": {
    "device_hostnames": [
      "Acc_Cisco_1"
    ],
    "device_role": "access",
    "building": null,
    "vendor_filter": null,
    "all_devices": false
  },
  "risk_level": "low",
  "justification": "Configure trunk port on SW1",
  "raw_user_input": "Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1",
  "requires_disambiguation": false,
  "disambiguation_message": "",
  "requires_resolution": false,
  "resolved_devices": [
    "Acc_Cisco_1"
  ],
  "created_at": "2026-03-30T01:27:15.299872+00:00",
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
      "Acc_Cisco_1"
    ],
    "why": "Target device(s) were chosen explicitly in **Target inventory** or the device picker (not inferred only from chat text).",
    "clues": [],
    "mapping": [
      {
        "hostname": "Acc_Cisco_1",
        "reason": "User-selected target (Target inventory or API)"
      }
    ]
  },
  "intent_pipeline": {
    "segments": [
      "Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1"
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
          "hostname": "Acc_Cisco_1",
          "value": "Acc_Cisco_1:Ethernet0/3",
          "field": "interfaces",
          "message": "Interface 'Acc_Cisco_1:Ethernet0/3' was not found on 'Acc_Cisco_1' in the planned network context. Select the correct interface from the list below.",
          "suggestions": [
            "Ethernet0/1",
            "Ethernet0/2",
            "Ethernet0/3",
            "Ethernet1/0",
            "Management",
            "Vlan100"
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
        "hostname": "Acc_Cisco_1",
        "value": "Acc_Cisco_1:Ethernet0/3",
        "question": "Interface 'Acc_Cisco_1:Ethernet0/3' was not found on 'Acc_Cisco_1' in the planned network context. Select the correct interface from the list below.",
        "suggestions": [
          "Ethernet0/1",
          "Ethernet0/2",
          "Ethernet0/3",
          "Ethernet1/0",
          "Management",
          "Vlan100"
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
