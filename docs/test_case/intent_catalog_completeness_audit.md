# Intent catalog completeness audit
Generated from `testcase_list.md` checklist prompts. This document tracks **schema + validation + cross-check** coverage (not LLM extraction quality).

## Global fixes applied (architecture)

- **`required_parameter_groups.any_of`** in `validation_engine.py`: ACL create vs apply, QoS policy vs interface, static route next-hop vs egress-if, OSPF variants.
- **`_canonical_operation`** preserved in `operation_adapter.py` so validation uses fine-grained catalog rows after legacy action rewrite.
- **`resolve_operation_spec_for_intent`**: prefers `parameters._canonical_operation` for spec lookup.
- **Field satisfiers**: `rules` non-empty, `interfaces` / `interfaces_to_shutdown` (incl. all/*), `destination`/`default_route`, `egress_interface` aliases, OSPF `ospf_networks`/`passive_interfaces`/`redistribute`/`router_id`, ACL/QoS aliases.
- **New / split catalog operations**: `create_acl`, `apply_acl_to_interface`, `create_qos_policy`, `apply_qos_policy_to_interface`, `add_default_route`, OSPF fine-grained ops + `configure_ospf_redistribute` adapter mapping.
- **`cross_check.py`**: inventory candidates include `device_deduction.deduced`; wildcard shutdown lists skip bogus interface validation.
- **`keyword_map.yaml`**: hints for apply ACL, OSPF passive/redistribute, routed L3, route-via-interface, QoS uplink/DSCP.
- **`interface.yaml`**: `implicit_requires_targets: true` on all interface operations.

## Per checklist prompt

### 1. Prompt:
Create VLAN 67 named ShuYing on access switch 1, 2, and 3

### Detected (expected):
- category: **vlan**
- operation(s): **create_vlan**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 2. Prompt:
Create VLAN 120 named WEB_Vlan on SW1 and SW2

### Detected (expected):
- category: **vlan**
- operation(s): **create_vlan**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 3. Prompt:
Create VLAN 30 and apply it as access VLAN on GigabitEthernet1/0/1 on SW1

### Detected (expected):
- category: **vlan, interface**
- operation(s): **create_vlan**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 4. Prompt:
Create VLAN 10 and allow it on trunk port GigabitEthernet1/0/2 on SW1

### Detected (expected):
- category: **vlan, interface**
- operation(s): **create_vlan, assign_vlan_to_trunk_port / set_native_vlan**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 5. Prompt:
Create VLAN 200 named **Guest** and set native VLAN 200 on GigabitEthernet1/0/3

### Detected (expected):
- category: **vlan, interface**
- operation(s): **create_vlan, set_native_vlan**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 6. Prompt:
Create VLAN on devices *(generic / ambiguous test)*

### Detected (expected):
- category: **vlan**
- operation(s): **create_vlan, (ambiguous → requires_resolution)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 7. Prompt:
Admin-down all interfaces on SW1

### Detected (expected):
- category: **interface**
- operation(s): **shutdown_interface**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 8. Prompt:
Shut down GigabitEthernet1/0/1 on router 2

### Detected (expected):
- category: **interface**
- operation(s): **shutdown_interface, configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 9. Prompt:
Configure GigabitEthernet1/0/1 and GigabitEthernet1/0/2 as access ports on SW1

### Detected (expected):
- category: **interface**
- operation(s): **(detect from catalog + LLM)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 10. Prompt:
Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1

### Detected (expected):
- category: **vlan, interface**
- operation(s): **assign_vlan_to_trunk_port / set_native_vlan**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 11. Prompt:
Set Ethernet0/1 with IP address 192.168.10.1/24 on Access_Cisco_1

### Detected (expected):
- category: **interface**
- operation(s): **configure_interface (L3)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 12. Prompt:
Set GigabitEthernet1/0/3 as routed interface with IP address 192.168.10.1/24 on SW1

### Detected (expected):
- category: **interface**
- operation(s): **configure_interface (L3), configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 13. Prompt:
Configure interfaces on devices *(generic / ambiguous test)*

### Detected (expected):
- category: **interface**
- operation(s): **(ambiguous → requires_resolution)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 14. Prompt:
Create ACL 2000 to permit SSH from 192.168.1.0/24 on SW1

### Detected (expected):
- category: **acl**
- operation(s): **configure_interface (L3), create_acl / configure_acl**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 15. Prompt:
Create ACL named **BLOCK_HTTP** to deny TCP port 80 on SW1

### Detected (expected):
- category: **interface, acl**
- operation(s): **create_acl / configure_acl**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 16. Prompt:
Apply ACL 3000 inbound on GigabitEthernet1/0/1 on SW1

### Detected (expected):
- category: **interface, acl**
- operation(s): **apply_acl_to_interface / configure_acl**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 17. Prompt:
Apply ACL **BLOCK_HTTP** outbound on GigabitEthernet1/0/2 on SW1

### Detected (expected):
- category: **interface, acl**
- operation(s): **apply_acl_to_interface / configure_acl**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 18. Prompt:
Create ACL to deny ICMP and apply it on access switch 3

### Detected (expected):
- category: **acl**
- operation(s): **apply_acl_to_interface / configure_acl**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 19. Prompt:
Add ACL rule on devices *(generic / ambiguous test)*

### Detected (expected):
- category: **acl**
- operation(s): **create_acl / configure_acl, (ambiguous → requires_resolution)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 20. Prompt:
Create QoS policy **VOICE_POLICY** on SW1

### Detected (expected):
- category: **qos**
- operation(s): **configure_qos / create_qos_policy / apply_qos_policy_to_interface**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 21. Prompt:
Configure QoS to prioritize voice traffic on GigabitEthernet1/0/1

### Detected (expected):
- category: **interface, qos**
- operation(s): **configure_qos / create_qos_policy / apply_qos_policy_to_interface**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 22. Prompt:
Apply QoS policy **IN_POLICY** inbound on GigabitEthernet1/0/2 on SW1

### Detected (expected):
- category: **interface, qos**
- operation(s): **configure_qos / create_qos_policy / apply_qos_policy_to_interface**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 23. Prompt:
Mark traffic with DSCP 46 for voice on access switch 2

### Detected (expected):
- category: **qos**
- operation(s): **(detect from catalog + LLM)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 24. Prompt:
Create policing policy for HTTP traffic on SW1

### Detected (expected):
- category: **qos**
- operation(s): **(detect from catalog + LLM)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 25. Prompt:
Configure QoS on devices *(generic / ambiguous test)*

### Detected (expected):
- category: **qos**
- operation(s): **configure_qos / create_qos_policy / apply_qos_policy_to_interface, (ambiguous → requires_resolution)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 26. Prompt:
Add a static route to 10.10.10.0/24 via 192.168.1.1 on SW1

### Detected (expected):
- category: **static_route**
- operation(s): **configure_interface (L3), configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 27. Prompt:
Add a default route on router 2

### Detected (expected):
- category: **static_route**
- operation(s): **configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 28. Prompt:
Add a static route to 172.16.0.0/16 via GigabitEthernet0/1 on router 2

### Detected (expected):
- category: **interface, static_route**
- operation(s): **configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 29. Prompt:
Add a static route to 192.168.50.0/24 with next hop 10.0.0.2 on SW1

### Detected (expected):
- category: **static_route**
- operation(s): **configure_interface (L3), configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 30. Prompt:
Add a static route for the management network on devices

### Detected (expected):
- category: **static_route**
- operation(s): **configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 31. Prompt:
Create a route on devices *(generic / ambiguous test)*

### Detected (expected):
- category: **static_route**
- operation(s): **configure_static_route / add_default_route, (ambiguous → requires_resolution)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 32. Prompt:
Configure OSPF process 1 on router 2

### Detected (expected):
- category: **ospf**
- operation(s): **configure_static_route / add_default_route, configure_ospf + fine-grained OSPF ops**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 33. Prompt:
Add network 192.168.1.0/24 to OSPF area 0 on router 2

### Detected (expected):
- category: **ospf**
- operation(s): **configure_interface (L3), configure_static_route / add_default_route, configure_ospf + fine-grained OSPF ops**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 34. Prompt:
Set router ID 1.1.1.1 and advertise 10.0.0.0/24 in area 0 on SW1

### Detected (expected):
- category: **general**
- operation(s): **configure_interface (L3), configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 35. Prompt:
Make GigabitEthernet1/0/1 passive in OSPF on router 1

### Detected (expected):
- category: **interface, ospf**
- operation(s): **configure_static_route / add_default_route, configure_ospf + fine-grained OSPF ops**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 36. Prompt:
Enable OSPF redistribution of static routes on router 2

### Detected (expected):
- category: **static_route, ospf**
- operation(s): **configure_static_route / add_default_route, configure_ospf + fine-grained OSPF ops**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 37. Prompt:
Configure OSPF on devices *(generic / ambiguous test)*

### Detected (expected):
- category: **ospf**
- operation(s): **configure_ospf + fine-grained OSPF ops, (ambiguous → requires_resolution)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 38. Prompt:
Configure VLAN on SW1

### Detected (expected):
- category: **vlan**
- operation(s): **(detect from catalog + LLM)**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 39. Prompt:
Admin-down all interfaces on SW1 and SW3

### Detected (expected):
- category: **interface**
- operation(s): **shutdown_interface**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 40. Prompt:
Apply ACL inbound on GigabitEthernet1/0/1

### Detected (expected):
- category: **interface, acl**
- operation(s): **apply_acl_to_interface / configure_acl**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 41. Prompt:
Add static route via next hop 192.168.1.254

### Detected (expected):
- category: **static_route**
- operation(s): **configure_static_route / add_default_route**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 42. Prompt:
Put QoS on the uplink port

### Detected (expected):
- category: **interface, qos**
- operation(s): **configure_qos / create_qos_policy / apply_qos_policy_to_interface**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

### 43. Prompt:
Add OSPF network for the access network

### Detected (expected):
- category: **ospf**
- operation(s): **configure_ospf + fine-grained OSPF ops**

### Issues Found (historical vs current codebase):
- See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates.

### Fix Applied:
- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.

---

## Summary: operations supported (catalog keys)

Run: `python -c "from ansible.intent_pipeline.schema_loader import reload_catalog; c=reload_catalog(); print(sorted({k for cat in c.categories.values() for k in (cat.get('operations') or {})}))"` from `backend/`.

### VLAN (`vlan.yaml`)
- `create_vlan`, `configure_interface` (legacy in category)

### Interface (`interface.yaml`)
- `configure_interface`, `assign_vlan_to_access_port`, `assign_vlan_to_trunk_port`, `set_native_vlan`, `shutdown_interface`

### ACL (`acl.yaml`)
- `create_acl`, `apply_acl_to_interface`, `configure_acl` (any-of groups)

### QoS (`qos.yaml`)
- `create_qos_policy`, `apply_qos_policy_to_interface`, `configure_qos` (any-of groups)

### Static route (`static_route.yaml`)
- `add_default_route`, `configure_static_route` (any-of groups)

### OSPF (`ospf.yaml`)
- `enable_ospf_process`, `advertise_network`, `configure_passive_interface`, `configure_ospf_redistribute`, `configure_ospf` (any-of groups)

## Summary: category / shared fields

- Per-category: `category_key`, `display_name`, `operations` (each with `action`, `feature`, `required_parameters` OR `required_parameter_groups.any_of`, `implicit_requires_targets`, `optional_parameters`, `keyword_triggers`, `rules`, `dependencies`, `forbidden_fields`).
- Global: `global_fields.yaml` (`target_devices`, `category`, `operation`, …).

## Summary: validation rules (engine)

- Required fields / **any-of groups** per operation spec.
- `implicit_requires_targets`: scope / target_devices / resolved_devices / device_deduction (unless suppressed).
- `forbidden_fields`: non-empty presence → violation + interactive ask.
- `dependencies`: surfaced as `schema_dependency_note` asks.
- VLAN `vlans` virtual expansion → `vlan_id` / `vlan_name` missing flags.
- Cross-check: devices/interfaces vs DB; wildcard shutdown skips iface checks; deduced devices included.
- Unknown ops: `schema_recovery` (adapter → catalog, optional synthetic with `INTENT_SCHEMA_AUTO_RECOVER`).
