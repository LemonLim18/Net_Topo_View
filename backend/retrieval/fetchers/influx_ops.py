import os
import urllib.request
import csv
import io
import logging
from typing import Dict, Any, List

logger = logging.getLogger("retrieval.fetchers.influx_ops")

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "netauto")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "telegraf")

async def fetch_historical_metrics_fallback(influx_client, device_name: str, window_min: int = 5) -> Dict[str, Any]:
    """
    Query InfluxDB directly for syslog logs as a fallback when the Redis cache is offline.
    """
    if not INFLUXDB_TOKEN:
        return {"logs": [], "status": "UNKNOWN", "warning": "InfluxDB token not configured."}
        
    flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{window_min}m)
  |> filter(fn: (r) => r._measurement == "syslog")
  |> filter(fn: (r) => r.host == "{device_name}" or r.device == "{device_name}")
  |> tail(n: 15)
'''
    try:
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
        
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            csv_data = resp.read().decode("utf-8")
            data_lines = [line for line in csv_data.splitlines() if line and not line.startswith("#")]
            reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
            
            logs = []
            for row in reader:
                if not row:
                    continue
                logs.append({
                    "time": row.get("_time"),
                    "msg": row.get("message") or row.get("_value") or "",
                    "severity": row.get("severity") or "info"
                })
            return {
                "status": "DEGRADED",
                "age": 0.0,
                "logs": logs,
                "interfaces": {}
            }
    except Exception as e:
        logger.warning("InfluxDB query failed: %s", e)
        return {"logs": [], "status": "UNKNOWN", "warning": f"InfluxDB query error: {str(e)}"}


def fetch_historical_metrics_sync(device_name: str, window_min: int = 5) -> Dict[str, Any]:
    """
    Synchronous helper for callers that need InfluxDB-backed history without an async boundary.
    """
    if not INFLUXDB_TOKEN:
        return {"logs": [], "status": "UNKNOWN", "warning": "InfluxDB token not configured."}

    flux_query = f'''
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -{window_min}m)
  |> filter(fn: (r) => r._measurement == "syslog")
  |> filter(fn: (r) => r.host == "{device_name}" or r.device == "{device_name}")
  |> tail(n: 15)
'''
    try:
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

        with urllib.request.urlopen(req, timeout=4.0) as resp:
            csv_data = resp.read().decode("utf-8")
            data_lines = [line for line in csv_data.splitlines() if line and not line.startswith("#")]
            reader = csv.DictReader(io.StringIO("\n".join(data_lines)))

            logs = []
            for row in reader:
                if not row:
                    continue
                logs.append({
                    "time": row.get("_time"),
                    "msg": row.get("message") or row.get("_value") or "",
                    "severity": row.get("severity") or "info"
                })
            return {
                "status": "DEGRADED" if logs else "UNKNOWN",
                "age": 0.0,
                "logs": logs,
                "interfaces": {}
            }
    except Exception as e:
        logger.warning("InfluxDB sync query failed: %s", e)
        return {"logs": [], "status": "UNKNOWN", "warning": f"InfluxDB query error: {str(e)}"}
