import asyncio
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from ansible import api as ansible_api
from ansible.bundle_artifacts import AnsibleArtifactBundle, AnsibleArtifactFile
from ansible.builder import PlaybookBuilder


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

    def validate_playbook(self, intent, playbook_yaml):
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


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content
        self.finish_reason = "stop"


class _YamlOnlyProvider:
    def __init__(self, content: str):
        self._content = content

    async def generate(self, messages, temperature=0.1, tools=None):
        return _FakeLLMResponse(self._content)


class _ReferenceFirstBundleBuilder:
    def __init__(self):
        self.used_reference_bundle = False
        self.used_llm_bundle_generation = False
        self._delegate = PlaybookBuilder(_YamlOnlyProvider(""))

    @staticmethod
    def supports_role_bundle(feature: str) -> bool:
        return feature == "interface"

    def build_bundle_from_playbook(self, intent, playbook_yaml, run_id=None):
        self.used_reference_bundle = True
        return self._delegate.build_bundle_from_playbook(
            intent=intent,
            playbook_yaml=playbook_yaml,
            run_id=run_id,
        )

    async def build_bundle_draft(self, *args, **kwargs):  # pragma: no cover
        self.used_llm_bundle_generation = True
        raise AssertionError("Juniper reference-backed generation must not call LLM bundle generation")

    def validate_bundle(self, intent, bundle):
        return self._delegate.validate_bundle(intent=intent, bundle=bundle)

    def validate_playbook(self, intent, playbook_yaml):
        return self._delegate.validate_playbook(intent=intent, playbook_yaml=playbook_yaml)


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

    assert response["artifact_type"] == "playbook"
    assert response["entrypoint_path"] == "generated/job_1/playbook.yml"
    assert response["files"] == []

    with db._connect() as conn:
        row = conn.execute(
            "SELECT ansible_playbook, ansible_artifact_manifest, ansible_entrypoint_path, playbook_versions "
            "FROM jobs WHERE id = 1"
        ).fetchone()

    assert row["ansible_artifact_manifest"] in (None, "")
    assert row["ansible_entrypoint_path"] == "generated/job_1/playbook.yml"
    assert "Configure VLAN" in row["ansible_playbook"]
    assert json.loads(row["playbook_versions"])[0]["artifact_type"] == "playbook"

    entrypoint = Path(tmp_path / "ansible" / "playbooks" / "generated" / "job_1" / "playbook.yml")
    assert entrypoint.exists()


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


def test_build_bundle_draft_synthesizes_role_bundle_from_yaml_only_juniper_response():
    provider = _YamlOnlyProvider(
        "```yaml\n"
        "---\n"
        "- name: Configure interfaces (Juniper Junos)\n"
        "  hosts: \"{{ target_hosts | default('all') }}\"\n"
        "  gather_facts: false\n"
        "  vars:\n"
        "    interfaces_to_shutdown: \"{{ interfaces_to_shutdown | default([]) }}\"\n"
        "  tasks:\n"
        "    - name: Shutdown interface (Juniper Junos)\n"
        "      juniper.device.junos_interfaces:\n"
        "        config:\n"
        "          - name: \"{{ item }}\"\n"
        "            enabled: false\n"
        "        state: merged\n"
        "      loop: \"{{ interfaces_to_shutdown | default([]) }}\"\n"
        "      when:\n"
        "        - inventory_hostname in groups.get('vendor_juniper_junos', [])\n"
        "```"
    )
    builder = PlaybookBuilder(provider)

    bundle = asyncio.run(
        builder.build_bundle_draft(
            intent={
                "vendor": "juniper_junos",
                "feature": "interface",
                "target_hosts": ["Juniper_Acc_4"],
                "params": {"interfaces_to_shutdown": ["ge-0/0/1"]},
            },
            rag_context="",
            kb_snippets=[],
            run_id="323",
        )
    )

    file_map = bundle.file_map()
    assert bundle.normalized_entrypoint() == "playbooks/generated/job_323/playbook.yml"
    assert "roles/generated/job_323/interface/tasks/main.yml" in file_map
    assert "roles/generated/job_323/interface/tasks/juniper.yml" in file_map
    assert "juniper.device.junos_interfaces" in file_map["roles/generated/job_323/interface/tasks/juniper.yml"]
    assert "roles:" in file_map[bundle.normalized_entrypoint()]
    assert "generated/job_323/interface" in file_map[bundle.normalized_entrypoint()]

    report = builder.validate_bundle(
        intent={
            "vendor": "juniper_junos",
            "feature": "interface",
            "target_hosts": ["Juniper_Acc_4"],
        },
        bundle=bundle,
    )
    assert report["passed"] is True
    assert report["errors"] == []


def test_build_bundle_draft_repairs_missing_role_dispatch_and_vendor_files():
    payload = {
        "entrypoint_path": "playbooks/generated/job_340/playbook.yml",
        "files": [
            {
                "path": "playbooks/generated/job_340/playbook.yml",
                "kind": "playbook",
                "content": (
                    "---\n"
                    "- name: Configure VLAN and access ports\n"
                    "  hosts: all\n"
                    "  gather_facts: false\n"
                    "  roles:\n"
                    "    - generated/job_340/vlan\n"
                ),
            },
            {
                "path": "roles/generated/job_340/vlan/tasks/huawei.yml",
                "kind": "task",
                "content": (
                    "- name: Huawei VLAN task\n"
                    "  community.network.ce_config:\n"
                    "    lines:\n"
                    "      - vlan 43\n"
                ),
            },
            {
                "path": "roles/generated/job_340/vlan/tasks/juniper.yml",
                "kind": "task",
                "content": (
                    "- name: Juniper VLAN task\n"
                    "  juniper.device.junos_config:\n"
                    "    lines:\n"
                    "      - set vlans VLAN43 vlan-id 43\n"
                ),
            },
        ],
        "manifest": {"generator": "test"},
    }
    provider = _YamlOnlyProvider("```json\n" + json.dumps(payload) + "\n```")
    builder = PlaybookBuilder(provider)

    bundle = asyncio.run(
        builder.build_bundle_draft(
            intent={
                "vendor": "multi_vendor",
                "feature": "vlan",
                "target_hosts": ["Cisco_Acc_1", "Huawei_Acc_2", "Juniper_Acc_4"],
                "params": {"vlan_id": 43},
            },
            rag_context="",
            kb_snippets=[],
            run_id="340",
        )
    )

    file_map = bundle.file_map()
    assert "roles/generated/job_340/vlan/tasks/main.yml" in file_map
    assert "roles/generated/job_340/vlan/tasks/cisco.yml" in file_map
    assert "roles/generated/job_340/vlan/tasks/huawei.yml" in file_map
    assert "roles/generated/job_340/vlan/tasks/juniper.yml" in file_map
    assert "ansible.builtin.include_tasks: cisco.yml" in file_map["roles/generated/job_340/vlan/tasks/main.yml"]
    assert "Cisco IOS not targeted by this request" in file_map["roles/generated/job_340/vlan/tasks/cisco.yml"]

    report = builder.validate_bundle(
        intent={
            "vendor": "multi_vendor",
            "feature": "vlan",
            "target_hosts": ["Cisco_Acc_1", "Huawei_Acc_2", "Juniper_Acc_4"],
        },
        bundle=bundle,
    )
    assert not any("missing required role task file" in err.lower() for err in report["errors"])


def test_plan_ansible_uses_reference_bundle_for_juniper_interface(tmp_path, monkeypatch):
    db = _FakeDb(tmp_path / "ansible_bundle.db")
    builder = _ReferenceFirstBundleBuilder()
    monkeypatch.setattr(ansible_api, "_db", db)
    monkeypatch.setattr(ansible_api, "_builder", builder)
    monkeypatch.setattr(ansible_api, "_pipeline", object())
    monkeypatch.setattr(ansible_api, "ANSIBLE_BASE", tmp_path / "ansible")

    async def _fake_planning_context(**kwargs):
        return (
            {
                "devices": [
                    {
                        "hostname": "Juniper_Acc_4",
                        "management_ip": "192.0.2.4",
                        "vendor": "juniper_junos",
                        "role": "access",
                        "interfaces": [{"name": "ge-0/0/1"}],
                    }
                ]
            },
            {"reconciled": False, "issues": []},
        )

    monkeypatch.setattr(ansible_api, "_ensure_planning_context", _fake_planning_context)
    monkeypatch.setattr(
        ansible_api,
        "fill_playbook_from_reference",
        lambda **kwargs: (
            "---\n"
            "- name: Configure interfaces (Juniper Junos)\n"
            "  hosts: Juniper_Acc_4\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: Shutdown interface (Juniper Junos)\n"
            "      juniper.device.junos_interfaces:\n"
            "        config:\n"
            "          - name: ge-0/0/1\n"
            "            enabled: false\n"
            "        state: merged\n"
        ),
    )

    response = asyncio.run(
        ansible_api.plan_ansible(
            1,
            ansible_api.PlanAnsibleRequest(
                intent_description="Turn off ge-0/0/1 on Juniper Acc 4",
                target_hosts=["Juniper_Acc_4"],
                vendor="juniper_junos",
                feature="interface",
                params={"interfaces_to_shutdown": ["ge-0/0/1"]},
                auto_refine=False,
            ),
            user={"username": "tester", "role": "admin"},
        )
    )

    assert builder.used_reference_bundle is False
    assert builder.used_llm_bundle_generation is False
    assert response["artifact_type"] == "playbook"
    assert response["entrypoint_path"] == "generated/job_1/playbook.yml"
    assert response["files"] == []
    assert "juniper.device.junos_interfaces" in response["playbook"]
