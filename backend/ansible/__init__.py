"""
M22 — Ansible-First Automation Layer.

Provides:
  router.py       — execution routing decision tree (Ansible native / CLI / SSH fallback)
  builder.py      — LLM → Ansible playbook (prompt + lint + check_mode validation)
  runner.py       — ansible-runner wrapper
  inventory.py    — dynamic inventory from live topology
  vault.py        — Ansible Vault credential helper
  verifier.py     — post-change verification via Context-Injection RAG
  api.py          — FastAPI router /ansible/...
  canary.py       — canary host selector
  fallback/       — Netmiko SSH fallback worker
  roles/          — Ansible roles (access_vlan, junos_vlan, ...)
  playbooks/      — ready-to-run playbooks
  awx/            — AWX/Tower job template definitions
"""
