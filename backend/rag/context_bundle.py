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
    CONTEXT_DOMAIN_SDN,
    CONTEXT_DOMAIN_TELEMETRY,
    FreshnessMetadata,
    compute_freshness,
    missing_freshness,
)

logger = logging.getLogger("rag.context_bundle")

# InfluxDB settings (from environment)
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")

# Prometheus (optional — for SNMP exporter metrics)
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "")


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
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_INVENTORY,
            "local_db",
            "planned_inventory",
        )
    )


@dataclass
class TelemetryContext:
    """Recent telemetry samples for target devices."""
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "influxdb"
    query_range: str = "-15m"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_TELEMETRY,
            "influxdb",
            "device_metrics",
        )
    )


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
    source: str = "config_snapshots"
    freshness: FreshnessMetadata = field(
        default_factory=lambda: missing_freshness(
            CONTEXT_DOMAIN_CONFIG,
            "config_snapshots",
            "device_configs",
        )
    )


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
    telemetry: TelemetryContext = field(default_factory=TelemetryContext)
    sdn_state: SDNContext = field(default_factory=SDNContext)
    config_history: ConfigHistoryContext = field(default_factory=ConfigHistoryContext)
    incidents: IncidentContext = field(default_factory=IncidentContext)
    drift: DriftContext = field(default_factory=DriftContext)
    knowledge: KnowledgeContext = field(default_factory=KnowledgeContext)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scope_filter: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        """Render the bundle as a human-readable markdown block for the LLM."""
        lines: List[str] = ["## Network Context Bundle", f"*Generated: {self.timestamp}*", ""]

        # Inventory
        inv = self.inventory
        if inv.devices:
            lines.append(f"### Inventory ({inv.total_devices} devices, source: {inv.source})")
            lines.append(f"Freshness: {inv.freshness.to_prompt_text()}")
            for d in inv.devices[:20]:  # Cap at 20 to save tokens
                status = d.get("status", "unknown")
                lines.append(
                    f"- **{d.get('hostname', '?')}** ({d.get('management_ip', 'N/A')}) "
                    f"— {d.get('vendor', '?')} {d.get('os_version', '')} [{status}]"
                )
            if inv.interfaces:
                lines.append(f"\n  Interfaces included: {len(inv.interfaces)}")
            lines.append("")

        # Telemetry
        tel = self.telemetry
        if tel.metrics:
            lines.append(f"### Telemetry (range: {tel.query_range}, source: {tel.source})")
            lines.append(f"Freshness: {tel.freshness.to_prompt_text()}")
            for m in tel.metrics[:15]:
                lines.append(
                    f"- {m.get('device', '?')}: "
                    f"{m.get('metric', '?')} = {m.get('value', '?')} "
                    f"({m.get('unit', '')})"
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
            for snapshot in cfg.snapshots[:10]:
                lines.append(
                    f"- {snapshot.get('hostname', '?')}: captured_at={snapshot.get('captured_at', 'unknown')} hash={snapshot.get('config_hash', '')[:12]}"
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
        include_telemetry: bool = True,
        include_sdn: bool = True,
        include_config: bool = True,
        include_incidents: bool = True,
        include_drift: bool = True,
        include_knowledge: bool = True,
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

        # 2. Telemetry (InfluxDB)
        if include_telemetry:
            device_ips = self._resolve_device_ips(bundle.inventory.devices, device_hostnames)
            bundle.telemetry = self._fetch_telemetry(device_ips)

        # 3. SDN State
        if include_sdn:
            bundle.sdn_state = self._fetch_sdn_state(device_hostnames)

        if include_config:
            bundle.config_history = self._fetch_config_history(device_hostnames)

        if include_incidents:
            bundle.incidents = self._fetch_incidents(device_hostnames)

        if include_drift:
            bundle.drift = self._fetch_drift(device_hostnames)

        if include_knowledge:
            bundle.knowledge = self._fetch_knowledge_summary()

        return bundle

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

    def _fetch_inventory(
        self,
        hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
    ) -> InventoryContext:
        """Fetch device inventory from NetBox (if available) or local DB."""
        ctx = InventoryContext()

        # Try NetBox first
        if self._netbox is not None:
            try:
                ctx = self._fetch_netbox_inventory(hostnames, vendor_filter)
                ctx.source = "netbox"
                return ctx
            except Exception as e:
                logger.warning("NetBox inventory fetch failed, falling back to local DB: %s", e)

        # Fallback to local DB
        if self._db is not None:
            try:
                ctx = self._fetch_local_inventory(hostnames, vendor_filter)
                ctx.source = "local_db"
            except Exception as e:
                logger.warning("Local DB inventory fetch failed: %s", e)

        return ctx

    def _fetch_netbox_inventory(
        self,
        hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
    ) -> InventoryContext:
        """Query NetBox via pynetbox for device inventory."""
        ctx = InventoryContext(source="netbox")
        last_updated_candidates: List[str] = []

        try:
            from netbox.client import NetBoxClient
            client = self._netbox if self._netbox else NetBoxClient()
            nb = client._nb  # pynetbox Api object

            # Fetch devices
            filters: Dict[str, Any] = {}
            if hostnames:
                # pynetbox supports name__in for multiple values
                filters["name"] = hostnames
            devices = list(nb.dcim.devices.filter(**filters) if filters else nb.dcim.devices.all())

            for dev in devices:
                if vendor_filter and vendor_filter.lower() not in str(getattr(dev, 'platform', '')).lower():
                    continue
                last_updated = getattr(dev, "last_updated", None)
                if last_updated:
                    last_updated_candidates.append(str(last_updated))
                ctx.devices.append({
                    "hostname": str(dev.name),
                    "management_ip": str(dev.primary_ip) if dev.primary_ip else None,
                    "vendor": str(dev.platform) if dev.platform else "unknown",
                    "model": str(dev.device_type) if dev.device_type else None,
                    "role": str(dev.role) if dev.role else None,
                    "site": str(dev.site) if dev.site else None,
                    "status": str(dev.status) if dev.status else "unknown",
                    "serial": str(dev.serial) if dev.serial else None,
                })

            ctx.total_devices = len(ctx.devices)

            # Fetch interfaces for scoped devices
            if hostnames and len(hostnames) <= 10:
                for dev in devices:
                    try:
                        ifaces = list(nb.dcim.interfaces.filter(device=dev.name))
                        for iface in ifaces[:20]:  # Cap per device
                            ctx.interfaces.append({
                                "device": str(dev.name),
                                "name": str(iface.name),
                                "type": str(iface.type) if iface.type else None,
                                "enabled": bool(iface.enabled),
                                "mac_address": str(iface.mac_address) if iface.mac_address else None,
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
                f"serial_number, status FROM devices{where_sql} LIMIT 50",
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
                        "SELECT i.name, i.ip_address, i.mac_address, i.status "
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

    def _fetch_telemetry(self, device_ips: List[str]) -> TelemetryContext:
        """Fetch latest telemetry from InfluxDB for the given device IPs."""
        ctx = TelemetryContext()

        if not INFLUXDB_TOKEN or not device_ips:
            return ctx

        try:
            # Build Flux query for target devices
            ip_filter = " or ".join(f'r.agent_host == "{ip}"' for ip in device_ips[:10])
            flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -15m)
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

        ctx.freshness = compute_freshness(
            domain=CONTEXT_DOMAIN_CONFIG,
            captured_at=latest_captured,
            ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source="config_snapshots",
            scope="device_configs",
            confidence=0.9 if ctx.snapshots else 0.0,
        )
        return ctx

    def _fetch_incidents(
        self,
        hostnames: Optional[List[str]] = None,
    ) -> IncidentContext:
        ctx = IncidentContext()
        try:
            from incidents.store import IncidentStore

            rows = IncidentStore().list_all(limit=20)
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
