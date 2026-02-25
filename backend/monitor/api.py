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
