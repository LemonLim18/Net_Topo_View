"""
Phase 9 — Automatic Network State Embedding.

Builds and maintains RAG partitions for live topology, planned inventory,
config snapshots, incidents, and drift findings. The embedder supports both
full refreshes and event-driven delta updates so mutation paths can refresh
only the affected vector documents instead of clearing whole partitions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
)
from llm.vector_store import (
    PARTITION_CHANGE_OUTCOMES,
    PARTITION_CONFIG_HISTORY,
    PARTITION_INCIDENTS,
    PARTITION_LIVE_STATE,
    PARTITION_PLANNED_STATE,
)

logger = logging.getLogger("rag.auto_embedder")

# Chunk size limits
_MAX_CHUNK_CHARS = 2000
_STATE_DOC_TYPE = "network_state"
_STATE_VENDOR = "auto"
_INCIDENT_DOC_TYPE = "incident_report"
_DRIFT_DOC_TYPE = "change_outcome"
_PLANNED_DOC_VENDOR = "netauto"


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
        self._doc_signatures: Dict[tuple[str, str], str] = {}

    @property
    def last_embedded_at(self) -> Optional[str]:
        return self._last_embed_ts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_crawl_result(self, topology: dict, previous_topology: Optional[dict] = None) -> Dict[str, Any]:
        """
        Process a fresh crawl result and embed config snapshots plus live state documents.

        Parameters
        ----------
        topology : dict
            The topology dict from the BFS crawler, with structure:
            {"timestamp": "...", "seeds": [...], "nodes": {hostname: {...}}}

        Returns
        -------
        dict with summary: {config_chunks, total_chunks, ...}
        """
        if not self._store.available:
            logger.warning("Vector store not available — skipping auto-embedding")
            return {"status": "skipped", "reason": "vector_store_unavailable"}

        now = datetime.now(timezone.utc).isoformat()
        nodes = topology.get("nodes", {})
        if not nodes:
            return {"status": "skipped", "reason": "no_nodes"}

        config_documents = self._build_config_documents(nodes, now)
        live_documents = self._build_live_documents(nodes, now)

        if not config_documents and not live_documents:
            return {"status": "skipped", "reason": "no_chunks_generated"}

        summary = {
            "status": "embedded",
            "mode": "delta" if previous_topology is not None else "full",
            "devices_embedded": len(nodes),
            "link_chunks": 0,
            "config_chunks": 0,
            "interface_chunks": 0,
            "total_chunks": 0,
            "documents_updated": 0,
            "documents_removed": 0,
            "embedded_at": now,
        }

        # Embed config snapshots
        for doc_id, payload in config_documents.items():
            result = self._upsert_partitioned_document(
                doc_id=doc_id,
                chunks=payload["chunks"],
                metas=payload["metas"],
                partition=PARTITION_CONFIG_HISTORY,
            )
            if result["status"] == "updated":
                summary["documents_updated"] += 1
                summary["config_chunks"] += result["chunk_count"]
                summary["total_chunks"] += result["chunk_count"]

        # Embed live state (devices, links, interfaces)
        for doc_id, payload in live_documents.items():
            result = self._upsert_partitioned_document(
                doc_id=doc_id,
                chunks=payload["chunks"],
                metas=payload["metas"],
                partition=PARTITION_LIVE_STATE,
            )
            if result["status"] == "updated":
                summary["documents_updated"] += 1
                if "link" in doc_id:
                    summary["link_chunks"] += result["chunk_count"]
                elif "interface" in doc_id:
                    summary["interface_chunks"] += result["chunk_count"]
                summary["total_chunks"] += result["chunk_count"]

        # Stale live state cleanup
        if previous_topology is None:
            removed = self._delete_missing_documents(
                partition=PARTITION_LIVE_STATE,
                current_doc_ids=set(live_documents.keys()),
                prefix_filters=("live_device_", "live_link_", "live_interfaces_"),
            )
            summary["documents_removed"] += removed

        self._last_embed_ts = now
        logger.info(
            "Auto-embedded crawl results: %d docs updated, %d docs removed, %d total chunks",
            summary["documents_updated"],
            summary["documents_removed"],
            summary["total_chunks"],
        )
        return summary

    def embed_single_config(self, hostname: str, config_text: str, captured_at: Optional[str] = None) -> Dict[str, Any]:
        """
        Embed or re-embed a single device's config snapshot.

        Useful when a config push changes a device — re-embed just that
        device without doing a full crawl.
        """
        if not self._store.available:
            return {"status": "skipped"}

        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"config_{hostname}"
        chunks, metas = self._chunk_config(hostname, config_text, now, captured_at=captured_at)

        if not chunks:
            return {"status": "skipped", "reason": "empty_config"}

        result = self._upsert_partitioned_document(
            doc_id=doc_id,
            chunks=chunks,
            metas=metas,
            partition=PARTITION_CONFIG_HISTORY,
        )
        self._last_embed_ts = now
        return {"status": "embedded", "hostname": hostname, "chunks": result["chunk_count"]}

    def embed_planned_inventory(self, hostnames: Optional[List[str]] = None) -> Dict[str, Any]:
        # DEPRECATED: real-time snapshot injection is used instead of vector RAG for inventory
        return {"status": "skipped", "reason": "disabled"}

        now = datetime.now(timezone.utc).isoformat()
        all_devices = self._db.get_all_devices()
        selected_hostnames = {hostname.strip() for hostname in (hostnames or []) if hostname and hostname.strip()}
        devices = [device for device in all_devices if not selected_hostnames or device.hostname in selected_hostnames]
        documents = self._build_planned_inventory_documents(devices, now)
        if not selected_hostnames:
            self._delete_missing_documents(
                partition=PARTITION_PLANNED_STATE,
                current_doc_ids={*documents.keys(), *self._planned_link_doc_ids()},
                prefix_filters=("planned_device_", "planned_interfaces_"),
            )

        updated = 0
        total_chunks = 0
        for doc_id, payload in documents.items():
            result = self._upsert_partitioned_document(
                doc_id=doc_id,
                chunks=payload["chunks"],
                metas=payload["metas"],
                partition=PARTITION_PLANNED_STATE,
            )
            if result["status"] == "updated":
                updated += 1
                total_chunks += result["chunk_count"]

        self._last_embed_ts = now
        return {
            "status": "embedded",
            "partition": PARTITION_PLANNED_STATE,
            "documents_updated": updated,
            "hostnames": [device.hostname for device in devices],
            "total_chunks": total_chunks,
        }

    def embed_planned_links(self, hostnames: Optional[List[str]] = None) -> Dict[str, Any]:
        # DEPRECATED: real-time snapshot injection is used instead of vector RAG for topology
        return {"status": "skipped", "reason": "disabled"}

        now = datetime.now(timezone.utc).isoformat()
        documents = self._build_planned_link_documents(now, hostnames=hostnames)
        if not hostnames:
            self._delete_missing_documents(
                partition=PARTITION_PLANNED_STATE,
                current_doc_ids=set(documents),
                prefix_filters=("planned_link_",),
            )

        updated = 0
        total_chunks = 0
        for doc_id, payload in documents.items():
            result = self._upsert_partitioned_document(
                doc_id=doc_id,
                chunks=payload["chunks"],
                metas=payload["metas"],
                partition=PARTITION_PLANNED_STATE,
            )
            if result["status"] == "updated":
                updated += 1
                total_chunks += result["chunk_count"]

        self._last_embed_ts = now
        return {
            "status": "embedded",
            "partition": PARTITION_PLANNED_STATE,
            "documents_updated": updated,
            "total_chunks": total_chunks,
        }

    def embed_single_incident(self, incident_id: str) -> Dict[str, Any]:
        if not self._store.available:
            return {"status": "skipped", "reason": "vector_store_unavailable"}

        from incidents.store import IncidentStore

        incident = IncidentStore().get(incident_id)
        if not incident:
            return {"status": "skipped", "reason": "incident_not_found", "incident_id": incident_id}

        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"incident_{incident_id}"
        chunks, metas = self._build_incident_chunks(incident, now)
        result = self._upsert_partitioned_document(
            doc_id=doc_id,
            chunks=chunks,
            metas=metas,
            partition=PARTITION_INCIDENTS,
        )
        self._last_embed_ts = now
        return {"status": "embedded", "incident_id": incident_id, "chunks": result["chunk_count"]}

    def embed_latest_drift_for_device(
        self,
        *,
        device_id: Optional[int] = None,
        hostname: str = "",
    ) -> Dict[str, Any]:
        if not self._store.available:
            return {"status": "skipped", "reason": "vector_store_unavailable"}
        if self._db is None:
            return {"status": "skipped", "reason": "db_unavailable"}

        device = None
        if device_id is not None:
            device = self._db.get_device_by_id(device_id)
        elif hostname:
            device = self._db.get_device_by_hostname(hostname)

        if device is None:
            return {"status": "skipped", "reason": "device_not_found"}

        events = self._db.get_drift_events(device_id=device.id, limit=1)
        if not events:
            return {"status": "skipped", "reason": "drift_not_found", "hostname": device.hostname}

        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"drift_{device.hostname}"
        chunks, metas = self._build_drift_chunks(events[0], now)
        result = self._upsert_partitioned_document(
            doc_id=doc_id,
            chunks=chunks,
            metas=metas,
            partition=PARTITION_CHANGE_OUTCOMES,
        )
        self._last_embed_ts = now
        return {"status": "embedded", "hostname": device.hostname, "chunks": result["chunk_count"]}

    def handle_refresh_event(self, publication: Dict[str, Any]) -> List[Dict[str, object]]:
        metadata = publication.get("metadata") or {}
        domains = set(publication.get("domains") or [])
        source = str(publication.get("source") or "")
        action = str(publication.get("action") or "")
        downstream: List[Dict[str, object]] = []

        if metadata.get("skip_vector_refresh"):
            logger.debug(
                "Skipping vector refresh for source=%s action=%s due to skip_vector_refresh metadata",
                source,
                action,
            )
            return []

        if CONTEXT_DOMAIN_CONFIG in domains or source == "config_snapshot_writer":
            hostname = self._resolve_hostname(metadata)
            if hostname:
                result = self._embed_config_from_db(hostname)
                downstream.append(self._downstream_target("config_history", result, hostname=hostname))

        if CONTEXT_DOMAIN_INCIDENTS in domains or source == "incident_store":
            incident_id = str(metadata.get("incident_id") or "")
            if incident_id:
                result = self.embed_single_incident(incident_id)
                downstream.append(self._downstream_target("incidents", result, incident_id=incident_id))

        if CONTEXT_DOMAIN_DRIFT in domains or source == "drift_detector":
            result = self.embed_latest_drift_for_device(
                device_id=metadata.get("device_id") if isinstance(metadata.get("device_id"), int) else None,
                hostname=str(metadata.get("hostname") or ""),
            )
            downstream.append(self._downstream_target("change_outcomes", result, hostname=result.get("hostname")))

        if CONTEXT_DOMAIN_INVENTORY in domains or CONTEXT_DOMAIN_PLANNED_TOPOLOGY in domains:
            # Structued inventory RAG is disabled in favor of real-time snapshot injection
            # to prevent high latency during inventory mutations (e.g. deletions).
            pass
            # related_hostnames = self._extract_related_hostnames(metadata)
            # full_inventory_refresh = source in {"netbox_sync", "netbox_webhook"} or action == "inventory_sync_completed"
            # full_topology_refresh = source in {"netbox_topology_sync", "netbox_webhook"} or action == "planned_topology_sync_completed"
            #
            # if CONTEXT_DOMAIN_INVENTORY in domains:
            #     result = self.embed_planned_inventory(None if full_inventory_refresh else related_hostnames)
            #     downstream.append(self._downstream_target("planned_inventory", result))
            # if CONTEXT_DOMAIN_PLANNED_TOPOLOGY in domains:
            #     result = self.embed_planned_links(None if full_topology_refresh or not related_hostnames else related_hostnames)
            #     downstream.append(self._downstream_target("planned_topology", result))

        return [item for item in downstream if item.get("status") != "skipped"]

    # ------------------------------------------------------------------
    # Chunk builders
    # ------------------------------------------------------------------

    def _build_live_documents(self, nodes: dict, timestamp: str) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}
        documents.update(self._build_live_device_documents(nodes, timestamp))
        documents.update(self._build_live_link_documents(nodes, timestamp))
        documents.update(self._build_live_interface_documents(nodes, timestamp))
        return documents

    def _build_live_device_documents(self, nodes: dict, timestamp: str) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}

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
            doc_id = self._live_device_doc_id(hostname)
            documents[doc_id] = {
                "chunks": [text],
                "metas": [self._base_meta(doc_id, "topology", hostname, timestamp, 0, node_data=data)],
            }

        return documents

    def _build_live_link_documents(self, nodes: dict, timestamp: str) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}
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

                doc_id = self._live_link_doc_id(hostname, local_intf, remote, remote_intf)
                documents[doc_id] = {
                    "chunks": [text],
                    "metas": [self._base_meta(doc_id, "links", hostname, timestamp, 0, node_data=data)],
                }

        return documents

    def _build_config_documents(self, nodes: dict, timestamp: str) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        """Pull latest config snapshots from DB and chunk them."""
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}
        if self._db is None:
            return documents

        try:
            with self._db._connect() as conn:
                for hostname in nodes:
                    row = conn.execute(
                        """SELECT cs.config_text, cs.captured_at FROM config_snapshots cs
                           JOIN devices d ON d.id = cs.device_id
                           WHERE d.hostname = ?
                           ORDER BY cs.created_at DESC LIMIT 1""",
                        (hostname,),
                    ).fetchone()
                    if row and row["config_text"]:
                        doc_id = f"config_{hostname}"
                        c, m = self._chunk_config(
                            hostname,
                            row["config_text"],
                            timestamp,
                            captured_at=row["captured_at"],
                        )
                        documents[doc_id] = {"chunks": c, "metas": m}
        except Exception as exc:
            logger.debug("Config snapshot embedding failed: %s", exc)

        return documents

    def _build_live_interface_documents(self, nodes: dict, timestamp: str) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        """Build interface inventory chunks per device."""
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}

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
                        line += f" - IP: {ip}"
                    if status:
                        line += f" - Status: {status}"
                    lines.append(line)
                elif isinstance(intf, str) and intf:
                    lines.append(f"  {intf}")

            text = "\n".join(lines)
            # Split if too long
            doc_id = self._live_interface_doc_id(hostname)
            if len(text) > _MAX_CHUNK_CHARS:
                doc_chunks = []
                doc_metas = []
                for i in range(0, len(text), _MAX_CHUNK_CHARS):
                    chunk = text[i:i + _MAX_CHUNK_CHARS]
                    doc_chunks.append(chunk)
                    doc_metas.append(self._base_meta(doc_id, "interfaces", hostname, timestamp, len(doc_chunks) - 1, node_data=data))
            else:
                doc_chunks = [text]
                doc_metas = [self._base_meta(doc_id, "interfaces", hostname, timestamp, 0, node_data=data)]

            documents[doc_id] = {"chunks": doc_chunks, "metas": doc_metas}

        return documents

    def _build_planned_inventory_documents(self, devices: List[Any], timestamp: str) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}
        for device in devices:
            lines = [f"Planned device: {device.hostname}"]
            if device.management_ip:
                lines.append(f"  Management IP: {device.management_ip}")
            if device.vendor:
                lines.append(f"  Vendor: {device.vendor}")
            if device.model:
                lines.append(f"  Model: {device.model}")
            if device.os_version:
                lines.append(f"  OS Version: {device.os_version}")
            if device.serial_number:
                lines.append(f"  Serial: {device.serial_number}")
            lines.append(f"  Status: {device.status}")
            lines.append(f"  Role: {device.role}")
            lines.append(f"  Source: {device.source}")

            device_doc_id = self._planned_device_doc_id(device.hostname)
            documents[device_doc_id] = {
                "chunks": ["\n".join(lines)],
                "metas": [
                    self._planned_meta(
                        device_doc_id,
                        "planned_inventory",
                        device.hostname,
                        timestamp,
                        0,
                        applicable_vendor=device.vendor,
                        applicable_model=device.model,
                        applicable_feature="inventory",
                        freshness_weight=0.84,
                    )
                ],
            }

            interfaces = self._db.get_interfaces_for_device(device.id)
            if interfaces:
                interface_lines = [f"Planned interfaces for {device.hostname}:"]
                for interface in interfaces:
                    line = f"  {interface.name}"
                    if interface.ip_address:
                        line += f" - IP: {interface.ip_address}"
                    if interface.subnet_mask:
                        line += f"/{interface.subnet_mask}"
                    if interface.status:
                        line += f" - Status: {interface.status}"
                    interface_lines.append(line)

                interface_doc_id = self._planned_interface_doc_id(device.hostname)
                text = "\n".join(interface_lines)
                chunks = [text[i:i + _MAX_CHUNK_CHARS] for i in range(0, len(text), _MAX_CHUNK_CHARS)] or [text]
                metas = [
                    self._planned_meta(
                        interface_doc_id,
                        "planned_topology",
                        device.hostname,
                        timestamp,
                        index,
                        applicable_vendor=device.vendor,
                        applicable_model=device.model,
                        applicable_feature="interfaces",
                        freshness_weight=0.8,
                    )
                    for index, _chunk in enumerate(chunks)
                ]
                documents[interface_doc_id] = {"chunks": chunks, "metas": metas}

        return documents

    def _build_planned_link_documents(
        self,
        timestamp: str,
        hostnames: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, List[dict] | List[str]]]:
        documents: Dict[str, Dict[str, List[dict] | List[str]]] = {}
        selected = {hostname.strip() for hostname in (hostnames or []) if hostname and hostname.strip()}
        for link in self._db.get_all_links():
            src_device = self._db.get_device_by_id(link.src_device_id)
            dst_device = self._db.get_device_by_id(link.dst_device_id)
            if src_device is None or dst_device is None:
                continue
            if selected and src_device.hostname not in selected and dst_device.hostname not in selected:
                continue

            src_interface = None
            dst_interface = None
            for interface in self._db.get_interfaces_for_device(src_device.id):
                if interface.id == link.src_interface_id:
                    src_interface = interface
                    break
            for interface in self._db.get_interfaces_for_device(dst_device.id):
                if interface.id == link.dst_interface_id:
                    dst_interface = interface
                    break

            src_name = src_interface.name if src_interface else "?"
            dst_name = dst_interface.name if dst_interface else "?"
            doc_id = self._planned_link_doc_id(src_device.hostname, src_name, dst_device.hostname, dst_name)
            text = (
                f"Planned link: {src_device.hostname} ({src_name}) <-> "
                f"{dst_device.hostname} ({dst_name}) via {link.discovery_protocol or 'unknown'}"
            )
            documents[doc_id] = {
                "chunks": [text],
                "metas": [
                    self._planned_meta(
                        doc_id,
                        "planned_topology",
                        src_device.hostname,
                        timestamp,
                        0,
                        applicable_vendor=getattr(src_device, "vendor", ""),
                        applicable_model=getattr(src_device, "model", ""),
                        applicable_feature="topology,links",
                        freshness_weight=0.82,
                    )
                ],
            }

        return documents

    def _chunk_config(
        self,
        hostname: str,
        config_text: str,
        timestamp: str,
        captured_at: Optional[str] = None,
    ) -> tuple[List[str], List[dict]]:
        """Split a config into chunks with metadata."""
        chunks, metas = [], []
        if not config_text or not config_text.strip():
            return chunks, metas

        # Prefix each chunk with the device name for context
        prefix = f"Configuration for {hostname}:\n"
        device = self._db.get_device_by_hostname(hostname) if self._db is not None else None
        vendor = getattr(device, "vendor", "") or _STATE_VENDOR
        model = getattr(device, "model", "") or ""

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
                    f"config_{hostname}",
                    "config",
                    hostname,
                    timestamp,
                    len(chunks) - 1,
                    captured_at=captured_at,
                    applicable_vendor=vendor,
                    applicable_model=model,
                    applicable_feature=self._infer_config_feature(config_text),
                    source_type="config_snapshot",
                    freshness_weight=0.88,
                ))
                current_chunk = prefix + "!" + section + "\n"
            else:
                current_chunk = candidate

        # Flush remaining
        if current_chunk.strip() != prefix.strip():
            chunks.append(current_chunk)
            metas.append(self._base_meta(
                f"config_{hostname}",
                "config",
                hostname,
                timestamp,
                len(chunks) - 1,
                captured_at=captured_at,
                applicable_vendor=vendor,
                applicable_model=model,
                applicable_feature=self._infer_config_feature(config_text),
                source_type="config_snapshot",
                freshness_weight=0.88,
            ))

        return chunks, metas

    def _build_incident_chunks(self, incident: dict, timestamp: str) -> tuple[List[str], List[dict]]:
        incident_id = str(incident.get("id") or "unknown")
        affected_devices = incident.get("affected_devices") or "[]"
        if isinstance(affected_devices, str):
            try:
                affected_devices = json.loads(affected_devices)
            except Exception:
                affected_devices = [affected_devices]
        lines = [
            f"Incident: {incident_id}",
            f"  Anomaly Type: {incident.get('anomaly_type', 'unknown')}",
            f"  Severity: {incident.get('severity', 'unknown')}",
            f"  Status: {incident.get('status', 'open')}",
            f"  Affected Devices: {', '.join(affected_devices or []) or 'unknown'}",
        ]
        if incident.get("root_cause_summary"):
            lines.append(f"  Root Cause: {incident['root_cause_summary']}")
        if incident.get("proposed_description"):
            lines.append(f"  Proposed Action: {incident['proposed_description']}")
        if incident.get("estimated_impact"):
            lines.append(f"  Estimated Impact: {incident['estimated_impact']}")
        text = "\n".join(lines)
        doc_id = f"incident_{incident_id}"
        meta = {
            "doc_id": doc_id,
            "doc_name": f"incident_{incident_id}",
            "doc_type": _INCIDENT_DOC_TYPE,
            "vendor": _STATE_VENDOR,
            "incident_id": incident_id,
            "captured_at": incident.get("updated_at") or incident.get("detected_at") or incident.get("created_at") or timestamp,
            "severity": incident.get("severity", "unknown"),
            "status": incident.get("status", "open"),
            "applicable_vendor": _STATE_VENDOR,
            "applicable_model": "",
            "applicable_feature": incident.get("anomaly_type", "incident"),
            "source_type": "incident_report",
            "freshness_weight": 0.9,
            "chunk_index": 0,
            "ingested_at": timestamp,
            "token_count": 0,
        }
        return [text], [meta]

    def _build_drift_chunks(self, event: Any, timestamp: str) -> tuple[List[str], List[dict]]:
        hostname = getattr(event, "hostname", None) or f"device_{getattr(event, 'device_id', 'unknown')}"
        lines = [
            f"Drift result for {hostname}:",
            f"  Drift Present: {'yes' if getattr(event, 'has_drift', False) else 'no'}",
            f"  Severity: {getattr(event, 'severity', 'unknown') or 'unknown'}",
        ]
        if getattr(event, "diff_text", None):
            lines.append(f"  Diff: {event.diff_text}")
        text = "\n".join(lines)
        doc_id = f"drift_{hostname}"
        meta = {
            "doc_id": doc_id,
            "doc_name": f"drift_{hostname}",
            "doc_type": _DRIFT_DOC_TYPE,
            "vendor": _STATE_VENDOR,
            "hostname": hostname,
            "captured_at": getattr(event, "timestamp", None) or timestamp,
            "severity": getattr(event, "severity", "unknown") or "unknown",
            "applicable_vendor": _STATE_VENDOR,
            "applicable_model": "",
            "applicable_feature": "configuration_drift",
            "source_type": "drift_result",
            "freshness_weight": 0.78,
            "chunk_index": 0,
            "ingested_at": timestamp,
            "token_count": 0,
        }
        return [text], [meta]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _base_meta(
        self,
        doc_id: str,
        sub_type: str,
        hostname: str,
        timestamp: str,
        chunk_index: int,
        node_data: Optional[dict] = None,
        captured_at: Optional[str] = None,
        applicable_vendor: str = "",
        applicable_model: str = "",
        applicable_feature: str = "",
        source_type: str = "",
        freshness_weight: float = 0.0,
        severity: str = "info",
    ) -> dict:
        node = node_data or {}
        resolved_vendor = applicable_vendor or str(node.get("vendor") or _STATE_VENDOR)
        resolved_model = applicable_model or str(node.get("model") or "")
        resolved_feature = applicable_feature or self._feature_for_sub_type(sub_type)
        resolved_source_type = source_type or self._source_type_for_sub_type(sub_type)
        resolved_freshness = freshness_weight or self._freshness_weight_for_sub_type(sub_type)
        return {
            "doc_id": doc_id,
            "doc_name": f"network_state_{sub_type}_{hostname}",
            "doc_type": _STATE_DOC_TYPE,
            "sub_type": sub_type,
            "vendor": resolved_vendor,
            "hostname": hostname,
            "captured_at": captured_at or node.get("last_seen") or timestamp,
            "applicable_vendor": resolved_vendor,
            "applicable_model": resolved_model,
            "applicable_feature": resolved_feature,
            "source_type": resolved_source_type,
            "severity": severity,
            "freshness_weight": resolved_freshness,
            "chunk_index": chunk_index,
            "ingested_at": timestamp,
            "token_count": 0,  # approximate — not critical for state data
        }

    def _planned_meta(
        self,
        doc_id: str,
        doc_type: str,
        hostname: str,
        timestamp: str,
        chunk_index: int,
        applicable_vendor: str = "",
        applicable_model: str = "",
        applicable_feature: str = "",
        freshness_weight: float = 0.72,
    ) -> dict:
        return {
            "doc_id": doc_id,
            "doc_name": doc_id,
            "doc_type": doc_type,
            "vendor": applicable_vendor or _PLANNED_DOC_VENDOR,
            "hostname": hostname,
            "captured_at": timestamp,
            "applicable_vendor": applicable_vendor or _PLANNED_DOC_VENDOR,
            "applicable_model": applicable_model,
            "applicable_feature": applicable_feature or doc_type,
            "source_type": doc_type,
            "severity": "info",
            "freshness_weight": freshness_weight,
            "chunk_index": chunk_index,
            "ingested_at": timestamp,
            "token_count": 0,
        }

    def _upsert_partitioned_document(
        self,
        *,
        doc_id: str,
        chunks: List[str],
        metas: List[dict],
        partition: str,
    ) -> Dict[str, Any]:
        if not chunks:
            self._delete_document(doc_id, partition)
            return {"status": "removed", "chunk_count": 0, "doc_id": doc_id}

        signature = self._signature_for_chunks(chunks)
        signature_key = (partition, doc_id)
        if self._doc_signatures.get(signature_key) == signature:
            return {"status": "unchanged", "chunk_count": len(chunks), "doc_id": doc_id}

        self._delete_document(doc_id, partition)
        self._store.upsert_chunks(doc_id, chunks, metas, partition=partition)
        self._doc_signatures[signature_key] = signature
        return {"status": "updated", "chunk_count": len(chunks), "doc_id": doc_id}

    def _delete_document(self, doc_id: str, partition: str) -> int:
        try:
            deleted = self._store.delete_document(doc_id, partition=partition)
        except Exception:
            deleted = 0
        self._doc_signatures.pop((partition, doc_id), None)
        return deleted

    def _delete_missing_documents(
        self,
        *,
        partition: str,
        current_doc_ids: set[str],
        prefix_filters: tuple[str, ...],
    ) -> int:
        removed = 0
        existing_doc_ids = {
            document.get("doc_id", "")
            for document in self._store.list_documents(partition=partition)
            if any(str(document.get("doc_id", "")).startswith(prefix) for prefix in prefix_filters)
        }
        for doc_id in existing_doc_ids - current_doc_ids:
            removed += self._delete_document(doc_id, partition)
        return removed

    @staticmethod
    def _signature_for_chunks(chunks: List[str]) -> str:
        return json.dumps(chunks, ensure_ascii=True, separators=(",", ":"))

    def _embed_config_from_db(self, hostname: str) -> Dict[str, Any]:
        if self._db is None:
            return {"status": "skipped", "reason": "db_unavailable"}
        device = self._db.get_device_by_hostname(hostname)
        if device is None:
            return {"status": "skipped", "reason": "device_not_found", "hostname": hostname}
        history = self._db.get_config_history(device.id, limit=1)
        if not history:
            return {"status": "skipped", "reason": "config_not_found", "hostname": hostname}
        return self.embed_single_config(
            hostname,
            history[0].config_text,
            captured_at=getattr(history[0], "captured_at", None),
        )

    def _resolve_hostname(self, metadata: dict) -> str:
        hostname = str(metadata.get("hostname") or "").strip()
        if hostname:
            return hostname
        device_id = metadata.get("device_id")
        if isinstance(device_id, int) and self._db is not None:
            device = self._db.get_device_by_id(device_id)
            if device is not None:
                return device.hostname
        return ""

    def _extract_related_hostnames(self, metadata: dict) -> List[str]:
        hostnames = []
        for key in ("hostname", "src_hostname", "dst_hostname", "name"):
            value = str(metadata.get(key) or "").strip()
            if value and value not in hostnames:
                hostnames.append(value)
        return hostnames

    @staticmethod
    def _downstream_target(name: str, result: Dict[str, Any], **extra: object) -> Dict[str, object]:
        payload = {
            "kind": "embedding",
            "name": name,
            "component": "rag_auto_embedder",
            "action": "updated" if result.get("status") == "embedded" else str(result.get("status") or "skipped"),
            "status": str(result.get("status") or "skipped"),
        }
        payload.update({key: value for key, value in extra.items() if value not in (None, "", [])})
        return payload

    @staticmethod
    def _live_device_doc_id(hostname: str) -> str:
        return f"live_device_{hostname}"

    @staticmethod
    def _live_interface_doc_id(hostname: str) -> str:
        return f"live_interfaces_{hostname}"

    @staticmethod
    def _live_link_doc_id(src_hostname: str, src_interface: str, dst_hostname: str, dst_interface: str) -> str:
        endpoints = sorted([
            f"{src_hostname}:{src_interface}",
            f"{dst_hostname}:{dst_interface}",
        ])
        return "live_link_" + "__".join(item.replace("/", "_") for item in endpoints)

    @staticmethod
    def _planned_device_doc_id(hostname: str) -> str:
        return f"planned_device_{hostname}"

    @staticmethod
    def _planned_interface_doc_id(hostname: str) -> str:
        return f"planned_interfaces_{hostname}"

    @staticmethod
    def _planned_link_doc_id(src_hostname: str, src_interface: str, dst_hostname: str, dst_interface: str) -> str:
        endpoints = sorted([
            f"{src_hostname}:{src_interface}",
            f"{dst_hostname}:{dst_interface}",
        ])
        return "planned_link_" + "__".join(item.replace("/", "_") for item in endpoints)

    def _planned_link_doc_ids(self) -> set[str]:
        return {
            document.get("doc_id", "")
            for document in self._store.list_documents(partition=PARTITION_PLANNED_STATE)
            if str(document.get("doc_id", "")).startswith("planned_link_")
        }

    @staticmethod
    def _source_type_for_sub_type(sub_type: str) -> str:
        mapping = {
            "topology": "live_state",
            "links": "live_state",
            "interfaces": "live_state",
            "config": "config_snapshot",
        }
        return mapping.get(sub_type, "live_state")

    @staticmethod
    def _freshness_weight_for_sub_type(sub_type: str) -> float:
        mapping = {
            "topology": 0.95,
            "links": 0.93,
            "interfaces": 0.91,
            "config": 0.88,
        }
        return mapping.get(sub_type, 0.8)

    @staticmethod
    def _feature_for_sub_type(sub_type: str) -> str:
        mapping = {
            "topology": "topology",
            "links": "links,adjacency",
            "interfaces": "interfaces",
            "config": "configuration",
        }
        return mapping.get(sub_type, sub_type)

    @staticmethod
    def _infer_config_feature(config_text: str) -> str:
        normalized = (config_text or "").lower()
        features = []
        for feature in ("ospf", "bgp", "vlan", "mpls", "vrf", "acl", "qos", "nat"):
            if feature in normalized and feature not in features:
                features.append(feature)
        if not features:
            features.append("configuration")
        return ",".join(features)
