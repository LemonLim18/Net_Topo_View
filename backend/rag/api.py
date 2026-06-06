"""
REST API for the M21 Knowledge Vector Store (RAG admin endpoints).

All write operations (upload, delete, rebuild) require a logged-in user.
Read endpoints (list, status) are also auth-protected for consistency.

Endpoints mounted at /api/v1/rag/...

    POST   /rag/documents/upload       Upload and ingest a new document
    GET    /rag/documents              List all ingested documents
    DELETE /rag/documents/{doc_id}     Delete all chunks for a document
    POST   /rag/documents/rebuild      Re-ingest all files in knowledge_base/
    GET    /rag/status                 Health and stats for the vector store

DI pattern: call setup_rag(ingester, vector_store) from app lifespan *before*
the app starts serving requests.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from llm.advanced_differentiators import ensure_advanced_differentiator_available

from rag.knowledge_curation import (
    KnowledgeDocumentMetadata,
    build_curation_audit,
    load_document_metadata,
    load_manifest,
    register_manifest_entry,
    save_document_metadata,
)

logger = logging.getLogger("rag.api")

router = APIRouter(prefix="/rag", tags=["rag"])

# DI globals — populated by setup_rag() at startup
_ingester = None
_vector_store = None

# Directory where uploaded files are persisted for the rebuild command
_KNOWLEDGE_BASE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "knowledge_base"
)
# Ansible reference library (confirmed operational playbooks per feature/vendor)
_ANSIBLE_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "ansible" / "reference"


def setup_rag(ingester, vector_store) -> None:
    """Inject ingester and vector store at application startup."""
    global _ingester, _vector_store
    _ingester = ingester
    _vector_store = vector_store
    os.makedirs(_KNOWLEDGE_BASE_DIR, exist_ok=True)
    logger.info("RAG module ready")


# DI global for context bundle builder
_bundle_builder = None


def setup_context_bundle(db_manager) -> None:
    """Inject the context bundle builder at application startup."""
    global _bundle_builder
    from rag.context_bundle import ContextBundleBuilder
    _bundle_builder = ContextBundleBuilder(db_manager=db_manager)
    logger.info("Context bundle builder ready")


# ---------------------------------------------------------------------------
# Auth dependency (re-uses whatever auth scheme is already in the project)
# ---------------------------------------------------------------------------

# Re-use the project-wide JWT auth dependency (same pattern as ssh_terminal, rbac, etc.).
try:
    from auth.auth import get_current_user as _auth_dep  # type: ignore
except ImportError:  # pragma: no cover — only hit outside the normal app context
    def _auth_dep():  # type: ignore
        return {"username": "anonymous", "role": "admin"}


def _require_user(user=Depends(_auth_dep)):
    return user


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class DocumentInfo(BaseModel):
    doc_id: str
    doc_name: str
    doc_type: str
    vendor: str
    platform_family: str = ""
    applicable_model: str = ""
    applicable_feature: str = ""
    trust_level: str = ""
    source_type: str = ""
    ingested_at: str
    captured_at: str = ""
    chunk_count: int
    partition: Optional[str] = None
    collection_name: Optional[str] = None


class UploadResponse(BaseModel):
    doc_id: str
    doc_name: str
    chunks_stored: int
    message: str


class DeleteResponse(BaseModel):
    doc_id: str
    chunks_deleted: int
    message: str


class StatusResponse(BaseModel):
    available: bool
    total_chunks: int
    total_documents: int
    chroma_path: str
    partition_counts: dict[str, int] = {}
    curation_audit: dict[str, object] = {}
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None
    redis_online: Optional[bool] = None
    redis_bypassed: Optional[bool] = None
    redis_has_password: Optional[bool] = None


class DocumentPreviewResponse(BaseModel):
    doc_id: str
    doc_name: str
    doc_type: str
    vendor: str
    chunk_count: int
    preview_text: str
    chunks: list[dict]


# DI global for context bundle builder
_bundle_builder = None
_db_manager = None
_digital_twin_builder = None


def _controller_implementation_status() -> Optional[dict]:
    if _db_manager is None:
        return None
    agent = getattr(_db_manager, "agent", None)
    return getattr(agent, "controller_implementation_status", None)


def setup_context_bundle(db_manager) -> None:
    """Inject db_manager so the context-bundle preview endpoint can work."""
    global _bundle_builder, _db_manager, _digital_twin_builder
    from rag.context_bundle import ContextBundleBuilder
    from rag.digital_twin import DigitalTwinBuilder
    _db_manager = db_manager
    _bundle_builder = ContextBundleBuilder(db_manager=db_manager)
    _digital_twin_builder = DigitalTwinBuilder(db_manager=db_manager, context_bundle_builder=_bundle_builder)
    logger.info("RAG context-bundle builder ready")


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".html", ".htm"}

_ALLOWED_DOC_TYPES = {
    "vendor_doc",
    "cli_reference",
    "sop",
    "troubleshooting",
    "best_practice",
    "advisory",
    "ansible_reference",
}

_ALLOWED_VENDORS = {"cisco", "huawei", "juniper", "aruba", "general"}
_ALLOWED_TRUST_LEVELS = {"authoritative", "curated", "reference", "advisory", "supplemental"}


@router.post(
    "/documents/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest a document into the knowledge base",
)
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(..., description="One of: vendor_doc, cli_reference, sop, troubleshooting, best_practice"),
    vendor: str = Form(..., description="One of: cisco, huawei, juniper, aruba, general"),
    doc_name: Optional[str] = Form(None, description="Human-readable name override"),
    platform_family: str = Form("multi-vendor", description="Platform family such as ios-xe, junos, vrp, or multi-vendor"),
    applicable_model: str = Form("", description="Optional platform or model specificity"),
    applicable_feature: str = Form("", description="Comma-separated feature tags"),
    trust_level: str = Form("curated", description="One of: authoritative, curated, reference, advisory, supplemental"),
    source_type: str = Form("static_document", description="Static knowledge source type label"),
    captured_at: Optional[str] = Form(None, description="Optional original source capture timestamp"),
    current_user: dict = Depends(_auth_dep),
):
    """
    Upload a document (PDF / DOCX / HTML / TXT / MD) and ingest it into the
    ChromaDB knowledge store.

    The file is saved to knowledge_base/ for later rebuilds.
    """
    _check_rag_ready()

    # Validate extension
    original_name = file.filename or "unknown"
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    # Validate doc_type / vendor
    if doc_type not in _ALLOWED_DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid doc_type '{doc_type}'. Allowed: {sorted(_ALLOWED_DOC_TYPES)}",
        )
    if vendor not in _ALLOWED_VENDORS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid vendor '{vendor}'. Allowed: {sorted(_ALLOWED_VENDORS)}",
        )
    if trust_level not in _ALLOWED_TRUST_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trust_level '{trust_level}'. Allowed: {sorted(_ALLOWED_TRUST_LEVELS)}",
        )

    # Save to a temp file first, then move to knowledge_base/
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    # Persist permanently so rebuild can re-ingest
    kb_dir = Path(_KNOWLEDGE_BASE_DIR)
    kb_dir.mkdir(parents=True, exist_ok=True)
    display_name = doc_name or original_name
    safe_name = _safe_filename(display_name, suffix)
    dest_path = kb_dir / safe_name
    shutil.move(tmp_path, str(dest_path))
    metadata = KnowledgeDocumentMetadata(
        doc_type=doc_type,
        vendor=vendor,
        platform_family=platform_family,
        applicable_model=applicable_model,
        applicable_feature=applicable_feature,
        trust_level=trust_level,
        source_type=source_type,
        captured_at=captured_at or "",
        title=display_name,
        author=current_user.get("username", ""),
    )
    save_document_metadata(dest_path, metadata)
    register_manifest_entry(kb_dir, dest_path.name, metadata)

    try:
        result = await run_in_threadpool(
            _ingester.ingest_file,
            file_path=str(dest_path),
            doc_type=doc_type,
            vendor=vendor,
            doc_name=display_name,
            metadata_overrides=asdict(metadata),
        )
    except Exception as exc:
        logger.error("Ingestion failed for %s: %s", original_name, exc)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc

    logger.info(
        "user=%s uploaded '%s' → doc_id=%s chunks=%d",
        current_user.get("username"),
        display_name,
        result["doc_id"],
        result["chunks_stored"],
    )

    return UploadResponse(
        doc_id=result["doc_id"],
        doc_name=result["doc_name"],
        chunks_stored=result["chunks_stored"],
        message=f"Successfully ingested {result['chunks_stored']} chunks.",
    )


# ---------------------------------------------------------------------------
# List documents
# ---------------------------------------------------------------------------

@router.get(
    "/documents",
    response_model=list[DocumentInfo],
    summary="List all documents in the knowledge base",
)
async def list_documents(
    current_user: dict = Depends(_auth_dep),
):
    """Return metadata for every document currently stored in the vector DB."""
    _check_rag_ready()
    try:
        docs = _vector_store.list_documents()
        return [DocumentInfo(**d) for d in docs]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/documents/{doc_id}/preview",
    response_model=DocumentPreviewResponse,
    summary="Preview reconstructed document text from stored chunks",
)
async def preview_document(
    doc_id: str,
    limit: int = 120,
    current_user: dict = Depends(_auth_dep),
):
    _check_rag_ready()
    docs = _vector_store.list_documents()
    doc = next((d for d in docs if d.get("doc_id") == doc_id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")

    chunks = _vector_store.get_document_chunks(doc_id, partition=doc.get("partition"), limit=limit)
    if not chunks:
        return DocumentPreviewResponse(
            doc_id=doc_id,
            doc_name=doc.get("doc_name", ""),
            doc_type=doc.get("doc_type", ""),
            vendor=doc.get("vendor", ""),
            chunk_count=0,
            preview_text="",
            chunks=[],
        )

    preview_text = "\n\n".join((c.get("text") or "").strip() for c in chunks if (c.get("text") or "").strip())
    return DocumentPreviewResponse(
        doc_id=doc_id,
        doc_name=doc.get("doc_name", ""),
        doc_type=doc.get("doc_type", ""),
        vendor=doc.get("vendor", ""),
        chunk_count=len(chunks),
        preview_text=preview_text,
        chunks=chunks,
    )


# ---------------------------------------------------------------------------
# Delete document & Cleanup Helpers
# ---------------------------------------------------------------------------

def delete_source_file_by_name(doc_name: str, kb_dir: str | Path) -> None:
    kb_path = Path(kb_dir)
    # 1. Check if a file exists with name == doc_name
    target_file = kb_path / doc_name
    if target_file.is_file():
        try:
            target_file.unlink()
            meta_file = target_file.with_suffix(target_file.suffix + ".meta.json")
            if meta_file.is_file():
                meta_file.unlink()
            logger.info("Deleted source file and metadata for: %s", doc_name)
        except Exception as e:
            logger.warning("Failed to delete source file: %s", e)

    # 2. Check manifest for matches by key (filename) or by title (doc_name)
    from rag.knowledge_curation import load_manifest, save_manifest
    manifest = load_manifest(kb_path)
    updated_manifest = {}
    removed_any = False
    for filename, meta in manifest.items():
        if filename == doc_name or meta.title == doc_name:
            # Delete the file if it exists
            f = kb_path / filename
            if f.is_file():
                try:
                    f.unlink()
                    meta_file = f.with_suffix(f.suffix + ".meta.json")
                    if meta_file.is_file():
                        meta_file.unlink()
                except Exception as e:
                    logger.warning("Failed to delete file from manifest: %s", e)
            removed_any = True
            logger.info("Removing manifest entry: %s", filename)
        else:
            updated_manifest[filename] = meta
            
    if removed_any:
        save_manifest(kb_path, updated_manifest)


@router.delete(
    "/documents/non-ansible",
    summary="Delete all non-Ansible documents and their source files",
)
async def delete_non_ansible_documents(
    current_user: dict = Depends(_auth_dep),
):
    """
    Remove all vector chunks for documents not typed 'ansible_reference' from ChromaDB,
    and permanently delete their source files, metadata, and manifest entries.
    """
    _check_rag_ready()
    try:
        docs = _vector_store.list_documents()
        non_ansible_docs = [d for d in docs if d.get("doc_type") != "ansible_reference"]
        
        deleted_count = 0
        deleted_docs = []
        for d in non_ansible_docs:
            doc_id = d.get("doc_id")
            deleted = _vector_store.delete_document(doc_id)
            deleted_count += deleted
            deleted_docs.append(doc_id)
            
            doc_name = d.get("doc_name")
            if doc_name:
                delete_source_file_by_name(doc_name, _KNOWLEDGE_BASE_DIR)
                
        logger.info(
            "user=%s bulk deleted %d non-Ansible documents (%d chunks)",
            current_user.get("username"),
            len(deleted_docs),
            deleted_count
        )
        return {
            "status": "success",
            "docs_deleted": len(deleted_docs),
            "chunks_deleted": deleted_count,
            "message": f"Successfully deleted {len(deleted_docs)} non-Ansible documents and {deleted_count} chunks.",
        }
    except Exception as exc:
        logger.error("Bulk delete non-ansible failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete(
    "/documents/{doc_id}",
    response_model=DeleteResponse,
    summary="Delete a document and all its chunks",
)
async def delete_document(
    doc_id: str,
    current_user: dict = Depends(_auth_dep),
):
    """
    Remove all vector chunks associated with doc_id from ChromaDB,
    and permanently delete the source file, metadata, and manifest entries.
    """
    _check_rag_ready()
    try:
        # Get document info first to find its doc_name
        docs = _vector_store.list_documents()
        doc = next((d for d in docs if d.get("doc_id") == doc_id), None)

        deleted = _vector_store.delete_document(doc_id)
        if deleted == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No chunks found for doc_id='{doc_id}'",
            )
            
        if doc and doc.get("doc_name"):
            delete_source_file_by_name(doc.get("doc_name"), _KNOWLEDGE_BASE_DIR)
            
        logger.info("user=%s deleted doc_id=%s (%d chunks)", current_user.get("username"), doc_id, deleted)
        return DeleteResponse(
            doc_id=doc_id,
            chunks_deleted=deleted,
            message=f"Deleted {deleted} chunks and cleaned up source files.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Rebuild — re-ingest all files in knowledge_base/
# ---------------------------------------------------------------------------

class RebuildResponse(BaseModel):
    files_processed: int
    total_chunks: int
    errors: list[str]


@router.post(
    "/documents/rebuild",
    summary="Wipe the vector store and re-ingest all files in knowledge_base/",
)
async def rebuild_knowledge_base(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(_auth_dep),
):
    """
    Drop all existing chunks, then re-ingest every file in knowledge_base/.
    Runs in background — returns immediately with status 'accepted'.
    """
    _check_rag_ready()

    def _run():
        from llm.vector_store import PARTITION_STATIC_KNOWLEDGE

        _vector_store.clear_partition(PARTITION_STATIC_KNOWLEDGE)
        total_chunks = 0
        errors = []
        kb_dir = Path(_KNOWLEDGE_BASE_DIR)
        supported = {".pdf", ".txt", ".md", ".docx", ".html", ".htm"}
        manifest = load_manifest(kb_dir)
        # Only ingest knowledge_base files that are in the curation manifest (avoids irrelevant uploads)
        use_manifest_filter = len(manifest) > 0
        if use_manifest_filter:
            files = [kb_dir / name for name in manifest if (kb_dir / name).is_file()]
        else:
            files = []
            if kb_dir.exists():
                files = [f for f in kb_dir.iterdir() if f.is_file() and f.suffix.lower() in supported]
        for f in files:
            metadata = load_document_metadata(f, kb_dir)
            doc_type = metadata.doc_type
            vendor = metadata.vendor
            try:
                r = _ingester.ingest_file(
                    file_path=str(f),
                    doc_type=doc_type,
                    vendor=vendor,
                    doc_name=f.name,
                    metadata_overrides=asdict(metadata),
                )
                total_chunks += r.get("chunks_stored", 0)
            except Exception as exc:
                errors.append(f"{f.name}: {exc}")

        # Ingest Ansible reference library (reference/{feature}/{vendor}/reference.md)
        ansible_ref_dir = Path(_ANSIBLE_REFERENCE_DIR)
        if ansible_ref_dir.exists():
            for ref_md in ansible_ref_dir.rglob("reference.md"):
                try:
                    # Path like .../reference/vlan/cisco_ios/reference.md -> feature=vlan, vendor=cisco_ios
                    vendor = ref_md.parent.name
                    feature = ref_md.parent.parent.name
                    meta = KnowledgeDocumentMetadata(
                        doc_type="ansible_reference",
                        vendor=vendor,
                        applicable_feature=feature,
                        title=f"Ansible reference {feature} {vendor}",
                    )
                    r = _ingester.ingest_file(
                        file_path=str(ref_md),
                        doc_type=meta.doc_type,
                        vendor=meta.vendor,
                        doc_name=ref_md.name,
                        metadata_overrides=asdict(meta),
                    )
                    total_chunks += r.get("chunks_stored", 0)
                except Exception as exc:
                    errors.append(f"{ref_md!s}: {exc}")
        logger.info("Rebuild complete: %d chunks, %d errors", total_chunks, len(errors))

    background_tasks.add_task(_run)
    return {"status": "accepted", "message": "Knowledge base rebuild started in background"}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Get RAG vector store health and statistics",
)
async def get_rag_status(
    current_user: dict = Depends(_auth_dep),
):
    """Return health information for the ChromaDB knowledge store."""
    # Do not call _check_rag_ready() here - status works even when !available
    from retrieval.fetchers.redis_ops import (
        _REDIS_BYPASS,
        _OVERRIDE_REDIS_HOST,
        _OVERRIDE_REDIS_PORT,
        _OVERRIDE_REDIS_PASSWORD,
    )
    
    redis_host = _OVERRIDE_REDIS_HOST or os.environ.get("REDIS_HOST", "localhost")
    port_env = os.environ.get("REDIS_PORT", "6379")
    redis_port = _OVERRIDE_REDIS_PORT or (int(port_env) if port_env.isdigit() else 6379)
    redis_password = _OVERRIDE_REDIS_PASSWORD or os.environ.get("REDIS_PASSWORD", None)
    redis_has_password = bool(redis_password)
    
    redis_online = False
    
    # Only test connection if not bypassed
    if not _REDIS_BYPASS and os.environ.get("REDIS_BYPASS", "0") != "1":
        try:
            import redis
            client = redis.Redis(host=redis_host, port=redis_port, password=redis_password, db=0, socket_timeout=1.0)
            client.ping()
            redis_online = True
        except Exception:
            pass

    if _vector_store is None:
        return StatusResponse(
            available=False,
            total_chunks=0,
            total_documents=0,
            chroma_path=_KNOWLEDGE_BASE_DIR,
            partition_counts={},
            redis_host=redis_host,
            redis_port=redis_port,
            redis_online=redis_online,
            redis_bypassed=_REDIS_BYPASS or os.environ.get("REDIS_BYPASS", "0") == "1",
            redis_has_password=redis_has_password,
        )

    try:
        total_chunks = _vector_store.count()
        docs = _vector_store.list_documents()
        return StatusResponse(
            available=_vector_store._available,  # noqa: SLF001
            total_chunks=total_chunks,
            total_documents=len(docs),
            chroma_path=str(Path(_KNOWLEDGE_BASE_DIR).resolve()),
            partition_counts=_vector_store.count_by_partition() if hasattr(_vector_store, "count_by_partition") else {},
            curation_audit=build_curation_audit(docs),
            redis_host=redis_host,
            redis_port=redis_port,
            redis_online=redis_online,
            redis_bypassed=_REDIS_BYPASS or os.environ.get("REDIS_BYPASS", "0") == "1",
            redis_has_password=redis_has_password,
        )
    except Exception as exc:
        return StatusResponse(
            available=False,
            total_chunks=0,
            total_documents=0,
            chroma_path=_KNOWLEDGE_BASE_DIR,
            partition_counts={},
            curation_audit=build_curation_audit([]),
            redis_host=redis_host,
            redis_port=redis_port,
            redis_online=redis_online,
            redis_bypassed=_REDIS_BYPASS or os.environ.get("REDIS_BYPASS", "0") == "1",
            redis_has_password=redis_has_password,
        )


class RedisBypassRequest(BaseModel):
    bypass: bool


@router.post(
    "/redis/bypass",
    summary="Bypass Redis connection and force local memory cache fallback",
)
async def set_redis_bypass_endpoint(
    body: RedisBypassRequest,
    current_user: dict = Depends(_auth_dep),
):
    from retrieval.fetchers.redis_ops import set_redis_bypass
    set_redis_bypass(body.bypass)
    return {"status": "success", "redis_bypassed": body.bypass}


class RedisConfigRequest(BaseModel):
    host: str
    port: int
    password: Optional[str] = None


@router.post(
    "/redis/config",
    summary="Update and test Redis operational cache configuration",
)
async def update_redis_config_endpoint(
    body: RedisConfigRequest,
    current_user: dict = Depends(_auth_dep),
):
    """
    Test and persist new Redis connection parameters.
    Saves to environmental variables and updates the running overrides in retrieval.fetchers.redis_ops.
    Also updates the backend/etc/.env file to survive restarts.
    """
    try:
        import redis
        client = redis.Redis(
            host=body.host,
            port=body.port,
            password=body.password if body.password else None,
            db=0,
            socket_timeout=2.0
        )
        client.ping()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Redis connection test failed: {exc}"
        )

    # Update dynamic memory settings in the module
    from retrieval.fetchers import redis_ops
    redis_ops.set_redis_config(body.host, body.port, body.password)
    redis_ops.set_redis_bypass(False)

    # Persist overrides to .env so they survive restart
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            content = env_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            new_lines = []
            
            has_host = False
            has_port = False
            has_password = False
            
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("REDIS_HOST="):
                    new_lines.append(f"REDIS_HOST={body.host}")
                    has_host = True
                elif stripped.startswith("REDIS_PORT="):
                    new_lines.append(f"REDIS_PORT={body.port}")
                    has_port = True
                elif stripped.startswith("REDIS_PASSWORD="):
                    if body.password:
                        new_lines.append(f"REDIS_PASSWORD={body.password}")
                    else:
                        new_lines.append("REDIS_PASSWORD=")
                    has_password = True
                else:
                    new_lines.append(line)
                    
            if not has_host:
                new_lines.append(f"REDIS_HOST={body.host}")
            if not has_port:
                new_lines.append(f"REDIS_PORT={body.port}")
            if not has_password and body.password:
                new_lines.append(f"REDIS_PASSWORD={body.password}")
                
            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write to .env file: %s", e)

    return {"status": "success", "message": "Redis connection updated successfully and written to .env."}


@router.post(
    "/redis/test-active",
    summary="Test current active Redis connection configuration and return status or error details",
)
async def test_active_redis_connection_endpoint(
    current_user: dict = Depends(_auth_dep),
):
    """
    Test the current active Redis connection configuration (including dynamic overrides or env values).
    Returns success or raises a 400 Bad Request with the connection error details.
    """
    from retrieval.fetchers.redis_ops import (
        _OVERRIDE_REDIS_HOST,
        _OVERRIDE_REDIS_PORT,
        _OVERRIDE_REDIS_PASSWORD,
    )
    
    redis_host = _OVERRIDE_REDIS_HOST or os.environ.get("REDIS_HOST", "localhost")
    port_env = os.environ.get("REDIS_PORT", "6379")
    redis_port = _OVERRIDE_REDIS_PORT or (int(port_env) if port_env.isdigit() else 6379)
    redis_password = _OVERRIDE_REDIS_PASSWORD or os.environ.get("REDIS_PASSWORD", None)

    try:
        import redis
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password if redis_password else None,
            db=0,
            socket_timeout=2.0
        )
        client.ping()
        return {
            "status": "success",
            "message": f"Successfully connected to Redis cache at {redis_host}:{redis_port}"
        }
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Redis connection failed: {exc}"
        )


# ---------------------------------------------------------------------------
# Context Bundle Preview (UC06-UC09)
# ---------------------------------------------------------------------------

class BundlePreviewRequest(BaseModel):
    device_hostnames: Optional[list[str]] = None
    vendor_filter: Optional[str] = None
    include_telemetry: bool = True
    include_sdn: bool = True


class DigitalTwinPreviewRequest(BaseModel):
    device_hostnames: Optional[list[str]] = None
    vendor_filter: Optional[str] = None
    include_telemetry: bool = True
    include_sdn: bool = True


@router.post(
    "/context-bundle/preview",
    summary="Preview the fused context bundle that would be sent to the LLM",
)
async def preview_context_bundle(
    body: BundlePreviewRequest,
    current_user: dict = Depends(_auth_dep),
):
    """
    Build (but do not send to the LLM) a context bundle for the given scope.

    Returns:
        - ``prompt_text``: The markdown string injected into the system prompt.
        - ``bundle``: The raw structured bundle (JSON-serialisable dict).
    """
    if _bundle_builder is None:
        raise HTTPException(status_code=503, detail="Context bundle builder not initialised")

    bundle = await run_in_threadpool(
        _bundle_builder.build_bundle,
        device_hostnames=body.device_hostnames,
        vendor_filter=body.vendor_filter,
        include_telemetry=body.include_telemetry,
        include_sdn=body.include_sdn,
    )

    return {
        "prompt_text": bundle.to_prompt_text(),
        "bundle": bundle.to_dict(),
    }


@router.post(
    "/digital-twin/preview",
    summary="Preview the formal digital-twin graph snapshot built from the current grounding sources",
)
async def preview_digital_twin(
    body: DigitalTwinPreviewRequest,
    current_user: dict = Depends(_auth_dep),
):
    """Build the formal Step 30 digital-twin snapshot without invoking the chat agent."""
    ensure_advanced_differentiator_available(
        "digital_twin",
        _controller_implementation_status(),
    )
    if _digital_twin_builder is None:
        raise HTTPException(status_code=503, detail="Digital twin builder not initialised")

    snapshot = await run_in_threadpool(
        _digital_twin_builder.build_snapshot,
        body.device_hostnames,
        body.vendor_filter,
        body.include_telemetry,
        body.include_sdn,
    )

    return {
        "prompt_text": snapshot.to_prompt_text(),
        "digital_twin": snapshot.to_dict(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_rag_ready() -> None:
    if _ingester is None or _vector_store is None:
        raise HTTPException(
            status_code=503,
            detail="RAG module is not initialised. Check server startup logs.",
        )
    if not _vector_store._available:  # noqa: SLF001
        raise HTTPException(
            status_code=503,
            detail="ChromaDB is not available — install chromadb or check GEMINI_API_KEY.",
        )


def _safe_filename(name: str, suffix: str) -> str:
    """Return a filesystem-safe filename, preserving extension."""
    stem = Path(name).stem
    safe_stem = "".join(c if c.isalnum() or c in "-_. " else "_" for c in stem)
    safe_stem = safe_stem.strip()[:128] or str(uuid.uuid4())
    return f"{safe_stem}{suffix}"


# ---------------------------------------------------------------------------
# Phase 9 — Network State Auto-Embedding
# ---------------------------------------------------------------------------

_state_embedder = None


def setup_state_embedder(embedder_instance) -> None:
    """Inject the NetworkStateEmbedder (called by backend.py lifespan)."""
    global _state_embedder
    _state_embedder = embedder_instance


def get_state_embedding_status_snapshot() -> dict:
    if _state_embedder is None:
        return {"available": False, "last_embedded_at": None}
    return {
        "available": True,
        "last_embedded_at": _state_embedder.last_embedded_at,
    }


@router.get("/state-embedding/status")
async def state_embedding_status():
    """Return status of the network state auto-embedder."""
    return get_state_embedding_status_snapshot()


@router.post("/state-embedding/trigger")
async def trigger_state_embedding(background: BackgroundTasks):
    """
    Manually trigger a re-embedding of the current network state.
    Reads the latest topology from disk and embeds it.
    """
    if _state_embedder is None:
        raise HTTPException(503, "Network state embedder not initialised")

    import json as _json

    topo_path = os.path.join(
        os.path.dirname(__file__), "..", "topology_store", "current_topo.json"
    )
    if not os.path.exists(topo_path):
        raise HTTPException(404, "No current topology file found")

    try:
        with open(topo_path, "r") as f:
            topology = _json.load(f)
    except Exception as exc:
        raise HTTPException(500, f"Failed to read topology: {exc}")

    result = _state_embedder.embed_crawl_result(topology)
    return result
