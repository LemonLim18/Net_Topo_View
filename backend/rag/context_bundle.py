"""
Phase 2 — RAG Context Bundle Builder (UC06-UC09).

Systematically fuses three authoritative context sources into a single
structured bundle that is injected into every LLM prompt:

    1. **NetBox Inventory** — devices, interfaces, IPs, sites, VLANs
    2. **Telemetry Samples** — latest CPU, memory, interface counters from InfluxDB
    3. **SDN Controller State** — routing tables, ARP, flow rules via drivers

The resulting ``ContextBundle`` is compact on purpose — only the most
relevant items for the given intent scope are included, keeping the
LLM token budget manageable while ensuring accurate grounding.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_KNOWLEDGE,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_SDN,
    CONTEXT_DOMAIN_TELEMETRY,
    FreshnessMetadata,
    QueryClass,
    QueryRoutingDecision,
    compute_freshness,
    missing_freshness,
)
from llm.semantics import (
    annotate_inventory_interfaces,
    annotate_inventory_records,
    annotate_planned_links,
    build_canonical_device_matrix,
    build_canonical_semantics_map,
    build_canonical_semantics_prompt_text,
    device_state_index,
)

logger = logging.getLogger("rag.context_bundle")

# InfluxDB settings (from environment)
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")

# Prometheus (optional — for SNMP exporter metrics)
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "")


def _role_from_hostname(hostname: str) -> str:
    """Infer device role from hostname prefix (Acc_/Dist_/Core_) when DB role is missing."""
    lower = (hostname or "").strip().lower()
    if lower.startswith("acc_"):
        return "access"
    if lower.startswith("dist_"):
        return "distribution"
    if lower.startswith("core_"):
        return "core"
    return ""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class InventoryContext:
    """Subset of NetBox/local device inventory relevant to the intent."""
    devices: List[Dict[str, Any]] = field(default_factory=list)
    interfaces: List[Dict[str, Any]] = field(default_factory=list)
    total_devices: int = 0
    source: str = "local_db"  # "local_db" | "netbox"
    source_detail: str = "Using local DB fallback for planned inventory."
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_INVENTORY,
            "local_db",
            "planned_inventory",
        )
    )


@dataclass
class PlannedTopologyContext:
    topology: Dict[str, Any] = field(default_factory=lambda: {"nodes": {}})
    links: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "local_db"
    source_detail: str = "Using local DB fallback for planned topology."
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
            "local_db",
            "planned_topology",
        )
    )


@dataclass
class TelemetryContext:
    """Recent telemetry samples for target devices."""
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "influxdb"
    query_range: str = "-15m"
    policy_name: str = "recent_latest"
    retrieval_reason: str = "default telemetry window"
    sample_strategy: str = "latest"
    aggregate_every: Optional[str] = None
    anomaly_baselines: List[Dict[str, Any]] = field(default_factory=list)
    change_markers: List[Dict[str, Any]] = field(default_factory=list)
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_TELEMETRY,
            "influxdb",
            "device_metrics",
        )
    )


@dataclass(frozen=True)
class TelemetryQueryPolicy:
    name: str
    query_range: str
    sample_strategy: str
    retrieval_reason: str
    aggregate_every: Optional[str] = None
    include_incident_baselines: bool = False
    include_change_markers: bool = False


@dataclass
class SDNContext:
    """SDN controller state for target devices."""
    controllers: List[Dict[str, Any]] = field(default_factory=list)
    topology_links: List[Dict[str, Any]] = field(default_factory=list)
    managed_devices: List[Dict[str, Any]] = field(default_factory=list)
    flow_rules: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "sdn_controllers"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_SDN,
            "sdn_controller",
            "sdn_state",
        )
    )


@dataclass
class ConfigHistoryContext:
    snapshots: List[Dict[str, Any]] = field(default_factory=list)
    latest_snapshot_at: Optional[str] = None
    devices_with_snapshots: int = 0
    devices_missing_snapshots: List[str] = field(default_factory=list)
    source: str = "config_snapshots"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_CONFIG,
            "config_snapshots",
            "device_configs",
        )
    )


@dataclass
class DeploymentContext:
    intents: List[Dict[str, Any]] = field(default_factory=list)
    change_requests: List[Dict[str, Any]] = field(default_factory=list)
    jobs: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "intent_store+deployment_pipeline"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_CONFIG,
            "intent_store+deployment_pipeline",
            "change_management",
        )
    )


@dataclass
class ThreatContext:
    security_events: List[Dict[str, Any]] = field(default_factory=list)
    threshold_alerts: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "security_events"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_INCIDENTS,
            "security_events",
            "security_alerts",
        )
    )


@dataclass
class FreshnessWarningsContext:
    warnings: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IncidentContext:
    incidents: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "incident_store"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_INCIDENTS,
            "incident_store",
            "incident_state",
        )
    )


@dataclass
class DriftContext:
    events: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "drift_store"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_DRIFT,
            "drift_store",
            "drift_state",
        )
    )


@dataclass
class KnowledgeContext:
    documents: List[Dict[str, Any]] = field(default_factory=list)
    total_chunks: int = 0
    source: str = "vector_store"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_KNOWLEDGE,
            "vector_store",
            "knowledge_documents",
        )
    )


@dataclass
class ContextBundle:
    """The unified context payload injected into LLM prompts."""
    inventory: InventoryContext = field(default_factory=InventoryContext)
    planned_topology: PlannedTopologyContext = field(default_factory=PlannedTopologyContext)
    telemetry: TelemetryContext = field(default_factory=TelemetryContext)
    sdn_state: SDNContext = field(default_factory=SDNContext)
    config_history: ConfigHistoryContext = field(default_factory=ConfigHistoryContext)
    deployment_status: DeploymentContext = field(default_factory=DeploymentContext)
    incidents: IncidentContext = field(default_factory=IncidentContext)
    threat_detections: ThreatContext = field(default_factory=ThreatContext)
    drift: DriftContext = field(default_factory=DriftContext)
    knowledge: KnowledgeContext = field(default_factory=KnowledgeContext)
    freshness_warnings: FreshnessWarningsContext = field(default_factory=FreshnessWarningsContext)
    semantics_map: Dict[str, Any] = field(default_factory=build_canonical_semantics_map)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scope_filter: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        """Render the bundle as a human-readable markdown block for the LLM."""
        lines: List[str] = ["## Network Context Bundle", f"*Generated: {self.timestamp}*", ""]
        lines.append("### Canonical Semantics")
        lines.append(build_canonical_semantics_prompt_text().replace("## Canonical Semantics\n", ""))
        lines.append("")

        warnings = self.freshness_warnings
        if warnings.warnings:
            lines.append("### Freshness Warnings")
            for warning in warnings.warnings[:12]:
                lines.append(
                    f"- [{warning.get('severity', 'warning')}] {warning.get('section', 'unknown')}: {warning.get('message', '')}"
                )
            lines.append("")

        # Inventory
        inv = self.inventory
        if inv.devices:
            lines.append(f"### Inventory ({inv.total_devices} devices, source: {inv.source})")
            lines.append(f"Source selection: {inv.source_detail}")
            lines.append(f"Freshness: {inv.freshness.to_prompt_text()}")
            for d in inv.devices[:20]:  # Cap at 20 to save tokens
                status = d.get("status", "unknown")
                lines.append(
                    f"- **{d.get('hostname', '?')}** ({d.get('management_ip', 'N/A')}) "
                    f"— {d.get('vendor', '?')} {d.get('os_version', '')} [{status}] presence={d.get('presence_state', 'unknown')}"
                )
            if inv.interfaces:
                lines.append(f"\n  Interfaces included: {len(inv.interfaces)}")
            # Devices by role with indices so LLM can resolve "access switch 1, 2, and 3" to exact hostnames
            by_role: Dict[str, List[str]] = {}
            _role_norm = {"access": "access", "access_switch": "access", "distribution": "distribution", "dist": "distribution", "core": "core", "core_router": "core"}
            for d in inv.devices[:30]:
                hostname = d.get("hostname") or "?"
                if hostname == "?":
                    continue
                raw = (d.get("role") or "").strip().lower() or _role_from_hostname(hostname)
                role = _role_norm.get(raw, raw) or "other"
                by_role.setdefault(role, []).append(hostname)
            if by_role:
                lines.append("\n**Devices by role (use when user says e.g. 'access switch 1, 2, and 3' — 1=first, 2=second, 3=third):**")
                for role_name in ("access", "distribution", "core", "other"):
                    if role_name not in by_role:
                        continue
                    hosts = by_role[role_name]
                    numbered = ", ".join(f"{i}. {h}" for i, h in enumerate(hosts, start=1))
                    lines.append(f"- {role_name.capitalize()}: {numbered}")
            lines.append("")

        planned = self.planned_topology
        planned_nodes = planned.topology.get("nodes", {}) if isinstance(planned.topology, dict) else {}
        if planned.links or planned_nodes:
            lines.append(
                f"### Planned Topology ({len(planned_nodes)} devices, {len(planned.links)} links, source: {planned.source})"
            )
            lines.append(f"Source selection: {planned.source_detail}")
            lines.append(f"Freshness: {planned.freshness.to_prompt_text()}")
            for link in planned.links[:15]:
                lines.append(
                    f"- {link.get('src_hostname', '?')}:{link.get('src_interface', '?')} "
                    f"-> {link.get('dst_hostname', '?')}:{link.get('dst_interface', '?')} presence={link.get('interface_presence_state', 'unknown')}"
                )
            lines.append("")

        # Telemetry
        tel = self.telemetry
        if tel.metrics or tel.anomaly_baselines or tel.change_markers:
            lines.append(
                f"### Telemetry (policy: {tel.policy_name}, range: {tel.query_range}, source: {tel.source})"
            )
            lines.append(f"Policy reason: {tel.retrieval_reason}")
            lines.append(f"Freshness: {tel.freshness.to_prompt_text()}")
            for m in tel.metrics[:15]:
                lines.append(
                    f"- {m.get('device', '?')}: "
                    f"{m.get('metric', '?')} = {m.get('value', '?')} "
                    f"({m.get('unit', '')}) at {m.get('timestamp', 'unknown')}"
                )
            if tel.anomaly_baselines:
                lines.append("Recent anomaly baselines:")
                for anomaly in tel.anomaly_baselines[:8]:
                    lines.append(
                        f"- {anomaly.get('incident_id', '?')}: {anomaly.get('anomaly_type', 'unknown')} current={anomaly.get('current', '?')} baseline={anomaly.get('baseline', '?')} threshold={anomaly.get('threshold', '?')} detected_at={anomaly.get('detected_at', 'unknown')}"
                    )
            if tel.change_markers:
                lines.append("Pre-change markers:")
                for marker in tel.change_markers[:8]:
                    lines.append(
                        f"- {marker.get('hostname', 'unknown')}: {marker.get('marker_type', 'change')} at {marker.get('captured_at', marker.get('updated_at', 'unknown'))}"
                    )
            lines.append("")

        # SDN State
        sdn = self.sdn_state
        if sdn.controllers or sdn.managed_devices or sdn.flow_rules:
            lines.append(f"### SDN State (source: {sdn.source})")
            lines.append(f"Freshness: {sdn.freshness.to_prompt_text()}")
            if sdn.controllers:
                for c in sdn.controllers:
                    lines.append(
                        f"- Controller: **{c.get('name', '?')}** ({c.get('type', '?')}) "
                        f"— {c.get('status', 'unknown')}"
                    )
            if sdn.managed_devices:
                lines.append(f"  Managed devices: {len(sdn.managed_devices)}")
            if sdn.topology_links:
                lines.append(f"  Topology links: {len(sdn.topology_links)}")
            if sdn.flow_rules:
                lines.append(f"  Flow rules sampled: {len(sdn.flow_rules)}")
            lines.append("")

        cfg = self.config_history
        if cfg.snapshots:
            lines.append(f"### Config History (source: {cfg.source})")
            lines.append(f"Freshness: {cfg.freshness.to_prompt_text()}")
            if cfg.latest_snapshot_at:
                lines.append(f"Latest snapshot: {cfg.latest_snapshot_at}")
            lines.append(
                f"Snapshot coverage: {cfg.devices_with_snapshots} devices with snapshots; missing={len(cfg.devices_missing_snapshots)}"
            )
            if cfg.devices_missing_snapshots:
                lines.append(f"Missing snapshots: {', '.join(cfg.devices_missing_snapshots[:10])}")
            for snapshot in cfg.snapshots[:10]:
                lines.append(
                    f"- {snapshot.get('hostname', '?')}: captured_at={snapshot.get('captured_at', 'unknown')} hash={snapshot.get('config_hash', '')[:12]}"
                )
            lines.append("")

        deployment = self.deployment_status
        if deployment.intents or deployment.change_requests or deployment.jobs:
            lines.append(f"### Deployment And Intent Status (source: {deployment.source})")
            lines.append(f"Freshness: {deployment.freshness.to_prompt_text()}")
            for intent in deployment.intents[:5]:
                lines.append(
                    f"- Intent {intent.get('id', '?')}: {intent.get('intent_type', 'unknown')} status={intent.get('status', 'unknown')} devices={', '.join(intent.get('resolved_devices', [])) or 'none'}"
                )
            for request in deployment.change_requests[:5]:
                lines.append(
                    f"- Change request {request.get('id', '?')}: {request.get('title', 'untitled')} status={request.get('status', 'unknown')}"
                )
            for job in deployment.jobs[:5]:
                lines.append(
                    f"- Job {job.get('id', '?')}: {job.get('hostname', 'unknown')} stage={job.get('stage', 'unknown')} status={job.get('status', 'unknown')}"
                )
            lines.append("")

        incidents = self.incidents
        if incidents.incidents:
            lines.append(f"### Incidents (source: {incidents.source})")
            lines.append(f"Freshness: {incidents.freshness.to_prompt_text()}")
            for incident in incidents.incidents[:10]:
                lines.append(
                    f"- {incident.get('id', '?')}: {incident.get('anomaly_type', 'unknown')} [{incident.get('severity', 'unknown')}] status={incident.get('status', 'unknown')}"
                )
            lines.append("")

        threats = self.threat_detections
        if threats.security_events or threats.threshold_alerts:
            lines.append(f"### Threat Detections (source: {threats.source})")
            lines.append(f"Freshness: {threats.freshness.to_prompt_text()}")
            for event in threats.security_events[:10]:
                lines.append(
                    f"- Threat {event.get('id', '?')}: {event.get('event_type', 'unknown')} [{event.get('severity', 'unknown')}] status={event.get('status', 'unknown')}"
                )
            for alert in threats.threshold_alerts[:10]:
                lines.append(
                    f"- Threshold alert {alert.get('id', '?')}: {alert.get('device', 'unknown')} {alert.get('metric', 'metric')}={alert.get('value', '?')} status={alert.get('status', 'unknown')}"
                )
            lines.append("")

        drift = self.drift
        if drift.events:
            lines.append(f"### Drift Findings (source: {drift.source})")
            lines.append(f"Freshness: {drift.freshness.to_prompt_text()}")
            for event in drift.events[:10]:
                lines.append(
                    f"- {event.get('hostname', '?')}: has_drift={event.get('has_drift')} severity={event.get('severity', 'unknown')}"
                )
            lines.append("")

        knowledge = self.knowledge
        if knowledge.documents or knowledge.total_chunks:
            lines.append(f"### Knowledge Base (source: {knowledge.source})")
            lines.append(f"Freshness: {knowledge.freshness.to_prompt_text()}")
            lines.append(f"Documents: {len(knowledge.documents)}; Chunks: {knowledge.total_chunks}")
            for document in knowledge.documents[:10]:
                lines.append(
                    f"- {document.get('doc_name', '?')} [{document.get('doc_type', 'unknown')} / {document.get('vendor', 'general')}] ingested_at={document.get('ingested_at', 'unknown')}"
                )
            lines.append("")

        if len(lines) <= 3:
            lines.append("*(No additional context available — using topology only)*")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class ContextBundleBuilder:
    """
    Constructs a fused ``ContextBundle`` from multiple network context sources.

    Dependencies are injected at init time — if a source is unavailable
    (e.g. InfluxDB not configured, no SDN controllers), that section is
    silently skipped, ensuring the bundle is always valid.
    """

    def __init__(self, db_manager=None, netbox_client=None):
        """
        Args:
            db_manager:    ``DatabaseManager`` for local device inventory.
            netbox_client: Optional ``NetBoxClient`` wrapper (pynetbox).
        """
        self._db = db_manager
        self._netbox = netbox_client

    # ---- Public API ----

    def build_bundle(
        self,
        device_hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
        include_inventory: bool = True,
        include_planned_topology: bool = True,
        include_telemetry: bool = True,
        include_sdn: bool = True,
        include_config: bool = True,
        include_deployment: bool = True,
        include_incidents: bool = True,
        include_threats: bool = True,
        include_drift: bool = True,
        include_knowledge: bool = True,
        include_freshness_warnings: bool = True,
        query: Optional[str] = None,
        routing_decision: Optional[QueryRoutingDecision] = None,
    ) -> ContextBundle:
        """
        Build a complete context bundle, optionally scoped to specific devices.

        Args:
            device_hostnames: List of hostnames to scope the bundle to (None = all).
            vendor_filter:    Filter devices by vendor string.
            include_telemetry: Whether to fetch telemetry metrics.
            include_sdn:      Whether to fetch SDN controller state.

        Returns:
            A populated ``ContextBundle`` ready for LLM injection.
        """
        bundle = ContextBundle(
            scope_filter={
                "device_hostnames": device_hostnames,
                "vendor_filter": vendor_filter,
            }
        )

        # 1. Inventory (NetBox preferred, local DB fallback)
        if include_inventory:
            bundle.inventory = self._fetch_inventory(device_hostnames, vendor_filter)

        if include_planned_topology:
            bundle.planned_topology = self._fetch_planned_topology(device_hostnames)

        # 2. Telemetry (InfluxDB)
        if include_telemetry:
            device_ips = self._resolve_device_ips(bundle.inventory.devices, device_hostnames)
            bundle.telemetry = self._fetch_telemetry(
                device_ips,
                hostnames=device_hostnames,
                query=query,
                routing_decision=routing_decision,
            )

        # 3. SDN State
        if include_sdn:
            bundle.sdn_state = self._fetch_sdn_state(device_hostnames)

        if include_config:
            bundle.config_history = self._fetch_config_history(device_hostnames)

        if include_deployment:
            bundle.deployment_status = self._fetch_deployment_status(device_hostnames)

        if include_incidents:
            bundle.incidents = self._fetch_incidents(device_hostnames)

        if include_threats:
            bundle.threat_detections = self._fetch_threat_detections(device_hostnames)

        if include_drift:
            bundle.drift = self._fetch_drift(device_hostnames)

        if include_knowledge:
            bundle.knowledge = self._fetch_knowledge_summary()

        if include_freshness_warnings:
            bundle.freshness_warnings = self._build_freshness_warnings(
                bundle,
                include_inventory=include_inventory,
                include_planned_topology=include_planned_topology,
                include_telemetry=include_telemetry,
                include_sdn=include_sdn,
                include_config=include_config,
                include_deployment=include_deployment,
                include_incidents=include_incidents,
                include_threats=include_threats,
                include_drift=include_drift,
                include_knowledge=include_knowledge,
            )

        self._apply_canonical_semantics(bundle)

        return bundle

    def _apply_canonical_semantics(self, bundle: ContextBundle) -> None:
        live_nodes = {}
        if self._db is not None:
            topo = self._db.get_topology()
            live_nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}

        device_matrix = build_canonical_device_matrix(
            bundle.inventory.devices,
            live_nodes,
            inventory_source=bundle.inventory.source,
        )
        semantics_index = device_state_index(device_matrix)
        bundle.inventory.devices = annotate_inventory_records(bundle.inventory.devices, live_nodes, bundle.inventory.source)
        bundle.inventory.interfaces = annotate_inventory_interfaces(bundle.inventory.interfaces, semantics_index)
        bundle.planned_topology.links = annotate_planned_links(bundle.planned_topology.links, semantics_index)
        bundle.semantics_map = build_canonical_semantics_map()

    def _resolve_device_ips(
        self,
        inventory_devices: List[Dict[str, Any]],
        hostnames: Optional[List[str]] = None,
    ) -> List[str]:
        device_ips = [
            d.get("management_ip")
            for d in inventory_devices
            if d.get("management_ip")
        ]

        if device_ips or self._db is None:
            return device_ips

        topo = self._db.get_topology()
        nodes = topo.get("nodes", {}) if isinstance(topo, dict) else {}
        for hostname, data in nodes.items():
            if hostnames and hostname not in hostnames:
                continue
            if isinstance(data, dict) and data.get("mgmt_ip"):
                device_ips.append(data["mgmt_ip"])

        return device_ips

    # ---- Inventory (UC06) ----

    def _get_netbox_client(self):
        if self._netbox is not None:
            return self._netbox

        try:
            from netbox.client import NetBoxClient

            client = NetBoxClient.get_client()
            self._netbox = client
            return client
        except Exception:
            return None

    def _fetch_inventory(
        self,
        hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
    ) -> InventoryContext:
        """Fetch device inventory from NetBox (if available) or local DB."""
        ctx = InventoryContext()

        # Try NetBox first
        netbox_client = self._get_netbox_client()
        if netbox_client is not None and getattr(netbox_client, "api", None):
            try:
                ctx = self._fetch_netbox_inventory(hostnames, vendor_filter)
                if ctx.devices or self._db is None:
                    ctx.source = "netbox"
                    ctx.source_detail = "Using NetBox as the source of truth for planned inventory."
                    return ctx
                logger.warning("NetBox inventory returned no scoped devices, falling back to local DB planned inventory.")
            except Exception as e:
                logger.warning("NetBox inventory fetch failed, falling back to local DB: %s", e)

        # Fallback to local DB
        if self._db is not None:
            try:
                ctx = self._fetch_local_inventory(hostnames, vendor_filter)
                ctx.source = "local_db"
                ctx.source_detail = "NetBox unavailable or not authoritative for this query; using local DB fallback for planned inventory."
            except Exception as e:
                logger.warning("Local DB inventory fetch failed: %s", e)

        return ctx

    def _netbox_vendor_from_device(self, dev: Any) -> str:
        """Derive our vendor slug from NetBox device: prefer platform slug, else manufacturer (e.g. Cisco -> cisco_ios)."""
        platform = getattr(dev, "platform", None)
        if platform:
            slug = getattr(platform, "slug", None) or str(platform).strip()
            if slug and slug in ("cisco_ios", "huawei_vrp", "juniper_junos"):
                return slug
        # Fallback: manufacturer (NetBox often has this when Platform is unset on Planned devices)
        device_type = getattr(dev, "device_type", None)
        if device_type:
            mfr = getattr(device_type, "manufacturer", None)
            if mfr:
                name = (getattr(mfr, "name", None) or str(mfr)).lower()
                if "cisco" in name:
                    return "cisco_ios"
                if "huawei" in name:
                    return "huawei_vrp"
                if "juniper" in name:
                    return "juniper_junos"
        return "unknown"

    def _fetch_netbox_inventory(
        self,
        hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
    ) -> InventoryContext:
        """Query NetBox via pynetbox for device inventory."""
        ctx = InventoryContext(source="netbox")
        last_updated_candidates: List[str] = []

        client = self._get_netbox_client()
        if client is None or not getattr(client, "api", None):
            return ctx

        try:
            nb = client.api

            # Fetch devices
            filters: Dict[str, Any] = {}
            if hostnames:
                # pynetbox/NetBox API: use name__in for multiple names
                filters["name__in"] = hostnames
            devices = list(nb.dcim.devices.filter(**filters) if filters else nb.dcim.devices.all())

            for dev in devices:
                vendor_slug = self._netbox_vendor_from_device(dev)
                # Apply vendor_filter using platform, manufacturer, or derived slug (so we don't drop devices when Platform is empty)
                if vendor_filter:
                    vf = vendor_filter.lower()
                    platform_str = str(getattr(dev, "platform", "") or "").lower()
                    device_type = getattr(dev, "device_type", None)
                    mfr_str = ""
                    if device_type:
                        mfr = getattr(device_type, "manufacturer", None)
                        if mfr:
                            mfr_str = (getattr(mfr, "name", None) or str(mfr)).lower()
                    if vf not in platform_str and vf not in mfr_str and vf not in vendor_slug:
                        continue
                last_updated = getattr(dev, "last_updated", None)
                if last_updated:
                    last_updated_candidates.append(str(last_updated))
                primary_ip = getattr(dev, "primary_ip4", None) or getattr(dev, "primary_ip", None)
                role = getattr(dev, "role", None)
                platform = getattr(dev, "platform", None)
                device_type = getattr(dev, "device_type", None)
                ctx.devices.append({
                    "hostname": str(dev.name),
                    "management_ip": str(primary_ip).split("/")[0] if primary_ip else None,
                    "vendor": vendor_slug,
                    "model": str(getattr(device_type, "model", device_type or "")) or None,
                    "role": str(getattr(role, "slug", role or "")) or None,
                    "site": str(dev.site) if dev.site else None,
                    "status": str(getattr(dev.status, "value", dev.status)) if dev.status else "unknown",
                    "serial": str(dev.serial) if dev.serial else None,
                })

            ctx.total_devices = len(ctx.devices)

            # Fetch interfaces for scoped devices
            if hostnames and len(hostnames) <= 10:
                for dev in devices:
                    try:
                        for iface in client.get_interfaces_for_device(dev.id)[:20]:
                            ctx.interfaces.append({
                                "device": str(dev.name),
                                "name": iface.get("name"),
                                "ip_address": iface.get("ip_address"),
                                "subnet_mask": iface.get("subnet_mask"),
                                "type": iface.get("type"),
                                "interface_type": iface.get("interface_type") or iface.get("type"),
                                "is_management": bool(iface.get("is_management")),
                                "mac_address": iface.get("mac_address"),
                            })
                    except Exception:
                        pass

        except Exception as e:
            logger.warning("NetBox inventory query failed: %s", e)

        ctx.freshness = compute_freshness(
            domain=CONTEXT_DOMAIN_INVENTORY,
            captured_at=max(last_updated_candidates) if last_updated_candidates else None,
            ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source="netbox",
            scope="planned_inventory",
            confidence=0.95 if ctx.devices else 0.0,
        )

        return ctx

    def _fetch_planned_topology(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> PlannedTopologyContext:
        ctx = PlannedTopologyContext()
        netbox_client = self._get_netbox_client()

        if self._db is not None and netbox_client is not None and getattr(netbox_client, "api", None):
            try:
                from netbox.sync_topology import TopologySyncer

                planned = TopologySyncer(self._db).get_planned_topology()
                nodes = planned.get("nodes", {}) if isinstance(planned, dict) else {}
                if hostnames:
                    allowed = set(hostnames)
                    nodes = {name: data for name, data in nodes.items() if name in allowed}
                if nodes:
                    ctx.topology = {**planned, "nodes": nodes}
                    ctx.links = self._planned_topology_links_from_nodes(nodes)
                    captured_at = planned.get("timestamp")
                    ctx.source = "netbox"
                    ctx.source_detail = "Using NetBox planned topology as the source of truth."
                    ctx.freshness = compute_freshness(
                        domain=CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
                        captured_at=captured_at,
                        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        source="netbox",
                        scope="planned_topology",
                        confidence=0.9 if ctx.links or nodes else 0.0,
                    )
                    return ctx
            except Exception as exc:
                logger.warning("NetBox planned topology fetch failed, falling back to local DB: %s", exc)

        if self._db is None:
            return ctx

        try:
            with self._db._connect() as conn:
                rows = conn.execute(
                    "SELECT sd.hostname AS src_hostname, si.name AS src_interface, dd.hostname AS dst_hostname, di.name AS dst_interface, l.discovery_protocol, l.updated_at "
                    "FROM links l "
                    "LEFT JOIN devices sd ON sd.id = l.src_device_id "
                    "LEFT JOIN devices dd ON dd.id = l.dst_device_id "
                    "LEFT JOIN interfaces si ON si.id = l.src_interface_id "
                    "LEFT JOIN interfaces di ON di.id = l.dst_interface_id "
                    "ORDER BY sd.hostname, dd.hostname"
                ).fetchall()

            links = [dict(row) for row in rows]
            if hostnames:
                allowed = set(hostnames)
                links = [
                    link for link in links
                    if link.get("src_hostname") in allowed or link.get("dst_hostname") in allowed
                ]

            nodes: Dict[str, Dict[str, Any]] = {}
            for link in links:
                src_hostname = link.get("src_hostname")
                dst_hostname = link.get("dst_hostname")
                if src_hostname:
                    nodes.setdefault(src_hostname, {"connectedNode": [], "edgeLink": {}})
                if dst_hostname:
                    nodes.setdefault(dst_hostname, {"connectedNode": [], "edgeLink": {}})

            ctx.topology = {"nodes": nodes}
            ctx.links = links
            ctx.source = "local_db"
            ctx.source_detail = "NetBox unavailable or missing planned topology data; using local DB fallback for planned topology."
            captured_at = max((link.get("updated_at") for link in links if link.get("updated_at")), default=None)
            ctx.freshness = compute_freshness(
                domain=CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
                captured_at=captured_at,
                ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source="local_db",
                scope="planned_topology",
                confidence=0.75 if links else 0.0,
            )
        except Exception as exc:
            logger.debug("Planned topology local fallback failed: %s", exc)

        return ctx

    @staticmethod
    def _planned_topology_links_from_nodes(nodes: Dict[str, Any]) -> List[Dict[str, Any]]:
        links: List[Dict[str, Any]] = []
        seen = set()

        for src_hostname, node in nodes.items():
            if not isinstance(node, dict):
                continue
            for src_interface, edge in (node.get("edgeLink") or {}).items():
                if not isinstance(edge, dict):
                    continue
                dst_hostname = edge.get("remote_node")
                dst_interface = edge.get("remote_interface")
                if not dst_hostname:
                    continue
                key = tuple(sorted((str(src_hostname), str(dst_hostname)))) + tuple(
                    sorted((str(src_interface), str(dst_interface or "?")))
                )
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        "src_hostname": src_hostname,
                        "src_interface": src_interface,
                        "dst_hostname": dst_hostname,
                        "dst_interface": dst_interface,
                    }
                )

        return sorted(
            links,
            key=lambda link: (
                str(link.get("src_hostname") or ""),
                str(link.get("dst_hostname") or ""),
                str(link.get("src_interface") or ""),
            ),
        )

    def _fetch_local_inventory(
        self,
        hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
    ) -> InventoryContext:
        """Query local SQLite device inventory."""
        ctx = InventoryContext(source="local_db")

        with self._db._connect() as conn:
            where_parts: List[str] = []
            params: List[Any] = []

            if hostnames:
                placeholders = ",".join("?" for _ in hostnames)
                where_parts.append(f"hostname IN ({placeholders})")
                params.extend(hostnames)
            if vendor_filter:
                where_parts.append("vendor = ?")
                params.append(vendor_filter)

            where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

            rows = conn.execute(
                f"SELECT hostname, management_ip, vendor, model, os_version, "
                f"serial_number, status, role FROM devices{where_sql} LIMIT 50",
                params,
            ).fetchall()

            last_updated = conn.execute(
                f"SELECT MAX(updated_at) AS last_updated FROM devices{where_sql}",
                params,
            ).fetchone()

            for r in rows:
                ctx.devices.append(dict(r))

            ctx.total_devices = len(ctx.devices)

            # Interfaces for scoped devices
            if hostnames and len(hostnames) <= 10:
                for host in hostnames:
                    ifaces = conn.execute(
                        "SELECT i.name, i.ip_address, i.subnet_mask, i.mac_address, i.status, "
                        "i.interface_type, i.is_management "
                        "FROM interfaces i JOIN devices d ON i.device_id = d.id "
                        "WHERE d.hostname = ? LIMIT 20",
                        (host,),
                    ).fetchall()
                    for iface in ifaces:
                        ctx.interfaces.append({"device": host, **dict(iface)})

        ctx.freshness = compute_freshness(
            domain=CONTEXT_DOMAIN_INVENTORY,
            captured_at=(last_updated["last_updated"] if last_updated else None),
            ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source="local_db",
            scope="planned_inventory",
            confidence=0.75 if ctx.devices else 0.0,
        )

        return ctx

    # ---- Telemetry (UC07) ----

    def _extract_explicit_telemetry_range(self, query: str) -> Optional[str]:
        normalized = (query or "").lower()
        match = re.search(r"(?:last|past)\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)", normalized)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("m"):
                return f"-{amount}m"
            if unit.startswith("h"):
                return f"-{amount}h"
            if unit.startswith("d"):
                return f"-{amount}d"
            if unit.startswith("w"):
                return f"-{amount}w"

        aliases = {
            "today": "-24h",
            "last day": "-24h",
            "last 24h": "-24h",
            "last week": "-7d",
            "this week": "-7d",
            "last hour": "-1h",
        }
        for phrase, value in aliases.items():
            if phrase in normalized:
                return value
        return None

    @staticmethod
    def _suggest_aggregate_window(query_range: str) -> str:
        if query_range.endswith("w") or query_range.endswith("d"):
            return "30m"
        if query_range.endswith("h"):
            hours = int(query_range[1:-1]) if query_range[1:-1].isdigit() else 1
            if hours >= 12:
                return "30m"
            return "5m" if hours <= 2 else "15m"
        return "1m"

    def _select_telemetry_policy(
        self,
        query: Optional[str] = None,
        routing_decision: Optional[QueryRoutingDecision] = None,
    ) -> TelemetryQueryPolicy:
        normalized_query = (query or "").lower()
        explicit_range = self._extract_explicit_telemetry_range(normalized_query)
        trend_triggers = (
            "trend",
            "over time",
            "history",
            "historical",
            "last ",
            "past ",
            "baseline",
            "over the",
        )
        incident_triggers = (
            "incident",
            "anomaly",
            "alert",
            "breach",
            "spike",
            "latency",
            "packet loss",
            "errors",
            "root cause",
        )

        query_class = getattr(routing_decision, "query_class", QueryClass.GENERAL.value)

        if query_class == QueryClass.LIVE_ONLY.value:
            return TelemetryQueryPolicy(
                name="live_now",
                query_range=explicit_range or "-5m",
                sample_strategy="latest",
                retrieval_reason="live operational query should prefer the shortest recent telemetry window",
                aggregate_every="1m",
            )

        if query_class == QueryClass.TROUBLESHOOTING.value or any(trigger in normalized_query for trigger in incident_triggers):
            query_range = explicit_range or "-60m"
            return TelemetryQueryPolicy(
                name="incident_focus",
                query_range=query_range,
                sample_strategy="incident_focus",
                retrieval_reason="incident-style troubleshooting should pull recent anomalies and pre-change baselines",
                aggregate_every=self._suggest_aggregate_window(query_range),
                include_incident_baselines=True,
                include_change_markers=True,
            )

        if any(trigger in normalized_query for trigger in trend_triggers):
            query_range = explicit_range or "-6h"
            return TelemetryQueryPolicy(
                name="trend_analysis",
                query_range=query_range,
                sample_strategy="trend",
                retrieval_reason="trend questions should use a longer telemetry horizon with aggregated samples",
                aggregate_every=self._suggest_aggregate_window(query_range),
                include_change_markers=True,
            )

        if query_class == QueryClass.CHANGE_PLANNING.value:
            return TelemetryQueryPolicy(
                name="pre_change_check",
                query_range=explicit_range or "-30m",
                sample_strategy="latest",
                retrieval_reason="change-planning should confirm recent device health before execution",
                aggregate_every="5m",
                include_change_markers=True,
            )

        return TelemetryQueryPolicy(
            name="recent_latest",
            query_range=explicit_range or "-15m",
            sample_strategy="latest",
            retrieval_reason="default telemetry policy uses a recent operational window",
            aggregate_every="5m",
        )

    def _fetch_telemetry(
        self,
        device_ips: List[str],
        hostnames: Optional[List[str]] = None,
        query: Optional[str] = None,
        routing_decision: Optional[QueryRoutingDecision] = None,
    ) -> TelemetryContext:
        """Fetch latest telemetry from InfluxDB for the given device IPs."""
        ctx = TelemetryContext()
        policy = self._select_telemetry_policy(query=query, routing_decision=routing_decision)
        ctx.query_range = policy.query_range
        ctx.policy_name = policy.name
        ctx.retrieval_reason = policy.retrieval_reason
        ctx.sample_strategy = policy.sample_strategy
        ctx.aggregate_every = policy.aggregate_every

        if policy.include_incident_baselines:
            ctx.anomaly_baselines = self._fetch_incident_telemetry_baselines(hostnames)

        if policy.include_change_markers:
            ctx.change_markers = self._fetch_pre_change_markers(hostnames)

        if not INFLUXDB_TOKEN or not device_ips:
            return ctx

        try:
            # Build Flux query for target devices
            ip_filter = " or ".join(f'r.agent_host == "{ip}"' for ip in device_ips[:10])
            if policy.sample_strategy == "trend":
                flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {policy.query_range})
  |> filter(fn: (r) => r._measurement == "snmp" or r._measurement == "interface" or r._measurement == "cpu" or r._measurement == "mem")
  |> filter(fn: (r) => {ip_filter})
  |> aggregateWindow(every: {policy.aggregate_every or "5m"}, fn: mean, createEmpty: false)
  |> tail(n: 60)
'''
            elif policy.sample_strategy == "incident_focus":
                flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {policy.query_range})
  |> filter(fn: (r) => r._measurement == "snmp" or r._measurement == "interface" or r._measurement == "cpu" or r._measurement == "mem")
  |> filter(fn: (r) => {ip_filter})
  |> aggregateWindow(every: {policy.aggregate_every or "5m"}, fn: last, createEmpty: false)
  |> tail(n: 40)
'''
            else:
                flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {policy.query_range})
  |> filter(fn: (r) => r._measurement == "snmp" or r._measurement == "interface" or r._measurement == "cpu" or r._measurement == "mem")
  |> filter(fn: (r) => {ip_filter})
  |> last()
  |> limit(n: 50)
'''
            body = flux_query.encode("utf-8")
            req = urllib.request.Request(
                f"{INFLUXDB_URL}/api/v2/query?org={INFLUXDB_ORG}",
                data=body,
                headers={
                    "Authorization": f"Token {INFLUXDB_TOKEN}",
                    "Content-Type": "application/vnd.flux",
                    "Accept": "application/csv",
                },
            )

            with urllib.request.urlopen(req, timeout=5) as resp:
                csv_data = resp.read().decode("utf-8")
                data_lines = [line for line in csv_data.splitlines() if line and not line.startswith("#")]
                reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
                latest_timestamp: Optional[str] = None
                for row in reader:
                    if not row:
                        continue
                    metric_time = row.get("_time") or row.get("_stop")
                    if metric_time and (latest_timestamp is None or metric_time > latest_timestamp):
                        latest_timestamp = metric_time
                    ctx.metrics.append({
                        "device": row.get("agent_host") or row.get("host") or row.get("source") or "?",
                        "metric": row.get("_field") or row.get("_measurement") or "?",
                        "value": row.get("_value") or "?",
                        "timestamp": metric_time,
                        "unit": row.get("unit") or "",
                    })

                ctx.freshness = compute_freshness(
                    domain=CONTEXT_DOMAIN_TELEMETRY,
                    captured_at=latest_timestamp,
                    ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    source="influxdb",
                    scope="device_metrics",
                    confidence=0.9 if ctx.metrics else 0.0,
                )

        except Exception as e:
            logger.debug("Telemetry fetch failed (non-critical): %s", e)

        return ctx

    def _fetch_incident_telemetry_baselines(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        baselines: List[Dict[str, Any]] = []
        try:
            from incidents.store import IncidentStore

            for row in IncidentStore().list_all(limit=10):
                affected_devices = row.get("affected_devices") or []
                if isinstance(affected_devices, str):
                    try:
                        affected_devices = json.loads(affected_devices)
                    except Exception:
                        affected_devices = [affected_devices]
                if hostnames and not set(affected_devices).intersection(hostnames):
                    continue
                baselines.append(
                    {
                        "incident_id": row.get("id"),
                        "anomaly_type": row.get("anomaly_type"),
                        "current": row.get("metrics_current"),
                        "baseline": row.get("metrics_baseline"),
                        "threshold": row.get("metrics_threshold"),
                        "detected_at": row.get("detected_at") or row.get("created_at") or row.get("updated_at"),
                        "telemetry_snapshot": row.get("telemetry_snapshot") or {},
                    }
                )
        except Exception as exc:
            logger.debug("Incident baseline fetch failed (non-critical): %s", exc)
        return baselines

    def _fetch_pre_change_markers(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        markers: List[Dict[str, Any]] = []
        if self._db is None:
            return markers

        try:
            with self._db._connect() as conn:
                job_rows = conn.execute(
                    "SELECT j.id, j.updated_at, j.status, j.stage, d.hostname "
                    "FROM jobs j LEFT JOIN devices d ON d.id = j.device_id "
                    "ORDER BY j.updated_at DESC LIMIT 10"
                ).fetchall()

            for row in job_rows:
                record = dict(row)
                if hostnames and record.get("hostname") not in hostnames:
                    continue
                markers.append(
                    {
                        "marker_type": f"deployment_{record.get('stage', 'change')}",
                        "hostname": record.get("hostname"),
                        "updated_at": record.get("updated_at"),
                        "status": record.get("status"),
                    }
                )

            for device in self._db.get_all_devices():
                if hostnames and device.hostname not in hostnames:
                    continue
                history = self._db.get_config_history(device.id, limit=1) if device.id else []
                if not history:
                    continue
                markers.append(
                    {
                        "marker_type": "config_snapshot",
                        "hostname": device.hostname,
                        "captured_at": history[0].captured_at,
                    }
                )
        except Exception as exc:
            logger.debug("Pre-change marker fetch failed (non-critical): %s", exc)

        return markers[:10]

    # ---- SDN State (UC08) ----

    def _fetch_sdn_state(self, hostnames: Optional[List[str]] = None) -> SDNContext:
        """Fetch SDN controller state for the given device scope."""
        ctx = SDNContext()

        if self._db is None:
            return ctx

        try:
            with self._db._connect() as conn:
                # Check if sdn_controllers table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='sdn_controllers'"
                ).fetchone()

                if not table_check:
                    return ctx

                controllers = conn.execute(
                    "SELECT id, name, controller_type, url, status, managed_device_count, last_synced, updated_at "
                    "FROM sdn_controllers WHERE status = 'online'"
                ).fetchall()
                latest_sync: Optional[str] = None

                for ctrl in controllers:
                    ctrl_dict = dict(ctrl)
                    controller_sync = ctrl_dict.get("last_synced") or ctrl_dict.get("updated_at")
                    if controller_sync and (latest_sync is None or controller_sync > latest_sync):
                        latest_sync = controller_sync
                    ctx.controllers.append({
                        "name": ctrl_dict.get("name"),
                        "type": ctrl_dict.get("controller_type"),
                        "url": ctrl_dict.get("url"),
                        "status": ctrl_dict.get("status"),
                        "managed_devices": ctrl_dict.get("managed_device_count", 0),
                    })

                    # Try to fetch live topology from controller
                    try:
                        topo_data = self._query_sdn_controller(ctrl_dict, hostnames)
                        if topo_data:
                            ctx.managed_devices.extend(topo_data.get("devices", []))
                            ctx.topology_links.extend(topo_data.get("links", []))
                            ctx.flow_rules.extend(topo_data.get("flows", [])[:10])
                    except Exception as e:
                        logger.debug("SDN controller query failed for %s: %s", ctrl_dict.get("name"), e)

                ctx.freshness = compute_freshness(
                    domain=CONTEXT_DOMAIN_SDN,
                    captured_at=latest_sync,
                    ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    source="sdn_controller",
                    scope="sdn_state",
                    confidence=0.85 if ctx.controllers else 0.0,
                )

        except Exception as e:
            logger.debug("SDN state fetch failed (non-critical): %s", e)

        return ctx

    def _query_sdn_controller(
        self, controller: Dict[str, Any], hostnames: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Query a single SDN controller for topology/flows via its driver."""
        try:
            from drivers.factory import DriverFactory

            ctrl_type = controller.get("controller_type", "")
            driver = DriverFactory.create_driver(
                driver_type=ctrl_type,
                host=controller.get("url", ""),
                username="",  # Controllers use URL-embedded auth or separate creds
                password="",
            )

            result: Dict[str, Any] = {"devices": [], "links": [], "flows": []}

            if hasattr(driver, "get_managed_devices"):
                devices = driver.get_managed_devices()
                result["devices"] = devices if isinstance(devices, list) else []

            if hasattr(driver, "get_topology_links"):
                links = driver.get_topology_links()
                result["links"] = links if isinstance(links, list) else []

            return result

        except Exception as e:
            logger.debug("SDN driver query failed: %s", e)
            return None

    def _fetch_config_history(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> ConfigHistoryContext:
        ctx = ConfigHistoryContext()
        if self._db is None:
            return ctx

        devices = self._db.get_all_devices()
        latest_captured: Optional[str] = None
        for device in devices:
            if hostnames and device.hostname not in hostnames:
                continue
            history = self._db.get_config_history(device.id, limit=1) if device.id else []
            if not history:
                ctx.devices_missing_snapshots.append(device.hostname)
                continue
            snapshot = history[0]
            captured_at = snapshot.captured_at
            if captured_at and (latest_captured is None or captured_at > latest_captured):
                latest_captured = captured_at
            ctx.snapshots.append(
                {
                    "hostname": device.hostname,
                    "device_id": device.id,
                    "captured_at": captured_at,
                    "config_hash": snapshot.config_hash,
                }
            )

        ctx.latest_snapshot_at = latest_captured
        ctx.devices_with_snapshots = len(ctx.snapshots)

        ctx.freshness = compute_freshness(
            domain=CONTEXT_DOMAIN_CONFIG,
            captured_at=latest_captured,
            ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source="config_snapshots",
            scope="device_configs",
            confidence=0.9 if ctx.snapshots else 0.0,
        )
        return ctx

    def _fetch_deployment_status(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> DeploymentContext:
        ctx = DeploymentContext()
        if self._db is None:
            return ctx

        latest_timestamp: Optional[str] = None
        try:
            with self._db._connect() as conn:
                intent_rows = conn.execute(
                    "SELECT id, intent_type, status, resolved_devices, created_at FROM intents ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
                request_rows = conn.execute(
                    "SELECT id, title, status, requester, created_at, updated_at FROM change_requests ORDER BY updated_at DESC LIMIT 10"
                ).fetchall()
                job_rows = conn.execute(
                    "SELECT j.id, j.status, j.stage, j.updated_at, d.hostname, cr.title AS request_title "
                    "FROM jobs j "
                    "LEFT JOIN devices d ON d.id = j.device_id "
                    "LEFT JOIN change_requests cr ON cr.id = j.request_id "
                    "ORDER BY j.updated_at DESC LIMIT 10"
                ).fetchall()

            for row in intent_rows:
                record = dict(row)
                resolved_devices = json.loads(record.get("resolved_devices") or "[]")
                if hostnames and not set(resolved_devices).intersection(hostnames):
                    continue
                record["resolved_devices"] = resolved_devices
                ctx.intents.append(record)
                created_at = record.get("created_at")
                if created_at and (latest_timestamp is None or created_at > latest_timestamp):
                    latest_timestamp = created_at

            for row in request_rows:
                record = dict(row)
                ctx.change_requests.append(record)
                updated_at = record.get("updated_at") or record.get("created_at")
                if updated_at and (latest_timestamp is None or updated_at > latest_timestamp):
                    latest_timestamp = updated_at

            for row in job_rows:
                record = dict(row)
                if hostnames and record.get("hostname") not in hostnames:
                    continue
                ctx.jobs.append(record)
                updated_at = record.get("updated_at")
                if updated_at and (latest_timestamp is None or updated_at > latest_timestamp):
                    latest_timestamp = updated_at

            ctx.freshness = compute_freshness(
                domain=CONTEXT_DOMAIN_CONFIG,
                captured_at=latest_timestamp,
                ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source=ctx.source,
                scope="change_management",
                confidence=0.85 if (ctx.intents or ctx.change_requests or ctx.jobs) else 0.0,
            )
        except Exception as exc:
            logger.debug("Deployment status fetch failed (non-critical): %s", exc)

        return ctx

    def _fetch_threat_detections(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> ThreatContext:
        ctx = ThreatContext()
        if self._db is None:
            return ctx

        latest_timestamp: Optional[str] = None
        try:
            with self._db._connect() as conn:
                event_rows = conn.execute(
                    "SELECT se.id, se.event_type, se.severity, se.source_ip, se.dest_ip, se.description, se.status, se.created_at, d.hostname "
                    "FROM security_events se "
                    "LEFT JOIN devices d ON d.id = se.device_id "
                    "ORDER BY se.created_at DESC LIMIT 10"
                ).fetchall()
                alert_rows = conn.execute(
                    "SELECT id, device, metric, value, threshold, severity, status, incident_id, created_at "
                    "FROM threshold_alerts ORDER BY created_at DESC LIMIT 10"
                ).fetchall()

            for row in event_rows:
                record = dict(row)
                if hostnames and record.get("hostname") and record.get("hostname") not in hostnames:
                    continue
                ctx.security_events.append(record)
                created_at = record.get("created_at")
                if created_at and (latest_timestamp is None or created_at > latest_timestamp):
                    latest_timestamp = created_at

            for row in alert_rows:
                record = dict(row)
                if hostnames and record.get("device") and record.get("device") not in hostnames:
                    continue
                ctx.threshold_alerts.append(record)
                created_at = record.get("created_at")
                if created_at and (latest_timestamp is None or created_at > latest_timestamp):
                    latest_timestamp = created_at

            ctx.freshness = compute_freshness(
                domain=CONTEXT_DOMAIN_INCIDENTS,
                captured_at=latest_timestamp,
                ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source=ctx.source,
                scope="security_alerts",
                confidence=0.85 if (ctx.security_events or ctx.threshold_alerts) else 0.0,
            )
        except Exception as exc:
            logger.debug("Threat detection fetch failed (non-critical): %s", exc)

        return ctx

    def _fetch_incidents(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> IncidentContext:
        ctx = IncidentContext()
        try:
            from incidents.store import IncidentStore

            incident_db_path = getattr(self._db, "_db_path", None) if self._db is not None else None
            rows = IncidentStore(db_path=incident_db_path or "db/netauto.db").list_all(limit=20)
            latest_updated: Optional[str] = None
            for row in rows:
                affected_devices = json.loads(row.get("affected_devices") or "[]")
                if hostnames and not set(affected_devices).intersection(hostnames):
                    continue
                updated_at = row.get("updated_at") or row.get("created_at")
                if updated_at and (latest_updated is None or updated_at > latest_updated):
                    latest_updated = updated_at
                ctx.incidents.append(
                    {
                        "id": row.get("id"),
                        "anomaly_type": row.get("anomaly_type"),
                        "severity": row.get("severity"),
                        "status": row.get("status"),
                        "affected_devices": affected_devices,
                        "updated_at": updated_at,
                    }
                )

            ctx.freshness = compute_freshness(
                domain=CONTEXT_DOMAIN_INCIDENTS,
                captured_at=latest_updated,
                ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source="incident_store",
                scope="incident_state",
                confidence=0.9 if ctx.incidents else 0.0,
            )
        except Exception as exc:
            logger.debug("Incident context fetch failed (non-critical): %s", exc)
        return ctx

    def _fetch_drift(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> DriftContext:
        ctx = DriftContext()
        if self._db is None:
            return ctx

        try:
            events = self._db.get_drift_events(limit=20)
            latest_timestamp: Optional[str] = None
            for event in events:
                if hostnames and event.hostname not in hostnames:
                    continue
                if event.timestamp and (latest_timestamp is None or event.timestamp > latest_timestamp):
                    latest_timestamp = event.timestamp
                ctx.events.append(
                    {
                        "device_id": event.device_id,
                        "hostname": event.hostname,
                        "has_drift": event.has_drift,
                        "severity": event.severity,
                        "timestamp": event.timestamp,
                    }
                )

            ctx.freshness = compute_freshness(
                domain=CONTEXT_DOMAIN_DRIFT,
                captured_at=latest_timestamp,
                ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source="drift_store",
                scope="drift_state",
                confidence=0.9 if ctx.events else 0.0,
            )
        except Exception as exc:
            logger.debug("Drift context fetch failed (non-critical): %s", exc)
        return ctx

    def _fetch_knowledge_summary(self) -> KnowledgeContext:
        ctx = KnowledgeContext()
        try:
            from llm.vector_store import KnowledgeVectorStore

            store = KnowledgeVectorStore(embedder=None)
            if not store.available:
                return ctx

            docs = store.list_documents()
            ctx.documents = docs[:20]
            ctx.total_chunks = store.count()
            latest_ingested = max((doc.get("ingested_at") for doc in docs if doc.get("ingested_at")), default=None)
            ctx.freshness = compute_freshness(
                domain=CONTEXT_DOMAIN_KNOWLEDGE,
                captured_at=latest_ingested,
                ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source="vector_store",
                scope="knowledge_documents",
                confidence=0.95 if docs else 0.0,
            )
        except Exception as exc:
            logger.debug("Knowledge summary fetch failed (non-critical): %s", exc)
        return ctx

    def _build_freshness_warnings(
        self,
        bundle: ContextBundle,
        **include_flags: bool,
    ) -> FreshnessWarningsContext:
        ctx = FreshnessWarningsContext()

        sections = [
            ("Inventory", bundle.inventory.freshness, include_flags.get("include_inventory", False)),
            ("Planned Topology", bundle.planned_topology.freshness, include_flags.get("include_planned_topology", False)),
            ("Telemetry", bundle.telemetry.freshness, include_flags.get("include_telemetry", False)),
            ("SDN State", bundle.sdn_state.freshness, include_flags.get("include_sdn", False)),
            ("Config History", bundle.config_history.freshness, include_flags.get("include_config", False)),
            ("Deployment And Intent Status", bundle.deployment_status.freshness, include_flags.get("include_deployment", False)),
            ("Incidents", bundle.incidents.freshness, include_flags.get("include_incidents", False)),
            ("Threat Detections", bundle.threat_detections.freshness, include_flags.get("include_threats", False)),
            ("Drift Findings", bundle.drift.freshness, include_flags.get("include_drift", False)),
            ("Knowledge Base", bundle.knowledge.freshness, include_flags.get("include_knowledge", False)),
        ]

        for section_name, freshness, include_section in sections:
            if not include_section:
                continue
            if freshness.freshness_class not in {"stale", "missing"}:
                continue
            ctx.warnings.append(
                {
                    "section": section_name,
                    "severity": "warning" if freshness.freshness_class == "stale" else "critical",
                    "message": (
                        f"{section_name} is {freshness.freshness_class}; source={freshness.source}; "
                        f"captured_at={freshness.captured_at or 'unknown'}"
                    ),
                    "freshness_class": freshness.freshness_class,
                    "age_seconds": freshness.age_seconds,
                }
            )

        if bundle.config_history.devices_missing_snapshots:
            ctx.warnings.append(
                {
                    "section": "Config History",
                    "severity": "warning",
                    "message": "Missing recent config snapshots for: " + ", ".join(bundle.config_history.devices_missing_snapshots[:10]),
                    "freshness_class": bundle.config_history.freshness.freshness_class,
                    "age_seconds": bundle.config_history.freshness.age_seconds,
                }
            )

        return ctx
