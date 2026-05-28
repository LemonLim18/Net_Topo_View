import os
import json
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("retrieval.fetchers.redis_ops")

# Thread-safe in-memory cache to act as a fallback when Redis is offline or not installed
_IN_MEMORY_STATUS_CACHE: Dict[str, Any] = {}
_IN_MEMORY_SYSLOG_CACHE: Dict[str, List[Dict[str, Any]]] = {}

_REDIS_BYPASS = False

# Overrides for dynamic configuration
_OVERRIDE_REDIS_HOST: Optional[str] = None
_OVERRIDE_REDIS_PORT: Optional[int] = None
_OVERRIDE_REDIS_PASSWORD: Optional[str] = None

def set_redis_bypass(bypass: bool):
    global _REDIS_BYPASS
    _REDIS_BYPASS = bypass
    if bypass:
        os.environ["REDIS_BYPASS"] = "1"
    else:
        os.environ["REDIS_BYPASS"] = "0"

def set_redis_config(host: str, port: int, password: Optional[str]):
    global _OVERRIDE_REDIS_HOST, _OVERRIDE_REDIS_PORT, _OVERRIDE_REDIS_PASSWORD
    _OVERRIDE_REDIS_HOST = host
    _OVERRIDE_REDIS_PORT = port
    _OVERRIDE_REDIS_PASSWORD = password

# Safe redis client acquisition
def get_redis_client():
    global _REDIS_BYPASS, _OVERRIDE_REDIS_HOST, _OVERRIDE_REDIS_PORT, _OVERRIDE_REDIS_PASSWORD
    if _REDIS_BYPASS or os.environ.get("REDIS_BYPASS", "0") == "1":
        logger.debug("Redis connection is bypassed by user request")
        return None
    try:
        import redis
        host = _OVERRIDE_REDIS_HOST or os.environ.get("REDIS_HOST", "localhost")
        port_env = os.environ.get("REDIS_PORT", "6379")
        port = _OVERRIDE_REDIS_PORT or (int(port_env) if port_env.isdigit() else 6379)
        password = _OVERRIDE_REDIS_PASSWORD or os.environ.get("REDIS_PASSWORD", None)
        
        # Normalize empty string password to None
        if password == "":
            password = None
            
        client = redis.Redis(host=host, port=port, password=password, db=0, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as e:
        logger.debug("Redis not available, using in-memory operational cache fallback. Reason: %s", e)
        return None

async def fetch_cached_live_status(redis_client, device_name: str) -> Dict[str, Any]:
    """
    Fetch ephemeral interface states and syslog events for a device.
    Falls back to local memory if Redis connection is not established.
    """
    if redis_client is None:
        redis_client = get_redis_client()

    # If Redis is active
    if redis_client is not None:
        try:
            status_bytes = redis_client.get(f"ops:state:interfaces:{device_name}")
            # Get logs from sorted set (last 10 minutes)
            now = time.time()
            log_bytes_list = redis_client.zrangebyscore(
                f"ops:syslog:stream:{device_name}", 
                min=now - 600, 
                max=now
            )
            
            status = {}
            if status_bytes:
                status = json.loads(status_bytes.decode('utf-8'))
                
            logs = []
            for b in log_bytes_list:
                logs.append(json.loads(b.decode('utf-8')))
                
            age = now - status.get("timestamp", now) if status else 999.0
            
            freshness_class = "live" if age <= 30 else "warm" if age <= 120 else "stale"
            return {
                "status": status.get("status", "UNKNOWN"),
                "age": age,
                "freshness_class": freshness_class,
                "source": "redis" if logs or status else "memory",
                "logs": logs,
                "interfaces": status.get("interfaces", {}),
            }
        except Exception as e:
            logger.warning("Redis operation failed, falling back to in-memory store: %s", e)

    # Fallback in-memory query
    now = time.time()
    status = _IN_MEMORY_STATUS_CACHE.get(device_name, {})
    all_logs = _IN_MEMORY_SYSLOG_CACHE.get(device_name, [])
    
    # Filter logs to last 10 minutes
    logs = [log for log in all_logs if now - log.get("timestamp", 0) <= 600]
    _IN_MEMORY_SYSLOG_CACHE[device_name] = logs  # purge older logs
    
    age = now - status.get("timestamp", now) if status else 999.0
    
    freshness_class = "live" if age <= 30 else "warm" if age <= 120 else "stale"
    return {
        "status": status.get("status", "UNKNOWN"),
        "age": age,
        "freshness_class": freshness_class,
        "source": "memory",
        "logs": logs,
        "interfaces": status.get("interfaces", {}),
    }

def set_cached_live_status_local(device_name: str, status_str: str, interfaces: Dict[str, Any]):
    """Helper to update the in-memory cache directly (for testing/fallback)."""
    _IN_MEMORY_STATUS_CACHE[device_name] = {
        "status": status_str,
        "timestamp": time.time(),
        "interfaces": interfaces
    }

def add_cached_syslog_local(device_name: str, message: str, severity: str = "warning"):
    """Helper to append a syslog to the in-memory cache (for testing/fallback)."""
    if device_name not in _IN_MEMORY_SYSLOG_CACHE:
        _IN_MEMORY_SYSLOG_CACHE[device_name] = []
    _IN_MEMORY_SYSLOG_CACHE[device_name].append({
        "timestamp": time.time(),
        "msg": message,
        "severity": severity
    })


def fetch_cached_live_status_sync(redis_client, device_name: str) -> Dict[str, Any]:
    """
    Synchronous helper mirroring fetch_cached_live_status for prompt assembly.
    """
    if redis_client is None:
        redis_client = get_redis_client()

    if redis_client is not None:
        try:
            status_bytes = redis_client.get(f"ops:state:interfaces:{device_name}")
            now = time.time()
            log_bytes_list = redis_client.zrangebyscore(
                f"ops:syslog:stream:{device_name}",
                min=now - 600,
                max=now,
            )

            status = {}
            if status_bytes:
                status = json.loads(status_bytes.decode("utf-8"))

            logs = []
            for b in log_bytes_list:
                logs.append(json.loads(b.decode("utf-8")))

            age = now - status.get("timestamp", now) if status else 999.0
            freshness_class = "live" if age <= 30 else "warm" if age <= 120 else "stale"
            return {
                "status": status.get("status", "UNKNOWN"),
                "age": age,
                "freshness_class": freshness_class,
                "source": "redis" if logs or status else "memory",
                "logs": logs,
                "interfaces": status.get("interfaces", {}),
            }
        except Exception as e:
            logger.warning("Redis sync operation failed, falling back to in-memory store: %s", e)

    now = time.time()
    status = _IN_MEMORY_STATUS_CACHE.get(device_name, {})
    all_logs = _IN_MEMORY_SYSLOG_CACHE.get(device_name, [])
    logs = [log for log in all_logs if now - log.get("timestamp", 0) <= 600]
    _IN_MEMORY_SYSLOG_CACHE[device_name] = logs
    age = now - status.get("timestamp", now) if status else 999.0
    freshness_class = "live" if age <= 30 else "warm" if age <= 120 else "stale"
    return {
        "status": status.get("status", "UNKNOWN"),
        "age": age,
        "freshness_class": freshness_class,
        "source": "memory",
        "logs": logs,
        "interfaces": status.get("interfaces", {}),
    }
