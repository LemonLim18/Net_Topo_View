"""
Prometheus Query Client — CogniShield Integration.

Queries Prometheus (+ SNMP Exporter) on 192.168.1.252:9090 for network
device metrics that complement the InfluxDB/Telegraf telemetry pipeline.

Prometheus provides:
  - SNMP Exporter scraped metrics (interface counters, device status)
  - Alert rules evaluation (Alertmanager integration)
  - Node Exporter metrics for VM self-monitoring

This module exposes:
  - PrometheusClient          — low-level PromQL query helper
  - query_device_metrics()    — fetch device CPU/mem/interface via SNMP exporter
  - query_active_alerts()     — fetch firing alerts from Alertmanager
  - get_health()              — check Prometheus reachability
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("monitor.prometheus")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "")


# ---------------------------------------------------------------------------
# Low-level Prometheus client
# ---------------------------------------------------------------------------

class PrometheusClient:
    """Lightweight PromQL query client using stdlib only (no extra deps)."""

    def __init__(self, base_url: str = ""):
        self.base_url = (base_url or PROMETHEUS_URL).rstrip("/")
        if not self.base_url:
            logger.warning("PROMETHEUS_URL not configured — Prometheus queries disabled")

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    # -- instant query ---------------------------------------------------------
    def query(self, promql: str, time: Optional[str] = None) -> List[Dict[str, Any]]:
        """Run an instant PromQL query, return result vector."""
        if not self.enabled:
            return []
        params: Dict[str, str] = {"query": promql}
        if time:
            params["time"] = time
        return self._get("/api/v1/query", params).get("result", [])

    # -- range query -----------------------------------------------------------
    def query_range(
        self,
        promql: str,
        start: str,
        end: str,
        step: str = "60s",
    ) -> List[Dict[str, Any]]:
        """Run a range PromQL query, return result matrix."""
        if not self.enabled:
            return []
        params = {"query": promql, "start": start, "end": end, "step": step}
        return self._get("/api/v1/query_range", params).get("result", [])

    # -- active alerts ---------------------------------------------------------
    def alerts(self) -> List[Dict[str, Any]]:
        """Fetch currently firing alerts."""
        if not self.enabled:
            return []
        data = self._get("/api/v1/alerts", {})
        return data.get("alerts", [])

    # -- targets ---------------------------------------------------------------
    def targets(self) -> Dict[str, Any]:
        """Fetch scrape target status (UP/DOWN)."""
        if not self.enabled:
            return {}
        return self._get("/api/v1/targets", {})

    # -- health ----------------------------------------------------------------
    def healthy(self) -> bool:
        """Return True if Prometheus is reachable."""
        if not self.enabled:
            return False
        try:
            req = urllib.request.Request(
                f"{self.base_url}/-/healthy", method="GET"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    # -- internal --------------------------------------------------------------
    def _get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        qs = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{qs}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
                if body.get("status") == "success":
                    return body.get("data", {})
                logger.warning("Prometheus query error: %s", body.get("error", "unknown"))
                return {}
        except Exception as exc:
            logger.error("Prometheus request failed (%s): %s", url, exc)
            return {}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_client: Optional[PrometheusClient] = None


def get_client() -> PrometheusClient:
    global _client
    if _client is None:
        _client = PrometheusClient()
    return _client


# ---------------------------------------------------------------------------
# High-level helpers (used by monitor API + RAG context bundle)
# ---------------------------------------------------------------------------

def get_health() -> Dict[str, Any]:
    """Check Prometheus connectivity and return status dict."""
    c = get_client()
    reachable = c.healthy()
    targets = {}
    if reachable:
        try:
            targets = c.targets()
        except Exception:
            pass
    active_targets = targets.get("activeTargets", [])
    up_count = sum(1 for t in active_targets if t.get("health") == "up")
    return {
        "url": c.base_url,
        "reachable": reachable,
        "enabled": c.enabled,
        "active_targets": len(active_targets),
        "targets_up": up_count,
    }


def query_device_metrics(
    device_ips: List[str],
    metric_names: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Query SNMP Exporter metrics for a list of device IPs.

    Returns dict keyed by IP with sub-dicts of metric name → latest value.
    Default metrics: ifOperStatus, sysUpTime, ifHCInOctets, ifHCOutOctets.
    """
    c = get_client()
    if not c.enabled or not device_ips:
        return {}

    if metric_names is None:
        metric_names = [
            "sysUpTime",
            "ifOperStatus",
            "ifHCInOctets",
            "ifHCOutOctets",
            "ifHighSpeed",
        ]

    results: Dict[str, Dict[str, Any]] = {ip: {} for ip in device_ips}
    ip_regex = "|".join(device_ips)

    for metric in metric_names:
        try:
            # SNMP exporter labels target IPs as "instance" (ip:port)
            promql = f'{metric}{{instance=~"{ip_regex}:.*"}}'
            vec = c.query(promql)
            for sample in vec:
                labels = sample.get("metric", {})
                instance = labels.get("instance", "")
                ip = instance.split(":")[0] if ":" in instance else instance
                if ip in results:
                    value = sample.get("value", [None, None])
                    results[ip][metric] = {
                        "value": value[1] if len(value) > 1 else None,
                        "timestamp": value[0] if len(value) > 0 else None,
                        "labels": labels,
                    }
        except Exception as exc:
            logger.debug("Prometheus metric %s query failed: %s", metric, exc)

    return results


def query_active_alerts() -> List[Dict[str, Any]]:
    """
    Fetch currently firing alerts from Prometheus.
    Returns list of alert dicts with labels, annotations, state, activeAt.
    """
    c = get_client()
    raw = c.alerts()
    firing = [a for a in raw if a.get("state") == "firing"]
    return [
        {
            "name": a.get("labels", {}).get("alertname", "unknown"),
            "severity": a.get("labels", {}).get("severity", "warning"),
            "instance": a.get("labels", {}).get("instance", ""),
            "summary": a.get("annotations", {}).get("summary", ""),
            "description": a.get("annotations", {}).get("description", ""),
            "state": a.get("state"),
            "active_at": a.get("activeAt"),
        }
        for a in firing
    ]


def query_interface_traffic(
    device_ip: str,
    interface: str = "",
    range_minutes: int = 30,
) -> List[Dict[str, Any]]:
    """
    Query interface traffic rate for a device over the last N minutes.
    Returns time-series data suitable for charting.
    """
    c = get_client()
    if not c.enabled:
        return []

    iface_filter = f',ifDescr="{interface}"' if interface else ""
    promql_in = f'rate(ifHCInOctets{{instance=~"{device_ip}:.*"{iface_filter}}}[5m]) * 8'
    promql_out = f'rate(ifHCOutOctets{{instance=~"{device_ip}:.*"{iface_filter}}}[5m]) * 8'

    now = datetime.utcnow()
    end_ts = now.isoformat() + "Z"
    start_ts = (now.replace(minute=now.minute - range_minutes if now.minute >= range_minutes else 0)).isoformat() + "Z"

    in_data = c.query_range(promql_in, start_ts, end_ts, step="60s")
    out_data = c.query_range(promql_out, start_ts, end_ts, step="60s")

    series = []
    for entry in in_data:
        labels = entry.get("metric", {})
        values = entry.get("values", [])
        series.append({
            "direction": "in",
            "interface": labels.get("ifDescr", "unknown"),
            "unit": "bps",
            "datapoints": [{"ts": v[0], "value": float(v[1])} for v in values],
        })
    for entry in out_data:
        labels = entry.get("metric", {})
        values = entry.get("values", [])
        series.append({
            "direction": "out",
            "interface": labels.get("ifDescr", "unknown"),
            "unit": "bps",
            "datapoints": [{"ts": v[0], "value": float(v[1])} for v in values],
        })
    return series
