from __future__ import annotations

from db.manager import DatabaseManager
from db.models import Device
from deploy.models import ChangeRequest, DeploymentRepository, Job, JobStatus, PipelineStage
from llm.context_contract import (
    CONTEXT_DOMAIN_DEPLOYMENT,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_SECURITY,
)
from llm.context_state import ContextStateManager
from llm.refresh_fabric import RefreshFabric, RefreshFabricEvent
from netbox.api import _invalidate_inventory_context
from netbox.sync_devices import DeviceSyncer
from netbox.sync_topology import TopologySyncer


class _FakeRefreshState:
    def __init__(self) -> None:
        self.jobs = []

    def start_refresh_job(self, target: str, requested_by: str = "tool"):
        job = {"job_id": f"job-{target}", "target": target, "requested_by": requested_by}
        self.jobs.append(job)
        return job

    def run_refresh(self, target: str, requested_by: str = "tool"):
        return {"status": "completed", "target": target, "requested_by": requested_by}


def test_refresh_fabric_invalidates_domains_and_starts_selective_jobs():
    state = ContextStateManager()
    refresh_state = _FakeRefreshState()
    fabric = RefreshFabric(context_state=state, refresh_state=refresh_state)

    publication = fabric.publish(
        RefreshFabricEvent(
            source="unit_test",
            action="inventory_changed",
            summary="inventory changed",
            domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY),
            refresh_targets=("inventory", "planned_topology"),
        )
    )

    snapshot = state.snapshot()
    assert snapshot["versions"][CONTEXT_DOMAIN_INVENTORY] == 1
    assert snapshot["versions"][CONTEXT_DOMAIN_PLANNED_TOPOLOGY] == 1
    assert [job["target"] for job in refresh_state.jobs] == ["inventory", "planned_topology"]
    assert publication["versions"][CONTEXT_DOMAIN_INVENTORY] == 1


def test_refresh_fabric_notifies_registered_listener():
    state = ContextStateManager()
    refresh_state = _FakeRefreshState()
    fabric = RefreshFabric(context_state=state, refresh_state=refresh_state)
    observed = []
    fabric.register_listener("listener", lambda publication: observed.append(publication["action"]))

    fabric.publish(
        RefreshFabricEvent(
            source="unit_test",
            action="topology_changed",
            summary="live topology changed",
            domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY,),
            refresh_mode="none",
        )
    )

    assert observed == ["topology_changed"]


def test_db_manager_security_event_publishes_security_domain(tmp_path, monkeypatch):
    db_path = tmp_path / "netauto.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    calls = []

    monkeypatch.setattr(
        "db.manager.publish_context_refresh_event",
        lambda **kwargs: calls.append(kwargs) or {"event_id": "evt-security"},
    )

    event_id = db.insert_security_event(
        event_type="rogue_device",
        severity="high",
        description="rogue detected",
        source_ip="10.0.0.99",
    )

    assert event_id == 1
    assert calls[0]["domains"] == (CONTEXT_DOMAIN_SECURITY,)
    assert calls[0]["metadata"]["event_type"] == "rogue_device"


def test_inventory_mutation_helper_routes_targeted_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "netbox.api.publish_context_refresh_event",
        lambda **kwargs: calls.append(kwargs) or {"event_id": "evt-netbox"},
    )

    _invalidate_inventory_context(
        source="unit_test",
        summary="device updated",
        metadata={"hostname": "R1"},
    )

    assert calls[0]["domains"] == (CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY)
    assert calls[0]["refresh_targets"] == ("inventory", "planned_topology")


def test_deployment_commit_publishes_deployment_and_live_refresh(tmp_path, monkeypatch):
    db_path = tmp_path / "deploy.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    device_id = db.upsert_device(Device(hostname="R1", management_ip="10.0.0.1"))
    repo = DeploymentRepository(db)
    request_id = repo.create_request(ChangeRequest(title="Push ACL", requester="tester"))
    job_id = repo.create_job(Job(request_id=request_id, device_id=device_id))
    calls = []

    monkeypatch.setattr(
        "deploy.models.publish_context_refresh_event",
        lambda **kwargs: calls.append(kwargs) or {"event_id": "evt-deploy"},
    )

    repo.update_job_status(job_id, JobStatus.SUCCESS, PipelineStage.COMMIT)

    assert calls[0]["domains"] == (CONTEXT_DOMAIN_DEPLOYMENT, CONTEXT_DOMAIN_LIVE_TOPOLOGY)
    assert calls[0]["refresh_targets"] == ("live_topology",)


def test_netbox_device_sync_publishes_refresh_fabric_event(monkeypatch):
    calls = []

    class _FakeClient:
        api = object()

        def get_devices(self):
            return []

    db = type("_Db", (), {})()
    monkeypatch.setattr("netbox.sync_devices.NetBoxClient.get_client", lambda: _FakeClient())
    monkeypatch.setattr("netbox.sync_devices.publish_context_refresh_event", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("netbox.sync_devices.get_refresh_state_manager", lambda: type("_State", (), {
        "record_start": lambda *args, **kwargs: None,
        "record_skipped": lambda *args, **kwargs: None,
        "record_success": lambda *args, **kwargs: None,
    })())

    result = DeviceSyncer(db).sync_all_devices()

    assert result["status"] == "success"
    assert calls[0]["domains"] == (CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY)
    assert calls[0]["refresh_mode"] == "none"


def test_netbox_topology_sync_publishes_refresh_fabric_event(monkeypatch):
    calls = []

    class _FakeClient:
        api = object()

        def get_cables(self):
            return []

    db = type("_Db", (), {})()
    monkeypatch.setattr("netbox.sync_topology.NetBoxClient.get_client", lambda: _FakeClient())
    monkeypatch.setattr("netbox.sync_topology.publish_context_refresh_event", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("netbox.sync_topology.get_refresh_state_manager", lambda: type("_State", (), {
        "record_start": lambda *args, **kwargs: None,
        "record_skipped": lambda *args, **kwargs: None,
        "record_success": lambda *args, **kwargs: None,
    })())

    result = TopologySyncer(db).sync_connections()

    assert result["status"] == "success"
    assert calls[0]["domains"] == (CONTEXT_DOMAIN_PLANNED_TOPOLOGY,)
    assert calls[0]["refresh_mode"] == "none"