from __future__ import annotations

import hashlib
import logging
import sqlite3
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_KNOWLEDGE,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_SDN,
    CONTEXT_DOMAIN_TELEMETRY,
    QueryRoutingDecision,
    classify_query,
)
from llm.context import ContextBuilder
from rag.context_bundle import ContextBundleBuilder
from retrieval.graph_engine import TopologyGraphManager
from retrieval.fetchers.redis_ops import fetch_cached_live_status_sync
from retrieval.fetchers.influx_ops import fetch_historical_metrics_sync
from llm.retriever import KnowledgeRetriever
from retrieval.memory import OperationalMemoryStore
from llm.context_contract import _extract_hostname_hints

logger = logging.getLogger("retrieval.orchestrator")


def _short_hash(*parts: str) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update((part or "").encode("utf-8", errors="replace"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


@dataclass
class RetrievalPackage:
    query: str
    query_class: str
    routing_decision: QueryRoutingDecision
    device_hostnames: List[str] = field(default_factory=list)
    prompt_text: str = ""
    knowledge_context: str = ""
    knowledge_snippets: List[str] = field(default_factory=list)
    bundle: Dict[str, Any] = field(default_factory=dict)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    operational_context: str = ""
    source_provenance: List[Dict[str, Any]] = field(default_factory=list)
    impact_analysis: List[Dict[str, Any]] = field(default_factory=list)
    cache_key: str = ""
    vendor_hint: str = ""
    feature_hint: str = ""


class RetrievalOrchestrator:
    """
    Central retrieval coordinator for chat and playbook planning.

    It chooses the source set, applies freshness-aware context assembly,
    and keeps the LLM behind a curated evidence envelope.
    """

    def __init__(
        self,
        db_manager=None,
        retriever: Optional[KnowledgeRetriever] = None,
        memory: Optional[OperationalMemoryStore] = None,
        netbox_client=None,
    ):
        self._db = db_manager
        self._retriever = retriever
        self._memory = memory or OperationalMemoryStore.from_env()
        self._context = ContextBuilder(db_manager=db_manager)
        self._bundle_builder = ContextBundleBuilder(db_manager=db_manager, netbox_client=netbox_client)

    @property
    def memory(self) -> OperationalMemoryStore:
        return self._memory

    def _context_flags(self, decision: QueryRoutingDecision) -> Dict[str, bool]:
        return {
            "include_inventory": bool(decision.include_inventory),
            "include_planned_topology": bool(decision.include_inventory),
            "include_telemetry": bool(decision.include_telemetry),
            "include_sdn": bool(decision.include_sdn),
            "include_config": bool(decision.query_class in {"change_planning", "compliance"}),
            "include_deployment": bool(decision.query_class in {"change_planning", "compliance"}),
            "include_incidents": bool(decision.query_class in {"troubleshooting", "live_only", "planned_vs_live"}),
            "include_threats": bool(decision.query_class in {"troubleshooting", "live_only"}),
            "include_drift": bool(decision.query_class in {"planned_vs_live", "compliance"}),
            "include_knowledge": bool(decision.use_knowledge),
            "include_freshness_warnings": True,
        }

    def _bundle_sources(self, bundle) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        for name, section in (
            ("inventory", bundle.inventory),
            ("planned_topology", bundle.planned_topology),
            ("telemetry", bundle.telemetry),
            ("sdn_state", bundle.sdn_state),
            ("config_history", bundle.config_history),
            ("deployment_status", bundle.deployment_status),
            ("incidents", bundle.incidents),
            ("threat_detections", bundle.threat_detections),
            ("drift", bundle.drift),
            ("knowledge", bundle.knowledge),
        ):
            freshness = getattr(section, "freshness", None)
            sources.append(
                {
                    "section": name,
                    "source": getattr(section, "source", ""),
                    "freshness": freshness.to_dict() if freshness else {},
                }
            )
        return sources

    def build_context_package(
        self,
        query: str,
        device_hostnames: Optional[List[str]] = None,
        vendor_hint: Optional[str] = None,
        feature_hint: Optional[str] = None,
        include_bundle: bool = True,
    ) -> RetrievalPackage:
        decision = classify_query(query)
        cache_key = _short_hash(
            query,
            decision.query_class,
            ",".join(sorted(device_hostnames or [])),
            vendor_hint or "",
            feature_hint or "",
        )
        cached = self._memory.load_query_context(cache_key)
        if isinstance(cached, dict) and cached.get("prompt_text"):
            return RetrievalPackage(
                query=query,
                query_class=decision.query_class,
                routing_decision=decision,
                device_hostnames=list(device_hostnames or []),
                prompt_text=str(cached.get("prompt_text") or ""),
                knowledge_context=str(cached.get("knowledge_context") or ""),
                knowledge_snippets=list(cached.get("knowledge_snippets") or []),
                bundle=dict(cached.get("bundle") or {}),
                sources=list(cached.get("sources") or []),
                operational_context=str(cached.get("operational_context") or ""),
                source_provenance=list(cached.get("source_provenance") or []),
                impact_analysis=list(cached.get("impact_analysis") or []),
                cache_key=cache_key,
                vendor_hint=str(vendor_hint or ""),
                feature_hint=str(feature_hint or ""),
            )

        flags = self._context_flags(decision)
        bundle = self._bundle_builder.build_bundle(
            device_hostnames=device_hostnames,
            vendor_filter=vendor_hint or None,
            query=query,
            routing_decision=decision,
            **flags,
        ) if include_bundle else None

        prompt_parts: List[str] = []
        bundle_dict: Dict[str, Any] = {}
        sources: List[Dict[str, Any]] = []
        if bundle is not None:
            bundle_dict = bundle.to_dict()
            prompt_parts.append(bundle.to_prompt_text())
            sources = self._bundle_sources(bundle)

        knowledge_context = ""
        knowledge_snippets: List[str] = []
        if decision.use_knowledge and self._retriever is not None:
            retrieval_query = query
            if feature_hint:
                retrieval_query = f"{retrieval_query} feature {feature_hint}"
            if vendor_hint:
                retrieval_query = f"{retrieval_query} vendor {vendor_hint}"
            try:
                results = self._retriever.retrieve(
                    retrieval_query,
                    k=3,
                    vendor_filter=vendor_hint or None,
                    doc_type_filter="ansible_reference" if feature_hint else None,
                )
                knowledge_snippets = [item["text"] for item in results[:3] if item.get("text")]
                knowledge_context = self._retriever.retrieve_as_text(retrieval_query, k=3) if knowledge_snippets else ""
            except Exception as exc:
                logger.debug("Knowledge retrieval skipped: %s", exc)
            if knowledge_context:
                prompt_parts.append(knowledge_context)

        operational_context = ""
        source_provenance: List[Dict[str, Any]] = []
        impact_analysis: List[Dict[str, Any]] = []
        if include_bundle and bundle is not None:
            appendix = self._build_operational_appendix(
                query=query,
                decision=decision,
                device_hostnames=device_hostnames,
                bundle=bundle,
            )
            operational_context = str(appendix.get("appendix") or "")
            source_provenance = list(appendix.get("source_provenance") or [])
            impact_analysis = [
                {"section": "topology_impact", "summary": operational_context}
            ] if "### Topology Impact Analysis" in operational_context else []
            if operational_context:
                prompt_parts.append(operational_context)

        if not prompt_parts:
            prompt_parts.append(self._context.build_full_context(
                device_hostnames=device_hostnames,
                query=query,
                routing_decision=decision,
            ))

        prompt_text = "\n\n".join(part for part in prompt_parts if part)
        payload = {
            "prompt_text": prompt_text,
            "knowledge_context": knowledge_context,
            "knowledge_snippets": knowledge_snippets,
            "bundle": bundle_dict,
            "sources": sources,
            "operational_context": operational_context,
            "source_provenance": source_provenance,
            "impact_analysis": impact_analysis,
        }
        self._memory.remember_query_context(cache_key, payload, ttl_seconds=30)
        return RetrievalPackage(
            query=query,
            query_class=decision.query_class,
            routing_decision=decision,
            device_hostnames=list(device_hostnames or []),
            prompt_text=prompt_text,
            knowledge_context=knowledge_context,
            knowledge_snippets=knowledge_snippets,
            bundle=bundle_dict,
            sources=sources,
            operational_context=operational_context,
            source_provenance=source_provenance,
            impact_analysis=impact_analysis,
            cache_key=cache_key,
            vendor_hint=str(vendor_hint or ""),
            feature_hint=str(feature_hint or ""),
        )

    def build_chat_context(
        self,
        query: str,
        device_hostnames: Optional[List[str]] = None,
        vendor_hint: Optional[str] = None,
        feature_hint: Optional[str] = None,
    ) -> str:
        return self.build_context_package(
            query=query,
            device_hostnames=device_hostnames,
            vendor_hint=vendor_hint,
            feature_hint=feature_hint,
        ).prompt_text

    def build_knowledge_snippets(
        self,
        query: str,
        k: int = 3,
        vendor_filter: Optional[str] = None,
        doc_type_filter: Optional[str] = None,
    ) -> List[str]:
        if self._retriever is None:
            return []
        try:
            return [
                item["text"]
                for item in self._retriever.retrieve(
                    query,
                    k=k,
                    vendor_filter=vendor_filter,
                    doc_type_filter=doc_type_filter,
                )
                if item.get("text")
            ][:k]
        except Exception as exc:
            logger.debug("build_knowledge_snippets failed: %s", exc)
            return []

    def _extract_hostnames_from_query(
        self,
        query: str,
        device_hostnames: Optional[List[str]] = None,
    ) -> List[str]:
        hints = list(device_hostnames or [])
        try:
            hints.extend(sorted(_extract_hostname_hints(query)))
        except Exception:
            pass
        deduped: List[str] = []
        seen = set()
        for host in hints:
            normalized = str(host or "").strip()
            if not normalized:
                continue
            lower = normalized.lower()
            if lower in seen:
                continue
            seen.add(lower)
            deduped.append(normalized)
        return deduped[:10]

    @staticmethod
    def _freshness_to_label(age_seconds: Optional[float], fallback: str = "unknown") -> str:
        if age_seconds is None:
            return fallback
        if age_seconds <= 30:
            return "live"
        if age_seconds <= 120:
            return "warm"
        return "stale"

    def _render_source_provenance(self, bundle) -> List[Dict[str, Any]]:
        provenance: List[Dict[str, Any]] = []
        for section_name, section in (
            ("inventory", bundle.inventory),
            ("planned_topology", bundle.planned_topology),
            ("telemetry", bundle.telemetry),
            ("sdn_state", bundle.sdn_state),
            ("config_history", bundle.config_history),
            ("deployment_status", bundle.deployment_status),
            ("incidents", bundle.incidents),
            ("threat_detections", bundle.threat_detections),
            ("drift", bundle.drift),
            ("knowledge", bundle.knowledge),
        ):
            freshness = getattr(section, "freshness", None)
            provenance.append(
                {
                    "section": section_name,
                    "source": getattr(section, "source", ""),
                    "source_detail": getattr(section, "source_detail", ""),
                    "captured_at": getattr(freshness, "captured_at", None) if freshness else None,
                    "ingested_at": getattr(freshness, "ingested_at", None) if freshness else None,
                    "age_seconds": getattr(freshness, "age_seconds", None) if freshness else None,
                    "freshness_class": getattr(freshness, "freshness_class", "missing") if freshness else "missing",
                    "confidence": getattr(freshness, "confidence", 0.0) if freshness else 0.0,
                }
            )
        return provenance

    def _build_live_operational_context(
        self,
        query: str,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        hostnames = self._extract_hostnames_from_query(query, device_hostnames)
        if not hostnames:
            return ""

        parts: List[str] = ["### Live Operational Snapshot"]
        live_found = False
        for hostname in hostnames:
            snapshot = self._memory.load_device_snapshot(hostname)
            if snapshot is None:
                try:
                    snapshot = fetch_cached_live_status_sync(None, hostname)
                except Exception as exc:
                    logger.debug("Live snapshot fetch failed for %s: %s", hostname, exc)
                    snapshot = None

            if not snapshot:
                continue

            live_found = True
            freshness = self._freshness_to_label(snapshot.get("age"))
            age = snapshot.get("age")
            status = snapshot.get("status", "UNKNOWN")
            parts.append(f"- **{hostname}**: status={status}; freshness={freshness}; age_seconds={age if age is not None else 'unknown'}")
            if freshness == "stale":
                parts.append("  - warning: stale live snapshot; do not treat as current truth")
            elif freshness == "warm":
                parts.append("  - note: warm live snapshot; verify before acting")
            interfaces = snapshot.get("interfaces") or {}
            if interfaces:
                interface_chunks = []
                for iface_name, iface_data in list(interfaces.items())[:8]:
                    if isinstance(iface_data, dict):
                        interface_chunks.append(f"{iface_name}={iface_data.get('status', 'unknown')}")
                    else:
                        interface_chunks.append(f"{iface_name}={iface_data}")
                if interface_chunks:
                    parts.append(f"  - interfaces: {', '.join(interface_chunks)}")
            logs = snapshot.get("logs") or []
            if logs:
                recent_logs = []
                for log in logs[:5]:
                    msg = str(log.get("msg") or "").strip()
                    sev = str(log.get("severity") or "info")
                    if msg:
                        recent_logs.append(f"{sev}: {msg}")
                if recent_logs:
                    parts.append("  - recent logs:")
                    for item in recent_logs[:5]:
                        parts.append(f"    - {item}")

            influx_fallback = None
            if not logs:
                try:
                    influx_fallback = fetch_historical_metrics_sync(hostname, window_min=5)
                except Exception as exc:
                    logger.debug("Influx fallback fetch failed for %s: %s", hostname, exc)
            if influx_fallback and influx_fallback.get("logs"):
                parts.append("  - influx syslog fallback:")
                for log in influx_fallback["logs"][:5]:
                    msg = str(log.get("msg") or "").strip()
                    if msg:
                        parts.append(f"    - {log.get('severity', 'info')}: {msg}")

        return "\n".join(parts) if live_found else ""

    def _build_live_topology_context(self, query: str) -> str:
        topology = self._memory.load_topology_snapshot()
        if not topology:
            return ""
        if "impact" not in query.lower() and "topology" not in query.lower() and "connect" not in query.lower() and "path" not in query.lower() and "failure" not in query.lower():
            # Still include a terse live snapshot when the query is live/troubleshooting oriented.
            if not any(token in query.lower() for token in ("status", "unstable", "down", "flap", "latency", "packet loss", "telemetry", "syslog")):
                return ""

        nodes = topology.get("nodes") if isinstance(topology, dict) else None
        if not isinstance(nodes, dict):
            return ""

        lines = ["### Live Topology Snapshot"]
        lines.append(f"- nodes={len(nodes)}")
        edge_count = 0
        for node in nodes.values():
            if isinstance(node, dict):
                edge_count += len(node.get("edgeLink") or {})
        lines.append(f"- edges={edge_count}")
        sample_nodes = list(nodes.keys())[:8]
        if sample_nodes:
            lines.append(f"- sample_nodes={', '.join(sample_nodes)}")
        return "\n".join(lines)

    def _build_change_timeline_context(
        self,
        query: str,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        hostnames = self._extract_hostnames_from_query(query, device_hostnames)
        if not self._db:
            return ""

        lines: List[str] = ["### Recent Change Timeline"]
        events: List[Dict[str, Any]] = []

        try:
            with self._db._connect() as conn:  # type: ignore[attr-defined]
                table_exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='change_timeline'"
                ).fetchone()
                if table_exists:
                    if hostnames:
                        placeholders = ",".join("?" for _ in hostnames)
                        rows = conn.execute(
                            f"""
                            SELECT timestamp, device_name, change_type, operator, description, status
                            FROM change_timeline
                            WHERE device_name IN ({placeholders})
                            ORDER BY timestamp DESC
                            LIMIT 8
                            """,
                            hostnames,
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            """
                            SELECT timestamp, device_name, change_type, operator, description, status
                            FROM change_timeline
                            ORDER BY timestamp DESC
                            LIMIT 8
                            """
                        ).fetchall()
                    events.extend(dict(row) for row in rows)
        except Exception as exc:
            logger.debug("Change timeline fetch failed: %s", exc)

        if not events:
            try:
                with self._db._connect() as conn:  # type: ignore[attr-defined]
                    rows = conn.execute(
                        """
                        SELECT j.updated_at AS timestamp, d.hostname AS device_name, j.stage AS change_type,
                               cr.requester AS operator, cr.title AS description, j.status
                        FROM jobs j
                        LEFT JOIN devices d ON d.id = j.device_id
                        LEFT JOIN change_requests cr ON cr.id = j.request_id
                        ORDER BY j.updated_at DESC
                        LIMIT 8
                        """
                    ).fetchall()
                events.extend(dict(row) for row in rows if row)
            except Exception as exc:
                logger.debug("Deployment-based change timeline fetch failed: %s", exc)

        if not events:
            return ""

        for event in events[:8]:
            lines.append(
                "- {timestamp} | {device_name} | {change_type} | {operator} | {status} | {description}".format(
                    timestamp=event.get("timestamp", ""),
                    device_name=event.get("device_name", ""),
                    change_type=event.get("change_type", ""),
                    operator=event.get("operator", ""),
                    status=event.get("status", ""),
                    description=event.get("description", ""),
                )
            )
        return "\n".join(lines)

    def _build_topology_impact_context(
        self,
        query: str,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        hostnames = self._extract_hostnames_from_query(query, device_hostnames)
        if not hostnames and "impact" not in query.lower() and "blast" not in query.lower():
            return ""

        try:
            mgr = TopologyGraphManager()
            if self._db is not None and hasattr(mgr, "rebuild_graph_from_db"):
                try:
                    mgr.rebuild_graph_from_db(self._db)
                except Exception as exc:
                    logger.debug("Topology graph rebuild failed: %s", exc)
        except Exception as exc:
            logger.debug("Topology graph manager unavailable: %s", exc)
            return ""

        impacted: List[Dict[str, Any]] = []
        for hostname in hostnames[:5]:
            try:
                impacted_nodes = mgr.calculate_blast_radius(hostname)
                impacted.append(
                    {
                        "hostname": hostname,
                        "impacted_nodes": impacted_nodes[:20],
                    }
                )
            except Exception as exc:
                logger.debug("Blast radius calculation failed for %s: %s", hostname, exc)

        if not impacted:
            return ""

        lines = ["### Topology Impact Analysis"]
        for item in impacted:
            lines.append(f"- {item['hostname']}: impacted_nodes={', '.join(item['impacted_nodes']) if item['impacted_nodes'] else 'none'}")
        return "\n".join(lines)

    def _build_operational_appendix(
        self,
        query: str,
        decision: QueryRoutingDecision,
        device_hostnames: Optional[List[str]] = None,
        bundle=None,
    ) -> Dict[str, Any]:
        hostnames = self._extract_hostnames_from_query(query, device_hostnames)
        provenance = self._render_source_provenance(bundle) if bundle is not None else []
        live_context = self._build_live_operational_context(query, hostnames)
        live_topology_context = self._build_live_topology_context(query)
        change_context = self._build_change_timeline_context(query, hostnames)
        impact_context = self._build_topology_impact_context(query, hostnames)

        parts = []
        if provenance:
            parts.append("### Source Provenance")
            for entry in provenance:
                parts.append(
                    "- {section}: source={source}; freshness={freshness_class}; age_seconds={age_seconds}; confidence={confidence}".format(
                        section=entry.get("section", ""),
                        source=entry.get("source", ""),
                        freshness_class=entry.get("freshness_class", "missing"),
                        age_seconds=entry.get("age_seconds", "unknown"),
                        confidence=entry.get("confidence", 0.0),
                    )
                )
        if live_context:
            parts.append(live_context)
        if live_topology_context:
            parts.append(live_topology_context)
        if change_context:
            parts.append(change_context)
        if impact_context:
            parts.append(impact_context)

        appendix = "\n\n".join(part for part in parts if part)
        return {
            "appendix": appendix,
            "source_provenance": provenance,
            "hostnames": hostnames,
            "decision": decision,
        }

    def remember_topology(self, topology: Dict[str, Any]) -> None:
        self._memory.remember_topology_snapshot(topology, ttl_seconds=30)

    def remember_device_snapshot(self, hostname: str, snapshot: Dict[str, Any]) -> None:
        self._memory.remember_device_snapshot(hostname, snapshot, ttl_seconds=30)

    def remember_incident(self, incident_id: str, incident: Dict[str, Any]) -> None:
        self._memory.remember_incident(incident_id, incident, ttl_seconds=300)

    def remember_inventory_summary(self, scope: str, summary: Dict[str, Any]) -> None:
        self._memory.remember_inventory_summary(scope, summary, ttl_seconds=120)

    def _change_memory_key(self, hostname: str) -> str:
        return f"ctx:changes:{hostname}"

    def _load_change_events(self, hostname: str) -> List[Dict[str, Any]]:
        cached = self._memory.get(self._change_memory_key(hostname))
        if isinstance(cached, list):
            return [item for item in cached if isinstance(item, dict)]
        return []

    def _store_change_events(self, hostname: str, events: List[Dict[str, Any]]) -> None:
        self._memory.set(self._change_memory_key(hostname), events[-25:], ttl_seconds=900)

    def log_change_event(
        self,
        hostname: str,
        change_type: str,
        operator: str,
        description: str,
        status: str,
    ) -> Dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device_name": hostname,
            "change_type": change_type,
            "operator": operator,
            "description": description,
            "status": status,
        }

        events = self._load_change_events(hostname)
        events.append(record)
        self._store_change_events(hostname, events)

        if self._db is not None:
            try:
                with self._db._connect() as conn:  # type: ignore[attr-defined]
                    conn.execute(
                        """
                        INSERT INTO change_timeline (
                            timestamp, device_name, change_type, operator, description, status
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record["timestamp"],
                            hostname,
                            change_type,
                            operator,
                            description,
                            status,
                        ),
                    )
                    if hasattr(conn, "commit"):
                        conn.commit()
            except Exception as exc:
                logger.debug("Failed to persist change event for %s: %s", hostname, exc)

        return record

    def _render_change_events(self, hostname: str) -> str:
        events = self._load_change_events(hostname)
        if self._db is not None:
            try:
                with self._db._connect() as conn:  # type: ignore[attr-defined]
                    cursor = conn.execute(
                        """
                        SELECT timestamp, device_name, change_type, operator, description, status
                        FROM change_timeline
                        WHERE device_name = ?
                        ORDER BY timestamp DESC
                        LIMIT 5
                        """,
                        (hostname,),
                    )
                    rows = cursor.fetchall()
                    for row in rows:
                        if isinstance(row, sqlite3.Row):
                            events.append(dict(row))
                        else:
                            events.append(
                                {
                                    "timestamp": row[0],
                                    "device_name": row[1],
                                    "change_type": row[2],
                                    "operator": row[3],
                                    "description": row[4],
                                    "status": row[5],
                                }
                            )
            except Exception as exc:
                logger.debug("Failed to load change history for %s: %s", hostname, exc)

        if not events:
            return ""

        lines = ["Recent change history:"]
        for event in events[-5:]:
            lines.append(
                "- {timestamp} | {change_type} | {operator} | {status} | {description}".format(
                    timestamp=event.get("timestamp", ""),
                    change_type=event.get("change_type", ""),
                    operator=event.get("operator", ""),
                    status=event.get("status", ""),
                    description=event.get("description", ""),
                )
            )
        return "\n".join(lines)

    async def gather_context(self, query: str, hostname: Optional[str] = None) -> str:
        parts: List[str] = []
        if hostname and self._db is not None:
            try:
                device = self._db.get_device_by_hostname(hostname)
            except Exception as exc:
                logger.debug("Device lookup failed for %s: %s", hostname, exc)
                device = None
            if device is not None:
                details = [
                    f"Hostname: {getattr(device, 'hostname', hostname)}",
                    f"Vendor: {getattr(device, 'vendor', '')}",
                    f"Model: {getattr(device, 'model', '')}",
                    f"Role: {getattr(device, 'role', '')}",
                    f"Status: {getattr(device, 'status', '')}",
                ]
                parts.append("Authoritative inventory:\n" + "\n".join(f"- {item}" for item in details))

            try:
                live = fetch_cached_live_status_sync(None, hostname)
                if live:
                    live_lines = [
                        f"Live status: {live.get('status', 'UNKNOWN')}",
                        f"Live age seconds: {live.get('age', 0):.1f}",
                    ]
                    interfaces = live.get("interfaces") or {}
                    if interfaces:
                        live_lines.append("Interfaces:")
                        for iface_name, iface_data in list(interfaces.items())[:10]:
                            if isinstance(iface_data, dict):
                                iface_status = iface_data.get("status", "")
                                live_lines.append(f"- {iface_name}: {iface_status}")
                            else:
                                live_lines.append(f"- {iface_name}: {iface_data}")
                    parts.append("Live operational snapshot:\n" + "\n".join(f"- {item}" for item in live_lines))
            except Exception as exc:
                logger.debug("Live status fetch failed for %s: %s", hostname, exc)

            change_text = self._render_change_events(hostname)
            if change_text:
                parts.append(change_text)

        if query:
            parts.append(f"User question:\n{query}")

        return "\n\n".join(part for part in parts if part)

    def hydrate_from_live_sources(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self._db is not None:
            try:
                payload["topology"] = self._db.get_topology()
            except Exception:
                payload["topology"] = {}
        return payload


def build_retrieval_orchestrator(
    db_manager=None,
    retriever: Optional[KnowledgeRetriever] = None,
    memory: Optional[OperationalMemoryStore] = None,
    netbox_client=None,
) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(
        db_manager=db_manager,
        retriever=retriever,
        memory=memory,
        netbox_client=netbox_client,
    )
