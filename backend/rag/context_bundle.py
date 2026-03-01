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

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("rag.context_bundle")

# InfluxDB settings (from environment)
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://192.168.1.250:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")


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


@dataclass
class TelemetryContext:
    """Recent telemetry samples for target devices."""
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "influxdb"
    query_range: str = "-15m"


@dataclass
class SDNContext:
    """SDN controller state for target devices."""
    controllers: List[Dict[str, Any]] = field(default_factory=list)
    topology_links: List[Dict[str, Any]] = field(default_factory=list)
    managed_devices: List[Dict[str, Any]] = field(default_factory=list)
    flow_rules: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "sdn_controllers"


@dataclass
class ContextBundle:
    """The unified context payload injected into LLM prompts."""
    inventory: InventoryContext = field(default_factory=InventoryContext)
    telemetry: TelemetryContext = field(default_factory=TelemetryContext)
    sdn_state: SDNContext = field(default_factory=SDNContext)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
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
        include_telemetry: bool = True,
        include_sdn: bool = True,
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
        bundle.inventory = self._fetch_inventory(device_hostnames, vendor_filter)

        # 2. Telemetry (InfluxDB)
        if include_telemetry:
            device_ips = [
                d.get("management_ip")
                for d in bundle.inventory.devices
                if d.get("management_ip")
            ]
            bundle.telemetry = self._fetch_telemetry(device_ips)

        # 3. SDN State
        if include_sdn:
            bundle.sdn_state = self._fetch_sdn_state(device_hostnames)

        return bundle

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
                # Parse CSV results into metric dicts
                for line in csv_data.strip().split("\n")[1:]:  # Skip header
                    parts = line.split(",")
                    if len(parts) >= 7:
                        ctx.metrics.append({
                            "device": parts[4] if len(parts) > 4 else "?",
                            "metric": parts[5] if len(parts) > 5 else "?",
                            "value": parts[6] if len(parts) > 6 else "?",
                            "unit": "",
                        })

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
                    "SELECT id, name, controller_type, url, status, managed_device_count, last_synced "
                    "FROM sdn_controllers WHERE status = 'online'"
                ).fetchall()

                for ctrl in controllers:
                    ctrl_dict = dict(ctrl)
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
