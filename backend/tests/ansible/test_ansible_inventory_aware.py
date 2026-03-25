import asyncio
import json
from types import SimpleNamespace

from ansible import api as ansible_api
from ansible.inventory import build_inventory
from ansible.planning_context import planning_context_readiness


class _DbWithVendors:
    def __init__(self, vendors):
        self._vendors = dict(vendors)

    def get_device_by_hostname(self, hostname):
        vendor = self._vendors.get(hostname)
        if not vendor:
            return None
        return SimpleNamespace(hostname=hostname, vendor=vendor)


def test_resolve_backend_intent_request_parses_nl_and_infers_vendor(monkeypatch):
    monkeypatch.setattr(ansible_api, "_db", _DbWithVendors({"SW1": "cisco_ios", "SW2": "cisco_ios"}))
    monkeypatch.setattr(ansible_api, "_build_intent_parse_context", lambda *_: "")
    monkeypatch.setattr(ansible_api, "_policy_validate_intent", lambda *args, **kwargs: {"approved": True})

    def _fake_parse(self, user_message, topology_context=""):
        return json.dumps(
            {
                "intent_type": "configure",
                "action": "create_vlan",
                "feature": "vlan",
                "vendor": "",
                "parameters": {"vlan_id": 20, "vlan_name": "TEST"},
                "scope": {"device_role": "access_switch", "device_hostnames": []},
                "resolved_devices": ["SW1", "SW2"],
                "risk_level": "medium",
                "justification": user_message,
            }
        )

    monkeypatch.setattr("llm.intent.IntentParser.parse", _fake_parse)

    req = ansible_api.CreateAndPlanRequest(
        intent_description="configure vlan 20 named TEST on all access switches",
    )

    plan_req, parsed_intent, policy = ansible_api._resolve_backend_intent_request(
        req,
        user={"username": "tester", "role": "admin"},
    )

    assert plan_req.feature == "vlan"
    assert plan_req.target_hosts == ["SW1", "SW2"]
    assert plan_req.vendor == "cisco_ios"
    assert plan_req.params == {"vlan_id": 20, "vlan_name": "TEST"}
    assert parsed_intent["resolved_devices"] == ["SW1", "SW2"]
    assert policy["approved"] is True


def test_ensure_planning_context_reconciles_then_retries(monkeypatch):
    monkeypatch.setattr(ansible_api, "_db", SimpleNamespace(get_all_devices=lambda: []))

    contexts = [
        {
            "target_hosts": ["SW1"],
            "feature": "routing",
            "inventory": {"source": "local_db", "freshness": {"freshness_class": "stale"}},
            "planned_topology": {"source": "local_db", "freshness": {"freshness_class": "stale"}},
            "devices": [{"hostname": "SW1", "management_ip": "", "vendor": "", "interfaces": []}],
        },
        {
            "target_hosts": ["SW1"],
            "feature": "routing",
            "inventory": {"source": "netbox", "freshness": {"freshness_class": "live"}},
            "planned_topology": {"source": "netbox", "freshness": {"freshness_class": "live"}},
            "devices": [
                {
                    "hostname": "SW1",
                    "management_ip": "10.0.0.10",
                    "vendor": "cisco_ios",
                    "interfaces": [{"name": "Gig0/1"}],
                }
            ],
        },
    ]
    reconcile_calls = []

    def _fake_build(*args, **kwargs):
        return contexts.pop(0)

    def _fake_reconcile(db_manager):
        reconcile_calls.append(db_manager)
        return {"inventory_sync": {"status": "success"}, "topology_sync": {"status": "success"}}

    monkeypatch.setattr(ansible_api, "build_scoped_planning_context", _fake_build)
    monkeypatch.setattr(ansible_api, "reconcile_inventory_state", _fake_reconcile)

    planning_context, meta = asyncio.run(
        ansible_api._ensure_planning_context(
            target_hosts=["SW1"],
            vendor="cisco_ios",
            intent_description="configure ospf",
            feature="routing",
        )
    )

    assert planning_context["inventory"]["source"] == "netbox"
    assert meta["reconciled"] is True
    assert len(reconcile_calls) == 1


def test_create_and_plan_uses_backend_parsed_request(monkeypatch):
    monkeypatch.setattr(ansible_api, "_db", SimpleNamespace())
    monkeypatch.setattr(ansible_api, "_pipeline", object())

    captured = {}

    def _fake_resolve(req, user):
        captured["intent_description"] = req.intent_description
        return (
            ansible_api.PlanAnsibleRequest(
                intent_description=req.intent_description,
                target_hosts=["SW1", "SW2"],
                vendor="cisco_ios",
                feature="vlan",
                params={"vlan_id": 20, "vlan_name": "TEST"},
            ),
            {"feature": "vlan", "resolved_devices": ["SW1", "SW2"]},
            {"approved": True},
        )

    class _FakeRepo:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        def create_request(self, change_request):
            return 12

        def create_job(self, job):
            captured["job_device_id"] = job.device_id
            return 34

    async def _fake_plan_ansible(job_id, req, user):
        captured["planned_hosts"] = req.target_hosts
        captured["planned_feature"] = req.feature
        return {
            "status": "playbook_generated",
            "playbook": "---\n- name: Test\n  hosts: SW1\n  tasks: []\n",
            "artifact_type": "playbook",
            "entrypoint_path": None,
            "files": [],
            "manifest": {},
        }

    monkeypatch.setattr(ansible_api, "_resolve_backend_intent_request", _fake_resolve)
    monkeypatch.setattr(ansible_api, "plan_ansible", _fake_plan_ansible)
    monkeypatch.setattr("deploy.models.DeploymentRepository", _FakeRepo)

    response = asyncio.run(
        ansible_api.create_and_plan(
            ansible_api.CreateAndPlanRequest(
                intent_description="configure vlan 20 named TEST on all access switches",
                device_id=99,
            ),
            user={"username": "tester", "role": "admin"},
        )
    )

    assert captured["planned_hosts"] == ["SW1", "SW2"]
    assert captured["planned_feature"] == "vlan"
    assert captured["job_device_id"] == 99
    assert response["parsed_intent"]["resolved_devices"] == ["SW1", "SW2"]
    assert response["policy"]["approved"] is True


def test_inventory_builds_role_and_site_groups():
    inventory = build_inventory(
        topology_override={
            "nodes": {
                "SW1": {
                    "hostname": "SW1",
                    "mgmt_ip": "10.0.0.10",
                    "vendor": "cisco_ios",
                    "role": "access_switch",
                    "site": "Building A",
                    "interfaces": [{"name": "Gig0/1"}],
                    "connectedNode": ["R1"],
                },
                "R1": {
                    "hostname": "R1",
                    "mgmt_ip": "10.0.0.1",
                    "vendor": "juniper_junos",
                    "role": "core_router",
                    "site": "Building A",
                },
            }
        }
    )

    children = inventory["all"]["children"]
    assert "vendor_cisco_ios" in children
    assert "vendor_juniper_junos" in children
    assert "access_switches" in children
    assert "core_routers" in children
    assert "site_building_a" in children
    assert inventory["all"]["hosts"]["SW1"]["nettopo_role"] == "access"
    assert inventory["all"]["hosts"]["SW1"]["nettopo_neighbors"] == ["R1"]


def test_planning_context_readiness_requires_interfaces_for_routing():
    readiness = planning_context_readiness(
        {
            "target_hosts": ["R1"],
            "feature": "routing",
            "inventory": {"source": "netbox", "freshness": {"freshness_class": "live"}},
            "planned_topology": {"source": "netbox", "freshness": {"freshness_class": "live"}},
            "devices": [
                {
                    "hostname": "R1",
                    "management_ip": "10.0.0.1",
                    "vendor": "cisco_ios",
                    "interfaces": [],
                }
            ],
        }
    )

    assert readiness["ready"] is False
    assert any("missing interface inventory" in issue for issue in readiness["issues"])


def test_planning_context_readiness_allows_ospf_with_stale_inventory_when_device_fields_present():
    readiness = planning_context_readiness(
        {
            "target_hosts": ["R1"],
            "feature": "ospf",
            "inventory": {"source": "netbox", "freshness": {"freshness_class": "stale"}},
            "planned_topology": {"source": "netbox", "freshness": {"freshness_class": "stale"}},
            "devices": [
                {
                    "hostname": "R1",
                    "management_ip": "10.0.0.1",
                    "vendor": "cisco_ios",
                    "interfaces": [],
                }
            ],
        }
    )

    assert readiness["ready"] is True
    assert not any("freshness is stale" in issue for issue in readiness["issues"])


def test_planning_context_readiness_allows_static_route_with_stale_inventory_when_device_fields_present():
    readiness = planning_context_readiness(
        {
            "target_hosts": ["R1"],
            "feature": "static_route",
            "inventory": {"source": "netbox", "freshness": {"freshness_class": "stale"}},
            "planned_topology": {"source": "netbox", "freshness": {"freshness_class": "stale"}},
            "devices": [
                {
                    "hostname": "R1",
                    "management_ip": "10.0.0.1",
                    "vendor": "cisco_ios",
                    "interfaces": [],
                }
            ],
        }
    )
    assert readiness["ready"] is True
    assert not any("freshness is stale" in issue for issue in readiness["issues"])
