from __future__ import annotations

import json

from llm.ingestion import DocumentIngester
from llm.retriever import KnowledgeRetriever, select_retrieval_partitions
from llm.vector_store import (
    PARTITION_CONFIG_HISTORY,
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


def test_retrieval_policy_prefers_live_partitions_for_live_queries():
    partitions = select_retrieval_partitions("Which devices are online right now?")

    assert partitions[0] == PARTITION_LIVE_STATE
    assert PARTITION_CONFIG_HISTORY in partitions


def test_retrieval_policy_prefers_planned_partition_for_inventory_queries():
    partitions = select_retrieval_partitions("What is the management IP of SW1 in inventory?")

    assert partitions[0] == PARTITION_PLANNED_STATE


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