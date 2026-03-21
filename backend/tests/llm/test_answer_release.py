from llm.answer_release import build_answer_release_state


def test_answer_release_requests_refresh_for_stale_live_evidence():
    state = build_answer_release_state(
        query_class="live_only",
        grounding={
            "freshness_class": "stale",
            "source_age": [
                {
                    "source": "crawler_cache",
                    "scope": "live_topology",
                    "freshness_class": "stale",
                }
            ],
            "conflicts": [],
            "evidence_authority": {
                "preferred_evidence_present": True,
                "substitution_risk": False,
                "summary": "live evidence present",
            },
        },
        tool_args={},
        allowed_tools=["get_last_sync_status", "trigger_context_refresh", "get_refresh_job_status"],
    )

    assert state["decision"] == "reroute"
    assert state["refresh_tool_calls"][0]["name"] == "get_last_sync_status"
    assert state["refresh_tool_calls"][1] == {
        "name": "trigger_context_refresh",
        "arguments": {"target": "live_topology", "wait": False},
    }


def test_answer_release_limits_conflicting_authoritative_evidence_without_refresh():
    state = build_answer_release_state(
        query_class="planned_vs_live",
        grounding={
            "freshness_class": "live",
            "source_age": [
                {"source": "local_db", "scope": "planned_inventory", "freshness_class": "live"},
                {"source": "crawler_cache", "scope": "live_topology", "freshness_class": "live"},
            ],
            "conflicts": [
                {
                    "code": "management_ip_mismatch",
                    "summary": "SW1 has different management IP values in planned and live state.",
                    "severity": "high",
                }
            ],
            "evidence_authority": {
                "preferred_evidence_present": True,
                "substitution_risk": False,
                "summary": "Primary authority families for this query are planned, live.",
            },
        },
        tool_args={},
        allowed_tools=["get_last_sync_status", "trigger_context_refresh"],
    )

    assert state["decision"] == "allow_limited"
    assert state["refresh_tool_calls"] == []
    assert "conflicting_evidence" in state["degraded_signals"]


def test_answer_release_prefers_immediate_limited_release_for_stale_inventory_reads():
    state = build_answer_release_state(
        query_class="inventory_only",
        grounding={
            "freshness_class": "stale",
            "source_age": [
                {
                    "source": "netbox",
                    "scope": "planned_inventory",
                    "freshness_class": "stale",
                }
            ],
            "conflicts": [],
            "evidence_authority": {
                "preferred_evidence_present": True,
                "substitution_risk": False,
                "summary": "planned evidence present",
            },
        },
        tool_args={},
        allowed_tools=["get_last_sync_status", "trigger_context_refresh"],
    )

    assert state["decision"] == "allow_limited"
    assert state["refresh_recommended"] is True
    assert state["refresh_attempted"] is False
    assert state["refresh_tool_calls"][0]["name"] == "get_last_sync_status"