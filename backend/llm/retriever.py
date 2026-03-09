"""
Routing + retrieval layer for M21 — Vector-Embedded RAG.

Responsibilities:
        1. route(query) — taxonomy-aligned intent detection.
       Returns (use_live: bool, use_knowledge: bool).
             • use_live and use_knowledge come from the authoritative query taxonomy
                 in llm.context_contract.
             • static knowledge is included only when the retrieval planner says it
                 is appropriate for the query class.

        2. KnowledgeRetriever — wraps KnowledgeVectorStore with retrieval planning,
             freshness-aware reranking, and source-authority guardrails.
       friendlier interface: retrieve() returns a list of result dicts,
       retrieve_as_text() formats them as a markdown block suitable for
       direct injection into the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DEPLOYMENT,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_KNOWLEDGE,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    classify_query,
    compute_freshness,
)
from llm.vector_store import (
    PARTITION_CHANGE_OUTCOMES,
    PARTITION_CONFIG_HISTORY,
    PARTITION_INCIDENTS,
    PARTITION_LIVE_STATE,
    PARTITION_PLANNED_STATE,
    PARTITION_STATIC_KNOWLEDGE,
)

if TYPE_CHECKING:
    from llm.vector_store import KnowledgeVectorStore
    from llm.embedder import GeminiEmbedder

logger = logging.getLogger("llm.retriever")


@dataclass(frozen=True)
class RetrievalPlan:
    query_class: str
    preferred_partitions: tuple[str, ...]
    fallback_partitions: tuple[str, ...]
    min_preferred_results: int
    suppress_fallback_when_fresh: tuple[str, ...] = ()
    reasoning: str = ""


_PARTITION_FRESHNESS = {
    PARTITION_STATIC_KNOWLEDGE: (CONTEXT_DOMAIN_KNOWLEDGE, "knowledge_documents", "vector_store"),
    PARTITION_LIVE_STATE: (CONTEXT_DOMAIN_LIVE_TOPOLOGY, "live_network_state", "vector_store_live_state"),
    PARTITION_PLANNED_STATE: (CONTEXT_DOMAIN_INVENTORY, "planned_inventory_topology", "vector_store_planned_state"),
    PARTITION_INCIDENTS: (CONTEXT_DOMAIN_INCIDENTS, "incident_and_alert_state", "vector_store_incidents"),
    PARTITION_CONFIG_HISTORY: (CONTEXT_DOMAIN_CONFIG, "config_history_state", "vector_store_config_history"),
    PARTITION_CHANGE_OUTCOMES: (CONTEXT_DOMAIN_DEPLOYMENT, "change_outcomes", "vector_store_change_outcomes"),
}


def build_retrieval_plan(query: str) -> RetrievalPlan:
    decision = classify_query(query)
    query_class = decision.query_class
    if query_class == "live_only":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_LIVE_STATE, PARTITION_INCIDENTS, PARTITION_CONFIG_HISTORY),
            fallback_partitions=(),
            min_preferred_results=2,
            suppress_fallback_when_fresh=(PARTITION_STATIC_KNOWLEDGE,),
            reasoning="live queries should answer from fresh live-state and operational evidence only",
        )
    if query_class == "inventory_only":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_PLANNED_STATE,),
            fallback_partitions=(PARTITION_LIVE_STATE,),
            min_preferred_results=1,
            suppress_fallback_when_fresh=(PARTITION_STATIC_KNOWLEDGE, PARTITION_LIVE_STATE),
            reasoning="inventory queries should prefer planned inventory and topology before any live or static fallback",
        )
    if query_class == "planned_vs_live":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_PLANNED_STATE, PARTITION_LIVE_STATE, PARTITION_CONFIG_HISTORY),
            fallback_partitions=(PARTITION_CHANGE_OUTCOMES,),
            min_preferred_results=2,
            suppress_fallback_when_fresh=(PARTITION_STATIC_KNOWLEDGE, PARTITION_CHANGE_OUTCOMES),
            reasoning="planned-vs-live comparison must come from planned and live state before narrative documents",
        )
    if query_class == "troubleshooting":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_LIVE_STATE, PARTITION_INCIDENTS, PARTITION_CONFIG_HISTORY),
            fallback_partitions=(PARTITION_CHANGE_OUTCOMES, PARTITION_STATIC_KNOWLEDGE),
            min_preferred_results=2,
            reasoning="troubleshooting should start with live telemetry, incidents, and config state; docs are guidance only",
        )
    if query_class == "change_planning":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_CONFIG_HISTORY, PARTITION_CHANGE_OUTCOMES, PARTITION_PLANNED_STATE),
            fallback_partitions=(PARTITION_STATIC_KNOWLEDGE, PARTITION_LIVE_STATE),
            min_preferred_results=2,
            reasoning="change planning should prioritize prior outcomes, current config context, and intended state",
        )
    if query_class == "compliance":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_CONFIG_HISTORY, PARTITION_STATIC_KNOWLEDGE, PARTITION_CHANGE_OUTCOMES),
            fallback_partitions=(PARTITION_PLANNED_STATE,),
            min_preferred_results=2,
            reasoning="compliance needs policy documentation plus current config and prior remediation evidence",
        )
    if query_class == "knowledge_heavy":
        return RetrievalPlan(
            query_class=query_class,
            preferred_partitions=(PARTITION_STATIC_KNOWLEDGE, PARTITION_CHANGE_OUTCOMES),
            fallback_partitions=(PARTITION_CONFIG_HISTORY,),
            min_preferred_results=2,
            reasoning="knowledge-heavy questions should prefer static docs and curated outcomes",
        )
    return RetrievalPlan(
        query_class=query_class,
        preferred_partitions=(PARTITION_PLANNED_STATE, PARTITION_LIVE_STATE),
        fallback_partitions=(PARTITION_CONFIG_HISTORY, PARTITION_CHANGE_OUTCOMES),
        min_preferred_results=2,
        suppress_fallback_when_fresh=(PARTITION_STATIC_KNOWLEDGE,),
        reasoning="general questions should start with planned and live state before fallback evidence",
    )


def select_retrieval_partitions(query: str) -> list[str]:
    plan = build_retrieval_plan(query)
    return [*plan.preferred_partitions, *plan.fallback_partitions]

def route(query: str) -> tuple[bool, bool]:
    """
    Route a user query to determine which context sources to activate.

    Returns:
        (use_live, use_knowledge)

        use_live      — from the query taxonomy in context_contract.
        use_knowledge — True when the retrieval planner includes static knowledge
                        partitions for the query class.
    """
    decision = classify_query(query)
    plan = build_retrieval_plan(query)
    use_knowledge = decision.use_knowledge or (PARTITION_STATIC_KNOWLEDGE in plan.preferred_partitions)
    return decision.use_live_context, use_knowledge


# ---------------------------------------------------------------------------
# KnowledgeRetriever
# ---------------------------------------------------------------------------

class KnowledgeRetriever:
    """
    High-level retrieval interface for use inside NetworkAgent.

    Args:
        vector_store: KnowledgeVectorStore instance.
        embedder:     GeminiEmbedder instance (used to embed the query).
    """

    _RELEVANCE_THRESHOLD = 0.35  # same default as KnowledgeVectorStore
    _FRESHNESS_BONUS = {"live": 0.12, "warm": 0.05, "stale": -0.05, "missing": -0.1}

    def __init__(
        self,
        vector_store: "KnowledgeVectorStore",
        embedder: "GeminiEmbedder",
    ):
        self._store = vector_store
        self._embedder = embedder

    def _build_where_clause(
        self,
        vendor_filter: Optional[str],
        doc_type_filter: Optional[str],
    ) -> Optional[dict]:
        where: dict | None = None
        conditions = []
        if vendor_filter:
            conditions.append({"vendor": {"$eq": vendor_filter}})
        if doc_type_filter:
            conditions.append({"doc_type": {"$eq": doc_type_filter}})
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}
        return where

    def _query_store(
        self,
        query_embedding: list[float],
        partitions: tuple[str, ...],
        where: Optional[dict],
        k: int,
        min_score: float,
    ) -> list[dict]:
        if not partitions:
            return []
        try:
            return self._store.query(
                query_embedding,
                k=k,
                where=where,
                min_score=min_score,
                partitions=list(partitions),
            )
        except TypeError:
            return self._store.query(
                query_embedding,
                k=k,
                where=where,
                min_score=min_score,
            )

    def _freshness_metadata_for_partition(self, metadata: dict, score: float) -> dict:
        partition = metadata.get("partition") or PARTITION_STATIC_KNOWLEDGE
        domain, scope, source = _PARTITION_FRESHNESS.get(
            partition,
            (CONTEXT_DOMAIN_KNOWLEDGE, "knowledge_documents", "vector_store"),
        )
        return compute_freshness(
            domain=domain,
            captured_at=metadata.get("ingested_at", ""),
            ingested_at=metadata.get("ingested_at", ""),
            source=source,
            scope=scope,
            confidence=float(score),
        ).to_dict()

    def _planner_score(self, raw_score: float, partition: str, freshness_class: str, plan: RetrievalPlan) -> float:
        score = float(raw_score)
        if partition in plan.preferred_partitions:
            score += 0.28 - (0.04 * plan.preferred_partitions.index(partition))
        elif partition in plan.fallback_partitions:
            score += 0.08 - (0.02 * plan.fallback_partitions.index(partition))
        else:
            score -= 0.12

        score += self._FRESHNESS_BONUS.get(freshness_class, 0.0)

        if plan.query_class in {"live_only", "inventory_only", "planned_vs_live", "troubleshooting", "general"} and partition == PARTITION_STATIC_KNOWLEDGE:
            score -= 0.38
        if plan.query_class == "knowledge_heavy" and partition == PARTITION_STATIC_KNOWLEDGE:
            score += 0.12
        return round(score, 4)

    @staticmethod
    def _dedupe_results(results: list[dict]) -> list[dict]:
        seen = set()
        deduped = []
        for result in results:
            key = (
                result.get("doc_name", ""),
                result.get("chunk_index", 0),
                result.get("partition", ""),
                result.get("text", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    def _suppress_fallback_results(self, results: list[dict], plan: RetrievalPlan) -> list[dict]:
        if not plan.suppress_fallback_when_fresh:
            return results
        authoritative_fresh = any(
            result.get("partition") in plan.preferred_partitions
            and result.get("source_metadata", {}).get("freshness_class") in {"live", "warm"}
            for result in results
        )
        if not authoritative_fresh:
            return results
        return [
            result for result in results
            if result.get("partition") not in plan.suppress_fallback_when_fresh
            or result.get("partition") in plan.preferred_partitions
        ]

    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        k: int = 5,
        vendor_filter: str | None = None,
        doc_type_filter: str | None = None,
        min_score: float = _RELEVANCE_THRESHOLD,
        partitions: list[str] | None = None,
    ) -> list[dict]:
        """
        Embed *query* and return the top-k most relevant chunks.

        Args:
            query:            Natural language query string.
            k:                Maximum number of chunks to return.
            vendor_filter:    Restrict to a specific vendor (e.g. "cisco").
            doc_type_filter:  Restrict to a specific doc type.
            min_score:        Minimum cosine similarity score [0, 1].

        Returns:
            List of dicts, each containing::

                {
                    "text":      str,
                    "score":     float,
                    "doc_name":  str,
                    "doc_type":  str,
                    "vendor":    str,
                    "page_number": int,
                    "chunk_index": int,
                }
        """
        if not self._store._available:  # noqa: SLF001
            return []

        try:
            query_embedding = self._embedder.embed_query(query)

            where = self._build_where_clause(vendor_filter, doc_type_filter)
            plan = build_retrieval_plan(query)
            if partitions is not None:
                plan = RetrievalPlan(
                    query_class=plan.query_class,
                    preferred_partitions=tuple(partitions),
                    fallback_partitions=(),
                    min_preferred_results=plan.min_preferred_results,
                    suppress_fallback_when_fresh=plan.suppress_fallback_when_fresh,
                    reasoning=plan.reasoning,
                )

            preferred_results = self._query_store(
                query_embedding,
                plan.preferred_partitions,
                where,
                k=max(k * 2, plan.min_preferred_results, k),
                min_score=min_score,
            )
            fallback_results: list[dict] = []
            if len(preferred_results) < plan.min_preferred_results:
                fallback_results = self._query_store(
                    query_embedding,
                    plan.fallback_partitions,
                    where,
                    k=max(k, plan.min_preferred_results),
                    min_score=min_score,
                )

            enriched = []
            for r in [*preferred_results, *fallback_results]:
                meta = r.get("metadata") or {}
                partition = meta.get("partition") or PARTITION_STATIC_KNOWLEDGE
                source_metadata = self._freshness_metadata_for_partition(meta, float(r["score"]))
                enriched.append(
                    {
                        "text":         r["text"],
                        "score":        r["score"],
                        "doc_name":     meta.get("doc_name", "Unknown"),
                        "doc_type":     meta.get("doc_type", "unknown"),
                        "vendor":       meta.get("vendor", "general"),
                        "page_number":  meta.get("page_number", 0),
                        "chunk_index":  meta.get("chunk_index", 0),
                        "ingested_at":  meta.get("ingested_at", ""),
                        "partition":    partition,
                        "collection_name": meta.get("collection_name", ""),
                        "source_metadata": source_metadata,
                        "planner_score": self._planner_score(
                            float(r["score"]),
                            partition,
                            source_metadata.get("freshness_class", "missing"),
                            plan,
                        ),
                    }
                )

            deduped = self._dedupe_results(enriched)
            filtered = self._suppress_fallback_results(deduped, plan)
            return sorted(
                filtered,
                key=lambda item: (item.get("planner_score", item["score"]), item["score"]),
                reverse=True,
            )[:k]

        except Exception as exc:
            logger.warning("Knowledge retrieval failed: %s", exc)
            return []

    # ------------------------------------------------------------------

    def retrieve_as_text(self, query: str, k: int = 5) -> str:
        """
        Retrieve top-k relevant chunks and format them as a markdown block
        ready for injection into the system prompt.

        Returns an empty string when the knowledge base is empty or
        unavailable (so the caller can skip injection cleanly).
        """
        results = self.retrieve(query, k=k)
        if not results:
            return ""

        lines = [
            "## Relevant Knowledge Base",
            "",
            "The following excerpts from your network documentation may "
            "help answer the query:",
            "",
        ]
        for r in results:
            page_info = f" · page {r['page_number']}" if r["page_number"] else ""
            partition_info = f" / {r['partition']}" if r.get("partition") else ""
            lines.append(
                f"**Source**: {r['doc_name']}{page_info} "
                f"[{r['doc_type']} / {r['vendor']}{partition_info}]  "
                f"relevance: {r['score']:.2f}; planner_score: {r.get('planner_score', r['score']):.2f}"
            )
            lines.append(f"Freshness: {r['source_metadata']['freshness_class']} @ {r['source_metadata']['captured_at'] or 'unknown'}")
            lines.append(r["text"])
            lines.append("")

        return "\n".join(lines).rstrip()
