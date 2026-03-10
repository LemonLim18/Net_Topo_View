import asyncio
import json
import sqlite3
from contextlib import contextmanager

import pytest

from ansible import api as ansible_api
from ansible.runner import AnsibleRunResult


class _FakeBuilder:
    def __init__(self, draft: str):
        self._draft = draft

    async def build_draft(self, intent, rag_context, kb_snippets, run_id=None, temperature=0.1, extra_guidance=""):
        return self._draft

    def validate_playbook(self, intent, playbook_yaml):
        if "BROKEN" in playbook_yaml:
            return {
                "passed": False,
                "issues": [
                    {
                        "phase": "lint",
                        "rule": "syntax_error",
                        "message": "Unexpected token BROKEN",
                        "refinable": True,
                    }
                ],
                "errors": ["[lint/syntax_error] Unexpected token BROKEN"],
            }
        return {"passed": True, "issues": [], "errors": []}


class _FakeDb:
    def __init__(self, path):
        self._db_path = str(path)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE jobs (id INTEGER PRIMARY KEY, ansible_playbook TEXT, stage TEXT, status TEXT, playbook_versions TEXT)"
            )
            conn.execute(
                "INSERT INTO jobs(id, ansible_playbook, stage, status, playbook_versions) VALUES (1, NULL, 'draft', 'pending', NULL)"
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


@pytest.fixture(autouse=True)
def _reset_ansible_globals(monkeypatch):
    monkeypatch.setattr(ansible_api, "_agent", None)
    monkeypatch.setattr(ansible_api, "_pipeline", None)


def test_plan_ansible_auto_refines_to_valid_playbook(tmp_path, monkeypatch):
    db = _FakeDb(tmp_path / "ansible.db")
    monkeypatch.setattr(ansible_api, "_db", db)
    monkeypatch.setattr(ansible_api, "_builder", _FakeBuilder("- name: draft\n  hosts: SW1\n  tasks:\n    - name: bad\n      debug: msg=BROKEN\n"))

    def _fake_build_inventory(target_hosts=None, topology_override=None):
        return {"all": {"hosts": {"SW1": {}}}}

    monkeypatch.setattr("ansible.inventory.build_inventory", _fake_build_inventory)

    async def _fake_refine(original, errors, feedback):
        assert errors
        return (
            "- name: draft\n  hosts: SW1\n  gather_facts: false\n  tasks:\n    - name: fixed\n      debug:\n        msg: fixed\n",
            ["removed BROKEN token"],
        )

    monkeypatch.setattr(ansible_api, "_ai_refine_playbook", _fake_refine)

    response = asyncio.run(
        ansible_api.plan_ansible(
            1,
            ansible_api.PlanAnsibleRequest(
                intent_description="Fix SW1",
                target_hosts=["SW1"],
                vendor="cisco_ios",
                feature="interface",
                params={},
                auto_refine=True,
                max_refinement_attempts=3,
            ),
            user={"username": "tester", "role": "admin"},
        )
    )

    assert response["status"] == "playbook_generated"
    assert response["refinement"]["attempts_used"] == 2
    assert response["refinement"]["validation"]["passed"] is True

    with db._connect() as conn:
        row = conn.execute("SELECT ansible_playbook, playbook_versions FROM jobs WHERE id=1").fetchone()

    assert "fixed" in row["ansible_playbook"]
    versions = json.loads(row["playbook_versions"])
    assert len(versions) == 2


def test_plan_ansible_fails_closed_when_max_attempts_reached(tmp_path, monkeypatch):
    db = _FakeDb(tmp_path / "ansible.db")
    monkeypatch.setattr(ansible_api, "_db", db)
    monkeypatch.setattr(ansible_api, "_builder", _FakeBuilder("- name: draft\n  hosts: SW1\n  tasks:\n    - name: bad\n      debug: msg=BROKEN\n"))

    def _fake_build_inventory(target_hosts=None, topology_override=None):
        return {"all": {"hosts": {"SW1": {}}}}

    monkeypatch.setattr("ansible.inventory.build_inventory", _fake_build_inventory)

    async def _fake_refine(original, errors, feedback):
        return original.replace("BROKEN", "BROKEN_AGAIN"), ["changed token"]

    monkeypatch.setattr(ansible_api, "_ai_refine_playbook", _fake_refine)

    with pytest.raises(Exception) as exc:
        asyncio.run(
            ansible_api.plan_ansible(
                1,
                ansible_api.PlanAnsibleRequest(
                    intent_description="Fix SW1",
                    target_hosts=["SW1"],
                    vendor="cisco_ios",
                    feature="interface",
                    params={},
                    auto_refine=True,
                    max_refinement_attempts=2,
                ),
                user={"username": "tester", "role": "admin"},
            )
        )

    assert getattr(exc.value, "status_code", None) == 422
    assert exc.value.detail["escalation_reason"] == "max_refinement_attempts_reached"

    with db._connect() as conn:
        row = conn.execute("SELECT ansible_playbook, playbook_versions FROM jobs WHERE id=1").fetchone()

    assert row["ansible_playbook"] is None
    versions = json.loads(row["playbook_versions"])
    assert len(versions) == 2


def test_validate_playbook_candidate_marks_inventory_gap_non_refinable(monkeypatch):
    monkeypatch.setattr(ansible_api, "_builder", _FakeBuilder("- name: ok\n  hosts: SW1\n  tasks: []\n"))

    def _fake_build_inventory(target_hosts=None, topology_override=None):
        return {"all": {"hosts": {}}}

    monkeypatch.setattr("ansible.inventory.build_inventory", _fake_build_inventory)

    validation = asyncio.run(
        ansible_api._validate_playbook_candidate(
            playbook_yaml="- name: ok\n  hosts: SW1\n  gather_facts: false\n  tasks: []\n",
            intent={"target_hosts": ["SW1"], "vendor": "cisco_ios", "feature": "interface"},
            job_id=1,
            verify_with_dry_run=False,
        )
    )

    assert validation["passed"] is False
    assert any(issue["phase"] == "inventory" and issue["refinable"] is False for issue in validation["issues"])


def test_validate_playbook_candidate_refines_dry_run_failures(monkeypatch):
    monkeypatch.setattr(ansible_api, "_builder", _FakeBuilder("- name: ok\n  hosts: SW1\n  tasks: []\n"))

    def _fake_build_inventory(target_hosts=None, topology_override=None):
        return {"all": {"hosts": {"SW1": {}}}}

    def _fake_run_playbook(playbook_content, inventory, extra_vars, check_mode=False, job_id=None, timeout=300, tags=None, limit=None):
        return AnsibleRunResult(
            success=False,
            rc=2,
            stdout="ERROR! unexpected parameter type in action",
            stderr="",
            stats={},
            changed=False,
        )

    monkeypatch.setattr("ansible.inventory.build_inventory", _fake_build_inventory)
    monkeypatch.setattr("ansible.runner.run_playbook", _fake_run_playbook)

    validation = asyncio.run(
        ansible_api._validate_playbook_candidate(
            playbook_yaml="- name: ok\n  hosts: SW1\n  gather_facts: false\n  tasks: []\n",
            intent={"target_hosts": ["SW1"], "vendor": "cisco_ios", "feature": "interface"},
            job_id=1,
            verify_with_dry_run=True,
        )
    )

    assert validation["passed"] is False
    assert any(issue["phase"] == "dry_run" and issue["refinable"] is True for issue in validation["issues"])