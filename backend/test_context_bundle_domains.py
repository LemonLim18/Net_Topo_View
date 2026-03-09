from rag.context_bundle import ContextBundleBuilder


class _FakeSnapshot:
    def __init__(self, config_hash: str, captured_at: str):
        self.config_hash = config_hash
        self.captured_at = captured_at


class _FakeDevice:
    def __init__(self, device_id: int, hostname: str):
        self.id = device_id
        self.hostname = hostname
        self.management_ip = "10.0.0.1"
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
        class _Rows:
            def fetchone(self_nonlocal):
                return None

            def fetchall(self_nonlocal):
                return []

        return _Rows()


class _FakeDb:
    def get_topology(self):
        return {"timestamp": "2026-03-09T10:08:00+00:00", "nodes": {"R1": {"mgmt_ip": "10.0.0.1", "down": False}}}

    def get_all_devices(self):
        return [_FakeDevice(1, "R1")]

    def get_config_history(self, device_id, limit=1):
        return [_FakeSnapshot("abc123def456", "2026-03-09T10:06:00+00:00")]

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