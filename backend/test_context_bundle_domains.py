from rag.context_bundle import ContextBundleBuilder
from llm.context import ContextBuilder


class _FakeSnapshot:
    def __init__(self, config_hash: str, captured_at: str):
        self.config_hash = config_hash
        self.captured_at = captured_at


class _FakeDevice:
    def __init__(self, device_id: int, hostname: str):
        self.id = device_id
        self.hostname = hostname
        self.management_ip = "10.0.0.1" if device_id == 1 else "10.0.0.2"
        self.vendor = "cisco_ios"
        self.model = "CSR"
        self.status = "active"
        self.role = "core"
        self.source = "manual"
        self.updated_at = "2026-03-09T10:00:00+00:00"
        self.created_at = self.updated_at
        self.last_polled = self.updated_at


class _FakeDriftEvent:
    def __init__(self):
        self.device_id = 1
        self.hostname = "R1"
        self.has_drift = True
        self.severity = "high"
        self.timestamp = "2026-03-09T10:07:00+00:00"


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        query_text = " ".join(str(query).split()).lower()

        class _Rows:
            def __init__(self, rows=None, row=None):
                self._rows = rows or []
                self._row = row

            def fetchone(self_nonlocal):
                return self_nonlocal._row

            def fetchall(self_nonlocal):
                return self_nonlocal._rows

        if "select hostname, management_ip, vendor, model, os_version, serial_number, status, role from devices" in query_text:
            return _Rows(
                rows=[
                    {
                        "hostname": "R1",
                        "management_ip": "10.0.0.1",
                        "vendor": "cisco_ios",
                        "model": "CSR",
                        "os_version": "17.9",
                        "serial_number": "SER123",
                        "status": "active",
                        "role": "core",
                    }
                ]
            )

        if "select max(updated_at) as last_updated from devices" in query_text:
            return _Rows(row={"last_updated": "2026-03-09T10:10:00+00:00"})

        if "from interfaces i join devices d" in query_text:
            return _Rows(
                rows=[
                    {
                        "name": "Gig0/0",
                        "ip_address": "10.0.0.1",
                        "mac_address": "aa:bb:cc:dd:ee:ff",
                        "status": "active",
                    }
                ]
            )

        if "select id, intent_type, status, resolved_devices, created_at from intents" in query_text:
            return _Rows(
                rows=[
                    {
                        "id": "intent-1",
                        "intent_type": "deploy",
                        "status": "planned",
                        "resolved_devices": '["R1"]',
                        "created_at": "2026-03-09T10:11:00+00:00",
                    }
                ]
            )

        if "select id, title, status, requester, created_at, updated_at from change_requests" in query_text:
            return _Rows(
                rows=[
                    {
                        "id": 14,
                        "title": "Deploy OSPF baseline",
                        "status": "running",
                        "requester": "ai_agent",
                        "created_at": "2026-03-09T10:12:00+00:00",
                        "updated_at": "2026-03-09T10:13:00+00:00",
                    }
                ]
            )

        if "from jobs j left join devices d on d.id = j.device_id left join change_requests cr on cr.id = j.request_id" in query_text:
            return _Rows(
                rows=[
                    {
                        "id": 21,
                        "status": "running",
                        "stage": "apply",
                        "updated_at": "2026-03-09T10:14:00+00:00",
                        "hostname": "R1",
                        "request_title": "Deploy OSPF baseline",
                    }
                ]
            )

        if "from security_events se left join devices d on d.id = se.device_id" in query_text:
            return _Rows(
                rows=[
                    {
                        "id": 301,
                        "event_type": "rogue_device",
                        "severity": "high",
                        "source_ip": "10.9.9.99",
                        "dest_ip": "10.0.0.1",
                        "description": "Unexpected device seen on access port",
                        "status": "new",
                        "created_at": "2026-03-09T10:16:00+00:00",
                        "hostname": "R1",
                    }
                ]
            )

        if "from threshold_alerts order by created_at desc limit 10" in query_text:
            return _Rows(
                rows=[
                    {
                        "id": 41,
                        "device": "R1",
                        "metric": "cpu_usage",
                        "value": 97.2,
                        "threshold": 90.0,
                        "severity": "critical",
                        "status": "active",
                        "incident_id": "INC-1",
                        "created_at": "2026-03-09T10:17:00+00:00",
                    }
                ]
            )

        return _Rows()


class _FakeDb:
    def get_topology(self):
        return {"timestamp": "2026-03-09T10:08:00+00:00", "nodes": {"R1": {"mgmt_ip": "10.0.0.1", "down": False}}}

    def get_all_devices(self):
        return [_FakeDevice(1, "R1"), _FakeDevice(2, "R2")]

    def get_config_history(self, device_id, limit=1):
        if device_id == 1:
            return [_FakeSnapshot("abc123def456", "2026-03-09T10:06:00+00:00")]
        return []

    def get_drift_events(self, limit=20):
        return [_FakeDriftEvent()]

    def _connect(self):
        return _FakeConn()


class _FakeIncidentStore:
    def list_all(self, limit=20):
        return [
            {
                "id": "INC-1",
                "anomaly_type": "latency_spike",
                "severity": "high",
                "status": "open",
                "affected_devices": '["R1"]',
                "updated_at": "2026-03-09T10:09:00+00:00",
                "created_at": "2026-03-09T10:09:00+00:00",
            }
        ]


class _FakeKnowledgeStore:
    def __init__(self, embedder=None):
        self.available = True

    def list_documents(self):
        return [
            {
                "doc_name": "ospf-guide.pdf",
                "doc_type": "vendor_doc",
                "vendor": "cisco",
                "ingested_at": "2026-03-09T09:00:00+00:00",
            }
        ]

    def count(self):
        return 12


class _FakeNetBoxDevice:
    def __init__(self, device_id: int, name: str, ip: str, updated_at: str):
        self.id = device_id
        self.name = name
        self.primary_ip4 = ip
        self.platform = type("Platform", (), {"slug": "cisco-ios"})()
        self.device_type = type("DeviceType", (), {"model": "C9300"})()
        self.role = type("Role", (), {"slug": "core"})()
        self.site = type("Site", (), {"name": "DC1", "__str__": lambda self: "DC1"})()
        self.status = type("Status", (), {"value": "active", "__str__": lambda self: "active"})()
        self.serial = "ABC123"
        self.last_updated = updated_at


class _FakeNetBoxEndpoint:
    def __init__(self, devices):
        self._devices = devices

    def all(self):
        return self._devices

    def filter(self, **filters):
        names = filters.get("name")
        if not names:
            return self._devices
        return [device for device in self._devices if device.name in names]


class _FakeApi:
    def __init__(self, devices):
        self.dcim = type("Dcim", (), {"devices": _FakeNetBoxEndpoint(devices)})()


class _FakeNetBoxClient:
    def __init__(self, devices):
        self.api = _FakeApi(devices)
        self._devices = devices

    def get_interfaces_for_device(self, device_id: int):
        if device_id != 7:
            return []
        return [
            {
                "name": "Gig0/0",
                "ip_address": "10.9.9.1",
                "subnet_mask": "255.255.255.0",
                "mac_address": "aa:bb:cc:dd:ee:ff",
                "type": "1000base-t",
            }
        ]


class _FakeTopologySyncer:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def get_planned_topology(self):
        return {
            "nodes": {
                "NB1": {
                    "mgmt_ip": "10.9.9.1",
                    "connectedNode": ["NB2"],
                    "edgeLink": {
                        "Eth1": {
                            "remote_node": "NB2",
                            "remote_interface": "Eth2",
                            "remote_ip": "10.9.9.2",
                        }
                    },
                },
                "NB2": {
                    "mgmt_ip": "10.9.9.2",
                    "connectedNode": ["NB1"],
                    "edgeLink": {
                        "Eth2": {
                            "remote_node": "NB1",
                            "remote_interface": "Eth1",
                            "remote_ip": "10.9.9.1",
                        }
                    },
                },
            },
            "source": "netbox",
            "timestamp": "2026-03-09T10:15:00+00:00",
            "device_count": 2,
        }


def test_context_bundle_includes_missing_authoritative_domains(monkeypatch):
    monkeypatch.setattr("incidents.store.IncidentStore", _FakeIncidentStore)
    monkeypatch.setattr("llm.vector_store.KnowledgeVectorStore", _FakeKnowledgeStore)

    builder = ContextBundleBuilder(db_manager=_FakeDb())
    bundle = builder.build_bundle(device_hostnames=["R1"], include_telemetry=False, include_sdn=False)
    text = bundle.to_prompt_text()

    assert "Config History" in text
    assert "Incidents" in text
    assert "Drift Findings" in text
    assert "Knowledge Base" in text


def test_context_bundle_prefers_netbox_for_inventory_and_planned_topology(monkeypatch):
    monkeypatch.setattr(
        "netbox.client.NetBoxClient.get_client",
        lambda: _FakeNetBoxClient([
            _FakeNetBoxDevice(7, "NB1", "10.9.9.1/24", "2026-03-09T10:12:00+00:00")
        ]),
    )
    monkeypatch.setattr("netbox.sync_topology.TopologySyncer", _FakeTopologySyncer)

    builder = ContextBundleBuilder(db_manager=_FakeDb())
    bundle = builder.build_bundle(device_hostnames=["NB1"], include_telemetry=False, include_sdn=False)

    assert bundle.inventory.source == "netbox"
    assert bundle.inventory.devices[0]["hostname"] == "NB1"
    assert bundle.inventory.interfaces[0]["ip_address"] == "10.9.9.1"
    assert bundle.planned_topology.source == "netbox"
    assert bundle.planned_topology.links[0]["src_hostname"] == "NB1"

    text = bundle.to_prompt_text()
    assert "Using NetBox as the source of truth for planned inventory." in text
    assert "Using NetBox planned topology as the source of truth." in text
    assert "Planned Topology" in text


def test_context_bundle_local_fallback_is_explicit(monkeypatch):
    monkeypatch.setattr("netbox.client.NetBoxClient.get_client", lambda: type("Client", (), {"api": None})())

    builder = ContextBundleBuilder(db_manager=_FakeDb())
    bundle = builder.build_bundle(device_hostnames=["R1"], include_telemetry=False, include_sdn=False)
    text = bundle.to_prompt_text()

    assert bundle.inventory.source == "local_db"
    assert bundle.planned_topology.source == "local_db"
    assert "using local DB fallback" in text


def test_context_builder_bundle_context_uses_netbox_first(monkeypatch):
    monkeypatch.setattr(
        "netbox.client.NetBoxClient.get_client",
        lambda: _FakeNetBoxClient([
            _FakeNetBoxDevice(7, "NB1", "10.9.9.1/24", "2026-03-09T10:12:00+00:00")
        ]),
    )
    monkeypatch.setattr("netbox.sync_topology.TopologySyncer", _FakeTopologySyncer)

    builder = ContextBuilder(db_manager=_FakeDb())
    text = builder.build_network_bundle_context(device_hostnames=["NB1"])

    assert "NB1" in text
    assert "source: netbox" in text
    assert "Planned Topology" in text


def test_context_bundle_includes_step10_operational_domains(monkeypatch):
    monkeypatch.setattr("incidents.store.IncidentStore", _FakeIncidentStore)
    monkeypatch.setattr("llm.vector_store.KnowledgeVectorStore", _FakeKnowledgeStore)

    builder = ContextBundleBuilder(db_manager=_FakeDb())
    bundle = builder.build_bundle(device_hostnames=["R1"], include_telemetry=False, include_sdn=False)
    text = bundle.to_prompt_text()

    assert "Deployment And Intent Status" in text
    assert "Threat Detections" in text
    assert "Snapshot coverage" in text
    assert bundle.deployment_status.jobs[0]["stage"] == "apply"
    assert bundle.threat_detections.security_events[0]["event_type"] == "rogue_device"


def test_context_bundle_emits_freshness_warnings_for_missing_or_partial_context(monkeypatch):
    monkeypatch.setattr("incidents.store.IncidentStore", _FakeIncidentStore)
    monkeypatch.setattr("llm.vector_store.KnowledgeVectorStore", _FakeKnowledgeStore)
    monkeypatch.setattr("rag.context_bundle.INFLUXDB_TOKEN", "")

    builder = ContextBundleBuilder(db_manager=_FakeDb())
    bundle = builder.build_bundle(device_hostnames=["R1", "R2"], include_telemetry=True, include_sdn=False)
    text = bundle.to_prompt_text()

    assert "Freshness Warnings" in text
    assert "Telemetry" in text
    assert "Missing recent config snapshots for: R2" in text