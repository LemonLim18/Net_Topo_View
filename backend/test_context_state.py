from llm.context_contract import CONTEXT_DOMAIN_INVENTORY
from llm.context_state import ContextStateManager


def test_context_state_increments_versions_and_records_event():
    state = ContextStateManager()

    version = state.invalidate(
        domain=CONTEXT_DOMAIN_INVENTORY,
        source="unit_test",
        summary="inventory refresh",
        metadata={"count": 2},
    )

    snapshot = state.snapshot()
    assert version == 1
    assert snapshot["versions"][CONTEXT_DOMAIN_INVENTORY] == 1
    assert snapshot["recent_events"][0]["source"] == "unit_test"
    assert snapshot["recent_events"][0]["metadata"]["count"] == 2


def test_context_state_prompt_text_includes_versions():
    state = ContextStateManager()
    state.invalidate(
        domain=CONTEXT_DOMAIN_INVENTORY,
        source="unit_test",
        summary="inventory refresh",
    )

    prompt_text = state.to_prompt_text()
    assert "Context Versions" in prompt_text
    assert "planned_inventory: version=1" in prompt_text