from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


CURATION_MANIFEST_FILENAME = "curation_manifest.json"

REQUIRED_DOC_TYPES = (
    "ansible_reference",
    "live_ops_state",
    "planned_inventory",
)

TRUST_LEVEL_ORDER = (
    "authoritative",
    "curated",
    "reference",
    "advisory",
    "supplemental",
)


@dataclass(frozen=True)
class KnowledgeDocumentMetadata:
    doc_type: str = "best_practice"
    vendor: str = "general"
    platform_family: str = "multi-vendor"
    applicable_model: str = ""
    applicable_feature: str = ""
    trust_level: str = "curated"
    source_type: str = "static_document"
    captured_at: str = ""
    author: str = ""
    title: str = ""
    summary: str = ""


def manifest_path(kb_dir: str | Path) -> Path:
    return Path(kb_dir) / CURATION_MANIFEST_FILENAME


def load_manifest(kb_dir: str | Path) -> dict[str, KnowledgeDocumentMetadata]:
    path = manifest_path(kb_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    entries = payload.get("documents", {}) if isinstance(payload, dict) else {}
    manifest: dict[str, KnowledgeDocumentMetadata] = {}
    for filename, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        manifest[filename] = _metadata_from_mapping(raw)
    return manifest


def save_manifest(kb_dir: str | Path, entries: dict[str, KnowledgeDocumentMetadata]) -> None:
    path = manifest_path(kb_dir)
    payload = {
        "documents": {
            filename: asdict(metadata)
            for filename, metadata in sorted(entries.items())
        }
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def load_document_metadata(file_path: str | Path, kb_dir: Optional[str | Path] = None) -> KnowledgeDocumentMetadata:
    path = Path(file_path)
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    manifest = load_manifest(kb_dir or path.parent)

    if meta_path.exists():
        try:
            return _metadata_from_mapping(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if path.name in manifest:
        return manifest[path.name]

    return KnowledgeDocumentMetadata(title=path.stem)


def save_document_metadata(file_path: str | Path, metadata: KnowledgeDocumentMetadata) -> None:
    path = Path(file_path)
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(asdict(metadata), indent=2, ensure_ascii=True), encoding="utf-8")


def register_manifest_entry(kb_dir: str | Path, filename: str, metadata: KnowledgeDocumentMetadata) -> None:
    entries = load_manifest(kb_dir)
    entries[filename] = metadata
    save_manifest(kb_dir, entries)


def build_curation_audit(documents: Iterable[dict[str, Any]]) -> dict[str, Any]:
    docs = list(documents)
    coverage_by_doc_type: dict[str, int] = {}
    coverage_by_vendor: dict[str, int] = {}
    coverage_by_platform: dict[str, int] = {}
    coverage_by_feature: dict[str, int] = {}
    coverage_by_trust: dict[str, int] = {}
    warnings: list[str] = []

    for doc in docs:
        doc_type = str(doc.get("doc_type") or "unknown")
        vendor = str(doc.get("vendor") or "general")
        platform_family = str(doc.get("platform_family") or "unknown")
        trust_level = str(doc.get("trust_level") or "unknown")

        coverage_by_doc_type[doc_type] = coverage_by_doc_type.get(doc_type, 0) + 1
        coverage_by_vendor[vendor] = coverage_by_vendor.get(vendor, 0) + 1
        coverage_by_platform[platform_family] = coverage_by_platform.get(platform_family, 0) + 1
        coverage_by_trust[trust_level] = coverage_by_trust.get(trust_level, 0) + 1

        features = _split_csv_field(doc.get("applicable_feature") or doc.get("feature") or "")
        if not features:
            warnings.append(f"{doc.get('doc_name', 'unknown')} has no tagged feature coverage")
        for feature in features:
            coverage_by_feature[feature] = coverage_by_feature.get(feature, 0) + 1

        if trust_level not in TRUST_LEVEL_ORDER:
            warnings.append(f"{doc.get('doc_name', 'unknown')} has unknown trust level '{trust_level}'")

    missing_doc_types = [doc_type for doc_type in REQUIRED_DOC_TYPES if coverage_by_doc_type.get(doc_type, 0) == 0]
    if not docs:
        warnings.append("knowledge base is empty or not curated")
    if len(docs) < 5:
        warnings.append("knowledge base coverage is still thin; fewer than 5 curated documents are present")
    for doc_type in missing_doc_types:
        warnings.append(f"missing curated document type coverage: {doc_type}")

    return {
        "total_documents": len(docs),
        "coverage_by_doc_type": coverage_by_doc_type,
        "coverage_by_vendor": coverage_by_vendor,
        "coverage_by_platform_family": coverage_by_platform,
        "coverage_by_feature": coverage_by_feature,
        "coverage_by_trust_level": coverage_by_trust,
        "missing_doc_types": missing_doc_types,
        "warnings": sorted(set(warnings)),
    }


def _metadata_from_mapping(raw: dict[str, Any]) -> KnowledgeDocumentMetadata:
    return KnowledgeDocumentMetadata(
        doc_type=str(raw.get("doc_type") or "best_practice"),
        vendor=str(raw.get("vendor") or "general"),
        platform_family=str(raw.get("platform_family") or "multi-vendor"),
        applicable_model=str(raw.get("applicable_model") or ""),
        applicable_feature=str(raw.get("applicable_feature") or ""),
        trust_level=str(raw.get("trust_level") or "curated"),
        source_type=str(raw.get("source_type") or "static_document"),
        captured_at=str(raw.get("captured_at") or ""),
        author=str(raw.get("author") or ""),
        title=str(raw.get("title") or ""),
        summary=str(raw.get("summary") or ""),
    )


def _split_csv_field(value: str) -> list[str]:
    return [item.strip().lower() for item in str(value).split(",") if item and item.strip()]