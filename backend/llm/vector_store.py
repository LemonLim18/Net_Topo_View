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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.embedder import GeminiEmbedder

logger = logging.getLogger("llm.vector_store")

_COLLECTION_NAME = "network_knowledge"
_CHROMA_PATH = os.path.join(os.path.dirname(__file__), "..", "chroma_db")
_DEFAULT_K = 5
_MIN_SCORE = 0.35          # cosine similarity threshold (0–1)


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
        self._collection = None
        self._available = False
        self._init_store()

    def _init_store(self) -> None:
        """Initialise ChromaDB persistent client and collection."""
        try:
            import chromadb  # type: ignore

            os.makedirs(_CHROMA_PATH, exist_ok=True)
            client = chromadb.PersistentClient(path=_CHROMA_PATH)
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info(
                "KnowledgeVectorStore ready (path=%s, chunks=%d)",
                _CHROMA_PATH,
                self._collection.count(),
            )
        except ImportError:
            logger.warning(
                "chromadb not installed — KnowledgeVectorStore unavailable. "
                "Run: pip install chromadb"
            )
        except Exception as exc:
            logger.error("KnowledgeVectorStore init failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._available and self._collection is not None

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        doc_id: str,
        chunks: list[str],
        metadatas: list[dict],
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

        ids = [f"{doc_id}_c{i:04d}" for i in range(len(chunks))]
        embeddings = self._embedder.embed_documents(chunks)

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        logger.info("Upserted %d chunks for doc %s", len(chunks), doc_id)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def query(
        self,
        query_embedding: list[float],
        k: int = _DEFAULT_K,
        where: dict | None = None,
        min_score: float = _MIN_SCORE,
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

        n_results = min(k, self._collection.count())
        if n_results == 0:
            return []

        try:
            kwargs: dict = {
                "query_embeddings": [query_embedding],
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where

            results = self._collection.query(**kwargs)

            output = []
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0]

            for doc, meta, dist in zip(docs, metas, distances):
                # ChromaDB cosine distance: 0 = identical, 2 = opposite
                # Convert to similarity: similarity = 1 - (distance / 2)
                score = 1.0 - (dist / 2.0)
                if score >= min_score:
                    output.append({"text": doc, "metadata": meta, "score": round(score, 4)})

            return sorted(output, key=lambda x: x["score"], reverse=True)

        except Exception as exc:
            logger.error("Vector store query failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    def delete_document(self, doc_id: str) -> int:
        """
        Delete all chunks belonging to a document.
        Returns the number of chunks deleted.
        """
        if not self.available:
            return 0
        try:
            before = self._collection.count()
            self._collection.delete(where={"doc_id": doc_id})
            after = self._collection.count()
            deleted = before - after
            logger.info("Deleted %d chunks for doc %s", deleted, doc_id)
            return deleted
        except Exception as exc:
            logger.error("Delete failed for doc %s: %s", doc_id, exc)
            return 0

    def list_documents(self) -> list[dict]:
        """
        Return a deduplicated list of document-level summaries.

        Each entry: {doc_id, doc_name, doc_type, vendor, ingested_at, chunk_count}
        """
        if not self.available:
            return []
        try:
            result = self._collection.get(include=["metadatas"])
            metadatas = result.get("metadatas") or []

            seen: dict[str, dict] = {}
            for meta in metadatas:
                doc_id = meta.get("doc_id", "unknown")
                if doc_id not in seen:
                    seen[doc_id] = {
                        "doc_id": doc_id,
                        "doc_name": meta.get("doc_name", ""),
                        "doc_type": meta.get("doc_type", ""),
                        "vendor": meta.get("vendor", ""),
                        "ingested_at": meta.get("ingested_at", ""),
                        "chunk_count": 0,
                    }
                seen[doc_id]["chunk_count"] += 1

            return sorted(seen.values(), key=lambda d: d["ingested_at"], reverse=True)

        except Exception as exc:
            logger.error("list_documents failed: %s", exc)
            return []

    def count(self) -> int:
        """Total number of chunks stored."""
        if not self.available:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0
