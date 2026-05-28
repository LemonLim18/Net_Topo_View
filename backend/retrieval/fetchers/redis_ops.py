import json
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("retrieval.fetchers.redis_ops")

# Thread-safe in-memory cache to act as a fallback when Redis is offline or not installed
_IN_MEMORY_STATUS_CACHE: Dict[str, Any] = {}
_IN_MEMORY_SYSLOG_CACHE: Dict[str, List[Dict[str, Any]]] = {}

# Safe redis client acquisition
def get_redis_client():
    try:
        import redis
        client = redis.Redis(host='localhost', port=6379, db=0, socket_timeout=2.0)
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
            
            return {
                "status": status.get("status", "UNKNOWN"),
                "age": age,
                "logs": logs,
                "interfaces": status.get("interfaces", {})
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
    
    return {
        "status": status.get("status", "UNKNOWN"),
        "age": age,
        "logs": logs,
        "interfaces": status.get("interfaces", {})
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
