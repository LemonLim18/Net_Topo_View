from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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

    def upsert_chunks(self, doc_id, chunks, metadatas, partition=None):
        self.upserts.append(
            {
                "doc_id": doc_id,
                "chunks": chunks,
                "metadatas": metadatas,
                "partition": partition,
            }
        )

    def delete_document(self, doc_id, partition=None):
        self.deletes.append({"doc_id": doc_id, "partition": partition})
        return 0

    def clear_partition(self, partition=None):
        self.clears.append(partition)
        return 0

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
                        return {"config_text": "hostname R1\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0"}

                return _Row()

        return _Conn()


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
                    "ingested_at": self.stale,
                    "partition": PARTITION_LIVE_STATE,
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
                    "ingested_at": self.recent,
                    "partition": PARTITION_LIVE_STATE,
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
    assert PARTITION_LIVE_STATE in store.clears
    assert PARTITION_CONFIG_HISTORY in store.clears
    assert store.upserts[0]["partition"] == PARTITION_LIVE_STATE
    assert store.upserts[1]["partition"] == PARTITION_CONFIG_HISTORY


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