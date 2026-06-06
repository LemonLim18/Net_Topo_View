"""
Document ingestion pipeline for M21 — Vector-Embedded RAG.

Takes a file on disk, parses it to plain text, splits it into overlapping
chunks, generates Gemini embeddings, and stores everything in the
KnowledgeVectorStore.

Supported formats:
    .pdf    — pdfplumber (preserves page numbers)
    .docx   — python-docx
    .html   — BeautifulSoup + lxml
    .txt    — plain text
    .md     — treated identically to .txt

Chunking strategy:
    1. Split on double-newlines (paragraph boundaries).
    2. If a paragraph exceeds CHUNK_TOKENS, split further on sentences (". ").
    3. If a sentence still exceeds CHUNK_TOKENS, split on word boundaries.
    4. Apply OVERLAP_TOKENS overlap from the tail of the previous chunk to
       maintain context across boundaries.
    5. CLI-heavy docs (detected by "# " / "!" line density) use a smaller
       CHUNK_TOKENS=256 so commands are not fragmented.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from llm.vector_store import KnowledgeVectorStore

from llm.vector_store import PARTITION_STATIC_KNOWLEDGE

logger = logging.getLogger("llm.ingestion")

_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64
_CLI_CHUNK_TOKENS = 256         # smaller chunks for CLI reference docs
_CLI_DENSITY_THRESHOLD = 0.15   # fraction of lines starting with # or !
_FEATURE_KEYWORDS = (
    "ospf",
    "bgp",
    "vlan",
    "spanning tree",
    "stp",
    "mpls",
    "vrf",
    "acl",
    "qos",
    "nat",
    "ipsec",
    "gre",
    "lldp",
    "cdp",
    "lacp",
    "snmp",
    "syslog",
    "netflow",
)


class DocumentIngester:
    """
    Offline ingestion pipeline: parse → chunk → embed → store.

    Usage::

        ingester = DocumentIngester(vector_store)
        result = ingester.ingest_file(
            file_path="docs/cisco_ospf_guide.pdf",
            doc_type="vendor_doc",
            vendor="cisco",
        )
        # {"doc_id": "...", "chunks_stored": 87}
    """

    def __init__(self, vector_store: "KnowledgeVectorStore"):
        self._store = vector_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_file(
        self,
        file_path: str,
        doc_type: str,
        vendor: str,
        doc_name: str | None = None,
        metadata_overrides: dict[str, Any] | None = None,
    ) -> dict:
        """
        Ingest a single file into the knowledge vector store.

        Args:
            file_path:  Absolute or relative path to the document.
            doc_type:   One of: "vendor_doc", "cli_reference", "sop",
                        "troubleshooting", "best_practice".
            vendor:     One of: "cisco", "huawei", "juniper", "aruba",
                        "general".  Used for metadata-filtering at query time.
            doc_name:   Human-readable name override (defaults to filename).

        Returns:
            {"doc_id": str, "chunks_stored": int, "doc_name": str}
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        name = doc_name or path.name
        logger.info("Ingesting %s (%s / %s)", name, doc_type, vendor)

        # 1. Parse to plain text + page info
        pages = self._parse(path)
        full_text = "\n\n".join(p["text"] for p in pages)

        # 2. Decide chunk size based on content type
        chunk_tokens = (
            _CLI_CHUNK_TOKENS if self._is_cli_doc(full_text) else _CHUNK_TOKENS
        )

        # 3. Chunk with paragraph / sentence / word splitting + overlap
        raw_chunks = self._chunk(full_text, chunk_tokens)

        if not raw_chunks:
            logger.warning("No chunks produced for %s — skipping", name)
            return {"doc_id": "", "chunks_stored": 0, "doc_name": name}

        # 4. Build metadata per chunk
        doc_id = str(uuid4())
        ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        applicable_features = self._infer_applicable_features(f"{name}\n{full_text}")
        applicable_model = self._infer_applicable_model(name)
        source_type = self._source_type_for_doc(doc_type)
        freshness_weight = self._freshness_weight_for_doc(doc_type)
        overrides = metadata_overrides or {}
        platform_family = str(overrides.get("platform_family") or vendor or "general")
        applicable_model = str(overrides.get("applicable_model") or applicable_model)
        applicable_features = str(overrides.get("applicable_feature") or applicable_features)
        source_type = str(overrides.get("source_type") or source_type)
        trust_level = str(overrides.get("trust_level") or "curated")
        captured_at = str(overrides.get("captured_at") or ingested_at)

        texts = [c["text"] for c in raw_chunks]
        metadatas = [
            {
                "doc_id":      doc_id,
                "doc_name":    name,
                "doc_type":    doc_type,
                "vendor":      vendor,
                "platform_family": platform_family,
                "chunk_index": i,
                "page_number": c.get("page", 0),
                "captured_at": captured_at,
                "ingested_at": ingested_at,
                "applicable_vendor": vendor,
                "applicable_model": applicable_model,
                "applicable_feature": applicable_features,
                "source_type": source_type,
                "trust_level": trust_level,
                "severity": "info",
                "freshness_weight": freshness_weight,
                "token_count": len(c["text"].split()),
            }
            for i, c in enumerate(raw_chunks)
        ]

        # 5. Upsert (embed + store)
        self._store.upsert_chunks(doc_id, texts, metadatas, partition=PARTITION_STATIC_KNOWLEDGE)

        logger.info(
            "Ingested '%s': doc_id=%s, chunks=%d",
            name, doc_id, len(texts),
        )
        return {"doc_id": doc_id, "chunks_stored": len(texts), "doc_name": name}

    def ingest_directory(
        self,
        dir_path: str,
        doc_type: str,
        vendor: str,
    ) -> list[dict]:
        """
        Ingest all supported files in a directory (non-recursive).
        Returns a list of result dicts from ingest_file().
        """
        results = []
        supported = {".pdf", ".txt", ".md", ".docx", ".html", ".htm"}
        for entry in Path(dir_path).iterdir():
            if entry.is_file() and entry.suffix.lower() in supported:
                try:
                    result = self.ingest_file(str(entry), doc_type, vendor)
                    results.append(result)
                except Exception as exc:
                    logger.error("Failed to ingest %s: %s", entry.name, exc)
        return results

    # ------------------------------------------------------------------
    # Parsing: file → list[{"text": str, "page": int}]
    # ------------------------------------------------------------------

    def _parse(self, path: Path) -> list[dict]:
        """Dispatch to format-specific parser. Returns list of page dicts."""
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                return self._parse_pdf(path)
            elif suffix == ".docx":
                return self._parse_docx(path)
            elif suffix in {".html", ".htm"}:
                return self._parse_html(path)
            else:  # .txt / .md / unknown
                return self._parse_text(path)
        except Exception as exc:
            logger.error("Parse error for %s: %s", path.name, exc)
            return [{"text": path.read_text(errors="replace"), "page": 0}]

    def _parse_pdf(self, path: Path) -> list[dict]:
        try:
            import pdfplumber  # type: ignore
            pages = []
            with pdfplumber.open(str(path)) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    raw = page.extract_text() or ""
                    if raw.strip():
                        pages.append({"text": raw, "page": i})
            return pages or [{"text": "", "page": 0}]
        except ImportError:
            logger.warning("pdfplumber not installed — reading PDF as binary text")
            return [{"text": path.read_bytes().decode("latin-1", errors="replace"), "page": 0}]

    def _parse_docx(self, path: Path) -> list[dict]:
        try:
            from docx import Document  # type: ignore
            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return [{"text": text, "page": 0}]
        except ImportError:
            logger.warning("python-docx not installed — skipping DOCX parse")
            return [{"text": "", "page": 0}]

    def _parse_html(self, path: Path) -> list[dict]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
            html = path.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(html, "lxml")
            # Remove script / style noise
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            return [{"text": text, "page": 0}]
        except ImportError:
            logger.warning("beautifulsoup4/lxml not installed — reading HTML as plain text")
            text = re.sub(r"<[^>]+>", " ", path.read_text(errors="replace"))
            return [{"text": text, "page": 0}]

    @staticmethod
    def _parse_text(path: Path) -> list[dict]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return [{"text": text, "page": 0}]

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk(self, text: str, chunk_tokens: int) -> list[dict]:
        """
        Split text into overlapping token-bounded chunks.

        Returns list of {"text": str, "page": int} dicts.
        Page number is 0 for non-PDF sources (page tracking would require
        carrying page boundaries through the text — too complex for now).
        """
        # Normalise whitespace
        text = re.sub(r"\n{3,}", "\n\n", text.strip())

        # Split on paragraph boundaries first
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0
        overlap_tail = ""

        for para in paragraphs:
            para_tokens = len(para.split())

            if para_tokens > chunk_tokens:
                # Para is too long — split on sentence boundaries
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sent in sentences:
                    sent_tokens = len(sent.split())
                    if current_tokens + sent_tokens > chunk_tokens:
                        if current:
                            chunk_text = overlap_tail + " ".join(current)
                            chunks.append(chunk_text.strip())
                            # Carry overlap from tail of this chunk
                            tail_words = " ".join(current).split()
                            overlap_tail = " ".join(tail_words[-_OVERLAP_TOKENS:]) + " " if len(tail_words) > _OVERLAP_TOKENS else ""
                            current = []
                            current_tokens = 0
                        if sent_tokens > chunk_tokens:
                            # Still too long — hard-split by word count
                            words = sent.split()
                            for i in range(0, len(words), chunk_tokens):
                                sub = " ".join(words[i: i + chunk_tokens])
                                chunks.append((overlap_tail + sub).strip())
                                overlap_tail = " ".join(words[max(0, i + chunk_tokens - _OVERLAP_TOKENS): i + chunk_tokens]) + " "
                        else:
                            current.append(sent)
                            current_tokens += sent_tokens
                    else:
                        current.append(sent)
                        current_tokens += sent_tokens
            else:
                if current_tokens + para_tokens > chunk_tokens:
                    if current:
                        chunk_text = overlap_tail + " ".join(current)
                        chunks.append(chunk_text.strip())
                        tail_words = " ".join(current).split()
                        overlap_tail = " ".join(tail_words[-_OVERLAP_TOKENS:]) + " " if len(tail_words) > _OVERLAP_TOKENS else ""
                        current = []
                        current_tokens = 0
                current.append(para)
                current_tokens += para_tokens

        # Flush remaining
        if current:
            chunks.append((overlap_tail + " ".join(current)).strip())

        return [{"text": c, "page": 0} for c in chunks if c]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_cli_doc(text: str) -> bool:
        """
        Heuristic: if more than CLI_DENSITY_THRESHOLD fraction of non-empty
        lines start with '#' or '!' (common CLI prompts/comments), treat as
        CLI reference and use the smaller chunk size.
        """
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            return False
        cli_lines = sum(1 for l in lines if l.lstrip().startswith(("#", "!")))
        return (cli_lines / len(lines)) > _CLI_DENSITY_THRESHOLD

    @staticmethod
    def _source_type_for_doc(doc_type: str) -> str:
        mapping = {
            "vendor_doc": "static_document",
            "cli_reference": "cli_reference",
            "troubleshooting": "troubleshooting_playbook",
            "best_practice": "best_practice",
            "sop": "standard_operating_procedure",
        }
        return mapping.get((doc_type or "").strip().lower(), "static_document")

    @staticmethod
    def _freshness_weight_for_doc(doc_type: str) -> float:
        mapping = {
            "troubleshooting": 0.58,
            "cli_reference": 0.52,
            "vendor_doc": 0.46,
            "best_practice": 0.4,
            "sop": 0.42,
        }
        return mapping.get((doc_type or "").strip().lower(), 0.4)

    @staticmethod
    def _infer_applicable_model(name: str) -> str:
        model_tokens = re.findall(r"\b[A-Z]{2,}[0-9][A-Z0-9-]*\b", name or "")
        return ",".join(model_tokens[:3])

    @staticmethod
    def _infer_applicable_features(text: str) -> str:
        normalized = (text or "").lower()
        matches = [feature for feature in _FEATURE_KEYWORDS if feature in normalized]
        return ",".join(matches[:5])
