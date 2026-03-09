from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from llm.ingestion import DocumentIngester
from llm.retriever import KnowledgeRetriever, build_retrieval_plan, select_retrieval_partitions
from llm.vector_store import (
    PARTITION_CHANGE_OUTCOMES,
    PARTITION_CONFIG_HISTORY,
    PARTITION_INCIDENTS,
    PARTITION_LIVE_STATE,
    PARTITION_PLANNED_STATE,
    PARTITION_STATIC_KNOWLEDGE,
)
from rag.auto_embedder import NetworkStateEmbedder


class _CaptureStore:
    def __init__(self):
        self.available = True
        self._available = True
        self.upserts = []
        self.deletes = []
        self.clears = []
        self.queries = []
        self.documents = {}

    def upsert_chunks(self, doc_id, chunks, metadatas, partition=None):
        self.documents[(partition, doc_id)] = {
            "chunks": list(chunks),
            "metadatas": list(metadatas),
        }
        self.upserts.append(
            {
                "doc_id": doc_id,
                "chunks": chunks,
                "metadatas": metadatas,
                "partition": partition,
            }
        )

    def delete_document(self, doc_id, partition=None):
        self.documents.pop((partition, doc_id), None)
        self.deletes.append({"doc_id": doc_id, "partition": partition})
        return 0

    def clear_partition(self, partition=None):
        self.clears.append(partition)
        return 0

    def list_documents(self, partition=None):
        rows = []
        for (doc_partition, doc_id), payload in self.documents.items():
            if partition and doc_partition != partition:
                continue
            metadata = payload["metadatas"][0] if payload["metadatas"] else {}
            rows.append(
                {
                    "doc_id": doc_id,
                    "doc_name": metadata.get("doc_name", doc_id),
                    "doc_type": metadata.get("doc_type", ""),
                    "vendor": metadata.get("vendor", ""),
                    "ingested_at": metadata.get("ingested_at", ""),
                    "chunk_count": len(payload["chunks"]),
                    "partition": doc_partition,
                    "collection_name": metadata.get("collection_name", ""),
                }
            )
        return rows

    def query(self, query_embedding, k=5, where=None, min_score=0.35, partitions=None):
        self.queries.append({"partitions": partitions, "where": where, "k": k})
        return [
            {
                "text": "Use passive-interface on access links.",
                "score": 0.82,
                "metadata": {
                    "doc_name": "ospf-guide.pdf",
                    "doc_type": "vendor_doc",
                    "vendor": "cisco",
                    "page_number": 12,
                    "chunk_index": 0,
                    "ingested_at": "2026-03-09T09:00:00+00:00",
                    "partition": PARTITION_STATIC_KNOWLEDGE,
                },
            }
        ]


class _FakeEmbedder:
    def embed_query(self, query: str):
        return [0.1, 0.2, 0.3]


class _FakeDb:
    def _connect(self):
        class _Conn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            def execute(self_inner, query, params):
                class _Row:
                    def fetchone(self_row):
                        return {
                            "config_text": "hostname R1\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0",
                            "captured_at": "2026-03-09T10:10:00+00:00",
                        }

                return _Row()

        return _Conn()

    def get_all_devices(self):
        return [
            SimpleNamespace(
                id=1,
                hostname="R1",
                management_ip="10.0.0.1",
                vendor="cisco_ios",
                model="ISR",
                os_version="17.9",
                serial_number="ABC123",
                status="active",
                role="core",
                source="netbox",
            )
        ]

    def get_interfaces_for_device(self, device_id):
        return [
            SimpleNamespace(id=11, name="Gi0/0", ip_address="10.0.0.1", subnet_mask="255.255.255.0", status="up"),
            SimpleNamespace(id=12, name="Gi0/1", ip_address=None, subnet_mask=None, status="down"),
        ]

    def get_all_links(self):
        return [SimpleNamespace(src_device_id=1, src_interface_id=11, dst_device_id=1, dst_interface_id=12, discovery_protocol="manual")]

    def get_device_by_id(self, device_id):
        if device_id == 1:
            return SimpleNamespace(id=1, hostname="R1")
        return None

    def get_device_by_hostname(self, hostname):
        if hostname == "R1":
            return SimpleNamespace(id=1, hostname="R1")
        return None

    def get_config_history(self, device_id, limit=1):
        return [SimpleNamespace(config_text="hostname R1\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0")]

    def get_drift_events(self, device_id=None, limit=1):
        return [SimpleNamespace(device_id=device_id or 1, hostname="R1", has_drift=True, severity="high", diff_text="- old\n+ new")]


class _PlannerStore:
    def __init__(self):
        self.available = True
        self._available = True
        self.queries = []
        self._recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._stale = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(timespec="seconds")

    def query(self, query_embedding, k=5, where=None, min_score=0.35, partitions=None):
        self.queries.append({"partitions": partitions, "where": where, "k": k})
        partition_list = partitions or []
        if partition_list == [PARTITION_PLANNED_STATE]:
            return [
                {
                    "text": "SW1 management IP is 10.0.0.2 in planned inventory.",
                    "score": 0.48,
                    "metadata": {
                        "doc_name": "netbox-inventory",
                        "doc_type": "inventory_snapshot",
                        "vendor": "netbox",
                        "chunk_index": 0,
                        "ingested_at": self._recent,
                        "partition": PARTITION_PLANNED_STATE,
                    },
                }
            ]
        if partition_list == [PARTITION_LIVE_STATE, PARTITION_INCIDENTS, PARTITION_CONFIG_HISTORY]:
            return [
                {
                    "text": "SW1 is currently down on uplink Gi0/1.",
                    "score": 0.43,
                    "metadata": {
                        "doc_name": "live-topology",
                        "doc_type": "topology_snapshot",
                        "vendor": "crawler",
                        "chunk_index": 0,
                        "ingested_at": self._recent,
                        "partition": PARTITION_LIVE_STATE,
                    },
                }
            ]
        if partition_list == [PARTITION_CHANGE_OUTCOMES, PARTITION_STATIC_KNOWLEDGE]:
            return [
                {
                    "text": "Check interface counters and spanning-tree state during link troubleshooting.",
                    "score": 0.96,
                    "metadata": {
                        "doc_name": "troubleshooting-guide.pdf",
                        "doc_type": "vendor_doc",
                        "vendor": "cisco",
                        "chunk_index": 0,
                        "ingested_at": self._recent,
                        "partition": PARTITION_STATIC_KNOWLEDGE,
                    },
                }
            ]
        if partition_list == [PARTITION_LIVE_STATE, PARTITION_INCIDENTS, PARTITION_CONFIG_HISTORY] and k >= 5:
            return [
                {
                    "text": "R1 last reported up five hours ago.",
                    "score": 0.71,
                    "metadata": {
                        "doc_name": "live-topology-old",
                        "doc_type": "topology_snapshot",
                        "vendor": "crawler",
                        "chunk_index": 1,
                        "ingested_at": self._stale,
                        "partition": PARTITION_LIVE_STATE,
                    },
                },
                {
                    "text": "R1 is currently reachable on the live graph.",
                    "score": 0.68,
                    "metadata": {
                        "doc_name": "live-topology-new",
                        "doc_type": "topology_snapshot",
                        "vendor": "crawler",
                        "chunk_index": 2,
                        "ingested_at": self._recent,
                        "partition": PARTITION_LIVE_STATE,
                    },
                },
            ]
        return []


class _FreshnessStore:
    def __init__(self):
        self.available = True
        self._available = True
        self.recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.stale = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(timespec="seconds")

    def query(self, query_embedding, k=5, where=None, min_score=0.35, partitions=None):
        return [
            {
                "text": "R2 was last seen four hours ago.",
                "score": 0.73,
                "metadata": {
                    "doc_name": "stale-live-state",
                    "doc_type": "topology_snapshot",
                    "vendor": "crawler",
                    "chunk_index": 0,
                    "captured_at": self.stale,
                    "ingested_at": self.stale,
                    "partition": PARTITION_LIVE_STATE,
                    "source_type": "live_state",
                    "freshness_weight": 0.3,
                },
            },
            {
                "text": "R2 is reachable now.",
                "score": 0.67,
                "metadata": {
                    "doc_name": "fresh-live-state",
                    "doc_type": "topology_snapshot",
                    "vendor": "crawler",
                    "chunk_index": 1,
                    "captured_at": self.recent,
                    "ingested_at": self.recent,
                    "partition": PARTITION_LIVE_STATE,
                    "source_type": "live_state",
                    "freshness_weight": 0.95,
                },
            },
        ]


class _ApplicabilityStore:
    def __init__(self):
        self.available = True
        self._available = True

    def query(self, query_embedding, k=5, where=None, min_score=0.35, partitions=None):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return [
            {
                "text": "OSPF adjacency on Cisco IOS uses the EXSTART and FULL states.",
                "score": 0.74,
                "metadata": {
                    "doc_name": "cisco-ospf-guide.pdf",
                    "doc_type": "vendor_doc",
                    "vendor": "cisco",
                    "chunk_index": 0,
                    "captured_at": now,
                    "ingested_at": now,
                    "partition": PARTITION_STATIC_KNOWLEDGE,
                    "applicable_vendor": "cisco",
                    "applicable_model": "ISR",
                    "applicable_feature": "ospf,routing",
                    "source_type": "static_document",
                    "freshness_weight": 0.46,
                    "severity": "info",
                },
            },
            {
                "text": "BGP on Junos requires matching peer AS values.",
                "score": 0.83,
                "metadata": {
                    "doc_name": "junos-bgp-guide.pdf",
                    "doc_type": "vendor_doc",
                    "vendor": "juniper",
                    "chunk_index": 0,
                    "captured_at": now,
                    "ingested_at": now,
                    "partition": PARTITION_STATIC_KNOWLEDGE,
                    "applicable_vendor": "juniper",
                    "applicable_model": "MX480",
                    "applicable_feature": "bgp,routing",
                    "source_type": "static_document",
                    "freshness_weight": 0.46,
                    "severity": "info",
                },
            },
        ]


class _CapturedAtStore:
    def __init__(self):
        self.available = True
        self._available = True
        self.recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")

    def query(self, query_embedding, k=5, where=None, min_score=0.35, partitions=None):
        return [
            {
                "text": "Core link was up during the prior crawl.",
                "score": 0.78,
                "metadata": {
                    "doc_name": "older-live-topology",
                    "doc_type": "topology_snapshot",
                    "vendor": "crawler",
                    "chunk_index": 0,
                    "captured_at": self.stale,
                    "ingested_at": self.recent,
                    "partition": PARTITION_LIVE_STATE,
                    "source_type": "live_state",
                    "freshness_weight": 0.25,
                },
            },
            {
                "text": "Core link is up now.",
                "score": 0.73,
                "metadata": {
                    "doc_name": "current-live-topology",
                    "doc_type": "topology_snapshot",
                    "vendor": "crawler",
                    "chunk_index": 1,
                    "captured_at": self.recent,
                    "ingested_at": self.recent,
                    "partition": PARTITION_LIVE_STATE,
                    "source_type": "live_state",
                    "freshness_weight": 0.98,
                },
            },
        ]


def test_retrieval_policy_prefers_live_partitions_for_live_queries():
    partitions = select_retrieval_partitions("Which devices are online right now?")

    assert partitions[0] == PARTITION_LIVE_STATE
    assert PARTITION_CONFIG_HISTORY in partitions


def test_retrieval_policy_prefers_planned_partition_for_inventory_queries():
    partitions = select_retrieval_partitions("What is the management IP of SW1 in inventory?")

    assert partitions[0] == PARTITION_PLANNED_STATE


def test_retrieval_plan_excludes_static_docs_for_inventory_queries():
    plan = build_retrieval_plan("What is the management IP of SW1 in inventory?")

    assert plan.preferred_partitions == (PARTITION_PLANNED_STATE,)
    assert PARTITION_STATIC_KNOWLEDGE not in plan.preferred_partitions
    assert PARTITION_STATIC_KNOWLEDGE not in plan.fallback_partitions


def test_document_ingester_writes_static_docs_to_static_partition(tmp_path):
    store = _CaptureStore()
    ingester = DocumentIngester(store)
    doc = tmp_path / "guide.txt"
    doc.write_text("OSPF overview\n\nUse passive-interface on access links.", encoding="utf-8")

    result = ingester.ingest_file(str(doc), doc_type="vendor_doc", vendor="cisco")

    assert result["chunks_stored"] >= 1
    assert store.upserts[0]["partition"] == PARTITION_STATIC_KNOWLEDGE
    metadata = store.upserts[0]["metadatas"][0]
    assert metadata["captured_at"] == metadata["ingested_at"]
    assert metadata["source_type"] == "static_document"
    assert metadata["applicable_vendor"] == "cisco"
    assert "ospf" in metadata["applicable_feature"]
    assert metadata["freshness_weight"] > 0


def test_auto_embedder_splits_live_and_config_partitions():
    store = _CaptureStore()
    embedder = NetworkStateEmbedder(store, embedder=None, db=_FakeDb())
    topology = {
        "nodes": {
            "R1": {
                "mgmt_ip": "10.0.0.1",
                "connectedNode": ["SW1"],
                "edgeLink": {"Gi0/0": {"remote_node": "SW1", "remote_interface": "Gi0/1", "remote_ip": "10.0.0.2"}},
                "interfaceList": [{"name": "Gi0/0", "ip": "10.0.0.1", "status": "up"}],
            }
        }
    }

    result = embedder.embed_crawl_result(topology)

    assert result["status"] == "embedded"
    assert store.clears == []
    assert {item["partition"] for item in store.upserts} == {PARTITION_LIVE_STATE, PARTITION_CONFIG_HISTORY}
    assert any(item["doc_id"] == "live_device_R1" for item in store.upserts)
    assert any(item["doc_id"] == "live_interfaces_R1" for item in store.upserts)
    assert any(item["doc_id"] == "config_R1" for item in store.upserts)
    live_meta = next(item for item in store.upserts if item["doc_id"] == "live_device_R1")["metadatas"][0]
    assert live_meta["source_type"] == "live_state"
    assert live_meta["applicable_feature"] == "topology"
    assert live_meta["freshness_weight"] > 0.9


def test_retriever_queries_store_with_selected_partitions():
    store = _CaptureStore()
    retriever = KnowledgeRetriever(store, _FakeEmbedder())

    results = retriever.retrieve("Explain OSPF adjacency states")

    assert results[0]["partition"] == PARTITION_STATIC_KNOWLEDGE
    assert store.queries[0]["partitions"][0] == PARTITION_STATIC_KNOWLEDGE


def test_retriever_only_uses_fallback_when_preferred_results_are_insufficient():
    store = _PlannerStore()
    retriever = KnowledgeRetriever(store, _FakeEmbedder())

    results = retriever.retrieve("What is the management IP of SW1 in inventory?")

    assert results[0]["partition"] == PARTITION_PLANNED_STATE
    assert len(store.queries) == 1
    assert store.queries[0]["partitions"] == [PARTITION_PLANNED_STATE]


def test_retriever_ranks_live_state_ahead_of_static_docs_for_operational_queries():
    store = _PlannerStore()
    retriever = KnowledgeRetriever(store, _FakeEmbedder())

    results = retriever.retrieve("How do I troubleshoot SW1 being down right now?")

    assert len(store.queries) == 2
    assert results[0]["partition"] == PARTITION_LIVE_STATE
    assert any(result["partition"] == PARTITION_STATIC_KNOWLEDGE for result in results)


def test_retriever_prefers_fresh_live_state_over_stale_live_state():
    store = _FreshnessStore()
    retriever = KnowledgeRetriever(store, _FakeEmbedder())

    results = retriever.retrieve("Which devices are online right now?")

    assert results[0]["doc_name"] == "fresh-live-state"
    assert results[0]["source_metadata"]["freshness_class"] == "live"
    assert results[0]["source_metadata"]["captured_at"] == store.recent


def test_retriever_prefers_vendor_and_feature_applicability_over_higher_raw_score():
    retriever = KnowledgeRetriever(_ApplicabilityStore(), _FakeEmbedder())

    results = retriever.retrieve("Explain Cisco OSPF adjacency states on ISR routers")

    assert results[0]["doc_name"] == "cisco-ospf-guide.pdf"
    assert results[0]["applicable_vendor"] == "cisco"
    assert "ospf" in results[0]["applicable_feature"]


def test_retriever_uses_captured_at_and_freshness_weight_for_temporal_reranking():
    retriever = KnowledgeRetriever(_CapturedAtStore(), _FakeEmbedder())

    results = retriever.retrieve("Which core links are up right now?")

    assert results[0]["doc_name"] == "current-live-topology"
    assert results[0]["source_metadata"]["captured_at"] == retriever._store.recent


def test_auto_embedder_only_updates_changed_live_documents():
    store = _CaptureStore()
    embedder = NetworkStateEmbedder(store, embedder=None, db=_FakeDb())
    first_topology = {
        "nodes": {
            "R1": {
                "mgmt_ip": "10.0.0.1",
                "connectedNode": ["SW1"],
                "edgeLink": {},
                "interfaceList": [{"name": "Gi0/0", "ip": "10.0.0.1", "status": "up"}],
            }
        }
    }
    second_topology = {
        "nodes": {
            "R1": {
                "mgmt_ip": "10.0.0.1",
                "connectedNode": ["SW1"],
                "edgeLink": {},
                "interfaceList": [{"name": "Gi0/0", "ip": "10.0.0.1", "status": "down"}],
            }
        }
    }

    embedder.embed_crawl_result(first_topology)
    store.upserts.clear()
    store.deletes.clear()

    result = embedder.embed_crawl_result(second_topology)

    assert result["documents_updated"] == 1
    assert store.upserts[0]["doc_id"] == "live_interfaces_R1"
    assert store.upserts[0]["partition"] == PARTITION_LIVE_STATE


def test_auto_embedder_handles_refresh_events_for_config_incident_drift_and_inventory(monkeypatch):
    class _FakeIncidentStore:
        def get(self, incident_id):
            return {
                "id": incident_id,
                "anomaly_type": "cpu_spike",
                "severity": "critical",
                "status": "open",
                "affected_devices": json.dumps(["R1"]),
                "root_cause_summary": "CPU saturation",
            }

    monkeypatch.setattr("incidents.store.IncidentStore", _FakeIncidentStore)

    store = _CaptureStore()
    embedder = NetworkStateEmbedder(store, embedder=None, db=_FakeDb())

    config_targets = embedder.handle_refresh_event(
        {
            "source": "config_snapshot_writer",
            "action": "config_snapshot_saved",
            "domains": ["config_history"],
            "metadata": {"device_id": 1},
        }
    )
    incident_targets = embedder.handle_refresh_event(
        {
            "source": "incident_store",
            "action": "incident_state_changed",
            "domains": ["incidents"],
            "metadata": {"incident_id": "INC-1"},
        }
    )
    drift_targets = embedder.handle_refresh_event(
        {
            "source": "drift_detector",
            "action": "drift_event_saved",
            "domains": ["drift"],
            "metadata": {"device_id": 1, "hostname": "R1"},
        }
    )
    inventory_targets = embedder.handle_refresh_event(
        {
            "source": "netbox_api",
            "action": "inventory_mutation",
            "domains": ["planned_inventory", "planned_topology"],
            "metadata": {"hostname": "R1"},
        }
    )

    assert any(item["doc_id"] == "config_R1" and item["partition"] == PARTITION_CONFIG_HISTORY for item in store.upserts)
    assert any(item["doc_id"] == "incident_INC-1" and item["partition"] == PARTITION_INCIDENTS for item in store.upserts)
    assert any(item["doc_id"] == "drift_R1" and item["partition"] == PARTITION_CHANGE_OUTCOMES for item in store.upserts)
    assert any(item["doc_id"] == "planned_device_R1" and item["partition"] == PARTITION_PLANNED_STATE for item in store.upserts)
    assert config_targets[0]["kind"] == "embedding"
    assert incident_targets[0]["name"] == "incidents"
    assert drift_targets[0]["name"] == "change_outcomes"
    assert {item["name"] for item in inventory_targets} == {"planned_inventory", "planned_topology"}