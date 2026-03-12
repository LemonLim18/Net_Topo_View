"""
Embedding wrapper for the Vector-Embedded RAG layer (M21).

Uses Google Gemini ``text-embedding-004`` (768-dim) when available.
If Gemini is unavailable or misconfigured, falls back to a deterministic local
embedding so retrieval remains functional.

Task types follow the Gemini embedding best practice:
    - RETRIEVAL_DOCUMENT during ingestion (document-side)
    - RETRIEVAL_QUERY at query time (query-side)

Batching:
    embed_documents() processes chunks in batches of BATCH_SIZE (100) to
    stay well within the free-tier rate limit of 1 500 RPM.
"""

import hashlib
import logging
import os
import re
import time
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("llm.embedder")

_EMBED_MODEL = "text-embedding-004"
_BATCH_SIZE = 100
_EMBED_DIM = 768
_TOKEN_RE = re.compile(r"[a-z0-9_./-]+")


class GeminiEmbedder:
    """
    Thin wrapper around the Gemini text-embedding-004 model.

    Usage::

        embedder = GeminiEmbedder()        # reads GEMINI_API_KEY from env
        vecs = embedder.embed_documents(["text1", "text2"])   # ingestion
        qvec = embedder.embed_query("how to configure ospf")  # query time
    """

    _QUERY_CACHE_MAXSIZE = 256

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._client = None
        self._available = False
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_lock = threading.Lock()
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
        if not texts:
            return []
        if not self._available:
            return [self._fallback_embedding(text) for text in texts]

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

        Returns a 768-dim float vector.  Results are cached in an LRU
        cache (maxsize 256) so repeated identical queries skip the API
        round-trip entirely (~300ms → <1ms on cache hit).
        """
        stripped = text.strip()
        if not stripped:
            return self._zero_vector()
        if not self._available:
            return self._fallback_embedding(stripped)

        with self._cache_lock:
            if stripped in self._query_cache:
                self._query_cache.move_to_end(stripped)
                return self._query_cache[stripped]

        results = self._embed_batch([stripped], task_type="RETRIEVAL_QUERY")
        vector = results[0] if results else self._fallback_embedding(stripped)

        with self._cache_lock:
            self._query_cache[stripped] = vector
            if len(self._query_cache) > self._QUERY_CACHE_MAXSIZE:
                self._query_cache.popitem(last=False)

        return vector

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
            return [self._fallback_embedding(text) for text in texts]

    def _fallback_embedding(self, text: str) -> list[float]:
        vector = [0.0] * _EMBED_DIM
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return self._zero_vector()

        for position, token in enumerate(tokens, start=1):
            digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
            weight = 1.0 / (position ** 0.5)
            primary_index = int.from_bytes(digest[:2], "big") % _EMBED_DIM
            secondary_index = int.from_bytes(digest[2:4], "big") % _EMBED_DIM
            primary_sign = 1.0 if digest[4] % 2 == 0 else -1.0
            secondary_sign = 1.0 if digest[5] % 2 == 0 else -1.0
            vector[primary_index] += primary_sign * weight
            vector[secondary_index] += secondary_sign * (weight * 0.5)

        magnitude = sum(component * component for component in vector) ** 0.5
        if magnitude == 0.0:
            return self._zero_vector()
        return [component / magnitude for component in vector]

    @staticmethod
    def _zero_vector() -> list[float]:
        return [0.0] * _EMBED_DIM
