"""
Routing + retrieval layer for M21 — Vector-Embedded RAG.

Responsibilities:
    1. route(query) — fast keyword-based intent detection.
       Returns (use_live: bool, use_knowledge: bool).
       • use_live is always True (live topology context is always helpful).
       • use_knowledge becomes True when the query looks like a "how to",
         "configure", "troubleshoot", or vendor-knowledge question.
       • LIVE_ONLY queries (e.g. "which devices are online right now")
         suppress knowledge retrieval (would just add noise).

    2. KnowledgeRetriever — wraps KnowledgeVectorStore to provide a
       friendlier interface: retrieve() returns a list of result dicts,
       retrieve_as_text() formats them as a markdown block suitable for
       direct injection into the system prompt.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.vector_store import KnowledgeVectorStore
    from llm.embedder import GeminiEmbedder

logger = logging.getLogger("llm.retriever")

# ---------------------------------------------------------------------------
# Intent keyword lists
# ---------------------------------------------------------------------------

# Any query containing one of these phrases → likely needs static knowledge
KNOWLEDGE_TRIGGERS: frozenset[str] = frozenset(
    [
        # Generic how-to
        "how to", "how do", "how can", "how should", "how would",
        "what is", "what are", "what does", "what happens when",
        "explain", "describe",
        # Configuration & deployment
        "configure", "configuration", "set up", "setup", "enable", "disable",
        "deploy", "installation", "install", "provision",
        # Troubleshooting
        "troubleshoot", "debug", "diagnose", "fix", "resolve", "investigation",
        "why is", "why does", "why would", "why can't",
        "not working", "fails", "failure", "error", "issue", "problem",
        # Best practices
        "best practice", "recommendation", "recommended", "guideline",
        "sop", "standard operating",
        # Protocol / feature specific
        "ospf", "bgp", "eigrp", "rip", "isis",
        "vlan", "spanning tree", "stp", "rstp", "mstp",
        "mpls", "vpn", "vrf", "gre", "ipsec", "nat",
        "acl", "access-list", "firewall", "policy",
        "qos", "traffic shaping", "policing",
        "snmp", "syslog", "netflow", "sflow",
        "hsrp", "vrrp", "glbp",
        "lldp", "cdp", "lacp",
        # Vendor CLI commands
        "command", "cli", "syntax", "show ", "debug ", "clear ",
        "interface", "routing", "switching",
    ]
)

# Queries that are purely about live state — knowledge retrieval would add noise
LIVE_ONLY_TRIGGERS: frozenset[str] = frozenset(
    [
        "right now", "currently", "at the moment",
        "which devices", "list devices", "list all",
        "how many devices", "how many nodes",
        "current status", "current topology",
        "is there", "are there",
        "what is the status", "what is the state",
        "latest crawl", "last crawl", "last seen",
        "is it online", "is it up", "is it down",
        "which links", "what links",
    ]
)


def route(query: str) -> tuple[bool, bool]:
    """
    Route a user query to determine which context sources to activate.

    Returns:
        (use_live, use_knowledge)

        use_live      — always True; live topology context is always included.
        use_knowledge — True when the query matches KNOWLEDGE_TRIGGERS and does
                        NOT exclusively match LIVE_ONLY_TRIGGERS.
    """
    q_lower = query.lower()

    # If the query is purely about live state, skip knowledge retrieval
    for trigger in LIVE_ONLY_TRIGGERS:
        if trigger in q_lower:
            return True, False

    # Check for knowledge-worthy phrases
    for trigger in KNOWLEDGE_TRIGGERS:
        if trigger in q_lower:
            return True, True

    # Fallback: short queries (< 6 words) that look like commands are kept live-only
    word_count = len(q_lower.split())
    if word_count < 6:
        return True, False

    # Default for longer conversational queries: try knowledge too
    return True, True


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

    def __init__(
        self,
        vector_store: "KnowledgeVectorStore",
        embedder: "GeminiEmbedder",
    ):
        self._store = vector_store
        self._embedder = embedder

    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        k: int = 5,
        vendor_filter: str | None = None,
        doc_type_filter: str | None = None,
        min_score: float = _RELEVANCE_THRESHOLD,
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

            # Build optional where filter for ChromaDB
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

            raw_results = self._store.query(
                query_embedding,
                k=k,
                where=where,
                min_score=min_score,
            )

            enriched = []
            for r in raw_results:
                meta = r.get("metadata") or {}
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
                    }
                )
            return enriched

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
            lines.append(
                f"**Source**: {r['doc_name']}{page_info} "
                f"[{r['doc_type']} / {r['vendor']}]  "
                f"relevance: {r['score']:.2f}"
            )
            lines.append(r["text"])
            lines.append("")

        return "\n".join(lines).rstrip()
