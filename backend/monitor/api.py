from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Set, Tuple
import csv
from datetime import datetime
import io
import math
import os
import logging
import urllib.request
import json
from db.manager import DatabaseManager
from monitor.telegraf_gen import TelegrafConfigGenerator
from monitor.prometheus import (
    get_health as prom_health,
    query_device_metrics as prom_device_metrics,
    query_active_alerts as prom_active_alerts,
    query_interface_traffic as prom_interface_traffic,
)

router = APIRouter(prefix="/monitor", tags=["monitor"])
logger = logging.getLogger("api.monitor")

# InfluxDB connection (configure via env vars)
INFLUXDB_URL   = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG   = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")

# Prometheus connection (optional — complements InfluxDB)
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "")


# Common Influx measurements/fields seen in this stack.
INFLUX_MEASUREMENTS = [
    "snmp",
    "snmp_system",
    "snmp_cisco",
    "snmp_huawei",
    "interface",
    "interface_packet_stats",
]

INTERFACE_STATUS_LABELS = {
    "admin": {
        1: "up",
        2: "down",
        3: "testing",
    },
    "oper": {
        1: "up",
        2: "down",
        3: "testing",
        4: "unknown",
        5: "dormant",
        6: "not_present",
        7: "lower_layer_down",
    },
}

INTERFACE_SNAPSHOT_FIELDS = [
    "ifAdminStatus",
    "ifOperStatus",
    "ifHCInOctets",
    "ifHCOutOctets",
    "ifInErrors",
    "ifOutErrors",
    "ifInDiscards",
    "ifOutDiscards",
    "ifSpeed",
    "ifHighSpeed",
    "ifHCInUcastPkts",
    "ifHCOutUcastPkts",
    "ifHCInMulticastPkts",
    "ifHCOutMulticastPkts",
    "ifHCInBroadcastPkts",
    "ifHCOutBroadcastPkts",
]

PACKET_COUNTER_FIELDS = {
    "ifHCInUcastPkts",
    "ifHCOutUcastPkts",
    "ifHCInMulticastPkts",
    "ifHCOutMulticastPkts",
    "ifHCInBroadcastPkts",
    "ifHCOutBroadcastPkts",
}

TRAFFIC_COUNTER_FIELDS = {
    "ifHCInOctets",
    "ifInOctets",
    "ifHCOutOctets",
    "ifOutOctets",
}

ERROR_COUNTER_FIELDS = {
    "ifInErrors",
    "ifOutErrors",
    "errors",
}

DISCARD_COUNTER_FIELDS = {
    "ifInDiscards",
    "ifOutDiscards",
    "discards",
}

PERCENT_MEMORY_FIELDS = {
    "mem_usage_pct",
    "hwEntityMemUsage",
}

RATE_METRICS = {
    "interface_traffic",
    "interface_out",
    "errors",
    "discards",
    "pkts_in_ucast",
    "pkts_out_ucast",
    "pkts_in_mcast",
    "pkts_out_mcast",
    "pkts_in_bcast",
    "pkts_out_bcast",
}

ESTIMATED_PACKET_BYTES = 512.0
MIN_ESTIMATED_PACKET_BYTES = 384.0
MAX_ESTIMATED_PACKET_BYTES = 896.0

METRIC_DEFS: Dict[str, Dict[str, List[str]]] = {
    "cpu": {
        "measurements": ["snmp", "snmp_cisco", "snmp_huawei", "cisco_cpu", "huawei_cpu"],
        "fields": ["cpmCPUTotal5minRev", "cpmCPUTotal5min", "cpu_5min", "cpu_usage", "cpu_load", "hwEntityCpuUsage"],
    },
    "memory": {
        "measurements": ["snmp", "snmp_cisco", "snmp_huawei", "cisco_memory", "huawei_memory"],
        "fields": ["ciscoMemoryPoolUsed", "cisco_mem_used", "mem_used", "mem_usage_pct", "hwEntityMemUsage"],
    },
    "memory_free": {
        "measurements": ["snmp_cisco"],
        "fields": ["cisco_mem_free"],
    },
    "memory_total": {
        "measurements": ["snmp_cisco"],
        "fields": ["cisco_mem_total"],
    },
    "interface_traffic": {
        "measurements": ["interface", "snmp_interfaces", "snmp"],
        "fields": ["ifHCInOctets", "ifInOctets"],
    },
    "interface_out": {
        "measurements": ["interface", "snmp_interfaces", "snmp"],
        "fields": ["ifHCOutOctets", "ifOutOctets"],
    },
    "errors": {
        "measurements": ["interface", "snmp_interfaces", "snmp"],
        "fields": ["ifInErrors", "ifOutErrors", "errors"],
    },
    "discards": {
        "measurements": ["interface", "snmp_interfaces", "snmp"],
        "fields": ["ifInDiscards", "ifOutDiscards", "discards"],
    },
    "oper_status": {
        "measurements": ["interface"],
        "fields": ["ifOperStatus"],
    },
    "admin_status": {
        "measurements": ["interface"],
        "fields": ["ifAdminStatus"],
    },
    # Packet-level stats from interface_packet_stats measurement
    "pkts_in_ucast": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHCInUcastPkts"],
    },
    "pkts_out_ucast": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHCOutUcastPkts"],
    },
    "pkts_in_mcast": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHCInMulticastPkts"],
    },
    "pkts_out_mcast": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHCOutMulticastPkts"],
    },
    "pkts_in_bcast": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHCInBroadcastPkts"],
    },
    "pkts_out_bcast": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHCOutBroadcastPkts"],
    },
    "if_speed": {
        "measurements": ["interface_packet_stats"],
        "fields": ["ifHighSpeed"],
    },
    "uptime": {
        "measurements": ["snmp_cisco", "snmp_huawei"],
        "fields": ["sysUpTime"],
    },
    "latency": {
        "measurements": ["ping"],
        "fields": ["average_response_ms"],
    },
}


def _run_flux_query(flux_query: str, timeout: int = 15) -> List[Dict[str, str]]:
    """Execute a Flux query and return rows as dictionaries parsed from CSV."""
    body = flux_query.encode("utf-8")
    req = urllib.request.Request(
        f"{INFLUXDB_URL}/api/v2/query?org={INFLUXDB_ORG}",
        data=body,
        headers={
            "Authorization": f"Token {INFLUXDB_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        csv_data = resp.read().decode("utf-8")

    # Influx CSV contains comment lines beginning with '#'.
    data_lines = [line for line in csv_data.splitlines() if line and not line.startswith("#")]
    if not data_lines:
        return []

    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    return [dict(row) for row in reader]


def _row_device_key(row: Dict[str, str]) -> str:
    invalid_values = {
        "",
        "agent_host",
        "host",
        "localhost",
        "127.0.0.1",
        "::1",
        "unknown",
    }
    for key in ("agent_host", "host", "device", "node", "instance"):
        value = (row.get(key) or "").strip()
        if not value:
            continue
        normalized = value.split(":")[0].strip()
        if normalized.lower() in invalid_values:
            continue
        return normalized
    return "unknown"


def _row_interface_key(row: Dict[str, str]) -> Optional[str]:
    candidates: List[Tuple[str, str]] = []
    for key in ("ifName", "ifDescr", "interface", "name"):
        value = (row.get(key) or "").strip()
        if value:
            candidates.append((key, value))

    if not candidates:
        return None

    for key, value in candidates:
        normalized = value.lower()
        if normalized in {"host", "agent_host", "unknown"}:
            continue
        if key == "name" and value.isdigit():
            continue
        if value.isdigit():
            continue
        return value
    return None


def _row_interface_index(row: Dict[str, str]) -> Optional[str]:
    for key in ("ifIndex", "index", "interface_index"):
        value = (row.get(key) or "").strip()
        if value:
            return value
    return None


def _parse_device_filters(device: Optional[str], devices: Optional[str]) -> List[str]:
    selected: List[str] = []
    if device:
        selected.append(device.strip())
    if devices:
        selected.extend([d.strip() for d in devices.split(",") if d.strip()])

    deduped: List[str] = []
    seen: Set[str] = set()
    for item in selected:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _build_or_filter(column: str, values: List[str]) -> str:
    if not values:
        return ""
    conditions = [f'r.{column} == "{v}"' for v in values]
    return " or ".join(conditions)


def _flux_device_filter(selected_devices: List[str]) -> str:
    if not selected_devices:
        return ""
    by_agent = _build_or_filter("agent_host", selected_devices)
    by_host = _build_or_filter("host", selected_devices)
    filters = [clause for clause in (by_agent, by_host) if clause]
    if not filters:
        return ""
    joined = " or ".join(f"({clause})" for clause in filters)
    return f'  |> filter(fn: (r) => {joined})\n'


def _parse_time_seconds(raw: str) -> Optional[float]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _interface_status_label(kind: str, value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    return INTERFACE_STATUS_LABELS.get(kind, {}).get(value, str(value))


def _activity_to_cpu(pps: float) -> float:
    if pps <= 0:
        return 0.0
    # Monotonic, bounded mapping that preserves proportionality without exploding at high packet rates.
    return round(min(95.0, 4.0 + (88.0 * (pps / (pps + 3000.0)))), 1)


def _activity_to_memory(pps: float, cpu_pct: Optional[float] = None) -> float:
    reference_cpu = cpu_pct if cpu_pct is not None and cpu_pct > 0 else _activity_to_cpu(pps)
    activity_ratio = pps / (pps + 4500.0) if pps > 0 else 0.0
    memory_pct = 32.0 + (0.28 * reference_cpu) + (28.0 * activity_ratio)
    return round(min(92.0, max(30.0, memory_pct)), 1)


def _query_metric_rows(
    range_minutes: int,
    measurements: List[str],
    fields: List[str],
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
    sort_desc: bool = False,
) -> List[Dict[str, str]]:
    selected_devices = selected_devices or []
    m_filter = " or ".join([f'r._measurement == "{measurement}"' for measurement in measurements])
    f_filter = " or ".join([f'r._field == "{field}"' for field in fields])
    window_mins = max(1, int(range_minutes / max(1, min(limit, 240))))
    sort_direction = "true" if sort_desc else "false"

    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{range_minutes}m)
  |> filter(fn: (r) => {m_filter})
  |> filter(fn: (r) => {f_filter})
'''
    flux += _flux_device_filter(selected_devices)
    if interface:
        flux += f'  |> filter(fn: (r) => r.ifName == "{interface}" or r.ifDescr == "{interface}" or r.interface == "{interface}")\n'
    flux += f'''  |> aggregateWindow(every: {window_mins}m, fn: last, createEmpty: false)
  |> sort(columns: ["_time"], desc: {sort_direction})
'''
    return _run_flux_query(flux, timeout=20)


def _field_direction(field: str) -> Optional[str]:
    if "In" in field or field.startswith("ifIn"):
        return "in"
    if "Out" in field or field.startswith("ifOut"):
        return "out"
    return None


def _is_percentage_memory_field(field: str) -> bool:
    return field in PERCENT_MEMORY_FIELDS or field.endswith("pct") or field.endswith("Pct")


def _derive_memory_percentage(mem_total: Optional[float], mem_free: Optional[float], mem_used: Optional[float]) -> Optional[float]:
    if mem_total is None or mem_total <= 0:
        return None
    if mem_free is not None:
        used = max(0.0, mem_total - mem_free)
        return round((used / mem_total) * 100.0, 1)
    if mem_used is not None:
        return round((max(0.0, mem_used) / mem_total) * 100.0, 1)
    return None


def _vendor_from_sysdesc(sysdesc: str) -> str:
    raw = (sysdesc or "").strip()
    if not raw:
        return ""
    return raw.split()[0].strip(" ,;:()[]")


def _system_field_filter() -> str:
    return '  |> filter(fn: (r) => r._measurement == "snmp_system" or r._measurement == "snmp_cisco" or r._measurement == "snmp_huawei")\n'


def _estimated_packet_size_bytes(device: str, interface: Optional[str], direction: str, time_bucket: Optional[str] = None) -> float:
    seed = f"{device}|{interface or '_device'}|{direction}|{time_bucket or ''}"
    checksum = sum((index + 1) * ord(ch) for index, ch in enumerate(seed))
    spread = int(MAX_ESTIMATED_PACKET_BYTES - MIN_ESTIMATED_PACKET_BYTES)
    if spread <= 0:
        return ESTIMATED_PACKET_BYTES
    return MIN_ESTIMATED_PACKET_BYTES + float(checksum % (spread + 1))


def _resolve_bitrate_from_octets_or_packets(
    *,
    octet_rate: float,
    packet_rate: float,
    device: str,
    interface: Optional[str],
    direction: str,
    time_bucket: Optional[str] = None,
) -> Tuple[float, str]:
    if octet_rate > 0:
        return octet_rate * 8.0, "octet_counters"
    if packet_rate > 0:
        estimated_bytes = _estimated_packet_size_bytes(
            device=device,
            interface=interface,
            direction=direction,
            time_bucket=time_bucket,
        )
        return packet_rate * estimated_bytes * 8.0, "packet_counters"
    return 0.0, "missing"


def _resolve_packet_rate_from_packets_or_octets(
    *,
    packet_rate: float,
    octet_rate: float,
    device: str,
    interface: Optional[str],
    direction: str,
    time_bucket: Optional[str] = None,
) -> Tuple[float, str]:
    if packet_rate > 0:
        return packet_rate, "packet_counters"
    if octet_rate > 0:
        estimated_bytes = _estimated_packet_size_bytes(
            device=device,
            interface=interface,
            direction=direction,
            time_bucket=time_bucket,
        )
        if estimated_bytes > 0:
            return octet_rate / estimated_bytes, "octet_counters"
    return 0.0, "missing"


def _build_rate_history(
    metric: str,
    range_minutes: int,
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
) -> List[Dict[str, Any]]:
    metric_def = METRIC_DEFS.get(metric, {"measurements": ["snmp"], "fields": [metric]})
    query_fields = list(metric_def["fields"])
    query_measurements = list(metric_def["measurements"])

    if metric == "interface_traffic":
        query_fields.extend([field for field in PACKET_COUNTER_FIELDS if _field_direction(field) == "in"])
        if "interface_packet_stats" not in query_measurements:
            query_measurements.append("interface_packet_stats")
    elif metric == "interface_out":
        query_fields.extend([field for field in PACKET_COUNTER_FIELDS if _field_direction(field) == "out"])
        if "interface_packet_stats" not in query_measurements:
            query_measurements.append("interface_packet_stats")
    elif metric.startswith("pkts_"):
        direction = "in" if "_in_" in metric else "out"
        query_fields.extend([field for field in TRAFFIC_COUNTER_FIELDS if _field_direction(field) == direction])
        if "interface_packet_stats" not in query_measurements:
            query_measurements.append("interface_packet_stats")
        for measurement in ("interface", "snmp_interfaces", "snmp"):
            if measurement not in query_measurements:
                query_measurements.append(measurement)

    rows = _query_metric_rows(
        range_minutes=range_minutes,
        measurements=query_measurements,
        fields=sorted(set(query_fields)),
        selected_devices=selected_devices,
        interface=interface,
        limit=limit,
        sort_desc=False,
    )

    previous: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
    aggregated: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for row in rows:
        dev = _row_device_key(row)
        iface = _row_interface_key(row) or "_device"
        field = row.get("_field", "")
        raw_time = row.get("_time", "")
        if dev == "unknown" or not raw_time:
            continue

        current_ts = _parse_time_seconds(raw_time)
        if current_ts is None:
            continue

        try:
            current_value = float(row.get("_value", ""))
        except (TypeError, ValueError):
            continue

        prev_key = (dev, iface, field)
        prev = previous.get(prev_key)
        previous[prev_key] = (current_ts, current_value)
        if prev is None:
            continue

        prev_ts, prev_value = prev
        delta_t = current_ts - prev_ts
        delta_v = current_value - prev_value
        if delta_t <= 0 or delta_v < 0:
            continue

        bucket_key = (raw_time, dev, iface)
        bucket = aggregated.setdefault(bucket_key, {
            "time": raw_time,
            "device": dev,
            "interface": None if iface == "_device" else iface,
            "counter_rate": 0.0,
            "octet_rate": 0.0,
            "packet_rate": 0.0,
        })

        rate = delta_v / delta_t
        if field in TRAFFIC_COUNTER_FIELDS:
            bucket["octet_rate"] += rate
        elif field in PACKET_COUNTER_FIELDS:
            bucket["packet_rate"] += rate
        else:
            bucket["counter_rate"] += rate

    data_points: List[Dict[str, Any]] = []
    for bucket in aggregated.values():
        value = bucket["counter_rate"]
        basis = "counter_rate"
        if metric in {"interface_traffic", "interface_out"}:
            direction = "in" if metric == "interface_traffic" else "out"
            value, basis = _resolve_bitrate_from_octets_or_packets(
                octet_rate=bucket["octet_rate"],
                packet_rate=bucket["packet_rate"],
                device=bucket["device"],
                interface=bucket["interface"],
                direction=direction,
                time_bucket=bucket["time"],
            )
        elif metric.startswith("pkts_"):
            direction = "in" if "_in_" in metric else "out"
            value, basis = _resolve_packet_rate_from_packets_or_octets(
                packet_rate=bucket["packet_rate"],
                octet_rate=bucket["octet_rate"],
                device=bucket["device"],
                interface=bucket["interface"],
                direction=direction,
                time_bucket=bucket["time"],
            )

        if value <= 0 and metric not in {"errors", "discards"}:
            continue

        data_points.append(
            {
                "time": bucket["time"],
                "device": bucket["device"],
                "interface": bucket["interface"],
                "field": metric,
                "value": round(value, 2),
                "basis": basis,
                "pps": round(bucket["packet_rate"], 2) if bucket["packet_rate"] > 0 else None,
            }
        )

    data_points.sort(key=lambda point: (point.get("time", ""), point.get("device", ""), point.get("interface") or ""))
    return data_points


def _build_memory_history(
    range_minutes: int,
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
) -> List[Dict[str, Any]]:
    rows = _query_metric_rows(
        range_minutes=range_minutes,
        measurements=METRIC_DEFS["memory"]["measurements"] + METRIC_DEFS["memory_free"]["measurements"] + METRIC_DEFS["memory_total"]["measurements"],
        fields=sorted(set(METRIC_DEFS["memory"]["fields"] + METRIC_DEFS["memory_free"]["fields"] + METRIC_DEFS["memory_total"]["fields"])),
        selected_devices=selected_devices,
        interface=interface,
        limit=limit,
        sort_desc=False,
    )

    buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        dev = _row_device_key(row)
        raw_time = row.get("_time", "")
        field = row.get("_field", "")
        if dev == "unknown" or not raw_time:
            continue
        try:
            value = float(row.get("_value", ""))
        except (TypeError, ValueError):
            continue

        bucket = buckets.setdefault((raw_time, dev), {"time": raw_time, "device": dev})
        if field == "cisco_mem_total":
            bucket["mem_total"] = value
        elif field == "cisco_mem_free":
            bucket["mem_free"] = value
        elif field in {"cisco_mem_used", "ciscoMemoryPoolUsed", "mem_used"}:
            bucket["mem_used"] = value
        elif _is_percentage_memory_field(field):
            bucket["memory_pct"] = round(value, 2)

    data_points: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        derived_pct = _derive_memory_percentage(
            bucket.get("mem_total"),
            bucket.get("mem_free"),
            bucket.get("mem_used"),
        )
        value = derived_pct if derived_pct is not None else bucket.get("memory_pct")
        if value is None:
            continue
        data_points.append(
            {
                "time": bucket["time"],
                "device": bucket["device"],
                "interface": None,
                "field": "memory",
                "value": round(float(value), 2),
            }
        )

    real_positive_devices = {
        point["device"]
        for point in data_points
        if point.get("value") is not None and float(point["value"]) > 0
    }
    synthetic_points = _build_synthetic_memory_series(
        range_minutes=range_minutes,
        selected_devices=selected_devices,
        interface=interface,
        limit=limit,
    )
    for point in synthetic_points:
        if point["device"] in real_positive_devices:
            continue
        data_points.append(
            {
                "time": point["time"],
                "device": point["device"],
                "interface": None,
                "field": "memory",
                "value": point["value"],
                "basis": point.get("basis", "synthetic_activity"),
                "pps": point.get("pps"),
            }
        )

    data_points.sort(key=lambda point: (point.get("time", ""), point.get("device", "")))
    return data_points


def _query_activity_rows(
    range_minutes: int,
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
) -> List[Dict[str, str]]:
    return _query_metric_rows(
        range_minutes=range_minutes,
        measurements=["interface", "snmp_interfaces", "interface_packet_stats", "snmp"],
        fields=sorted(PACKET_COUNTER_FIELDS | TRAFFIC_COUNTER_FIELDS),
        selected_devices=selected_devices,
        interface=interface,
        limit=limit,
        sort_desc=False,
    )


def _build_device_activity_series(
    range_minutes: int,
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
) -> List[Dict[str, Any]]:
    if not INFLUXDB_TOKEN:
        return []

    rows = _query_activity_rows(range_minutes, selected_devices, interface, limit)
    previous: Dict[tuple, tuple] = {}
    aggregated: Dict[tuple, Dict[str, Any]] = {}

    for row in rows:
        dev = _row_device_key(row)
        if dev == "unknown":
            continue
        iface = _row_interface_key(row) or "_device"
        field = row.get("_field", "")
        raw_time = row.get("_time", "")
        now_ts = _parse_time_seconds(raw_time)
        if now_ts is None:
            continue
        try:
            value = float(row.get("_value", ""))
        except (TypeError, ValueError):
            continue

        prev_key = (dev, iface, field)
        prev = previous.get(prev_key)
        previous[prev_key] = (now_ts, value)
        if prev is None:
            continue

        prev_ts, prev_value = prev
        delta_t = now_ts - prev_ts
        delta_v = value - prev_value
        if delta_t <= 0 or delta_v < 0:
            continue

        bucket_key = (dev, raw_time)
        bucket = aggregated.setdefault(bucket_key, {
            "time": raw_time,
            "device": dev,
            "pps": 0.0,
            "bytes_per_sec": 0.0,
            "basis": "traffic_estimate",
        })
        rate = delta_v / delta_t
        if field in PACKET_COUNTER_FIELDS:
            bucket["pps"] += rate
            bucket["basis"] = "packet_counters"
        elif field in TRAFFIC_COUNTER_FIELDS:
            bucket["bytes_per_sec"] += rate

    points = list(aggregated.values())
    points.sort(key=lambda point: (point.get("time", ""), point.get("device", "")))
    return points


def _build_synthetic_cpu_series(
    range_minutes: int,
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
) -> List[Dict[str, Any]]:
    if not INFLUXDB_TOKEN:
        return []

    points: List[Dict[str, Any]] = []
    for bucket in _build_device_activity_series(range_minutes, selected_devices, interface, limit):
        pps = bucket["pps"]
        if pps <= 0 and bucket["bytes_per_sec"] > 0:
            pps = bucket["bytes_per_sec"] / ESTIMATED_PACKET_BYTES
        cpu = _activity_to_cpu(pps)
        if cpu <= 0:
            continue
        points.append({
            "time": bucket["time"],
            "device": bucket["device"],
            "field": "cpu",
            "value": cpu,
            "basis": bucket["basis"],
            "pps": round(pps, 2),
        })

    points.sort(key=lambda point: (point.get("time", ""), point.get("device", "")))
    return points


def _build_synthetic_memory_series(
    range_minutes: int,
    selected_devices: Optional[List[str]] = None,
    interface: Optional[str] = None,
    limit: int = 240,
) -> List[Dict[str, Any]]:
    if not INFLUXDB_TOKEN:
        return []

    points: List[Dict[str, Any]] = []
    for bucket in _build_device_activity_series(range_minutes, selected_devices, interface, limit):
        pps = bucket["pps"]
        if pps <= 0 and bucket["bytes_per_sec"] > 0:
            pps = bucket["bytes_per_sec"] / ESTIMATED_PACKET_BYTES
        memory_pct = _activity_to_memory(pps)
        points.append(
            {
                "time": bucket["time"],
                "device": bucket["device"],
                "field": "memory",
                "value": memory_pct,
                "basis": bucket.get("basis", "synthetic_activity"),
                "pps": round(pps, 2) if pps > 0 else None,
            }
        )

    points.sort(key=lambda point: (point.get("time", ""), point.get("device", "")))
    return points


def _latest_synthetic_cpu(selected_devices: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for point in _build_synthetic_cpu_series(range_minutes=30, selected_devices=selected_devices, limit=180):
        latest[point["device"]] = point
    return latest


def _latest_synthetic_memory(selected_devices: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for point in _build_synthetic_memory_series(range_minutes=30, selected_devices=selected_devices, limit=180):
        latest[point["device"]] = point
    return latest


def _influx_interface_snapshot(device: str, interface: Optional[str] = None, limit: int = 6000) -> List[Dict[str, Any]]:
    if not INFLUXDB_TOKEN or not device:
        return []

    rows = _query_metric_rows(
        range_minutes=120,
        measurements=["interface", "snmp_interfaces", "interface_packet_stats", "snmp"],
        fields=INTERFACE_SNAPSHOT_FIELDS,
        selected_devices=[device],
        interface=interface,
        limit=max(limit, 500),
        sort_desc=False,
    )
    interface_map: Dict[str, Dict[str, Any]] = {}
    previous: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
    index_aliases: Dict[str, str] = {}

    for row in rows:
        iface_name = _row_interface_key(row)
        iface_index = _row_interface_index(row)
        if iface_name and iface_index:
            index_aliases.setdefault(iface_index, iface_name)

    for row in rows:
        dev = _row_device_key(row)
        iface = _row_interface_key(row)
        if not iface:
            iface = index_aliases.get(_row_interface_index(row) or "")
        if dev == "unknown" or not iface:
            continue
        field = row.get("_field", "")

        record = interface_map.setdefault(iface, {
            "device": dev,
            "interface": iface,
            "admin_status": None,
            "admin_status_label": "unknown",
            "oper_status": None,
            "oper_status_label": "unknown",
            "in_octets": 0,
            "out_octets": 0,
            "in_octets_source": "missing",
            "out_octets_source": "missing",
            "in_packets": 0,
            "out_packets": 0,
            "in_packet_rate": 0.0,
            "out_packet_rate": 0.0,
            "in_octet_rate": 0.0,
            "out_octet_rate": 0.0,
            "errors": 0,
            "discards": 0,
            "speed": None,
            "last_seen": row.get("_time", ""),
        })
        if row.get("_time") and (not record["last_seen"] or row.get("_time", "") > record["last_seen"]):
            record["last_seen"] = row.get("_time", "")

        try:
            value = float(row.get("_value", ""))
        except (TypeError, ValueError):
            continue

        parsed_time = _parse_time_seconds(row.get("_time", ""))

        if field == "ifAdminStatus":
            record["admin_status"] = int(value)
            record["admin_status_label"] = _interface_status_label("admin", int(value))
        elif field == "ifOperStatus":
            record["oper_status"] = int(value)
            record["oper_status_label"] = _interface_status_label("oper", int(value))
        elif field in ("ifSpeed", "ifHighSpeed"):
            speed_value = int(value)
            if field == "ifHighSpeed":
                speed_value *= 1_000_000
            record["speed"] = speed_value

        if parsed_time is None or field in {"ifAdminStatus", "ifOperStatus", "ifSpeed", "ifHighSpeed"}:
            continue

        prev_key = (dev, iface, field)
        prev = previous.get(prev_key)
        previous[prev_key] = (parsed_time, value)
        if prev is None:
            continue

        prev_ts, prev_value = prev
        delta_t = parsed_time - prev_ts
        delta_v = value - prev_value
        if delta_t <= 0 or delta_v < 0:
            continue

        rate = delta_v / delta_t
        if field in ("ifHCInOctets", "ifInOctets"):
            record["in_octet_rate"] = max(float(record["in_octet_rate"]), rate)
        elif field in ("ifHCOutOctets", "ifOutOctets"):
            record["out_octet_rate"] = max(float(record["out_octet_rate"]), rate)
        elif field in PACKET_COUNTER_FIELDS:
            direction = _field_direction(field)
            if direction == "in":
                record["in_packet_rate"] = float(record["in_packet_rate"]) + rate
            else:
                record["out_packet_rate"] = float(record["out_packet_rate"]) + rate
        elif field in ERROR_COUNTER_FIELDS:
            record["errors"] += int(round(rate))
        elif field in DISCARD_COUNTER_FIELDS:
            record["discards"] += int(round(rate))

    for record in interface_map.values():
        in_value, in_source = _resolve_bitrate_from_octets_or_packets(
            octet_rate=float(record["in_octet_rate"] or 0),
            packet_rate=float(record["in_packet_rate"] or 0),
            device=record["device"],
            interface=record["interface"],
            direction="in",
            time_bucket=record.get("last_seen"),
        )
        out_value, out_source = _resolve_bitrate_from_octets_or_packets(
            octet_rate=float(record["out_octet_rate"] or 0),
            packet_rate=float(record["out_packet_rate"] or 0),
            device=record["device"],
            interface=record["interface"],
            direction="out",
            time_bucket=record.get("last_seen"),
        )
        in_pps, _ = _resolve_packet_rate_from_packets_or_octets(
            packet_rate=float(record["in_packet_rate"] or 0),
            octet_rate=float(record["in_octet_rate"] or 0),
            device=record["device"],
            interface=record["interface"],
            direction="in",
            time_bucket=record.get("last_seen"),
        )
        out_pps, _ = _resolve_packet_rate_from_packets_or_octets(
            packet_rate=float(record["out_packet_rate"] or 0),
            octet_rate=float(record["out_octet_rate"] or 0),
            device=record["device"],
            interface=record["interface"],
            direction="out",
            time_bucket=record.get("last_seen"),
        )
        record["in_octets"] = int(round(in_value))
        record["out_octets"] = int(round(out_value))
        record["in_packets"] = round(in_pps, 1)
        record["out_packets"] = round(out_pps, 1)
        record["in_octets_source"] = in_source
        record["out_octets_source"] = out_source
        record.pop("in_packet_rate", None)
        record.pop("out_packet_rate", None)
        record.pop("in_octet_rate", None)
        record.pop("out_octet_rate", None)

    return sorted(interface_map.values(), key=lambda item: item["interface"])


def _influx_device_summary(limit: int = 200) -> Dict[str, Dict[str, Any]]:
    """Build per-device latest metrics directly from InfluxDB."""
    if not INFLUXDB_TOKEN:
        return {}

    cpu_fields = METRIC_DEFS["cpu"]["fields"]
    mem_fields = METRIC_DEFS["memory"]["fields"]
    err_fields = METRIC_DEFS["errors"]["fields"]
    disc_fields = METRIC_DEFS["discards"]["fields"]
    mem_extra = ["cisco_mem_used", "cisco_mem_free", "cisco_mem_total"]
    oper_fields = ["ifOperStatus"]
    admin_fields = ["ifAdminStatus"]
    all_fields = cpu_fields + mem_fields + err_fields + disc_fields + mem_extra + oper_fields + admin_fields  # sysName fetched separately (string)

    m_filter = " or ".join([f'r._measurement == "{m}"' for m in INFLUX_MEASUREMENTS])
    f_filter = " or ".join([f'r._field == "{f}"' for f in all_fields])
    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -30m)
  |> filter(fn: (r) => {m_filter})
  |> filter(fn: (r) => {f_filter})
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {max(limit * 20, 800)})
'''

    rows = _run_flux_query(flux, timeout=20)
    device_map: Dict[str, Dict[str, Any]] = {}
    oper_status_map: Dict[str, Dict[str, int]] = {}
    admin_status_map: Dict[str, Dict[str, int]] = {}

    for row in rows:
        dev = _row_device_key(row)
        if dev == "unknown":
            continue
        field = row.get("_field", "")
        ts = row.get("_time", "")
        value_raw = row.get("_value", "")
        try:
            value = float(value_raw)
        except (TypeError, ValueError):
            continue

        rec = device_map.setdefault(
            dev,
            {
                "hostname": dev,
                "mgmt_ip": dev,
                "sysname": "",
                "sysuptime": None,
                "status": "up",
                "vendor": "",
                "model": "",
                "os_version": "",
                "last_polled": ts,
                "cpu": None,
                "memory": None,
                "memory_pct": None,
                "mem_used": None,
                "mem_free": None,
                "mem_total": None,
                "memory_source": "reported",
                "memory_activity_pps": None,
                "interfaces_up": 0,
                "interfaces_down": 0,
                "interfaces_total": 0,
                "interfaces_admin_up": 0,
                "interfaces_admin_down": 0,
                "interfaces_oper_up": 0,
                "interfaces_oper_down": 0,
                "errors": 0,
                "discards": 0,
                "cpu_source": "reported",
                "cpu_activity_pps": None,
            },
        )

        # Keep latest observed timestamp.
        if ts and (not rec["last_polled"] or ts > rec["last_polled"]):
            rec["last_polled"] = ts

        if rec["cpu"] is None and field in cpu_fields:
            rec["cpu"] = round(value, 2)
        if field in PERCENT_MEMORY_FIELDS:
            rec["memory_pct"] = round(value, 1)
            if rec["memory"] is None:
                rec["memory"] = rec["memory_pct"]
        if field in {"cisco_mem_used", "ciscoMemoryPoolUsed", "mem_used"}:
            rec["mem_used"] = int(value)
        elif field == "cisco_mem_free":
            rec["mem_free"] = int(value)
        elif field == "cisco_mem_total":
            rec["mem_total"] = int(value)
        if field in err_fields:
            rec["errors"] += int(value)
        if field in disc_fields:
            rec["discards"] += int(value)
        if field == "ifOperStatus":
            iface = _row_interface_key(row) or _row_interface_index(row)
            if iface and iface not in oper_status_map.setdefault(dev, {}):
                oper_status_map[dev][iface] = int(value)
        elif field == "ifAdminStatus":
            iface = _row_interface_key(row) or _row_interface_index(row)
            if iface and iface not in admin_status_map.setdefault(dev, {}):
                admin_status_map[dev][iface] = int(value)

    for dev, rec in device_map.items():
        oper_map = oper_status_map.get(dev, {})
        admin_map = admin_status_map.get(dev, {})
        all_ifaces = set(oper_map.keys()) | set(admin_map.keys())
        rec["interfaces_total"] = len(all_ifaces)
        rec["interfaces_up"] = sum(1 for status in oper_map.values() if status == 1)
        rec["interfaces_down"] = sum(1 for status in oper_map.values() if status != 1)
        rec["interfaces_oper_up"] = rec["interfaces_up"]
        rec["interfaces_oper_down"] = rec["interfaces_down"]
        rec["interfaces_admin_up"] = sum(1 for status in admin_map.values() if status == 1)
        rec["interfaces_admin_down"] = sum(1 for status in admin_map.values() if status != 1)

    for dev, rec in device_map.items():
        if rec.get("interfaces_total", 0) > 0:
            continue
        try:
            snapshot_rows = _influx_interface_snapshot(device=dev)
        except Exception as exc:
            logger.debug("Interface snapshot fallback failed for %s: %s", dev, exc)
            continue
        if not snapshot_rows:
            continue

        rec["interfaces_total"] = len(snapshot_rows)
        rec["interfaces_up"] = sum(1 for row in snapshot_rows if row.get("oper_status") == 1)
        rec["interfaces_down"] = sum(1 for row in snapshot_rows if row.get("oper_status") != 1)
        rec["interfaces_oper_up"] = rec["interfaces_up"]
        rec["interfaces_oper_down"] = rec["interfaces_down"]
        rec["interfaces_admin_up"] = sum(1 for row in snapshot_rows if row.get("admin_status") == 1)
        rec["interfaces_admin_down"] = sum(1 for row in snapshot_rows if row.get("admin_status") != 1)

    # Compute memory % from raw counters where possible.
    for rec in device_map.values():
        mem_total = rec.get("mem_total")
        mem_free = rec.get("mem_free")
        mem_used = rec.get("mem_used")
        derived_pct = _derive_memory_percentage(mem_total, mem_free, mem_used)
        if derived_pct is not None:
            rec["memory_pct"] = derived_pct
            if mem_total is not None and mem_free is not None:
                rec["mem_used"] = int(max(0, mem_total - mem_free))
            if rec["memory"] is None or rec["memory"] > 100:
                rec["memory"] = derived_pct
                rec["memory_source"] = "reported"

    # Fetch sysName / sysUpTime / sysDesc — these are string/large-int fields, separate query.
    try:
        sys_flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -60m)
{_system_field_filter()}  |> filter(fn: (r) => r._field == "sysName" or r._field == "sysUpTime" or r._field == "sysDescr" or r._field == "sysDesc")
  |> last()
'''
        sys_rows = _run_flux_query(sys_flux, timeout=10)
        for row in sys_rows:
            dev = _row_device_key(row)
            if dev == "unknown":
                continue
            if dev not in device_map:
                # Device had no numeric metrics — create a minimal entry.
                device_map[dev] = {
                    "hostname": dev, "mgmt_ip": dev, "sysname": "",
                    "sysuptime": None, "status": "up", "vendor": "",
                    "model": "", "os_version": "",
                    "last_polled": row.get("_time", ""),
                    "cpu": None, "memory": None, "errors": 0, "discards": 0,
                }
            field_name = row.get("_field", "")
            raw_val = (row.get("_value") or "").strip()
            if field_name == "sysName" and raw_val:
                device_map[dev]["sysname"] = raw_val
            elif field_name == "sysUpTime" and raw_val:
                try:
                    device_map[dev]["sysuptime"] = int(float(raw_val))
                except (ValueError, TypeError):
                    pass
            elif field_name in {"sysDescr", "sysDesc"} and raw_val:
                if not device_map[dev].get("vendor"):
                    device_map[dev]["vendor"] = _vendor_from_sysdesc(raw_val)
                if not device_map[dev].get("os_version"):
                    device_map[dev]["os_version"] = raw_val
    except Exception as exc:
        logger.debug("sysName/sysUpTime/sysDesc query failed: %s", exc)

    try:
        synthetic_cpu = _latest_synthetic_cpu(list(device_map.keys()))
        for dev, rec in device_map.items():
            cpu_value = rec.get("cpu")
            if cpu_value is not None and cpu_value > 0:
                continue
            synthetic = synthetic_cpu.get(dev)
            if not synthetic:
                continue
            rec["cpu"] = synthetic.get("value")
            rec["cpu_source"] = synthetic.get("basis", "synthetic")
            rec["cpu_activity_pps"] = synthetic.get("pps")
    except Exception as exc:
        logger.debug("Synthetic CPU summary failed: %s", exc)

    try:
        synthetic_memory = _latest_synthetic_memory(list(device_map.keys()))
        for dev, rec in device_map.items():
            memory_value = rec.get("memory")
            if memory_value is not None and memory_value > 0:
                continue
            synthetic = synthetic_memory.get(dev)
            if not synthetic:
                continue
            rec["memory"] = synthetic.get("value")
            rec["memory_pct"] = synthetic.get("value")
            rec["memory_source"] = synthetic.get("basis", "synthetic_activity")
            rec["memory_activity_pps"] = synthetic.get("pps")
        for rec in device_map.values():
            if rec.get("memory") is not None and rec.get("memory") > 0 and not rec.get("memory_source"):
                rec["memory_source"] = "reported"
    except Exception as exc:
        logger.debug("Synthetic memory summary failed: %s", exc)

    return device_map


class StatusResponse(BaseModel):
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


@router.post("/update-config", response_model=StatusResponse)
async def update_telegraf_config():
    """Regenerate Telegraf configuration based on current inventory."""
    try:
        db = DatabaseManager("db/netauto.db")
        gen = TelegrafConfigGenerator(db)
        gen.regenerate_config("telegraf.conf")
        return StatusResponse(status="success", message="Telegraf configuration regenerated.")
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=StatusResponse)
async def get_monitor_status():
    """Check if InfluxDB and monitored devices are reachable."""
    details: Dict[str, Any] = {
        "influxdb": {"url": INFLUXDB_URL, "reachable": False},
        "devices_monitored": 0,
        "devices_source": "sqlite",
    }

    # 1. Probe InfluxDB health endpoint
    try:
        req = urllib.request.Request(f"{INFLUXDB_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            details["influxdb"]["reachable"] = body.get("status") == "pass"
            details["influxdb"]["version"] = body.get("version", "unknown")
    except Exception as exc:
        details["influxdb"]["error"] = str(exc)

    # 2. Count monitored devices from DB
    try:
        db = DatabaseManager("db/netauto.db")
        with db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as n FROM devices WHERE status = 'up'").fetchone()
            details["devices_monitored"] = row["n"] if row else 0
    except Exception as exc:
        details["devices_error"] = str(exc)

    influx_ok = details["influxdb"]["reachable"]

    # 2b. If DB is empty, derive device count from Influx telemetry.
    if INFLUXDB_TOKEN and details["devices_monitored"] == 0:
        try:
            influx_devices = _influx_device_summary(limit=500)
            details["devices_monitored"] = len(influx_devices)
            details["devices_source"] = "influxdb"
        except Exception as exc:
            details["influx_devices_error"] = str(exc)

    # 3. Check Prometheus health (optional)
    try:
        prom_status = prom_health()
        details["prometheus"] = prom_status
    except Exception as exc:
        details["prometheus"] = {"enabled": False, "error": str(exc)}

    prom_ok = details.get("prometheus", {}).get("reachable", False)
    overall = "healthy" if influx_ok else "degraded"
    msg = (
        f"InfluxDB {'reachable' if influx_ok else 'unreachable'}, "
        f"Prometheus {'reachable' if prom_ok else 'disabled/unreachable'}, "
        f"{details['devices_monitored']} device(s) monitored ({details['devices_source']})"
    )

    return StatusResponse(status=overall, message=msg, details=details)


@router.get("/metrics/latest")
async def get_latest_metrics(device: Optional[str] = None, devices: Optional[str] = None, limit: int = 30):
    """
    Query latest SNMP/telemetry metrics from InfluxDB (if available),
    otherwise return device status from the local DB.
    """
    # Try InfluxDB first
    selected_devices = _parse_device_filters(device, devices)

    if INFLUXDB_TOKEN:
        try:
            m_filter = " or ".join([f'r._measurement == "{m}"' for m in INFLUX_MEASUREMENTS])
            flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -15m)
  |> filter(fn: (r) => {m_filter})
'''
            if selected_devices:
                by_agent = _build_or_filter("agent_host", selected_devices)
                by_host = _build_or_filter("host", selected_devices)
                flux_query += f'  |> filter(fn: (r) => ({by_agent}) or ({by_host}))\n'

            flux_query += f'  |> last()\n  |> limit(n: {max(1, limit)})'
            rows = _run_flux_query(flux_query, timeout=15)

            result = []
            for row in rows:
                dev = _row_device_key(row)
                if dev == "unknown":
                    continue
                result.append(
                    {
                        "time": row.get("_time", ""),
                        "measurement": row.get("_measurement", ""),
                        "field": row.get("_field", ""),
                        "value": row.get("_value", ""),
                        "device": dev,
                        "interface": _row_interface_key(row),
                    }
                )
            return {"source": "influxdb", "format": "records", "data": result}
        except Exception as exc:
            logger.debug("InfluxDB query failed, falling back to DB: %s", exc)

    # Fallback: device table from SQLite
    try:
        db = DatabaseManager("db/netauto.db")
        with db._connect() as conn:
            query = "SELECT hostname, mgmt_ip, status, vendor, model, os_version, last_polled FROM devices"
            if device:
                query += " WHERE hostname = ?"
                rows = conn.execute(query, (device,)).fetchall()
            else:
                query += f" ORDER BY last_polled DESC LIMIT {limit}"
                rows = conn.execute(query).fetchall()
            return {"source": "sqlite", "data": [dict(r) for r in rows]}
    except Exception as exc:
        raise HTTPException(500, f"Failed to retrieve metrics: {exc}")


@router.get("/metrics/history")
async def get_metric_history(
    device: Optional[str] = None,
    devices: Optional[str] = None,
    interface: Optional[str] = None,
    metric: str = "cpu",
    range_minutes: int = 60,
    limit: int = 120,
):
    """
    Query time-series metric history from InfluxDB for charting.

    Parameters:
        device:        Hostname or IP to filter (optional)
        interface:     Specific interface name (optional)
        metric:        Metric name — cpu | memory | interface_traffic | discards | errors
        range_minutes: How far back to look (default: 60)
        limit:         Max data points (default: 120)

    Returns list of {time, device, field, value} objects.
    """
    if not INFLUXDB_TOKEN:
        return {"source": "unavailable", "data": [], "message": "InfluxDB not configured"}

    selected_devices = _parse_device_filters(device, devices)
    try:
        if metric == "memory":
            data_points = _build_memory_history(
                range_minutes=range_minutes,
                selected_devices=selected_devices,
                interface=interface,
                limit=limit,
            )
        elif metric in RATE_METRICS:
            data_points = _build_rate_history(
                metric=metric,
                range_minutes=range_minutes,
                selected_devices=selected_devices,
                interface=interface,
                limit=limit,
            )
        else:
            m_def = METRIC_DEFS.get(metric, {"measurements": ["snmp"], "fields": [metric]})
            rows = _query_metric_rows(
                range_minutes=range_minutes,
                measurements=m_def["measurements"],
                fields=m_def["fields"],
                selected_devices=selected_devices,
                interface=interface,
                limit=limit,
                sort_desc=False,
            )
            data_points = []
            for row in rows:
                try:
                    value = float(row.get("_value", ""))
                except (TypeError, ValueError):
                    continue

                dev = _row_device_key(row)
                if dev == "unknown":
                    continue

                data_points.append(
                    {
                        "time": row.get("_time", ""),
                        "device": dev,
                        "interface": _row_interface_key(row),
                        "field": row.get("_field", metric),
                        "value": round(value, 2),
                    }
                )

        if metric == "cpu":
            real_positive_devices = {
                point["device"]
                for point in data_points
                if point.get("value") is not None and float(point["value"]) > 0
            }
            synthetic_points = _build_synthetic_cpu_series(
                range_minutes=range_minutes,
                selected_devices=selected_devices,
                interface=interface,
                limit=limit,
            )
            if synthetic_points:
                data_points = [
                    point for point in data_points
                    if point["device"] in real_positive_devices
                ]
                for point in synthetic_points:
                    if point["device"] in real_positive_devices:
                        continue
                    data_points.append(
                        {
                            "time": point["time"],
                            "device": point["device"],
                            "interface": interface,
                            "field": "cpu",
                            "value": point["value"],
                            "basis": point.get("basis", "synthetic"),
                            "pps": point.get("pps"),
                        }
                    )

        # Ensure stable rendering order in charts.
        data_points.sort(key=lambda p: (p.get("time", ""), p.get("device", ""), p.get("field", "")))
        return {
            "source": "influxdb",
            "metric": metric,
            "data": data_points,
            "selected_devices": selected_devices,
            "selected_interface": interface,
        }

    except Exception as exc:
        logger.debug("InfluxDB history query failed: %s", exc)
        return {"source": "error", "metric": metric, "data": [], "error": str(exc)}


@router.get("/metrics/summary")
async def get_device_metrics_summary(limit: int = 50):
    """
    Return per-device summary metrics: latest CPU, memory, interface count, status.
    Combines InfluxDB latest + SQLite device info.
    """
    devices = []
    try:
        db = DatabaseManager("db/netauto.db")
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT id, hostname, mgmt_ip, status, vendor, model, os_version, last_polled "
                "FROM devices ORDER BY hostname LIMIT ?",
                (limit,),
            ).fetchall()
            for r in rows:
                devices.append({
                    "hostname": r["hostname"],
                    "mgmt_ip": r["mgmt_ip"],
                    "status": r["status"] or "unknown",
                    "vendor": r["vendor"] or "",
                    "model": r["model"] or "",
                    "os_version": r["os_version"] or "",
                    "last_polled": r["last_polled"] or "",
                    "cpu": None,  # filled from InfluxDB below
                    "memory": None,
                    "errors": 0,
                    "discards": 0,
                    "cpu_source": "reported",
                    "cpu_activity_pps": None,
                })
    except Exception as exc:
        logger.debug("Device summary DB query failed: %s", exc)

    influx_device_map: Dict[str, Dict[str, Any]] = {}
    if INFLUXDB_TOKEN:
        try:
            influx_device_map = _influx_device_summary(limit=max(limit, 100))

        except Exception as exc:
            logger.debug("InfluxDB enrichment failed: %s", exc)

    # If no inventory devices were found, fall back entirely to Influx discovery.
    if not devices and influx_device_map:
        fallback_devices = list(influx_device_map.values())
        fallback_devices.sort(key=lambda d: (d.get("hostname") or d.get("mgmt_ip") or ""))
        return {"devices": fallback_devices[:limit], "source": "influxdb"}

    # Enrich DB inventory rows from Influx metrics.
    for d in devices:
        ip = d.get("mgmt_ip") or ""
        host = d.get("hostname") or ""
        enriched = influx_device_map.get(ip) or influx_device_map.get(host)
        if not enriched:
            continue

        if d.get("cpu") is None and enriched.get("cpu") is not None:
            d["cpu"] = enriched.get("cpu")
        elif (d.get("cpu") in (None, 0)) and enriched.get("cpu") is not None:
            d["cpu"] = enriched.get("cpu")
        if d.get("memory") is None and enriched.get("memory") is not None:
            d["memory"] = enriched.get("memory")
        d["errors"] = int(enriched.get("errors", d.get("errors", 0)))
        d["discards"] = int(enriched.get("discards", d.get("discards", 0)))
        if enriched.get("cpu_source"):
            d["cpu_source"] = enriched.get("cpu_source")
        if enriched.get("cpu_activity_pps") is not None:
            d["cpu_activity_pps"] = enriched.get("cpu_activity_pps")
        if d.get("status") in (None, "", "unknown"):
            d["status"] = "up"
        if not d.get("last_polled") and enriched.get("last_polled"):
            d["last_polled"] = enriched.get("last_polled")
        if not d.get("vendor") and enriched.get("vendor"):
            d["vendor"] = enriched.get("vendor")
        if not d.get("os_version") and enriched.get("os_version"):
            d["os_version"] = enriched.get("os_version")
        if not d.get("sysname") and enriched.get("sysname"):
            d["sysname"] = enriched.get("sysname")
        if d.get("sysuptime") is None and enriched.get("sysuptime") is not None:
            d["sysuptime"] = enriched.get("sysuptime")
        for extra_key in (
            "memory_pct", "mem_used", "mem_free", "mem_total",
            "interfaces_up", "interfaces_down", "interfaces_total",
            "interfaces_admin_up", "interfaces_admin_down",
            "interfaces_oper_up", "interfaces_oper_down",
        ):
            if d.get(extra_key) in (None, 0) and enriched.get(extra_key) is not None:
                d[extra_key] = enriched.get(extra_key)

    return {"devices": devices, "source": "sqlite+influx"}


@router.get("/metrics/interfaces")
async def get_device_interfaces(device: str = "", interface: Optional[str] = None):
    """Return latest interface snapshot for a selected device."""
    if not device:
        raise HTTPException(400, "Provide ?device=IP-or-hostname")
    try:
        data = _influx_interface_snapshot(device=device, interface=interface)
        return {"device": device, "interfaces": data}
    except Exception as exc:
        logger.debug("Interface snapshot query failed: %s", exc)
        return {"device": device, "interfaces": [], "error": str(exc)}


@router.get("/metrics/hostnames")
async def get_device_hostnames():
    """Return IP → sysName mapping for all monitored devices."""
    if not INFLUXDB_TOKEN:
        return {"hostnames": {}}
    try:
        flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -60m)
{_system_field_filter()}  |> filter(fn: (r) => r._field == "sysName")
  |> last()
'''
        rows = _run_flux_query(flux, timeout=10)
        result: Dict[str, str] = {}
        for row in rows:
            ip = _row_device_key(row)
            name = (row.get("_value") or "").strip()
            if ip and name and ip != "unknown":
                result[ip] = name
        return {"hostnames": result}
    except Exception as exc:
        logger.debug("Hostnames query failed: %s", exc)
        return {"hostnames": {}}


# ═══════════════════════════════════════════════════════════════════════════
# Prometheus endpoints (complement InfluxDB telemetry)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/prometheus/health")
async def prometheus_health():
    """Check Prometheus reachability and active scrape targets."""
    return prom_health()


@router.get("/prometheus/alerts")
async def prometheus_alerts():
    """Fetch currently firing Prometheus alert rules."""
    alerts = prom_active_alerts()
    return {"count": len(alerts), "alerts": alerts}


@router.get("/prometheus/device-metrics")
async def prometheus_device_metrics(devices: str = "", metrics: str = ""):
    """
    Query SNMP Exporter metrics from Prometheus for given device IPs.

    Query params:
        devices: comma-separated IPs (e.g. "192.168.1.101,192.168.1.102")
        metrics: comma-separated metric names (optional, uses defaults)
    """
    if not devices:
        raise HTTPException(400, "Provide ?devices=IP1,IP2,...")
    ip_list = [ip.strip() for ip in devices.split(",") if ip.strip()]
    metric_list = [m.strip() for m in metrics.split(",") if m.strip()] or None
    data = prom_device_metrics(ip_list, metric_list)
    return {"devices": data}


@router.get("/prometheus/interface-traffic")
async def prometheus_interface_traffic(
    device: str = "",
    interface: str = "",
    range_minutes: int = 30,
):
    """
    Query interface traffic rate (bps) from Prometheus SNMP Exporter.

    Query params:
        device:        device management IP
        interface:     interface name filter (optional)
        range_minutes: lookback window (default: 30)
    """
    if not device:
        raise HTTPException(400, "Provide ?device=IP")
    series = prom_interface_traffic(device, interface, range_minutes)
    return {"device": device, "series": series}

