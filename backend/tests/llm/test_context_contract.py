import json
from datetime import datetime, timedelta, timezone

from llm import network_tools
from llm.answer_policy import build_answer_policy
from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
    CONTEXT_DOMAIN_SECURITY,
    CONTEXT_DOMAIN_TELEMETRY,
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
    how_to_query = classify_query("How to configure OSPF on Cisco?")
    live_query = classify_query("Show me the live topology")

    assert inventory_query.query_class == QueryClass.INVENTORY_ONLY.value
    assert compare_query.query_class == QueryClass.PLANNED_VS_LIVE.value
    assert knowledge_query.query_class == QueryClass.KNOWLEDGE_HEAVY.value
    assert how_to_query.query_class == QueryClass.KNOWLEDGE_HEAVY.value
    assert live_query.query_class == QueryClass.LIVE_ONLY.value


def test_query_taxonomy_routes_source_specific_operational_queries():
    telemetry_query = classify_query("Show CPU telemetry for R1")
    config_query = classify_query("Show the latest config backup for R1")
    topology_query = classify_query("Show the current topology neighbors for R1")
    syslog_query = classify_query("Give me the latest syslog for R1")

    assert telemetry_query.query_class == QueryClass.LIVE_ONLY.value
    assert telemetry_query.required_domains == (CONTEXT_DOMAIN_TELEMETRY,)
    assert config_query.query_class == QueryClass.COMPLIANCE.value
    assert config_query.required_domains == (CONTEXT_DOMAIN_CONFIG,)
    assert topology_query.query_class == QueryClass.LIVE_ONLY.value
    assert topology_query.required_domains == (CONTEXT_DOMAIN_LIVE_TOPOLOGY,)
    assert syslog_query.query_class == QueryClass.TROUBLESHOOTING.value
    assert syslog_query.required_domains == (CONTEXT_DOMAIN_SECURITY,)


def test_query_taxonomy_ignores_overlay_wrapper_and_cisco_is_not_cis_compliance():
    wrapped_query = classify_query(
        "Operating mode: Expert. Correlate inventory, live topology, telemetry, config, and syslog.\n"
        "User request:\n"
        "What is the vendor, model, and role of Cisco_Acc_1?"
    )
    management_ip_query = classify_query("What is the management IP of Cisco_Acc_1?")
    health_query = classify_query("Is Cisco_Acc_1 unhealthy right now?")

    assert wrapped_query.query_class == QueryClass.INVENTORY_ONLY.value
    assert wrapped_query.required_domains == (CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY)
    assert management_ip_query.query_class == QueryClass.INVENTORY_ONLY.value
    assert management_ip_query.required_domains == (CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY)
    assert health_query.query_class == QueryClass.LIVE_ONLY.value
    assert CONTEXT_DOMAIN_CONFIG not in health_query.required_domains


def test_query_taxonomy_routes_network_wide_telemetry_without_fake_hostname():
    decision = classify_query("Show me a network-wide telemetry summary")

    assert decision.query_class == QueryClass.LIVE_ONLY.value
    assert decision.required_domains == (CONTEXT_DOMAIN_TELEMETRY,)


def test_answer_policy_uses_source_specific_live_and_config_tools():
    telemetry_policy = build_answer_policy("Show CPU telemetry for R1", QueryClass.LIVE_ONLY.value, ["R1"])
    config_policy = build_answer_policy("Show the latest config backup for R1", QueryClass.COMPLIANCE.value, ["R1"])
    topology_policy = build_answer_policy("Show the current topology neighbors for R1", QueryClass.LIVE_ONLY.value, ["R1"])
    syslog_policy = build_answer_policy("Give me the latest syslog for R1", QueryClass.TROUBLESHOOTING.value, ["R1"])

    assert telemetry_policy["name"] == "telemetry_lookup"
    assert telemetry_policy["required_tool_calls"][0].name == "get_device_telemetry_snapshot"
    assert config_policy["name"] == "config_snapshot_lookup"
    assert config_policy["required_tool_calls"][0].name == "get_device_running_config"
    assert topology_policy["name"] == "live_state_lookup"
    assert topology_policy["required_tool_calls"][0].name == "get_device_details"
    assert syslog_policy["name"] == "syslog_lookup"
    assert syslog_policy["required_tool_calls"][0].name == "get_device_syslog_events"

    network_wide_policy = build_answer_policy("Show me a network-wide telemetry summary", QueryClass.LIVE_ONLY.value, [])
    assert network_wide_policy["name"] == "telemetry_lookup"
    assert network_wide_policy["required_tool_calls"][0].name == "get_network_telemetry_summary"


def test_query_taxonomy_routes_platform_architecture_prompts_away_from_compliance():
    architecture_query = classify_query(
        "Create a production-grade implementation plan to optimize LLM, RAG, and MCP latency in this controller architecture."
    )

    assert architecture_query.query_class == QueryClass.ARCHITECTURE_PLANNING.value
    assert architecture_query.use_live_context is False
    assert architecture_query.use_knowledge is False
    assert architecture_query.required_domains == ()


def test_route_uses_phase0_taxonomy():
    assert route("Which devices are online right now?") == (True, False)
    assert route("What is the management IP of SW1 in NetBox?") == (False, False)


def test_topology_tool_returns_source_metadata():
    network_tools.set_db_manager(_FakeDb())
    payload = json.loads(network_tools.list_all_devices())

    assert payload["total"] == 2
    assert payload["scope_requested"] == "live"
    assert payload["source_metadata"]["source"] == "crawler_cache"
    assert payload["source_metadata"]["scope"] == "live_topology"
