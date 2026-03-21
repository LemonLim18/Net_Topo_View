import json
import time

from llm import network_tools
from llm.refresh_state import get_refresh_state_manager


def setup_function():
    get_refresh_state_manager().reset()


def test_get_last_sync_status_returns_recorded_operations():
    state = get_refresh_state_manager()
    state.record_success(
        "live_topology",
        result={"status": "success", "node_count": 3},
        trigger="unit_test",
    )

    payload = json.loads(network_tools.get_last_sync_status())

    assert payload["operations"]["live_topology"]["status"] == "success"
    assert payload["operations"]["live_topology"]["last_result"]["node_count"] == 3


def test_trigger_context_refresh_wait_executes_registered_handler():
    state = get_refresh_state_manager()
    state.register_handler("inventory", lambda: {"status": "success", "synced_count": 4})

    payload = json.loads(network_tools.trigger_context_refresh(target="inventory", wait=True))

    assert payload["status"] == "completed"
    assert payload["target"] == "inventory"
    assert payload["result"]["synced_count"] == 4


def test_trigger_context_refresh_async_returns_pollable_job():
    state = get_refresh_state_manager()
    state.register_handler("planned_topology", lambda: {"status": "success", "cables_synced": 2})

    job = json.loads(network_tools.trigger_context_refresh(target="planned_topology", wait=False))

    status = None
    for _ in range(20):
        status = json.loads(network_tools.get_refresh_job_status(job["job_id"]))
        if status["status"] == "completed":
            break
        time.sleep(0.02)

    assert status is not None
    assert status["target"] == "planned_topology"
    assert status["status"] == "completed"
    assert status["result"]["result"]["cables_synced"] == 2


def test_get_refresh_job_status_returns_error_for_unknown_job():
    payload = json.loads(network_tools.get_refresh_job_status("refresh-missing"))

    assert "error" in payload