# Test Reference — staging and manual tests

This folder holds **interface, acl, ospf, qos, static_route** (and their vendor subfolders) for manual testing and staging. Only **vlan** is in `reference/` and consumed by RAG. When you want a feature to be used by the LLM, copy its `{feature}/{vendor}/` folders into `reference/` and run RAG Rebuild.

## Workflow when adding new scenarios

1. Add or edit in Test_Reference or directly in `reference/{feature}/{vendor}/`.
2. Run tests (Check in Ansible Studio or manual ansible-playbook).
3. Ensure `reference/{feature}/{vendor}/reference.md` exists and is accurate.
4. Run **RAG Rebuild** so the new/updated reference is ingested.
5. Test LLM output (create-and-plan with matching intent) and adjust reference if needed.

## Check, refine, and verify for RAG

See **[CHECK_REFINE_VERIFY_RAG.md](./CHECK_REFINE_VERIFY_RAG.md)** for:
- How to **run** Test_Reference playbooks manually (`ansible-playbook -i inventory.yml playbook.yml [--check]`).
- How to **refine** playbook and `reference.md` so the content is accurate and retrieval-friendly.
- How to **verify** that a scenario is suitable for the RAG/Ansible AI LLM (checklist, promote to `reference/`, RAG Rebuild, retrieval and output checks).

## Test prompts and rollback

See **[TEST_PROMPTS_AND_ROLLBACK.md](./TEST_PROMPTS_AND_ROLLBACK.md)** for:
- **Copy-paste prompts** to test each scenario (interface, acl, ospf, qos, static_route) on **access switches**.
- **Rollback**: what exists (pre-snapshot + rescue, Junos confirmed-commit), and whether a “perfect” rollback exists for LLM-generated playbook deployment.
