from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from rag.context_bundle import ContextBundleBuilder


DIGITAL_TWIN_VERSION = "phase9_step30_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _preview_config(config_text: str, max_lines: int = 12) -> List[str]:
    lines = [line.rstrip() for line in str(config_text or "").splitlines() if line.strip()]
    return lines[:max_lines]


@dataclass
class DigitalTwinSnapshot:
    version: str = DIGITAL_TWIN_VERSION
    generated_at: str = field(default_factory=_utc_now)
    scope_filter: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    freshness: Dict[str, Any] = field(default_factory=dict)
    twins: List[Dict[str, Any]] = field(default_factory=list)
    graph: Dict[str, Any] = field(default_factory=lambda: {"nodes": [], "edges": []})
    conflicts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        lines = [
            f"Digital Twin Version: {self.version}",
            f"Generated At: {self.generated_at}",
            (
                f"Scope: devices={', '.join(self.scope_filter.get('device_hostnames') or []) or 'all'}; "
                f"vendor_filter={self.scope_filter.get('vendor_filter') or 'none'}"
            ),
            (
                f"Summary: twins={self.summary.get('twin_count', 0)}; conflicts={self.summary.get('conflict_count', 0)}; "
                f"planned_links={self.summary.get('planned_link_count', 0)}; live_links={self.summary.get('live_link_count', 0)}"
            ),
        ]
        for twin in self.twins[:8]:
            lines.append(
                (
                    f"- {twin['hostname']}: presence={twin['presence_state']}; live_status={twin['live_status']}; "
                    f"intended_config={'yes' if twin['intended_config'] else 'no'}; actual_config={'yes' if twin['actual_config'] else 'no'}; "
                    f"incidents={twin['incident_count']}; drift={twin['drift_state']}"
                )
            )
        if self.conflicts:
            lines.append("Conflicts:")
            for conflict in self.conflicts[:10]:
                lines.append(f"- {conflict['hostname']}: {conflict['summary']}")
        return "\n".join(lines)


class DigitalTwinBuilder:
    def __init__(self, db_manager=None, context_bundle_builder: Optional[ContextBundleBuilder] = None):
        self._db = db_manager
        self._bundle_builder = context_bundle_builder or ContextBundleBuilder(db_manager=db_manager)

    def build_snapshot(
        self,
        device_hostnames: Optional[List[str]] = None,
        vendor_filter: Optional[str] = None,
        include_telemetry: bool = True,
        include_sdn: bool = True,
    ) -> DigitalTwinSnapshot:
        if self._db is None:
            raise ValueError("Database manager is required to build a digital twin snapshot")

        bundle = self._bundle_builder.build_bundle(
            device_hostnames=device_hostnames,
            vendor_filter=vendor_filter,
            include_telemetry=include_telemetry,
            include_sdn=include_sdn,
            include_knowledge=False,
        )

        live_topology = self._db.get_topology() or {}
        live_nodes = live_topology.get("nodes", {}) if isinstance(live_topology, dict) else {}
        planned_devices = list(bundle.inventory.devices)
        planned_by_name = {str(device.get("hostname") or ""): device for device in planned_devices}
        live_by_name = {
            str(hostname): node
            for hostname, node in live_nodes.items()
            if isinstance(node, dict)
        }

        hostnames = sorted(set(planned_by_name) | set(live_by_name))
        if device_hostnames:
            lowered = {item.lower() for item in device_hostnames}
            hostnames = [hostname for hostname in hostnames if hostname.lower() in lowered]

        intended_configs = self._fetch_intended_configs(hostnames)
        actual_configs = self._fetch_actual_configs(hostnames)
        telemetry = self._build_telemetry_by_hostname(bundle.telemetry.metrics, planned_by_name, live_by_name, bundle.telemetry)
        incidents = self._group_incidents_by_hostname(bundle.incidents.incidents)
        drift = self._group_drift_by_hostname(bundle.drift.events)

        twins: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []

        for hostname in hostnames:
            planned = planned_by_name.get(hostname)
            live = live_by_name.get(hostname)
            presence_state = self._presence_state(planned, live)
            intended = intended_configs.get(hostname)
            actual = actual_configs.get(hostname)
            telemetry_summary = telemetry.get(hostname)
            incident_items = incidents.get(hostname, [])
            drift_items = drift.get(hostname, [])

            twin = {
                "hostname": hostname,
                "presence_state": presence_state,
                "planned_state": planned,
                "live_state": self._live_state_payload(hostname, live),
                "live_status": self._live_status(live),
                "intended_config": intended,
                "actual_config": actual,
                "telemetry": telemetry_summary,
                "incident_count": len(incident_items),
                "incidents": incident_items,
                "drift_state": "drift_detected" if drift_items else "clean",
                "drift": drift_items,
            }
            twins.append(twin)

            nodes.extend(self._build_device_nodes(hostname, planned, live, intended, actual, telemetry_summary, incident_items, drift_items))
            edges.extend(self._build_device_edges(hostname, planned, live, intended, actual, telemetry_summary, incident_items, drift_items))
            conflicts.extend(self._build_conflicts(hostname, twin))

        planned_links = self._planned_link_edges(bundle.planned_topology.links)
        live_links = self._live_link_edges(live_by_name)
        edges.extend(planned_links)
        edges.extend(live_links)

        freshness = {
            "inventory": bundle.inventory.freshness.to_dict(),
            "planned_topology": bundle.planned_topology.freshness.to_dict(),
            "telemetry": bundle.telemetry.freshness.to_dict(),
            "config_history": bundle.config_history.freshness.to_dict(),
            "deployment_status": bundle.deployment_status.freshness.to_dict(),
            "incidents": bundle.incidents.freshness.to_dict(),
            "drift": bundle.drift.freshness.to_dict(),
        }

        snapshot = DigitalTwinSnapshot(
            scope_filter={
                "device_hostnames": device_hostnames,
                "vendor_filter": vendor_filter,
                "include_telemetry": include_telemetry,
                "include_sdn": include_sdn,
            },
            summary={
                "twin_count": len(twins),
                "conflict_count": len(conflicts),
                "planned_link_count": len(planned_links),
                "live_link_count": len(live_links),
                "incident_count": sum(len(items) for items in incidents.values()),
                "drift_count": sum(len(items) for items in drift.values()),
                "telemetry_device_count": len([item for item in telemetry.values() if item]),
            },
            freshness=freshness,
            twins=twins,
            graph={"nodes": nodes, "edges": edges},
            conflicts=conflicts,
        )
        return snapshot

    def _presence_state(self, planned: Optional[Dict[str, Any]], live: Optional[Dict[str, Any]]) -> str:
        if planned and live:
            return "planned_and_live"
        if planned:
            return "planned_only"
        if live:
            return "live_only"
        return "missing"

    def _live_status(self, live: Optional[Dict[str, Any]]) -> str:
        if not live:
            return "missing"
        return "down" if live.get("down") else "up"

    def _live_state_payload(self, hostname: str, live: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not live:
            return None
        return {
            "hostname": hostname,
            "management_ip": live.get("mgmt_ip"),
            "status": self._live_status(live),
            "neighbors": list(live.get("connectedNode") or []),
            "neighbor_count": len(list(live.get("connectedNode") or [])),
        }

    def _fetch_intended_configs(self, hostnames: List[str]) -> Dict[str, Dict[str, Any]]:
        if not hostnames:
            return {}
        placeholders = ",".join("?" for _ in hostnames)
        result: Dict[str, Dict[str, Any]] = {}
        with self._db._connect() as conn:
            proposal_rows = conn.execute(
                (
                    "SELECT target_device, status, feature, risk_level, generated_config, rollback_config, updated_at, created_at, id "
                    "FROM config_proposals "
                    f"WHERE lower(target_device) IN ({placeholders}) "
                    "ORDER BY updated_at DESC, created_at DESC"
                ),
                [hostname.lower() for hostname in hostnames],
            ).fetchall()
            job_rows = conn.execute(
                (
                    "SELECT d.hostname, j.status, j.stage, j.generated_config, j.rollback_config, j.updated_at, j.id "
                    "FROM jobs j JOIN devices d ON d.id = j.device_id "
                    f"WHERE lower(d.hostname) IN ({placeholders}) "
                    "ORDER BY j.updated_at DESC, j.id DESC"
                ),
                [hostname.lower() for hostname in hostnames],
            ).fetchall()

        for row in proposal_rows:
            record = dict(row)
            hostname = str(record.get("target_device") or "")
            if hostname and hostname not in result:
                generated_config = str(record.get("generated_config") or "")
                result[hostname] = {
                    "source": "config_proposals",
                    "id": record.get("id"),
                    "status": record.get("status"),
                    "feature": record.get("feature"),
                    "risk_level": record.get("risk_level"),
                    "updated_at": record.get("updated_at") or record.get("created_at"),
                    "line_count": len(generated_config.splitlines()) if generated_config else 0,
                    "preview_lines": _preview_config(generated_config),
                    "rollback_preview_lines": _preview_config(record.get("rollback_config") or ""),
                }
        for row in job_rows:
            record = dict(row)
            hostname = str(record.get("hostname") or "")
            if hostname and hostname not in result:
                generated_config = str(record.get("generated_config") or "")
                result[hostname] = {
                    "source": "deployment_jobs",
                    "id": record.get("id"),
                    "status": record.get("status"),
                    "stage": record.get("stage"),
                    "updated_at": record.get("updated_at"),
                    "line_count": len(generated_config.splitlines()) if generated_config else 0,
                    "preview_lines": _preview_config(generated_config),
                    "rollback_preview_lines": _preview_config(record.get("rollback_config") or ""),
                }
        return result

    def _fetch_actual_configs(self, hostnames: List[str]) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for hostname in hostnames:
            device = self._db.get_device_by_hostname(hostname)
            if not device:
                continue
            snapshots = self._db.get_config_history(device.id, limit=1)
            if not snapshots:
                continue
            snapshot = snapshots[0]
            result[hostname] = {
                "source": "config_snapshots",
                "captured_at": snapshot.captured_at,
                "config_hash": snapshot.config_hash,
                "line_count": len(str(snapshot.config_text or "").splitlines()),
                "preview_lines": _preview_config(snapshot.config_text),
            }
        return result

    def _build_telemetry_by_hostname(
        self,
        metrics: List[Dict[str, Any]],
        planned_by_name: Dict[str, Dict[str, Any]],
        live_by_name: Dict[str, Dict[str, Any]],
        telemetry_context: Any,
    ) -> Dict[str, Dict[str, Any]]:
        ip_to_host: Dict[str, str] = {}
        for hostname, device in planned_by_name.items():
            management_ip = str(device.get("management_ip") or "").strip()
            if management_ip:
                ip_to_host[management_ip] = hostname
        for hostname, live in live_by_name.items():
            management_ip = str(live.get("mgmt_ip") or "").strip()
            if management_ip:
                ip_to_host.setdefault(management_ip, hostname)

        grouped: Dict[str, Dict[str, Any]] = {}
        for metric in metrics:
            host = str(metric.get("hostname") or "")
            if not host:
                host = ip_to_host.get(str(metric.get("device") or "").strip(), "")
            if not host:
                continue
            entry = grouped.setdefault(
                host,
                {
                    "metric_names": set(),
                    "latest_timestamp": None,
                    "samples": [],
                    "anomaly_baseline_count": 0,
                    "change_marker_count": 0,
                    "trend_state": "snapshot",
                },
            )
            metric_name = str(metric.get("metric") or metric.get("field") or "unknown")
            entry["metric_names"].add(metric_name)
            timestamp = metric.get("timestamp")
            if timestamp and (entry["latest_timestamp"] is None or timestamp > entry["latest_timestamp"]):
                entry["latest_timestamp"] = timestamp
            entry["samples"].append(
                {
                    "metric": metric_name,
                    "value": metric.get("value"),
                    "unit": metric.get("unit"),
                    "timestamp": timestamp,
                }
            )

        baselines = telemetry_context.anomaly_baselines if telemetry_context else []
        for baseline in baselines:
            host = str(baseline.get("hostname") or ip_to_host.get(str(baseline.get("device") or "").strip(), "") or "")
            if not host:
                continue
            entry = grouped.setdefault(
                host,
                {
                    "metric_names": set(),
                    "latest_timestamp": None,
                    "samples": [],
                    "anomaly_baseline_count": 0,
                    "change_marker_count": 0,
                    "trend_state": "snapshot",
                },
            )
            entry["anomaly_baseline_count"] += 1
            entry["trend_state"] = "baseline_aware"

        markers = telemetry_context.change_markers if telemetry_context else []
        for marker in markers:
            host = str(marker.get("hostname") or ip_to_host.get(str(marker.get("device") or "").strip(), "") or "")
            if not host:
                continue
            entry = grouped.setdefault(
                host,
                {
                    "metric_names": set(),
                    "latest_timestamp": None,
                    "samples": [],
                    "anomaly_baseline_count": 0,
                    "change_marker_count": 0,
                    "trend_state": "snapshot",
                },
            )
            entry["change_marker_count"] += 1
            entry["trend_state"] = "changing"

        normalized: Dict[str, Dict[str, Any]] = {}
        for host, entry in grouped.items():
            normalized[host] = {
                "metric_names": sorted(entry["metric_names"]),
                "latest_timestamp": entry["latest_timestamp"],
                "sample_count": len(entry["samples"]),
                "samples": entry["samples"][:10],
                "anomaly_baseline_count": entry["anomaly_baseline_count"],
                "change_marker_count": entry["change_marker_count"],
                "trend_state": entry["trend_state"],
            }
        return normalized

    def _group_incidents_by_hostname(self, incidents: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for incident in incidents:
            affected = _json_loads(incident.get("affected_devices"), [])
            for hostname in affected:
                grouped.setdefault(str(hostname), []).append(
                    {
                        "id": incident.get("id"),
                        "severity": incident.get("severity"),
                        "status": incident.get("status"),
                        "anomaly_type": incident.get("anomaly_type"),
                        "updated_at": incident.get("updated_at") or incident.get("created_at"),
                    }
                )
        return grouped

    def _group_drift_by_hostname(self, events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for event in events:
            hostname = str(event.get("hostname") or "")
            if not hostname:
                continue
            grouped.setdefault(hostname, []).append(event)
        return grouped

    def _build_device_nodes(
        self,
        hostname: str,
        planned: Optional[Dict[str, Any]],
        live: Optional[Dict[str, Any]],
        intended: Optional[Dict[str, Any]],
        actual: Optional[Dict[str, Any]],
        telemetry: Optional[Dict[str, Any]],
        incidents: List[Dict[str, Any]],
        drift: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        nodes = []
        if planned:
            nodes.append({"id": f"planned:{hostname}", "kind": "planned_device", "label": hostname, "state": planned.get("status"), "domain": "planned_inventory"})
        if live:
            nodes.append({"id": f"live:{hostname}", "kind": "live_device", "label": hostname, "state": self._live_status(live), "domain": "live_topology"})
        if intended:
            nodes.append({"id": f"intended_config:{hostname}", "kind": "intended_config", "label": f"Intended config {hostname}", "state": intended.get("status"), "domain": "deployment_status"})
        if actual:
            nodes.append({"id": f"actual_config:{hostname}", "kind": "actual_config", "label": f"Actual config {hostname}", "state": actual.get("captured_at"), "domain": "config_history"})
        if telemetry:
            nodes.append({"id": f"telemetry:{hostname}", "kind": "telemetry", "label": f"Telemetry {hostname}", "state": telemetry.get("trend_state"), "domain": "telemetry"})
        for incident in incidents:
            nodes.append({"id": f"incident:{incident['id']}", "kind": "incident", "label": incident.get("anomaly_type") or incident["id"], "state": incident.get("severity"), "domain": "incidents"})
        for index, event in enumerate(drift):
            nodes.append({"id": f"drift:{hostname}:{index}", "kind": "drift", "label": f"Drift {hostname}", "state": event.get("severity"), "domain": "drift"})
        return nodes

    def _build_device_edges(
        self,
        hostname: str,
        planned: Optional[Dict[str, Any]],
        live: Optional[Dict[str, Any]],
        intended: Optional[Dict[str, Any]],
        actual: Optional[Dict[str, Any]],
        telemetry: Optional[Dict[str, Any]],
        incidents: List[Dict[str, Any]],
        drift: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        edges = []
        if planned and live:
            edges.append({"source": f"planned:{hostname}", "target": f"live:{hostname}", "relation": "device_twin", "layer": "state_alignment"})
        if planned and intended:
            edges.append({"source": f"planned:{hostname}", "target": f"intended_config:{hostname}", "relation": "intends", "layer": "configuration"})
        if live and actual:
            edges.append({"source": f"live:{hostname}", "target": f"actual_config:{hostname}", "relation": "runs", "layer": "configuration"})
        if live and telemetry:
            edges.append({"source": f"live:{hostname}", "target": f"telemetry:{hostname}", "relation": "observed_by", "layer": "telemetry"})
        for incident in incidents:
            edges.append({"source": f"planned:{hostname}", "target": f"incident:{incident['id']}", "relation": "impacted_by", "layer": "incidents"})
        for index, _event in enumerate(drift):
            edges.append({"source": f"actual_config:{hostname}", "target": f"drift:{hostname}:{index}", "relation": "drifted_from", "layer": "drift"})
        return edges

    def _build_conflicts(self, hostname: str, twin: Dict[str, Any]) -> List[Dict[str, Any]]:
        conflicts = []
        planned = twin.get("planned_state") or {}
        live = twin.get("live_state") or {}
        intended = twin.get("intended_config") or {}
        actual = twin.get("actual_config") or {}

        if twin["presence_state"] == "planned_only":
            conflicts.append({"hostname": hostname, "code": "missing_live_presence", "summary": f"{hostname} is present in planned state but missing from live topology."})
        elif twin["presence_state"] == "live_only":
            conflicts.append({"hostname": hostname, "code": "missing_planned_presence", "summary": f"{hostname} is present in live topology but missing from planned inventory."})

        planned_ip = str(planned.get("management_ip") or "")
        live_ip = str(live.get("management_ip") or "")
        if planned_ip and live_ip and planned_ip != live_ip:
            conflicts.append({"hostname": hostname, "code": "management_ip_mismatch", "summary": f"{hostname} has planned IP {planned_ip} but live IP {live_ip}."})

        if twin.get("live_status") == "down":
            conflicts.append({"hostname": hostname, "code": "live_device_down", "summary": f"{hostname} is currently down in live topology."})

        if intended and actual and intended.get("line_count", 0) and actual.get("line_count", 0):
            if intended.get("preview_lines") != actual.get("preview_lines"):
                conflicts.append({"hostname": hostname, "code": "intended_actual_divergence", "summary": f"{hostname} intended config preview differs from the latest actual config snapshot."})

        if twin.get("incident_count"):
            conflicts.append({"hostname": hostname, "code": "active_incident", "summary": f"{hostname} has {twin['incident_count']} active incident signal(s)."})

        if twin.get("drift"):
            conflicts.append({"hostname": hostname, "code": "config_drift_detected", "summary": f"{hostname} has recent drift events in the digital twin."})

        return conflicts

    def _planned_link_edges(self, links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        edges = []
        for link in links:
            src = str(link.get("src_hostname") or "")
            dst = str(link.get("dst_hostname") or "")
            if not src or not dst:
                continue
            edges.append(
                {
                    "source": f"planned:{src}",
                    "target": f"planned:{dst}",
                    "relation": "planned_link",
                    "layer": "planned_topology",
                    "src_interface": link.get("src_interface"),
                    "dst_interface": link.get("dst_interface"),
                }
            )
        return edges

    def _live_link_edges(self, live_by_name: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        edges = []
        seen = set()
        for src, node in live_by_name.items():
            for dst in list(node.get("connectedNode") or []):
                if str(dst) not in live_by_name:
                    continue
                key = tuple(sorted((src, str(dst))))
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    {
                        "source": f"live:{src}",
                        "target": f"live:{dst}",
                        "relation": "live_link",
                        "layer": "live_topology",
                    }
                )
        return edges