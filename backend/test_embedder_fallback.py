from __future__ import annotations

from llm.embedder import GeminiEmbedder


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def test_embedder_fallback_returns_non_zero_normalized_vectors():
    embedder = GeminiEmbedder(api_key="")
    vector = embedder.embed_query("How do I validate OSPF adjacency on Cisco IOS XE?")

    assert len(vector) == embedder.dimension
    assert any(component != 0.0 for component in vector)
    magnitude = sum(component * component for component in vector) ** 0.5
    assert abs(magnitude - 1.0) < 1e-6


def test_embedder_fallback_is_deterministic_and_semantically_coherent():
    embedder = GeminiEmbedder(api_key="")
    ospf_query = embedder.embed_query("cisco ospf adjacency troubleshooting")
    ospf_doc = embedder.embed_query("ospf adjacency troubleshooting on cisco ios xe")
    bgp_doc = embedder.embed_query("junos bgp route policy reference")

    assert ospf_query == embedder.embed_query("cisco ospf adjacency troubleshooting")
    assert _cosine_similarity(ospf_query, ospf_doc) > _cosine_similarity(ospf_query, bgp_doc)


def test_embed_documents_uses_fallback_when_remote_unavailable():
    embedder = GeminiEmbedder(api_key="")
    vectors = embedder.embed_documents([
        "ospf passive-interface baseline",
        "security advisory impact assessment",
    ])

    assert len(vectors) == 2
    assert all(len(vector) == embedder.dimension for vector in vectors)
    assert all(any(component != 0.0 for component in vector) for vector in vectors)