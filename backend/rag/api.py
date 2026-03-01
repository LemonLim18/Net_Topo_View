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
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

logger = logging.getLogger("rag.api")

router = APIRouter(prefix="/rag", tags=["rag"])

# DI globals — populated by setup_rag() at startup
_ingester = None
_vector_store = None

# Directory where uploaded files are persisted for the rebuild command
_KNOWLEDGE_BASE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "knowledge_base"
)


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
    ingested_at: str
    chunk_count: int


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


# DI global for context bundle builder
_bundle_builder = None
_db_manager = None


def setup_context_bundle(db_manager) -> None:
    """Inject db_manager so the context-bundle preview endpoint can work."""
    global _bundle_builder, _db_manager
    from rag.context_bundle import ContextBundleBuilder
    _db_manager = db_manager
    _bundle_builder = ContextBundleBuilder(db_manager=db_manager)
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
}

_ALLOWED_VENDORS = {"cisco", "huawei", "juniper", "aruba", "general"}


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

    try:
        result = await run_in_threadpool(
            _ingester.ingest_file,
            file_path=str(dest_path),
            doc_type=doc_type,
            vendor=vendor,
            doc_name=display_name,
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


# ---------------------------------------------------------------------------
# Delete document
# ---------------------------------------------------------------------------

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
    Remove all vector chunks associated with doc_id from ChromaDB.

    Does NOT delete the source file from knowledge_base/ so that a rebuild
    would re-ingest it.  If you want to permanently remove a document, delete
    the source file and then call rebuild.
    """
    _check_rag_ready()
    try:
        deleted = _vector_store.delete_document(doc_id)
        if deleted == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No chunks found for doc_id='{doc_id}'",
            )
        logger.info("user=%s deleted doc_id=%s (%d chunks)", current_user.get("username"), doc_id, deleted)
        return DeleteResponse(
            doc_id=doc_id,
            chunks_deleted=deleted,
            message=f"Deleted {deleted} chunks.",
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
        kb_dir = Path(_KNOWLEDGE_BASE_DIR)
        if not kb_dir.exists():
            return
        supported = {".pdf", ".txt", ".md", ".docx", ".html", ".htm"}
        files = [f for f in kb_dir.iterdir() if f.is_file() and f.suffix.lower() in supported]
        if not files:
            return
        _vector_store.clear_all()
        total_chunks = 0
        errors = []
        for f in files:
            meta_file = f.with_suffix(f.suffix + ".meta.json")
            doc_type = "best_practice"
            vendor = "general"
            if meta_file.exists():
                import json as _json
                try:
                    meta = _json.loads(meta_file.read_text())
                    doc_type = meta.get("doc_type", doc_type)
                    vendor = meta.get("vendor", vendor)
                except Exception:
                    pass
            try:
                r = _ingester.ingest_file(file_path=str(f), doc_type=doc_type, vendor=vendor, doc_name=f.name)
                total_chunks += r.get("chunks_stored", 0)
            except Exception as exc:
                errors.append(f"{f.name}: {exc}")
        logger.info("Rebuild complete: %d files, %d chunks, %d errors", len(files), total_chunks, len(errors))

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
    # Do not call _check_rag_ready() here — status works even when !available
    if _vector_store is None:
        return StatusResponse(
            available=False,
            total_chunks=0,
            total_documents=0,
            chroma_path=_KNOWLEDGE_BASE_DIR,
        )

    try:
        total_chunks = _vector_store.count()
        docs = _vector_store.list_documents()
        return StatusResponse(
            available=_vector_store._available,  # noqa: SLF001
            total_chunks=total_chunks,
            total_documents=len(docs),
            chroma_path=str(Path(_KNOWLEDGE_BASE_DIR).resolve()),
        )
    except Exception as exc:
        return StatusResponse(
            available=False,
            total_chunks=0,
            total_documents=0,
            chroma_path=_KNOWLEDGE_BASE_DIR,
        )


# ---------------------------------------------------------------------------
# Context Bundle Preview (UC06-UC09)
# ---------------------------------------------------------------------------

class BundlePreviewRequest(BaseModel):
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


@router.get("/state-embedding/status")
async def state_embedding_status():
    """Return status of the network state auto-embedder."""
    if _state_embedder is None:
        return {"available": False, "last_embedded_at": None}
    return {
        "available": True,
        "last_embedded_at": _state_embedder.last_embedded_at,
    }


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
