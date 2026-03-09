import json

from db.models import Device
from llm import network_tools
from llm.retriever import KnowledgeRetriever


def _assert_freshness_metadata(metadata: dict, expected_scope: str, expected_source: str):
    assert metadata["scope"] == expected_scope
    assert metadata["source"] == expected_source
    assert "captured_at" in metadata
    assert "ingested_at" in metadata
    assert "confidence" in metadata
    assert "freshness_class" in metadata


class _FakeSnapshot:
    def __init__(self):
        self.config_text = "interface Gig0/0\n description uplink"
        self.config_hash = "abc123def456"
        self.captured_at = "2026-03-09T10:10:00+00:00"


class _FakeDriftEvent:
    def __init__(self):
        self.device_id = 1
        self.hostname = "R1"
        self.has_drift = True
        self.severity = "high"
        self.timestamp = "2026-03-09T10:11:00+00:00"


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        class _Rows:
            def fetchall(self_nonlocal):
                return [
                    {
                        "id": 1,
                        "event_type": "rogue_device",
                        "severity": "high",
                        "source_ip": "10.0.0.99",
                        "dest_ip": "",
                        "description": "Unexpected device detected",
                        "status": "new",
                        "created_at": "2026-03-09T10:12:00+00:00",
                    }
                ]

        return _Rows()


class _FreshnessFakeDb:
    def get_topology(self):
        return {
            "timestamp": "2026-03-09T10:13:00+00:00",
            "nodes": {"R1": {"mgmt_ip": "10.0.0.1", "down": False}},
        }

    def get_all_devices(self):
        return [
            Device(
                hostname="R1",
                management_ip="10.0.0.1",
                vendor="cisco_ios",
                status="active",
                role="core",
                source="manual",
                updated_at="2026-03-09T10:09:00+00:00",
                id=1,
            )
        ]

    def get_device_by_hostname(self, hostname):
        if hostname.lower() == "r1":
            return self.get_all_devices()[0]
        return None

    def get_config_history(self, device_id, limit=1):
        return [_FakeSnapshot()]

    def get_drift_events(self, limit=10):
        return [_FakeDriftEvent()]

    def _connect(self):
        return _FakeConn()


class _FakeIncidentStore:
    def list_all(self, status=None, limit=10):
        return [
            {
                "id": "INC-1",
                "anomaly_type": "latency_spike",
                "severity": "high",
                "status": "open",
                "affected_devices": '["R1"]',
                "created_at": "2026-03-09T10:14:00+00:00",
                "updated_at": "2026-03-09T10:14:30+00:00",
            }
        ]


class _FakeKnowledgeStore:
    def __init__(self, embedder=None):
        self.available = True
        self._available = True

    def list_documents(self):
        return [
            {
                "doc_id": "doc-1",
                "doc_name": "ospf-guide.pdf",
                "doc_type": "vendor_doc",
                "vendor": "cisco",
                "ingested_at": "2026-03-09T09:00:00+00:00",
                "chunk_count": 3,
            }
        ]

    def count(self):
        return 3


class _FakeRetrieverStore:
    _available = True

    def query(self, query_embedding, k=5, where=None, min_score=0.35):
        return [
            {
                "text": "Use passive-interface on access links.",
                "score": 0.81,
                "metadata": {
                    "doc_name": "ospf-guide.pdf",
                    "doc_type": "vendor_doc",
                    "vendor": "cisco",
                    "page_number": 12,
                    "chunk_index": 0,
                    "ingested_at": "2026-03-09T09:00:00+00:00",
                },
            }
        ]


class _FakeEmbedder:
    def embed_query(self, query: str):
        return [0.1, 0.2, 0.3]


def test_config_tool_exposes_freshness_metadata():
    network_tools.set_db_manager(_FreshnessFakeDb())
    payload = json.loads(network_tools.get_device_running_config("R1"))
    _assert_freshness_metadata(payload["source_metadata"], "device_configs", "config_snapshots")


def test_incident_tool_exposes_freshness_metadata(monkeypatch):
    network_tools.set_db_manager(_FreshnessFakeDb())
    monkeypatch.setattr("incidents.store.IncidentStore", _FakeIncidentStore)
    payload = json.loads(network_tools.get_recent_incidents())
    _assert_freshness_metadata(payload["source_metadata"], "incident_state", "incident_store")


def test_drift_tool_exposes_freshness_metadata():
    network_tools.set_db_manager(_FreshnessFakeDb())
    payload = json.loads(network_tools.get_recent_drift_events())
    _assert_freshness_metadata(payload["source_metadata"], "drift_state", "drift_store")


def test_knowledge_status_exposes_freshness_metadata(monkeypatch):
    monkeypatch.setattr("llm.vector_store.KnowledgeVectorStore", _FakeKnowledgeStore)
    payload = json.loads(network_tools.get_knowledge_base_status())
    _assert_freshness_metadata(payload["source_metadata"], "knowledge_documents", "vector_store")


def test_security_events_expose_freshness_metadata():
    network_tools.set_db_manager(_FreshnessFakeDb())
    payload = json.loads(network_tools.get_security_events())
    _assert_freshness_metadata(payload["source_metadata"], "security_alerts", "security_events")


def test_retriever_exposes_freshness_metadata_for_chunks():
    retriever = KnowledgeRetriever(_FakeRetrieverStore(), _FakeEmbedder())
    results = retriever.retrieve("Explain OSPF")

    assert len(results) == 1
    _assert_freshness_metadata(results[0]["source_metadata"], "knowledge_documents", "vector_store")