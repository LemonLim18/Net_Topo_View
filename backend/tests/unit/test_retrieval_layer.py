import pytest
import sqlite3
import time
from db.manager import DatabaseManager
from retrieval.intent import classify_intent, extract_device_name, QueryType
from retrieval.fetchers.redis_ops import set_cached_live_status_local, fetch_cached_live_status
from retrieval.graph_engine import TopologyGraphManager
from retrieval.orchestrator import RetrievalOrchestrator

class MockDBManager:
    def __init__(self, conn):
        self.conn = conn
    def _connect(self):
        class ConnContext:
            def __init__(self, conn):
                self.conn = conn
            def __enter__(self):
                return self.conn
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return ConnContext(self.conn)

    def get_device_by_hostname(self, name):
        class DeviceMock:
            id = 1
            hostname = "edge-r1"
            vendor = "cisco_ios"
            model = "ISR4331"
            role = "edge"
            serial_number = "SR12345"
            status = "active"
            ssh_user = "admin"
            ssh_password = "password"
            ssh_port = 22
            last_polled = None
        if name == "edge-r1":
            return DeviceMock()
        return None

    def get_device_by_management_ip(self, ip):
        return None

    def get_interfaces_for_device(self, dev_id):
        class InterfaceMock:
            name = "GigabitEthernet1"
            ip_address = "192.0.2.1"
            subnet_mask = "255.255.255.252"
            mac_address = "00:11:22:33:44:55"
            speed = "1000"
            status = "up"
        return [InterfaceMock()]

@pytest.mark.anyio
async def test_intent_classification():
    assert classify_intent("Generate a VLAN playbook for edge-r1") == QueryType.CONFIG
    assert classify_intent("Why OSPF is flapping?") == QueryType.TROUBLESHOOT
    assert classify_intent("What devices fail if core-r1 goes down?") == QueryType.IMPACT
    assert classify_intent("IP configuration of edge-r1") == QueryType.INVENTORY
    assert extract_device_name("Show OSPF logs for edge-r1 now") == "edge-r1"

@pytest.mark.anyio
async def test_graph_blast_radius():
    mgr = TopologyGraphManager()
    if hasattr(mgr.planned_graph, 'clear'):
        mgr.planned_graph.clear()
    if hasattr(mgr.observed_graph, 'clear'):
        mgr.observed_graph.clear()
    # Build a small topology: core-1 connects to spine-1 and spine-2. spine-1 connects to leaf-1.
    mgr.observed_graph.add_node("core-1", role="core")
    mgr.observed_graph.add_node("spine-1", role="distribution")
    mgr.observed_graph.add_node("spine-2", role="distribution")
    mgr.observed_graph.add_node("leaf-1", role="access")
    
    mgr.observed_graph.add_edge("core-1", "spine-1")
    mgr.observed_graph.add_edge("core-1", "spine-2")
    mgr.observed_graph.add_edge("spine-1", "leaf-1")
    
    # If spine-1 fails, leaf-1 is isolated from core-1
    impacted = mgr.calculate_blast_radius("spine-1")
    assert "leaf-1" in impacted
    assert "spine-2" not in impacted

@pytest.mark.anyio
async def test_redis_cache_operations():
    # Cache status
    set_cached_live_status_local("edge-r1", "UP", {"GigabitEthernet1": {"status": "up"}})
    res = await fetch_cached_live_status(None, "edge-r1")
    assert res["status"] == "UP"
    assert "GigabitEthernet1" in res["interfaces"]

@pytest.mark.anyio
async def test_orchestrator_flow():
    # Setup temporary SQLite in-memory database
    conn = sqlite3.connect(":memory:")
    # Initialize schema
    conn.execute("""
    CREATE TABLE IF NOT EXISTS change_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        device_name TEXT,
        change_type TEXT,
        operator TEXT,
        description TEXT,
        status TEXT
    )""")
    
    db = MockDBManager(conn)
    orchestrator = RetrievalOrchestrator(db)
    
    # Log a change event
    orchestrator.log_change_event("edge-r1", "ansible_deployment", "admin", "Pushed OSPF configuration update", "success")
    
    # Set cached status
    set_cached_live_status_local("edge-r1", "UP", {"GigabitEthernet1": {"status": "up"}})
    
    # Gather context
    ctx = await orchestrator.gather_context("Why is edge-r1 flapping?", "edge-r1")
    
    assert "Pushed OSPF configuration update" in ctx
    assert "ISR4331" in ctx
    assert "edge-r1" in ctx
    
    conn.close()
