import asyncio
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from ansible import api as ansible_api
from ansible.bundle_artifacts import AnsibleArtifactBundle, AnsibleArtifactFile


class _FakeBundleBuilder:
    @staticmethod
    def supports_role_bundle(feature: str) -> bool:
        return feature == "vlan"

    async def build_bundle_draft(self, intent, rag_context, kb_snippets, run_id=None, temperature=0.1, extra_guidance=""):
        run_suffix = str(run_id or "unknown")
        return AnsibleArtifactBundle(
            entrypoint_path=f"playbooks/generated/job_{run_suffix}/playbook.yml",
            files=[
                AnsibleArtifactFile(
                    path=f"playbooks/generated/job_{run_suffix}/playbook.yml",
                    kind="playbook",
                    content=(
                        "---\n"
                        "- name: Configure VLAN\n"
                        "  hosts: SW1\n"
                        "  gather_facts: false\n"
                        "  roles:\n"
                        "    - generated/job_1/vlan\n"
                    ),
                ),
                AnsibleArtifactFile(
                    path="roles/generated/job_1/vlan/tasks/main.yml",
                    content='- name: Dispatch Cisco\n  include_tasks: cisco.yml\n',
                ),
                AnsibleArtifactFile(
                    path="roles/generated/job_1/vlan/tasks/cisco.yml",
                    content='- name: Cisco VLAN\n  cisco.ios.ios_vlans:\n    config: []\n    state: merged\n',
                ),
                AnsibleArtifactFile(
                    path="roles/generated/job_1/vlan/tasks/huawei.yml",
                    content='- name: Huawei placeholder\n  ansible.builtin.debug:\n    msg: huawei\n',
                ),
                AnsibleArtifactFile(
                    path="roles/generated/job_1/vlan/tasks/juniper.yml",
                    content='- name: Juniper placeholder\n  ansible.builtin.debug:\n    msg: juniper\n',
                ),
            ],
            manifest={"generator": "test"},
        )

    def validate_bundle(self, intent, bundle):
        return {"passed": True, "issues": [], "errors": []}


class _FakeDb:
    def __init__(self, path):
        self._db_path = str(path)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE jobs ("
                "id INTEGER PRIMARY KEY, "
                "ansible_playbook TEXT, "
                "ansible_artifact_manifest TEXT, "
                "ansible_entrypoint_path TEXT, "
                "stage TEXT, "
                "status TEXT, "
                "playbook_versions TEXT)"
            )
            conn.execute(
                "INSERT INTO jobs(id, ansible_playbook, ansible_artifact_manifest, ansible_entrypoint_path, stage, status, playbook_versions) "
                "VALUES (1, NULL, NULL, NULL, 'draft', 'pending', NULL)"
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
    monkeypatch.setattr(ansible_api, "_pipeline", object())


def test_plan_ansible_persists_generated_bundle(tmp_path, monkeypatch):
    db = _FakeDb(tmp_path / "ansible_bundle.db")
    monkeypatch.setattr(ansible_api, "_db", db)
    monkeypatch.setattr(ansible_api, "_builder", _FakeBundleBuilder())
    monkeypatch.setattr(ansible_api, "ANSIBLE_BASE", tmp_path / "ansible")

    response = asyncio.run(
        ansible_api.plan_ansible(
            1,
            ansible_api.PlanAnsibleRequest(
                intent_description="Configure VLAN 200",
                target_hosts=["SW1"],
                vendor="cisco_ios",
                feature="vlan",
                params={"vlan_id": 200, "vlan_name": "DATA"},
                auto_refine=False,
            ),
            user={"username": "tester", "role": "admin"},
        )
    )

    assert response["artifact_type"] == "bundle"
    assert response["entrypoint_path"] == "playbooks/generated/job_1/playbook.yml"
    assert len(response["files"]) == 5

    with db._connect() as conn:
        row = conn.execute(
            "SELECT ansible_playbook, ansible_artifact_manifest, ansible_entrypoint_path, playbook_versions "
            "FROM jobs WHERE id = 1"
        ).fetchone()

    manifest = json.loads(row["ansible_artifact_manifest"])
    assert manifest["entrypoint_path"] == "playbooks/generated/job_1/playbook.yml"
    assert row["ansible_entrypoint_path"] == "playbooks/generated/job_1/playbook.yml"
    assert "Configure VLAN" in row["ansible_playbook"]
    assert json.loads(row["playbook_versions"])[0]["artifact_type"] == "bundle"

    entrypoint = Path(tmp_path / "ansible" / "playbooks" / "generated" / "job_1" / "playbook.yml")
    role_task = Path(tmp_path / "ansible" / "roles" / "generated" / "job_1" / "vlan" / "tasks" / "cisco.yml")
    assert entrypoint.exists()
    assert role_task.exists()


def test_load_job_artifact_returns_bundle_metadata(tmp_path, monkeypatch):
    db = _FakeDb(tmp_path / "ansible_bundle.db")
    bundle = {
        "entrypoint_path": "playbooks/generated/job_1/playbook.yml",
        "files": [
            {
                "path": "playbooks/generated/job_1/playbook.yml",
                "content": "---\n- name: Test\n  hosts: SW1\n  gather_facts: false\n  tasks: []\n",
                "kind": "playbook",
                "writable": True,
            }
        ],
        "manifest": {"target_hosts": ["SW1"]},
    }
    with db._connect() as conn:
        conn.execute(
            "UPDATE jobs SET ansible_playbook=?, ansible_artifact_manifest=?, ansible_entrypoint_path=? WHERE id=1",
            ("---\n- name: Test\n  hosts: SW1\n  gather_facts: false\n  tasks: []\n", json.dumps(bundle), bundle["entrypoint_path"]),
        )

    monkeypatch.setattr(ansible_api, "_db", db)
    artifact = ansible_api._load_job_artifact(1)

    assert artifact["mode"] == "bundle"
    assert artifact["entrypoint_path"] == bundle["entrypoint_path"]
    assert artifact["target_hosts"] == ["SW1"]
