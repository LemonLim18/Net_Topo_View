import time
import math
import logging
from typing import Dict, Any, List, Optional
from retrieval.intent import classify_intent, extract_device_name, QueryType
from retrieval.fetchers.netbox import fetch_local_inventory_cache
from retrieval.fetchers.redis_ops import fetch_cached_live_status, get_redis_client
from retrieval.fetchers.influx_ops import fetch_historical_metrics_fallback
from retrieval.graph_engine import TopologyGraphManager

logger = logging.getLogger("retrieval.orchestrator")

# Budget limits
MAX_SYSLOG_EVENTS = 15
MAX_NEIGHBORS = 8
MAX_TIMELINE_CHANGES = 5
MAX_KB_SNIPPETS = 3

class RetrievalOrchestrator:
    def __init__(self, db_manager):
        self.db = db_manager
        self.redis = get_redis_client()
        self.graph_mgr = TopologyGraphManager()
        self._ensure_change_timeline_table()

    def _ensure_change_timeline_table(self):
        """Ensure the operational change event timeline table exists in SQLite."""
        ddl = """
        CREATE TABLE IF NOT EXISTS change_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            device_name TEXT NOT NULL,
            change_type TEXT NOT NULL,    -- 'ansible_deployment', 'manual_config', 'maintenance'
            operator TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
        try:
            with self.db._connect() as conn:
                conn.execute(ddl)
            logger.info("Change timeline database table verified.")
        except Exception as e:
            logger.error("Failed to verify change_timeline table: %s", e)

    def log_change_event(self, device_name: str, change_type: str, operator: str, description: str, status: str = "success"):
        """Log an operational change event to the timeline."""
        sql = """
        INSERT INTO change_timeline (device_name, change_type, operator, description, status, timestamp)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """
        try:
            with self.db._connect() as conn:
                conn.execute(sql, (device_name, change_type, operator, description, status))
            logger.info("Logged change event to timeline for '%s'.", device_name)
        except Exception as e:
            logger.error("Failed to log change event: %s", e)

    async def gather_context(self, user_prompt: str, target_device: str = None) -> str:
        """
        Orchestrate multi-source context retrieval, dynamic trust scoring, and context budgeting.
        """
        # Step 1: Intent & target extraction
        intent = classify_intent(user_prompt)
        extracted = extract_device_name(user_prompt)
        device = target_device or extracted

        # Rebuild graph if needed
        self.graph_mgr.rebuild_graph_from_db(self.db)

        # Step 2: Fetch and build contexts
        inventory = None
        if device:
            try:
                inventory = await fetch_local_inventory_cache(self.db, device)
            except Exception as e:
                logger.debug("Failed querying netbox local cache: %s", e)
                inventory = {"error": f"Failed querying inventory: {str(e)}"}

        live_state = None
        if intent == QueryType.TROUBLESHOOT and device:
            try:
                live_state = await fetch_cached_live_status(self.redis, device)
                
                # Check freshness - trigger asynchronous poll if stale (>30s)
                if live_state and live_state.get("age", 999.0) > 30.0:
                    from retrieval.tasks import refresh_device_status_task
                    # Run in background via Celery or run locally inside a thread if Celery isn't configured
                    try:
                        refresh_device_status_task.delay(device)
                    except Exception:
                        # Direct task call for local executor environments
                        refresh_device_status_task(device)
            except Exception as e:
                # Graceful degradation fallback to InfluxDB recent sliding window
                try:
                    live_state = await fetch_historical_metrics_fallback(None, device, window_min=10)
                    live_state["warning"] = "Redis Cache offline. Telemetry fallback loaded."
                except Exception as ex:
                    live_state = {"error": f"Failed live state fallback: {str(ex)}"}

        # Fetch recent timeline changes
        changes = []
        if device:
            try:
                changes = self._query_timeline_changes(device, limit=MAX_TIMELINE_CHANGES)
            except Exception as e:
                logger.warning("Timeline query failed: %s", e)
                changes = [{"error": "Change timeline store unreachable."}]

        # Compute dynamic trust score
        trust_score = self._calculate_trust(live_state, intent)

        # Assemble markdown context
        return self._format_context_markdown(intent, inventory, live_state, changes, trust_score)

    def _query_timeline_changes(self, device_name: str, limit: int) -> List[Dict[str, Any]]:
        sql = """
        SELECT timestamp, change_type, operator, description, status 
        FROM change_timeline 
        WHERE device_name = ? 
        ORDER BY timestamp DESC LIMIT ?
        """
        with self.db._connect() as conn:
            rows = conn.execute(sql, (device_name, limit)).fetchall()
            return [
                {
                    "time": r[0],
                    "type": r[1],
                    "operator": r[2],
                    "desc": r[3],
                    "status": r[4]
                }
                for r in rows
            ]

    def _calculate_trust(self, live_state: Optional[Dict], intent: QueryType) -> float:
        """
        Dynamic trust score calculation using:
        Trust = Source Weight * Freshness Weight * Confidence Weight
        """
        if not live_state or "error" in live_state:
            return 0.0
            
        # Source Weight
        source_weight = 0.9
        if "warning" in live_state:
            source_weight = 0.7  # InfluxDB fallback weight
            
        # Freshness Weight: 1.0 if <=30s, decays exponentially to 0.1 at 10 minutes (600s)
        age = live_state.get("age", 0.0)
        if age <= 30.0:
            freshness_weight = 1.0
        else:
            # Exponential decay factor lambda = 0.005
            freshness_weight = max(0.1, math.exp(-0.005 * (age - 30)))
            
        # Confidence Weight: 1.0 for matches, 0.8 for fallbacks
        confidence_weight = 1.0 if intent == QueryType.TROUBLESHOOT else 0.8
        
        return round(source_weight * freshness_weight * confidence_weight, 2)

    def _format_context_markdown(self, intent: QueryType, inventory: Optional[Dict], live_state: Optional[Dict], changes: List[Dict], trust: float) -> str:
        blocks = []
        blocks.append(f"## Network AI Retrieval Context (Intent: {intent.upper()})")
        
        if inventory:
            if "error" in inventory:
                blocks.append(f"### [TRUST: 0.0] Authoritative Inventory (Unavailable)\n- {inventory['error']}")
            else:
                ifaces_md = []
                for iface in inventory.get("interfaces", [])[:MAX_NEIGHBORS]:
                    ifaces_md.append(f"  * {iface['name']}: IP={iface['ip']}, Status={iface['status']}, MAC={iface['mac']}")
                blocks.append(
                    f"### [TRUST: 1.0] Authoritative Inventory Cache\n"
                    f"- Device Name: {inventory.get('name')}\n"
                    f"- Platform / Vendor: {inventory.get('platform')}\n"
                    f"- Model: {inventory.get('model')}\n"
                    f"- Role: {inventory.get('role')}\n"
                    f"- Serial Number: {inventory.get('serial')}\n"
                    f"- Hardware Status: {inventory.get('status')}\n"
                    f"- Interfaces Configured:\n" + "\n".join(ifaces_md)
                )

        if live_state:
            if "error" in live_state:
                blocks.append(f"### [TRUST: 0.0] Live Telemetry Cache (Unavailable)\n- {live_state['error']}")
            else:
                logs_md = []
                for log in live_state.get("logs", [])[:MAX_SYSLOG_EVENTS]:
                    logs_md.append(f"  * [{log.get('time', 'recent')}] {log.get('msg')}")
                
                warning_suffix = f" ({live_state['warning']})" if "warning" in live_state else ""
                
                blocks.append(
                    f"### [TRUST: {trust}] Live Telemetry Cache{warning_suffix}\n"
                    f"- Operational Status: {live_state.get('status')}\n"
                    f"- Ephemeral State Cache Age: {round(live_state.get('age', 0.0), 1)}s\n"
                    f"- Recent Logs & Alerts:\n" + ("\n".join(logs_md) if logs_md else "  * No active syslog alerts.")
                )

        if changes:
            change_md = []
            for c in changes:
                if "error" in c:
                    change_md.append(f"  * {c['error']}")
                else:
                    change_md.append(f"  * [{c['time']}] {c['type']} by {c['operator']}: {c['desc']} ({c['status']})")
            blocks.append(
                "### [TRUST: 1.0] Recent Change Event Timeline\n" + 
                ("\n".join(change_md) if change_md else "  * No recent changes logged.")
            )

        return "\n\n".join(blocks)
