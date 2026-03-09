from llm.resilience_suite import (
    RESILIENCE_SUITE_VERSION,
    evaluate_resilience_scenario,
    list_resilience_scenarios,
    run_resilience_suite,
)


def test_run_resilience_suite_covers_partial_outages_and_event_storms():
    report = run_resilience_suite()

    assert report["suite_version"] == RESILIENCE_SUITE_VERSION
    assert report["total_scenarios"] == 4
    assert report["passed_scenarios"] == 4
    assert report["regression_guard"]["passed"] is True
    assert report["metric_summary"]["replay_consistency"] == 1.0
    assert report["category_summary"]["partial_outage"]["passed"] == 3
    assert report["category_summary"]["event_storm"]["passed"] == 1


def test_resilience_scenarios_filter_by_category():
    scenarios = list_resilience_scenarios(["event_storm"])

    assert len(scenarios) == 1
    assert scenarios[0]["id"] == "event_storm_replay"


def test_evaluate_resilience_scenario_flags_missing_honesty_language():
    scenario = list_resilience_scenarios(["partial_outage"])[0]
    broken = dict(scenario)
    broken["response_fixture"] = dict(scenario["response_fixture"])
    broken["response_fixture"]["content"] = (
        "SW1 is healthy.\n\n"
        "Grounding: mixed; sources=local_db,crawler_cache,influxdb; "
        "source_age=local_db:0s/live,crawler_cache:18s/live,influxdb:1900s/stale; confidence=medium (0.73).\n"
        "Conflicts: Telemetry freshness is stale for SW1."
    )

    result = evaluate_resilience_scenario(broken)

    assert result["passed"] is False
    assert result["metrics"]["degraded_honesty"]["score"] == 0.0
    assert any("degraded_honesty:" in reason for reason in result["failure_reasons"])