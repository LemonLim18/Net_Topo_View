"""
Embedding wrapper for the Vector-Embedded RAG layer (M21).

Uses Google Gemini ``text-embedding-004`` (768-dim) exclusively — the
same API key already used by GeminiProvider, no new credentials required.

Task types follow the Gemini embedding best practice:
    - RETRIEVAL_DOCUMENT during ingestion (document-side)
    - RETRIEVAL_QUERY at query time (query-side)

Batching:
    embed_documents() processes chunks in batches of BATCH_SIZE (100) to
    stay well within the free-tier rate limit of 1 500 RPM.
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("llm.embedder")

_EMBED_MODEL = "text-embedding-004"
_BATCH_SIZE = 100
_EMBED_DIM = 768


class GeminiEmbedder:
    """
    Thin wrapper around the Gemini text-embedding-004 model.

    Usage::

        embedder = GeminiEmbedder()        # reads GEMINI_API_KEY from env
        vecs = embedder.embed_documents(["text1", "text2"])   # ingestion
        qvec = embedder.embed_query("how to configure ospf")  # query time
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._client = None
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        """Lazy-initialise the google-genai client."""
        try:
            from google import genai  # type: ignore
            self._client = genai.Client(api_key=self._api_key)
            self._available = True
            logger.info("GeminiEmbedder initialised (model=%s, dim=%d)", _EMBED_MODEL, _EMBED_DIM)
        except ImportError:
            logger.warning(
                "google-genai not installed — GeminiEmbedder unavailable. "
                "Run: pip install google-genai"
            )
        except Exception as exc:
            logger.warning("GeminiEmbedder init failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dimension(self) -> int:
        return _EMBED_DIM

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        Batch-embed a list of document chunks (RETRIEVAL_DOCUMENT task).

        Processes in batches of BATCH_SIZE to respect API rate limits.
        Returns a list of 768-dim float vectors in the same order as input.
        """
        if not self._available:
            return [self._zero_vector() for _ in texts]
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for batch_start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[batch_start: batch_start + _BATCH_SIZE]
            embeddings = self._embed_batch(batch, task_type="RETRIEVAL_DOCUMENT")
            all_embeddings.extend(embeddings)
            # Throttle between batches to avoid rate-limit errors
            if batch_start + _BATCH_SIZE < len(texts):
                time.sleep(0.1)

        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        """
        Embed a single query string (RETRIEVAL_QUERY task).

        Returns a 768-dim float vector, or a zero vector if unavailable.
        """
        if not self._available or not text.strip():
            return self._zero_vector()
        results = self._embed_batch([text], task_type="RETRIEVAL_QUERY")
        return results[0] if results else self._zero_vector()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        """Call the Gemini embed API for a single batch."""
        try:
            from google.genai import types as genai_types  # type: ignore

            response = self._client.models.embed_content(
                model=_EMBED_MODEL,
                contents=texts,
                config=genai_types.EmbedContentConfig(task_type=task_type),
            )
            # response.embeddings is a list of ContentEmbedding objects
            return [list(e.values) for e in response.embeddings]
        except Exception as exc:
            logger.error("Embedding batch failed (task=%s, n=%d): %s", task_type, len(texts), exc)
            return [self._zero_vector() for _ in texts]

    @staticmethod
    def _zero_vector() -> list[float]:
        return [0.0] * _EMBED_DIM
