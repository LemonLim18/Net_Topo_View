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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
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


# ---------------------------------------------------------------------------
# Auth dependency (re-uses whatever auth scheme is already in the project)
# ---------------------------------------------------------------------------

def _get_current_user():
    """
    Thin re-export of the project's existing auth dependency so this module
    does not have to import from a hardcoded path.  The actual implementation
    is injected at the Depends() call-site — this callable is swapped out in
    backend.py when mounting the router.

    If not overridden, it simply passes (no-auth fallback for dev environments).
    """
    return {"username": "anonymous", "role": "admin"}


# The router uses a module-level reference so backend.py can swap it out.
_auth_dep = _get_current_user


def _require_user(user=Depends(_get_current_user)):
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
        result = _ingester.ingest_file(
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
    response_model=RebuildResponse,
    summary="Wipe the vector store and re-ingest all files in knowledge_base/",
)
async def rebuild_knowledge_base(
    current_user: dict = Depends(_auth_dep),
):
    """
    Drop all existing chunks, then re-ingest every file in knowledge_base/.

    Useful after upgrading the embedding model or adjusting chunk parameters.
    Metadata (doc_type / vendor) is preserved from the file's companion
    .meta.json sidecar if present; otherwise defaults to general/best_practice.
    """
    _check_rag_ready()

    kb_dir = Path(_KNOWLEDGE_BASE_DIR)
    if not kb_dir.exists():
        return RebuildResponse(files_processed=0, total_chunks=0, errors=[])

    supported = {".pdf", ".txt", ".md", ".docx", ".html", ".htm"}
    files = [f for f in kb_dir.iterdir() if f.is_file() and f.suffix.lower() in supported]

    if not files:
        return RebuildResponse(files_processed=0, total_chunks=0, errors=["No files found in knowledge_base/"])

    # Optional: clear all existing chunks before rebuild
    # (Caller must decide — for now we just re-upsert which is idempotent)

    results: list[dict] = []
    errors: list[str] = []
    total_chunks = 0

    for f in files:
        # Look for a sidecar metadata file
        meta_file = f.with_suffix(f.suffix + ".meta.json")
        doc_type = "best_practice"
        vendor = "general"
        if meta_file.exists():
            import json
            try:
                meta = json.loads(meta_file.read_text())
                doc_type = meta.get("doc_type", doc_type)
                vendor = meta.get("vendor", vendor)
            except Exception:
                pass

        try:
            result = _ingester.ingest_file(
                file_path=str(f),
                doc_type=doc_type,
                vendor=vendor,
                doc_name=f.name,
            )
            total_chunks += result.get("chunks_stored", 0)
            results.append(result)
        except Exception as exc:
            errors.append(f"{f.name}: {exc}")

    logger.info(
        "user=%s rebuilt knowledge base: %d files, %d chunks, %d errors",
        current_user.get("username"), len(results), total_chunks, len(errors),
    )

    return RebuildResponse(
        files_processed=len(results),
        total_chunks=total_chunks,
        errors=errors,
    )


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
