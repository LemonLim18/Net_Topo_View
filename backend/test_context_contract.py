import json
from datetime import datetime, timedelta, timezone

from llm import network_tools
from llm.context_contract import (
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    QueryClass,
    compute_freshness,
    classify_query,
)
from llm.retriever import route


class _FakeDb:
    def get_topology(self):
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seeds": ["R1"],
            "nodes": {
                "R1": {
                    "mgmt_ip": "10.0.0.1",
                    "down": False,
                    "connectedNode": ["SW1"],
                    "interfaceList": [{"Gig0/0": "10.0.0.1/24"}],
                    "edgeLink": {},
                },
                "SW1": {
                    "mgmt_ip": "10.0.0.2",
                    "down": False,
                    "connectedNode": ["R1"],
                    "interfaceList": [{"Gig0/1": "10.0.0.2/24"}],
                    "edgeLink": {},
                },
            },
        }


def test_compute_freshness_marks_recent_live_state():
    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(timespec="seconds")
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        captured_at=recent,
        ingested_at=recent,
        source="crawler_cache",
        scope="live_topology",
        confidence=0.9,
    )

    assert metadata.freshness_class == "live"
    assert metadata.age_seconds is not None


def test_compute_freshness_marks_stale_live_state():
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    metadata = compute_freshness(
        domain=CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        captured_at=old,
        ingested_at=old,
        source="crawler_cache",
        scope="live_topology",
        confidence=0.9,
    )

    assert metadata.freshness_class == "stale"


def test_query_taxonomy_routes_inventory_and_comparison_queries():
    inventory_query = classify_query("What is the management IP of SW1 in inventory?")
    compare_query = classify_query("Compare planned vs live state for core switches")
    knowledge_query = classify_query("Explain OSPF adjacency states")

    assert inventory_query.query_class == QueryClass.INVENTORY_ONLY.value
    assert compare_query.query_class == QueryClass.PLANNED_VS_LIVE.value
    assert knowledge_query.query_class == QueryClass.KNOWLEDGE_HEAVY.value


def test_route_uses_phase0_taxonomy():
    assert route("Which devices are online right now?") == (True, False)
    assert route("What is the management IP of SW1 in NetBox?") == (False, False)


def test_topology_tool_returns_source_metadata():
    network_tools.set_db_manager(_FakeDb())
    payload = json.loads(network_tools.list_all_devices())

    assert payload["total"] == 2
    assert payload["source_metadata"]["source"] == "crawler_cache"
    assert payload["source_metadata"]["scope"] == "live_topology"