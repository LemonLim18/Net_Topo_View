from __future__ import annotations

import json

from llm.ingestion import DocumentIngester
from llm import network_tools
from rag.knowledge_curation import (
    KnowledgeDocumentMetadata,
    build_curation_audit,
    load_document_metadata,
    register_manifest_entry,
    save_document_metadata,
)


class _CaptureStore:
    def __init__(self):
        self.upserts = []
        self.available = True
        self._available = True

    def upsert_chunks(self, doc_id, chunks, metadatas, partition=None):
        self.upserts.append(
            {
                "doc_id": doc_id,
                "chunks": list(chunks),
                "metadatas": list(metadatas),
                "partition": partition,
            }
        )


class _FakeKnowledgeStore:
    def __init__(self, embedder=None):
        self.available = True
        self._available = True

    def list_documents(self):
        return [
            {
                "doc_id": "doc-1",
                "doc_name": "01_cisco_ios_xe_ospf_design_baseline.md",
                "doc_type": "planned_inventory",
                "vendor": "cisco",
                "platform_family": "ios-xe",
                "applicable_feature": "ospf,area-design",
                "trust_level": "curated",
                "source_type": "internal_design_guide",
                "ingested_at": "2026-03-09T09:00:00+00:00",
                "chunk_count": 2,
            },
            {
                "doc_id": "doc-2",
                "doc_name": "05_change_execution_sop.md",
                "doc_type": "planned_inventory",
                "vendor": "general",
                "platform_family": "multi-vendor",
                "applicable_feature": "change-control,rollback",
                "trust_level": "authoritative",
                "source_type": "standard_operating_procedure",
                "ingested_at": "2026-03-09T09:05:00+00:00",
                "chunk_count": 1,
            },
            {
                "doc_id": "doc-3",
                "doc_name": "04_network_incident_triage_playbook.md",
                "doc_type": "live_ops_state",
                "vendor": "general",
                "platform_family": "multi-vendor",
                "applicable_feature": "incident-response",
                "trust_level": "curated",
                "source_type": "playbook",
                "ingested_at": "2026-03-09T09:06:00+00:00",
                "chunk_count": 1,
            },
            {
                "doc_id": "doc-4",
                "doc_name": "06_security_advisory_assessment_playbook.md",
                "doc_type": "live_ops_state",
                "vendor": "general",
                "platform_family": "multi-vendor",
                "applicable_feature": "security-advisory",
                "trust_level": "advisory",
                "source_type": "advisory_playbook",
                "ingested_at": "2026-03-09T09:07:00+00:00",
                "chunk_count": 1,
            },
            {
                "doc_id": "doc-5",
                "doc_name": "02_juniper_junos_bgp_triage_reference.md",
                "doc_type": "ansible_reference",
                "vendor": "juniper",
                "platform_family": "junos",
                "applicable_feature": "bgp,neighbor-state",
                "trust_level": "curated",
                "source_type": "internal_command_reference",
                "ingested_at": "2026-03-09T09:08:00+00:00",
                "chunk_count": 1,
            },
        ]

    def count(self):
        return 6

    def count_by_partition(self):
        return {"static_knowledge": 6}


def test_ingester_applies_curated_metadata_overrides(tmp_path):
    doc_path = tmp_path / "ospf-guide.md"
    doc_path.write_text("ospf passive-interface area design", encoding="utf-8")
    store = _CaptureStore()
    ingester = DocumentIngester(store)

    result = ingester.ingest_file(
        file_path=str(doc_path),
        doc_type="planned_inventory",
        vendor="cisco",
        doc_name="ospf-guide.md",
        metadata_overrides={
            "platform_family": "ios-xe",
            "applicable_model": "catalyst-9300",
            "applicable_feature": "ospf,passive-interface",
            "trust_level": "authoritative",
            "source_type": "internal_design_guide",
            "captured_at": "2026-03-09T00:00:00+00:00",
        },
    )

    assert result["chunks_stored"] >= 1
    metadata = store.upserts[0]["metadatas"][0]
    assert metadata["platform_family"] == "ios-xe"
    assert metadata["applicable_model"] == "catalyst-9300"
    assert metadata["applicable_feature"] == "ospf,passive-interface"
    assert metadata["trust_level"] == "authoritative"
    assert metadata["source_type"] == "internal_design_guide"
    assert metadata["captured_at"] == "2026-03-09T00:00:00+00:00"


def test_manifest_and_sidecar_metadata_resolution(tmp_path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    manifest_file = kb_dir / "ospf-guide.md"
    manifest_file.write_text("content", encoding="utf-8")
    sidecar_file = kb_dir / "bgp-reference.md"
    sidecar_file.write_text("content", encoding="utf-8")

    register_manifest_entry(
        kb_dir,
        manifest_file.name,
        KnowledgeDocumentMetadata(
            doc_type="planned_inventory",
            vendor="cisco",
            platform_family="ios-xe",
            applicable_feature="ospf",
            trust_level="curated",
        ),
    )
    save_document_metadata(
        sidecar_file,
        KnowledgeDocumentMetadata(
            doc_type="ansible_reference",
            vendor="juniper",
            platform_family="junos",
            applicable_feature="bgp",
            trust_level="reference",
        ),
    )

    manifest_metadata = load_document_metadata(manifest_file, kb_dir)
    sidecar_metadata = load_document_metadata(sidecar_file, kb_dir)

    assert manifest_metadata.vendor == "cisco"
    assert manifest_metadata.platform_family == "ios-xe"
    assert sidecar_metadata.vendor == "juniper"
    assert sidecar_metadata.trust_level == "reference"


def test_curation_audit_reports_coverage_and_gaps():
    audit = build_curation_audit(
        [
            {
                "doc_name": "ospf-guide.md",
                "doc_type": "planned_inventory",
                "vendor": "cisco",
                "platform_family": "ios-xe",
                "applicable_feature": "ospf",
                "trust_level": "curated",
            },
            {
                "doc_name": "change-sop.md",
                "doc_type": "ansible_reference",
                "vendor": "general",
                "platform_family": "multi-vendor",
                "applicable_feature": "rollback",
                "trust_level": "authoritative",
            },
        ]
    )

    assert audit["coverage_by_doc_type"]["planned_inventory"] == 1
    assert audit["coverage_by_trust_level"]["authoritative"] == 1
    assert "live_ops_state" in audit["missing_doc_types"]
    assert audit["warnings"]


def test_knowledge_status_includes_curation_audit(monkeypatch):
    monkeypatch.setattr("llm.vector_store.KnowledgeVectorStore", _FakeKnowledgeStore)
    payload = json.loads(network_tools.get_knowledge_base_status(limit=3))

    assert payload["total_documents"] == 5
    assert payload["total_chunks"] == 6
    assert payload["curation_audit"]["coverage_by_doc_type"]["ansible_reference"] == 1
    assert payload["curation_audit"]["missing_doc_types"] == []
    assert len(payload["documents"]) == 3