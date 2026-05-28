import time
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("retrieval.tasks")

# Safe imports
try:
    from celery import Celery
    celery_available = True
except ImportError:
    celery_available = False

try:
    import redis
    redis_available = True
except ImportError:
    redis_available = False

# Fallback local lock set
_LOCAL_POLL_LOCKS = set()

# Initialize Celery app if Celery is installed
if celery_available:
    celery_app = Celery('network_ops', broker='redis://localhost:6379/0')
else:
    celery_app = None

def get_redis_client():
    if not redis_available:
        return None
    try:
        client = redis.Redis(host='localhost', port=6379, db=0, socket_timeout=2.0)
        client.ping()
        return client
    except Exception:
        return None

# Celery task wrapper
def task(*args, **kwargs):
    def decorator(func):
        if celery_app:
            return celery_app.task(*args, **kwargs)(func)
        return func
    return decorator

@task(name='tasks.refresh_device_status')
def refresh_device_status_task(device_name: str):
    """
    Background worker task to poll device status and update caches.
    Ensures concurrency control via Redis lock or local fallback.
    """
    lock_key = f"locks:poll:{device_name}"
    client = get_redis_client()
    
    # 1. Acquire Lock
    if client:
        try:
            # SETNX distributed lock with 15s expiry
            if not client.set(lock_key, "1", nx=True, ex=15):
                logger.info("Polling for device '%s' already in progress (Redis locked). Skipping.", device_name)
                return "SKIPPED_LOCKED"
        except Exception as e:
            logger.warning("Redis lock acquisition failed: %s", e)
            client = None

    if not client:
        # Local fallback lock
        if device_name in _LOCAL_POLL_LOCKS:
            logger.info("Polling for device '%s' already in progress (local locked). Skipping.", device_name)
            return "SKIPPED_LOCKED"
        _LOCAL_POLL_LOCKS.add(device_name)

    try:
        logger.info("Executing background status refresh for device: %s", device_name)
        
        # Simulating connection/polling (under real system, this uses netmiko/napalm)
        time.sleep(1.0)
        
        # Create mock interfaces state
        interfaces_status = {
            "status": "UP",
            "timestamp": time.time(),
            "interfaces": {
                "GigabitEthernet1": {"status": "up", "ip": "192.0.2.1"},
                "GigabitEthernet2": {"status": "up", "ip": "10.10.10.1"}
            }
        }
        
        # Write back to cache
        if client:
            try:
                client.set(f"ops:state:interfaces:{device_name}", json.dumps(interfaces_status), ex=30)
            except Exception as e:
                logger.warning("Failed writing to Redis cache: %s", e)
        
        # Also sync to local in-memory fallback inside redis_ops
        from retrieval.fetchers.redis_ops import set_cached_live_status_local
        set_cached_live_status_local(device_name, "UP", interfaces_status["interfaces"])
        
        logger.info("Completed background status refresh for device: %s", device_name)
        return "SUCCESS"
        
    finally:
        # 2. Release Lock
        if client:
            try:
                client.delete(lock_key)
            except Exception:
                pass
        else:
            _LOCAL_POLL_LOCKS.discard(device_name)
