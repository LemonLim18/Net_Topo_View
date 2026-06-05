import asyncio
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from ansible import api as ansible_api


class _FakeJobsDb:
    def __init__(self, status: str):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
        self.conn.execute("INSERT INTO jobs (id, status) VALUES (?, ?)", (42, status))
        self.conn.commit()

    @contextmanager
    def _connect(self):
        yield self.conn


def test_generated_artifact_job_id_detection():
    assert ansible_api._job_id_from_generated_artifact_path("generated/job_42/playbook.yml") == 42
    assert ansible_api._job_id_from_generated_artifact_path("playbooks/generated/job_42/playbook.yml") == 42
    assert ansible_api._job_id_from_generated_artifact_path("roles/generated/job_42/vlan/tasks/main.yml") == 42
    assert ansible_api._job_id_from_generated_artifact_path("playbooks/manual.yml") is None


def test_pending_generated_artifact_is_still_editable(monkeypatch):
    monkeypatch.setattr(ansible_api, "_db", _FakeJobsDb("pending"))

    ansible_api._assert_generated_artifact_unlocked("generated/job_42/playbook.yml", "modified")


def test_approved_generated_artifact_write_is_locked(monkeypatch):
    monkeypatch.setattr(ansible_api, "_db", _FakeJobsDb("approved"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ansible_api.write_file(
                "generated/job_42/playbook.yml",
                ansible_api.WriteFileRequest(content="---\n"),
                user={"username": "tester"},
            )
        )

    assert exc_info.value.status_code == 423
    assert "approved playbook artifacts are read-only" in str(exc_info.value.detail).lower()


def test_approved_job_candidate_selection_is_locked(monkeypatch):
    monkeypatch.setattr(ansible_api, "_db", _FakeJobsDb("approved"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ansible_api.select_candidate(
                42,
                candidate_index=0,
                playbook_yaml="---\n- hosts: all\n",
                user={"username": "tester"},
            )
        )

    assert exc_info.value.status_code == 423
