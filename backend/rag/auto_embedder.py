"""
Phase 9 — Automatic Network State Embedding.

After each crawl cycle (or on-demand), converts the latest topology,
device inventories, interface states, and config snapshots into text
chunks and upserts them into ChromaDB so the RAG system always has
up-to-date network state context.

Document types embedded:
    network_state/topology   — per-device topology node summary
    network_state/config     — latest config snapshot per device
    network_state/interfaces — interface inventory per device
    network_state/links      — link adjacency descriptions

All embeddings use doc_type="network_state" and vendor="auto" so they
can be filtered separately from manually-ingested knowledge docs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger("rag.auto_embedder")

# Chunk size limits
_MAX_CHUNK_CHARS = 2000
_STATE_DOC_TYPE = "network_state"
_STATE_VENDOR = "auto"


class NetworkStateEmbedder:
    """
    Converts live network state (topology + configs) into vector
    embeddings stored in ChromaDB for RAG retrieval.
    """

    def __init__(self, vector_store, embedder=None, db=None):
        """
        Parameters
        ----------
        vector_store : KnowledgeVectorStore
            The ChromaDB-backed store to upsert chunks into.
        embedder : GeminiEmbedder, optional
            If None, the vector_store's internal embedder is used.
        db : DatabaseManager, optional
            If provided, can pull configs and device data from SQL.
        """
        self._store = vector_store
        self._embedder = embedder
        self._db = db
        self._last_embed_ts: Optional[str] = None

    @property
    def last_embedded_at(self) -> Optional[str]:
        return self._last_embed_ts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_crawl_result(self, topology: dict) -> Dict[str, Any]:
        """
        Process a fresh crawl result and embed all network state.

        Parameters
        ----------
        topology : dict
            The topology dict from the BFS crawler, with structure:
            {"timestamp": "...", "seeds": [...], "nodes": {hostname: {...}}}

        Returns
        -------
        dict with summary: {devices_embedded, config_chunks, link_chunks, total_chunks}
        """
        if not self._store.available:
            logger.warning("Vector store not available — skipping auto-embedding")
            return {"status": "skipped", "reason": "vector_store_unavailable"}

        now = datetime.now(timezone.utc).isoformat()
        nodes = topology.get("nodes", {})
        if not nodes:
            return {"status": "skipped", "reason": "no_nodes"}

        all_chunks: List[str] = []
        all_metas: List[dict] = []

        # 1. Per-device topology summaries
        device_chunks, device_metas = self._build_device_chunks(nodes, now)
        all_chunks.extend(device_chunks)
        all_metas.extend(device_metas)

        # 2. Link/adjacency descriptions
        link_chunks, link_metas = self._build_link_chunks(nodes, now)
        all_chunks.extend(link_chunks)
        all_metas.extend(link_metas)

        # 3. Config snapshots from DB (if available)
        config_chunks, config_metas = self._build_config_chunks(nodes, now)
        all_chunks.extend(config_chunks)
        all_metas.extend(config_metas)

        # 4. Interface inventory
        intf_chunks, intf_metas = self._build_interface_chunks(nodes, now)
        all_chunks.extend(intf_chunks)
        all_metas.extend(intf_metas)

        if not all_chunks:
            return {"status": "skipped", "reason": "no_chunks_generated"}

        # Use a stable doc_id so re-embedding overwrites previous state
        doc_id = "network_state_latest"

        # Delete previous state chunks before upserting new ones
        try:
            self._store.delete_document(doc_id)
        except Exception:
            pass

        # Upsert all chunks
        self._store.upsert_chunks(doc_id, all_chunks, all_metas)

        self._last_embed_ts = now
        summary = {
            "status": "embedded",
            "doc_id": doc_id,
            "devices_embedded": len(device_chunks),
            "link_chunks": len(link_chunks),
            "config_chunks": len(config_chunks),
            "interface_chunks": len(intf_chunks),
            "total_chunks": len(all_chunks),
            "embedded_at": now,
        }
        logger.info(
            "Auto-embedded network state: %d total chunks "
            "(%d device, %d link, %d config, %d interface)",
            len(all_chunks), len(device_chunks), len(link_chunks),
            len(config_chunks), len(intf_chunks),
        )
        return summary

    def embed_single_config(self, hostname: str, config_text: str) -> Dict[str, Any]:
        """
        Embed or re-embed a single device's config snapshot.

        Useful when a config push changes a device — re-embed just that
        device without doing a full crawl.
        """
        if not self._store.available:
            return {"status": "skipped"}

        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"config_{hostname}"
        chunks, metas = self._chunk_config(hostname, config_text, now)

        if not chunks:
            return {"status": "skipped", "reason": "empty_config"}

        try:
            self._store.delete_document(doc_id)
        except Exception:
            pass

        self._store.upsert_chunks(doc_id, chunks, metas)
        return {"status": "embedded", "hostname": hostname, "chunks": len(chunks)}

    # ------------------------------------------------------------------
    # Chunk builders
    # ------------------------------------------------------------------

    def _build_device_chunks(
        self, nodes: dict, timestamp: str
    ) -> tuple[List[str], List[dict]]:
        """One chunk per device with its summary info."""
        chunks, metas = [], []

        for hostname, data in nodes.items():
            if not isinstance(data, dict):
                continue

            # Build a human-readable device summary
            lines = [f"Device: {hostname}"]

            if data.get("mgmt_ip"):
                lines.append(f"  Management IP: {data['mgmt_ip']}")
            if data.get("platform"):
                lines.append(f"  Platform: {data['platform']}")
            if data.get("model"):
                lines.append(f"  Model: {data['model']}")
            if data.get("os_version"):
                lines.append(f"  OS Version: {data['os_version']}")
            if data.get("serial"):
                lines.append(f"  Serial: {data['serial']}")
            if data.get("vendor"):
                lines.append(f"  Vendor: {data['vendor']}")

            # Status
            is_down = data.get("down", False)
            lines.append(f"  Status: {'DOWN' if is_down else 'UP'}")

            # Connected neighbors
            connected = data.get("connectedNode", [])
            if connected:
                lines.append(f"  Connected to: {', '.join(connected)}")

            text = "\n".join(lines)
            chunks.append(text)
            metas.append(self._base_meta(
                f"network_state_latest", "topology", hostname, timestamp, len(chunks) - 1
            ))

        return chunks, metas

    def _build_link_chunks(
        self, nodes: dict, timestamp: str
    ) -> tuple[List[str], List[dict]]:
        """Build adjacency/link descriptions."""
        chunks, metas = [], []
        seen_links = set()

        for hostname, data in nodes.items():
            if not isinstance(data, dict):
                continue
            edge_links = data.get("edgeLink", {})
            if not isinstance(edge_links, dict):
                continue

            for local_intf, link_info in edge_links.items():
                if not isinstance(link_info, dict):
                    continue
                remote = link_info.get("remote_node", "?")
                remote_intf = link_info.get("remote_interface", "?")
                remote_ip = link_info.get("remote_ip", "")

                # Dedup bidirectional links
                link_key = tuple(sorted([
                    f"{hostname}:{local_intf}",
                    f"{remote}:{remote_intf}",
                ]))
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)

                text = (
                    f"Link: {hostname} ({local_intf}) <-> {remote} ({remote_intf})"
                )
                if remote_ip:
                    text += f" [IP: {remote_ip}]"

                chunks.append(text)
                metas.append(self._base_meta(
                    "network_state_latest", "links", hostname, timestamp, len(chunks) - 1
                ))

        return chunks, metas

    def _build_config_chunks(
        self, nodes: dict, timestamp: str
    ) -> tuple[List[str], List[dict]]:
        """Pull latest config snapshots from DB and chunk them."""
        chunks, metas = [], []
        if self._db is None:
            return chunks, metas

        try:
            with self._db._connect() as conn:
                for hostname in nodes:
                    row = conn.execute(
                        """SELECT cs.config_text FROM config_snapshots cs
                           JOIN devices d ON d.id = cs.device_id
                           WHERE d.hostname = ?
                           ORDER BY cs.created_at DESC LIMIT 1""",
                        (hostname,),
                    ).fetchone()
                    if row and row["config_text"]:
                        c, m = self._chunk_config(hostname, row["config_text"], timestamp)
                        chunks.extend(c)
                        metas.extend(m)
        except Exception as exc:
            logger.debug("Config snapshot embedding failed: %s", exc)

        return chunks, metas

    def _build_interface_chunks(
        self, nodes: dict, timestamp: str
    ) -> tuple[List[str], List[dict]]:
        """Build interface inventory chunks per device."""
        chunks, metas = [], []

        for hostname, data in nodes.items():
            if not isinstance(data, dict):
                continue
            interfaces = data.get("interfaceList", [])
            if not interfaces:
                continue

            # Group interfaces into a single chunk per device
            lines = [f"Interface inventory for {hostname}:"]
            for intf in interfaces:
                if isinstance(intf, dict):
                    name = intf.get("name", intf.get("interface", "?"))
                    ip = intf.get("ip", "")
                    status = intf.get("status", "")
                    line = f"  {name}"
                    if ip:
                        line += f" — IP: {ip}"
                    if status:
                        line += f" — Status: {status}"
                    lines.append(line)
                elif isinstance(intf, str) and intf:
                    lines.append(f"  {intf}")

            text = "\n".join(lines)
            # Split if too long
            if len(text) > _MAX_CHUNK_CHARS:
                for i in range(0, len(text), _MAX_CHUNK_CHARS):
                    chunk = text[i:i + _MAX_CHUNK_CHARS]
                    chunks.append(chunk)
                    metas.append(self._base_meta(
                        "network_state_latest", "interfaces", hostname, timestamp, len(chunks) - 1
                    ))
            else:
                chunks.append(text)
                metas.append(self._base_meta(
                    "network_state_latest", "interfaces", hostname, timestamp, len(chunks) - 1
                ))

        return chunks, metas

    def _chunk_config(
        self, hostname: str, config_text: str, timestamp: str
    ) -> tuple[List[str], List[dict]]:
        """Split a config into chunks with metadata."""
        chunks, metas = [], []
        if not config_text or not config_text.strip():
            return chunks, metas

        # Prefix each chunk with the device name for context
        prefix = f"Configuration for {hostname}:\n"

        # Split on section boundaries (lines starting with !)
        sections = config_text.split("\n!")
        current_chunk = prefix

        for section in sections:
            section = section.strip()
            if not section:
                continue
            candidate = current_chunk + "!" + section + "\n"
            if len(candidate) > _MAX_CHUNK_CHARS and current_chunk != prefix:
                # Flush current chunk
                chunks.append(current_chunk)
                metas.append(self._base_meta(
                    f"config_{hostname}", "config", hostname, timestamp, len(chunks) - 1
                ))
                current_chunk = prefix + "!" + section + "\n"
            else:
                current_chunk = candidate

        # Flush remaining
        if current_chunk.strip() != prefix.strip():
            chunks.append(current_chunk)
            metas.append(self._base_meta(
                f"config_{hostname}", "config", hostname, timestamp, len(chunks) - 1
            ))

        return chunks, metas

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _base_meta(
        self, doc_id: str, sub_type: str, hostname: str,
        timestamp: str, chunk_index: int,
    ) -> dict:
        return {
            "doc_id": doc_id,
            "doc_name": f"network_state_{sub_type}_{hostname}",
            "doc_type": _STATE_DOC_TYPE,
            "sub_type": sub_type,
            "vendor": _STATE_VENDOR,
            "hostname": hostname,
            "chunk_index": chunk_index,
            "ingested_at": timestamp,
            "token_count": 0,  # approximate — not critical for state data
        }
