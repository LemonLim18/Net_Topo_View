"""
ChromaDB vector store wrapper for M21 — Vector-Embedded RAG.

Manages one ChromaDB collection (``network_knowledge``) backed by a
persistent SQLite store at ``backend/chroma_db/``.

The collection stores text chunks alongside metadata for source attribution
and optional vendor/doc-type filtering at query time.

Metadata schema per chunk:
    doc_id       str  — UUID for the parent document
    doc_name     str  — human-readable filename
    doc_type     str  — "vendor_doc" | "cli_reference" | "sop"
                        "troubleshooting" | "best_practice"
    vendor       str  — "cisco" | "huawei" | "juniper" | "aruba" | "general"
    chunk_index  int  — 0-based position within the document
    page_number  int  — PDF page number (0 if not applicable)
    ingested_at  str  — ISO-8601 UTC datetime
    token_count  int  — approximate word count of the chunk
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any, Iterable, Optional
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.embedder import GeminiEmbedder

logger = logging.getLogger("llm.vector_store")

PARTITION_STATIC_KNOWLEDGE = "static_knowledge"
PARTITION_LIVE_STATE = "live_network_state"
PARTITION_PLANNED_STATE = "planned_inventory_topology"
PARTITION_INCIDENTS = "incidents_and_alerts"
PARTITION_CONFIG_HISTORY = "config_history"
PARTITION_CHANGE_OUTCOMES = "change_outcomes"

ALL_COLLECTION_PARTITIONS = (
    PARTITION_STATIC_KNOWLEDGE,
    PARTITION_LIVE_STATE,
    PARTITION_PLANNED_STATE,
    PARTITION_INCIDENTS,
    PARTITION_CONFIG_HISTORY,
    PARTITION_CHANGE_OUTCOMES,
)

_COLLECTION_NAMES = {
    PARTITION_STATIC_KNOWLEDGE: "network_knowledge_static",
    PARTITION_LIVE_STATE: "network_knowledge_live_state",
    PARTITION_PLANNED_STATE: "network_knowledge_planned_state",
    PARTITION_INCIDENTS: "network_knowledge_incidents",
    PARTITION_CONFIG_HISTORY: "network_knowledge_config_history",
    PARTITION_CHANGE_OUTCOMES: "network_knowledge_change_outcomes",
}

_CHROMA_PATH = os.path.join(os.path.dirname(__file__), "..", "chroma_db")
_DEFAULT_K = 5
_MIN_SCORE = 0.35          # cosine similarity threshold (0–1)


def _normalize_partition(partition: Optional[str]) -> str:
    candidate = (partition or PARTITION_STATIC_KNOWLEDGE).strip().lower()
    return candidate if candidate in _COLLECTION_NAMES else PARTITION_STATIC_KNOWLEDGE


def _unique_partitions(partitions: Optional[Iterable[str]]) -> list[str]:
    if not partitions:
        return list(ALL_COLLECTION_PARTITIONS)
    seen = []
    for partition in partitions:
        normalized = _normalize_partition(partition)
        if normalized not in seen:
            seen.append(normalized)
    return seen


def infer_partition_from_metadata(metadata: Optional[dict]) -> str:
    meta = metadata or {}
    explicit = meta.get("partition") or meta.get("collection_partition")
    if explicit:
        return _normalize_partition(str(explicit))

    doc_type = str(meta.get("doc_type") or "").strip().lower()
    sub_type = str(meta.get("sub_type") or "").strip().lower()

    if doc_type == "network_state":
        if sub_type == "config":
            return PARTITION_CONFIG_HISTORY
        return PARTITION_LIVE_STATE
    if doc_type in {"incident", "alert", "incident_report"}:
        return PARTITION_INCIDENTS
    if doc_type in {"change_outcome", "deployment_result", "postmortem"}:
        return PARTITION_CHANGE_OUTCOMES
    if doc_type in {"planned_inventory", "planned_topology", "inventory_state"}:
        return PARTITION_PLANNED_STATE
    return PARTITION_STATIC_KNOWLEDGE


class KnowledgeVectorStore:
    """
    Persistent ChromaDB-backed knowledge store.

    Usage::

        store = KnowledgeVectorStore(embedder)

        # Ingestion
        store.upsert_chunks(doc_id, ["chunk1", "chunk2"], [meta1, meta2])

        # Query
        results = store.query(query_embedding, k=5)
        # → [{"text": str, "metadata": dict, "score": float}, ...]

        # Management
        store.delete_document(doc_id)
        store.list_documents()
        store.count()
    """

    def __init__(self, embedder: "GeminiEmbedder"):
        self._embedder = embedder
        self._client = None
        self._collections: dict[str, Any] = {}
        self._available = False
        self._init_store()

    def _init_store(self) -> None:
        """Initialise ChromaDB persistent client and collection."""
        try:
            import chromadb  # type: ignore

            os.makedirs(_CHROMA_PATH, exist_ok=True)
            client = chromadb.PersistentClient(path=_CHROMA_PATH)
            self._client = client
            self._collections = {
                partition: client.get_or_create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine", "partition": partition},
                )
                for partition, collection_name in _COLLECTION_NAMES.items()
            }
            self._available = True
            logger.info(
                "KnowledgeVectorStore ready (path=%s, chunks=%d)",
                _CHROMA_PATH,
                self.count(),
            )
        except ImportError:
            logger.warning(
                "chromadb not installed — KnowledgeVectorStore unavailable. "
                "Run: pip install chromadb"
            )
        except Exception as exc:
            logger.error("KnowledgeVectorStore init failed: %s", exc)

    def clear_all(self) -> int:
        """Delete every chunk in the collection by dropping and re-creating it.

        Returns the number of chunks that were removed.
        """
        return self.clear_partition()

    def clear_partition(self, partition: Optional[str] = None) -> int:
        """Delete every chunk in one partition, or across all partitions when omitted."""
        if not self.available:
            return 0
        try:
            cleared = 0
            for selected in _unique_partitions([partition] if partition else None):
                collection_name = _COLLECTION_NAMES[selected]
                current = self._collections[selected]
                cleared += current.count()
                self._client.delete_collection(collection_name)
                self._collections[selected] = self._client.get_or_create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine", "partition": selected},
                )
            logger.info("Cleared %d chunks from vector store partition=%s", cleared, partition or "all")
            return cleared
        except Exception as exc:
            logger.warning("clear_all failed: %s", exc)
            return 0

    @property
    def available(self) -> bool:
        return self._available and bool(self._collections)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        doc_id: str,
        chunks: list[str],
        metadatas: list[dict],
        partition: Optional[str] = None,
    ) -> None:
        """
        Embed and upsert a list of text chunks for one document.

        ``chunks`` and ``metadatas`` must have equal length.
        Uses upsert so re-ingesting the same doc_id is idempotent.
        """
        if not self.available:
            logger.warning("Vector store not available — upsert skipped")
            return
        if not chunks:
            return

        grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"chunks": [], "metadatas": [], "ids": []})
        for idx, (chunk, metadata) in enumerate(zip(chunks, metadatas)):
            meta = dict(metadata)
            selected_partition = _normalize_partition(partition or infer_partition_from_metadata(meta))
            meta["partition"] = selected_partition
            meta["collection_name"] = _COLLECTION_NAMES[selected_partition]
            meta_doc_id = str(meta.get("doc_id") or doc_id)
            chunk_index = int(meta.get("chunk_index", idx))
            grouped[selected_partition]["chunks"].append(chunk)
            grouped[selected_partition]["metadatas"].append(meta)
            grouped[selected_partition]["ids"].append(f"{meta_doc_id}_c{chunk_index:04d}")

        for selected_partition, payload in grouped.items():
            if self._embedder is None:
                logger.warning("Embedder unavailable — upsert skipped for partition %s", selected_partition)
                continue
            embeddings = self._embedder.embed_documents(payload["chunks"])
            self._collections[selected_partition].upsert(
                ids=payload["ids"],
                embeddings=embeddings,
                documents=payload["chunks"],
                metadatas=payload["metadatas"],
            )
            logger.info(
                "Upserted %d chunks for doc %s into partition %s",
                len(payload["chunks"]),
                doc_id,
                selected_partition,
            )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def query(
        self,
        query_embedding: list[float],
        k: int = _DEFAULT_K,
        where: dict | None = None,
        min_score: float = _MIN_SCORE,
        partitions: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Find the top-k most similar chunks to a query embedding.

        Args:
            query_embedding: 768-dim float vector from GeminiEmbedder.embed_query()
            k:               Number of results (before score filtering).
            where:           Optional ChromaDB metadata filter,
                             e.g. ``{"vendor": "cisco"}``
            min_score:       Minimum cosine similarity (0–1). Chunks below
                             this threshold are discarded.

        Returns:
            List of dicts, each: {"text": str, "metadata": dict, "score": float}
            Sorted by score descending.
        """
        if not self.available:
            return []

        try:
            output = []
            for partition_name in _unique_partitions(partitions):
                collection = self._collections.get(partition_name)
                if collection is None:
                    continue
                n_results = min(k, collection.count())
                if n_results == 0:
                    continue

                kwargs: dict = {
                    "query_embeddings": [query_embedding],
                    "n_results": n_results,
                    "include": ["documents", "metadatas", "distances"],
                }
                if where:
                    kwargs["where"] = where

                results = collection.query(**kwargs)
                docs = results["documents"][0]
                metas = results["metadatas"][0]
                distances = results["distances"][0]

                for doc, meta, dist in zip(docs, metas, distances):
                    score = 1.0 - (dist / 2.0)
                    if score >= min_score:
                        metadata = dict(meta or {})
                        metadata.setdefault("partition", partition_name)
                        metadata.setdefault("collection_name", _COLLECTION_NAMES[partition_name])
                        output.append({"text": doc, "metadata": metadata, "score": round(score, 4)})

            return sorted(output, key=lambda x: x["score"], reverse=True)[:k]

        except Exception as exc:
            logger.error("Vector store query failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    def delete_document(self, doc_id: str, partition: Optional[str] = None) -> int:
        """
        Delete all chunks belonging to a document.
        Returns the number of chunks deleted.
        """
        if not self.available:
            return 0
        try:
            deleted = 0
            for selected in _unique_partitions([partition] if partition else None):
                collection = self._collections[selected]
                before = collection.count()
                collection.delete(where={"doc_id": doc_id})
                deleted += before - collection.count()
            logger.info("Deleted %d chunks for doc %s", deleted, doc_id)
            return deleted
        except Exception as exc:
            logger.error("Delete failed for doc %s: %s", doc_id, exc)
            return 0

    def list_documents(self, partition: Optional[str] = None) -> list[dict]:
        """
        Return a deduplicated list of document-level summaries.

        Each entry: {doc_id, doc_name, doc_type, vendor, ingested_at, chunk_count}
        """
        if not self.available:
            return []
        try:
            seen: dict[tuple[str, str], dict] = {}
            for selected in _unique_partitions([partition] if partition else None):
                result = self._collections[selected].get(include=["metadatas"])
                metadatas = result.get("metadatas") or []
                for meta in metadatas:
                    doc_id = meta.get("doc_id", "unknown")
                    key = (selected, doc_id)
                    if key not in seen:
                        seen[key] = {
                            "doc_id": doc_id,
                            "doc_name": meta.get("doc_name", ""),
                            "doc_type": meta.get("doc_type", ""),
                            "vendor": meta.get("vendor", ""),
                            "ingested_at": meta.get("ingested_at", ""),
                            "chunk_count": 0,
                            "partition": selected,
                            "collection_name": _COLLECTION_NAMES[selected],
                        }
                    seen[key]["chunk_count"] += 1

            return sorted(seen.values(), key=lambda d: (d["ingested_at"], d["partition"]), reverse=True)

        except Exception as exc:
            logger.error("list_documents failed: %s", exc)
            return []

    def count(self, partition: Optional[str] = None) -> int:
        """Total number of chunks stored."""
        if not self.available:
            return 0
        try:
            return sum(self._collections[selected].count() for selected in _unique_partitions([partition] if partition else None))
        except Exception:
            return 0

    def count_by_partition(self) -> dict[str, int]:
        if not self.available:
            return {partition: 0 for partition in ALL_COLLECTION_PARTITIONS}
        try:
            return {partition: self._collections[partition].count() for partition in ALL_COLLECTION_PARTITIONS}
        except Exception:
            return {partition: 0 for partition in ALL_COLLECTION_PARTITIONS}
