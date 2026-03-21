from llm.semantics import (
    AUTHORITY_MAP_VERSION,
    SEMANTICS_VERSION,
    build_canonical_authority_map,
    build_canonical_semantics_prompt_text,
)


def test_canonical_authority_map_exposes_domain_and_query_contracts():
    payload = build_canonical_authority_map()

    assert payload["version"] == AUTHORITY_MAP_VERSION
    assert payload["semantics_version"] == SEMANTICS_VERSION
    assert "planned_inventory" in payload["context_domains"]
    assert "live_topology" in payload["context_domains"]
    assert any(item["query_class"] == "planned_vs_live" for item in payload["query_source_matrix"])
    assert payload["canonical_semantics"]["version"] == SEMANTICS_VERSION


def test_canonical_semantics_prompt_text_mentions_authority_version():
    prompt_text = build_canonical_semantics_prompt_text()

    assert AUTHORITY_MAP_VERSION in prompt_text
    assert SEMANTICS_VERSION in prompt_text
    assert "inventory authority" in prompt_text
