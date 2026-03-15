# Test prompts (access switches) and rollback

Use these prompts in **Ansible AI** to test each scenario in Test_Reference on **access switches**. Run **Check** (dry-run) first, then **Deploy** if the diff looks correct. Confirm which vendor(s) your access switches use (Cisco, Huawei, Juniper) so the right reference is retrieved.

---

## Test prompts by feature (access switches only)

Target: **access switches** in your inventory. Adjust VLAN IDs, names, and values as needed for your lab.

### 1. VLAN (reference already exists; use to baseline)
- **Prompt:**  
  `Configure VLAN 200 named TEST on all access switches. Put it on GigabitEthernet0/1 and GigabitEthernet0/2 (Cisco) or GigabitEthernet1/0/1 and 1/0/2 (Huawei).`
- **Expect:** Playbook uses `ios_vlans` / `ios_l2_interfaces` for Cisco and `ce_config` for Huawei. Check output shows VLAN and interface changes.

### 2. Interface (Test_Reference: interface/)
- **Prompt:**  
  `On access switches, set description "Uplink to dist" on interface GigabitEthernet0/1 (Cisco) or GigabitEthernet1/0/1 (Huawei). Enable the interface.`
- **Expect:** Generated playbook configures L2/L3 interface and description. Compare with `Test_Reference/interface/{vendor}/reference.md` and playbook placeholder.

### 3. ACL (Test_Reference: acl/)
- **Prompt:**  
  `On access switches, create an extended ACL named BLOCK-GUEST that denies 10.10.10.0/24 to any and permits the rest. Apply it on the VLAN 200 interface.`
- **Expect:** ACL create + apply; vendor-specific module (e.g. `ios_acls` for Cisco, `ce_config` for Huawei).

### 4. OSPF (Test_Reference: ospf/)
- **Prompt:**  
  `Enable OSPF on access switches in area 0, router-id 1.1.1.1, and advertise the connected interface GigabitEthernet0/0 (or equivalent) into OSPF.`
- **Expect:** OSPF router and interface config; vendor-specific modules.

### 5. QoS (Test_Reference: qos/)
- **Prompt:**  
  `On access switches, create a simple QoS policy that prioritizes voice (DSCP 46) and apply it to the uplink interface.`
- **Expect:** Queue/policy and interface attachment; vendor-specific syntax.

### 6. Static route (Test_Reference: static_route/)
- **Prompt:**  
  `On access switches, add a default static route 0.0.0.0/0 via 192.168.1.1 (or the next-hop for your lab).`
- **Expect:** Single static route; vendor-specific module or config lines.

---

## Efficiency and operational checks

After each run:

1. **Check (dry-run):** No module errors; diff shows only the intended changes.
2. **Deploy:** Run on one access switch first; verify with `show run` / equivalent.
3. **Idempotency:** Run the same playbook again; no further changes (or only expected no-op).
4. **Reference match:** If the feature exists in Reference (e.g. VLAN), generated tasks should follow the same modules and patterns as in `reference/{feature}/{vendor}/`.

When a Test_Reference scenario works reliably, move that `{feature}/{vendor}/` set into **Reference** and run RAG Rebuild.

---

## Rollback: what exists and what is “perfect”

### What exists today

1. **Pre-change snapshot (Cisco / CLI)**  
   Playbooks are prompted to include:
   - A **pre_tasks** step that runs `show running-config` (or vendor equivalent) and saves it to a file (e.g. `/tmp/nettopo_snapshot_{{ inventory_hostname }}_pre.txt`).
   - This gives a point-in-time copy of config **before** any change.

2. **Rescue block (failure rollback)**  
   The prompts require a **block/rescue**:
   - If any task in the block fails, the **rescue** runs.
   - On **Cisco IOS**, rescue can restore by pushing the snapshot file with `ios_config` `src: ...` (replace running-config with the saved file), then `save_when: always`.
   - So: **failure during the run** can be rolled back using that snapshot.

3. **Juniper Junos**  
   With NETCONF and candidate config:
   - Use **confirmed-commit** (e.g. 300 seconds). If you don’t commit again within the timer, Junos **auto-rollbacks** to the previous commit.
   - Or explicitly **rollback 1** and commit in the rescue block.

4. **Huawei (CLI)**  
   No built-in “rollback 1” like Junos. Rollback = re-push the pre-snapshot config (or a set of undo commands) in rescue. Best effort.

### Is there a “perfect” rollback?

- **No single universal “perfect” rollback** across all vendors and features:
  - **Cisco IOS:** No native “rollback 1”. Restore from pre-snapshot = replace running-config with the saved file; that’s **accurate** for “state before this run” if the snapshot was taken immediately before changes.
  - **Juniper Junos:** **Confirmed-commit + auto-rollback** is very strong: if you don’t confirm, device reverts. Explicit **rollback 1** in rescue is also reliable.
  - **Huawei (CLI):** Typically **restore from snapshot** or **precomputed undo commands**; correctness depends on capturing the right pre-state and having the right restore/undo commands.

- **Best practice for LLM-generated playbooks on access switches:**
  1. **Always** capture a **pre-change snapshot** (show running-config → file) and keep it.
  2. **Always** use a **block/rescue**: on failure, restore from that snapshot (Cisco/Huawei) or use Junos rollback/confirmed-commit.
  3. **Optional:** Store a **rollback_config** or **undo_config** (e.g. list of “no …” or “undo …” commands) per job and, in rescue, push that instead of full snapshot if your stack supports it.

So: **reliable rollback to “state before this deployment”** is achievable for access switches by:
- Taking a pre-snapshot,
- On failure (or on demand), restoring that snapshot (or using Junos rollback/confirmed-commit).

That is **as close to “perfect” rollback** as you get for arbitrary LLM-generated playbooks across Cisco/Huawei/Juniper without a full config versioning system. Ensuring every generated playbook includes **pre_tasks (snapshot)** and **block/rescue (restore or vendor rollback)** keeps rollback behavior consistent and testable.
