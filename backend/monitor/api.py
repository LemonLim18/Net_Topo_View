from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import logging
import urllib.request
import json
from db.manager import DatabaseManager
from monitor.telegraf_gen import TelegrafConfigGenerator

router = APIRouter(prefix="/monitor", tags=["monitor"])
logger = logging.getLogger("api.monitor")

# InfluxDB connection (configure via env vars)
INFLUXDB_URL   = os.getenv("INFLUXDB_URL", "http://192.168.1.250:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG   = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")


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
    overall = "healthy" if influx_ok else "degraded"
    msg = (
        f"InfluxDB {'reachable' if influx_ok else 'unreachable'}, "
        f"{details['devices_monitored']} device(s) monitored"
    )

    return StatusResponse(status=overall, message=msg, details=details)


@router.get("/metrics/latest")
async def get_latest_metrics(device: Optional[str] = None, limit: int = 30):
    """
    Query latest SNMP/telemetry metrics from InfluxDB (if available),
    otherwise return device status from the local DB.
    """
    # Try InfluxDB first
    if INFLUXDB_TOKEN:
        try:
            flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "snmp" or r._measurement == "interface")
'''
            if device:
                flux_query += f'  |> filter(fn: (r) => r.agent_host == "{device}")\n'
            flux_query += f'  |> last()\n  |> limit(n: {limit})'

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
            with urllib.request.urlopen(req, timeout=10) as resp:
                csv_data = resp.read().decode("utf-8")
                return {"source": "influxdb", "format": "csv", "data": csv_data}
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
    metric: str = "cpu",
    range_minutes: int = 60,
    limit: int = 120,
):
    """
    Query time-series metric history from InfluxDB for charting.

    Parameters:
        device:        Hostname or IP to filter (optional)
        metric:        Metric name — cpu | memory | interface_traffic | errors
        range_minutes: How far back to look (default: 60)
        limit:         Max data points (default: 120)

    Returns list of {time, device, field, value} objects.
    """
    if not INFLUXDB_TOKEN:
        return {"source": "unavailable", "data": [], "message": "InfluxDB not configured"}

    # Map friendly metric names to Flux measurement + field
    metric_map = {
        "cpu": ("snmp", "cpmCPUTotal5minRev"),
        "memory": ("snmp", "ciscoMemoryPoolUsed"),
        "interface_traffic": ("interface", "ifHCInOctets"),
        "interface_out": ("interface", "ifHCOutOctets"),
        "errors": ("interface", "ifInErrors"),
        "latency": ("ping", "average_response_ms"),
    }

    measurement, field = metric_map.get(metric, ("snmp", metric))

    flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{range_minutes}m)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r._field == "{field}")
'''
    if device:
        flux += f'  |> filter(fn: (r) => r.agent_host == "{device}" or r.host == "{device}")\n'
    flux += f'''  |> aggregateWindow(every: {max(1, range_minutes // limit)}m, fn: mean)
  |> yield(name: "mean")
  |> limit(n: {limit})
'''

    try:
        body = flux.encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            csv_data = resp.read().decode("utf-8")

        # Parse CSV into structured data points
        data_points = []
        lines = csv_data.strip().split("\n")
        if len(lines) > 1:
            # Skip header comments and blank lines
            for line in lines[1:]:
                if line.startswith("#") or not line.strip() or line.startswith(","):
                    continue
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                try:
                    ts = parts[5] if len(parts) > 5 else ""
                    value = float(parts[6]) if parts[6] else None
                    device_name = parts[4] if len(parts) > 4 else "unknown"
                    if value is not None:
                        data_points.append({
                            "time": ts,
                            "device": device_name,
                            "field": field,
                            "value": round(value, 2),
                        })
                except (ValueError, IndexError):
                    continue

        return {"source": "influxdb", "metric": metric, "data": data_points}

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
                })
    except Exception as exc:
        logger.debug("Device summary DB query failed: %s", exc)

    # Enrich with latest InfluxDB metrics (best-effort)
    if INFLUXDB_TOKEN and devices:
        try:
            flux = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "snmp")
  |> filter(fn: (r) => r._field == "cpmCPUTotal5minRev" or r._field == "ciscoMemoryPoolUsed")
  |> last()
  |> limit(n: 200)
'''
            body = flux.encode("utf-8")
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
            with urllib.request.urlopen(req, timeout=10) as resp:
                csv_data = resp.read().decode("utf-8")

            # Build device→metric map
            metric_map: Dict[str, Dict[str, float]] = {}
            for line in csv_data.strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                try:
                    dev = parts[4]
                    fld = parts[3] if len(parts) > 3 else ""
                    val = float(parts[6])
                    if dev not in metric_map:
                        metric_map[dev] = {}
                    metric_map[dev][fld] = val
                except (ValueError, IndexError):
                    continue

            for d in devices:
                ip = d["mgmt_ip"]
                host = d["hostname"]
                m = metric_map.get(ip) or metric_map.get(host) or {}
                if "cpmCPUTotal5minRev" in m:
                    d["cpu"] = round(m["cpmCPUTotal5minRev"], 1)
                if "ciscoMemoryPoolUsed" in m:
                    d["memory"] = round(m["ciscoMemoryPoolUsed"], 1)

        except Exception as exc:
            logger.debug("InfluxDB enrichment failed: %s", exc)

    return {"devices": devices}
