# Test Reference — candidate playbooks before moving to Reference

Use this folder to **test** new feature/vendor playbooks and reference notes. When a playbook is confirmed operational, move (or copy) the `{feature}/{vendor}/` set into **Reference** so the LLM-RAG can use it for plan-ansible.

## Workflow

1. Add or edit playbooks here: `Test_Reference/{feature}/{vendor}/playbook.yml`, `inventory.yml`, `reference.md`.
2. Run your tests (e.g. Check/Deploy in Ansible Studio, or manual `ansible-playbook` runs).
3. When satisfied, move the folder to **Reference**:  
   `Test_Reference/{feature}/{vendor}/` → `reference/{feature}/{vendor}/`
4. Run **RAG Rebuild** (Knowledge Base → Rebuild) so the new `reference.md` is ingested.

## Current placeholders

| Feature       | Cisco IOS | Huawei VRP | Juniper Junos |
|---------------|-----------|------------|----------------|
| interface     | placeholder | placeholder | placeholder |
| acl           | placeholder | placeholder | placeholder |
| ospf          | placeholder | placeholder | placeholder |
| qos           | placeholder | placeholder | placeholder |
| static_route  | placeholder | placeholder | placeholder |

Replace placeholders with real, tested content before moving to Reference.

## Check, refine, and verify for RAG

See **[CHECK_REFINE_VERIFY_RAG.md](./CHECK_REFINE_VERIFY_RAG.md)** for:
- How to **run** Test_Reference playbooks manually (`ansible-playbook -i inventory.yml playbook.yml [--check]`).
- How to **refine** playbook and `reference.md` so the content is accurate and retrieval-friendly.
- How to **verify** that a scenario is suitable for the RAG/Ansible AI LLM (checklist, promote to `reference/`, RAG Rebuild, retrieval and output checks).

## Test prompts and rollback

See **[TEST_PROMPTS_AND_ROLLBACK.md](./TEST_PROMPTS_AND_ROLLBACK.md)** for:
- **Copy-paste prompts** to test each scenario (interface, acl, ospf, qos, static_route) on **access switches**.
- **Rollback**: what exists (pre-snapshot + rescue, Junos confirmed-commit), and whether a “perfect” rollback exists for LLM-generated playbook deployment.
