# M21 — Vector-Embedded RAG

Add a **vector-embedded knowledge base** to NetAutoController AI so the LLM agent can answer "how-to", configuration, and troubleshooting questions by retrieving chunks from uploaded vendor documentation, SOPs, CLI references, and best-practice guides — rather than relying solely on training data.

Implemented as a **hybrid RAG** layer that sits alongside the existing Context-Injection RAG (M5): live topology context is always present; curated static knowledge is injected *additionally* and only when the query warrants it.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          NetAutoController AI — M21 RAG Architecture        │
└─────────────────────────────────────────────────────────────────────────────┘

INGESTION PATH (offline, admin-triggered)
─────────────────────────────────────────

   Admin → POST /api/v1/rag/documents/upload
               │
               ▼
   ┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐
   │  DocumentIngester │────▶│  GeminiEmbedder   │────▶│KnowledgeVectorSto │
   │  (_parse + _chunk)│     │ text-embedding-004│     │re (ChromaDB)      │
   └───────────────────┘     └───────────────────┘     └───────────────────┘
         │                         │                          │
     PDF/DOCX/HTML/            768-dim vectors           SQLite-backed
     TXT/MD → plain            RETRIEVAL_DOCUMENT        chroma_db/
     text → 512-token          task type, batch=100      network_knowledge
     overlapping chunks                                  collection


QUERY PATH (per chat turn, inside FastAPI request)
──────────────────────────────────────────────────

   User message
        │
        ▼
   ┌─────────────────────────────────────────┐
   │  route(query) → (use_live, use_knowledge)│
   │  keyword-based intent classifier         │
   └─────────────┬───────────────────────────┘
                 │
        ┌────────┴─────────┐
        │                  │
   use_knowledge=False  use_knowledge=True
        │                  │
        │         ┌────────▼──────────────┐
        │         │  GeminiEmbedder       │
        │         │  embed_query()        │
        │         │  RETRIEVAL_QUERY type │
        │         └────────┬──────────────┘
        │                  │ 768-dim query vec
        │         ┌────────▼──────────────┐
        │         │  KnowledgeVectorStore │
        │         │  .query(k=5,          │
        │         │   min_score=0.35)     │
        │         └────────┬──────────────┘
        │                  │ top-k chunks + scores
        │         ┌────────▼──────────────┐
        │         │  KnowledgeRetriever   │
        │         │  .retrieve_as_text()  │
        │         │  → markdown block     │
        │         └────────┬──────────────┘
        │                  │
        └────────┬─────────┘
                 │
        ┌────────▼──────────────────────────────────────────────────────────┐
        │  NetworkAgent.chat()                                               │
        │                                                                    │
        │  per_turn_system = _system_prompt (always has live topology M5)   │
        │  if use_knowledge:                                                 │
        │      per_turn_system += "\n\n## Relevant Knowledge Base\n..."     │
        │                                                                    │
        │  messages = [LLMMessage(system, per_turn_system), *history]       │
        └───────────────────────────────────────────────────────────────────┘
                 │
        ┌────────▼──────────────────────────────────────────────────────────┐
        │  GeminiProvider.generate()                                         │
        │  (tool-calling loop unchanged — M19 proposals still work)         │
        └───────────────────────────────────────────────────────────────────┘
```

### Hybrid Design — Two RAG Layers

```
Layer           Source              Refresh           Injected
─────────────────────────────────────────────────────────────────
M5 Context RAG  Live topology DB    Every crawl       Always (system prompt)
M21 Vector RAG  Uploaded documents  Admin-triggered   Conditionally (per-turn)
```

The two layers **never conflict**: M5's live state lives in `_system_prompt` (baked at session start); M21's chunks are appended to a per-turn copy `per_turn_system` — the original `_system_prompt` is never mutated.

---

## What Already Existed (No Changes Needed)

| Component | File | Used By M21 |
|---|---|---|
| `GeminiProvider` | `llm/gemini_provider.py` | Re-uses `GEMINI_API_KEY` env var |
| `ContextBuilder` | `llm/context.py` | Extended with `build_knowledge_context()` |
| `NetworkAgent` | `llm/agent.py` | Extended with `retriever` param + per-turn injection |
| `CredentialVault` | `auth/credentials.py` | Not used — API key already in env |
| `get_current_user` | `auth/auth.py` | Injected into all RAG REST endpoints |
| FastAPI app + lifespan | `backend.py` | RAG stack init + router mount added |

---

## New Files Created

### `backend/llm/embedder.py` — `GeminiEmbedder`

Thin wrapper around `google-genai` SDK that calls `text-embedding-004`.

```
GeminiEmbedder
├── __init__(api_key=None)           reads GEMINI_API_KEY from env
├── embed_documents(texts)           batched ingestion embeddings
│     • task_type = RETRIEVAL_DOCUMENT
│     • batch size = 100 (rate-limit safe)
│     • 0.1 s sleep between batches
│     • returns list[list[float]]   (768-dim each)
├── embed_query(text)                single query embedding
│     • task_type = RETRIEVAL_QUERY
│     • returns list[float]         (768-dim)
├── _embed_batch(texts, task_type)   internal API call
└── _zero_vector()                   fallback when API unavailable
```

**Graceful degradation**: `_available = False` if `google-genai` is not installed or the API key is missing. All public methods silently return zero vectors so the server starts regardless.

---

### `backend/llm/vector_store.py` — `KnowledgeVectorStore`

Persistent ChromaDB collection wrapper.

```
KnowledgeVectorStore(embedder)
│
├── ChromaDB PersistentClient
│     path: backend/chroma_db/
│     collection: "network_knowledge"
│     hnsw:space: "cosine"
│
├── upsert_chunks(doc_id, texts, metadatas)
│     call embedder.embed_documents()
│     collection.upsert(ids, embeddings, documents, metadatas)
│     chunk IDs: "{doc_id}_c{i:04d}"
│
├── query(query_embedding, k=5, where=None, min_score=0.35)
│     collection.query(query_embeddings, n_results=k, where=where)
│     ChromaDB returns COSINE DISTANCE [0, 2]
│     Convert → similarity: score = 1.0 - (dist / 2.0)
│     Filter by min_score, sort desc
│     returns list[{"text", "metadata", "score"}]
│
├── delete_document(doc_id) → int    deletes all chunks for doc_id
├── list_documents() → list[dict]   deduplicated by doc_id
└── count() → int                   total chunk count
```

**Chunk metadata schema**:

```
doc_id       str   UUID of parent document
doc_name     str   human-readable filename
doc_type     str   vendor_doc | cli_reference | sop | troubleshooting | best_practice
vendor       str   cisco | huawei | juniper | aruba | general
chunk_index  int   0-based position within the document
page_number  int   PDF page (0 if not applicable)
ingested_at  str   ISO-8601 UTC
token_count  int   approximate word count
```

---

### `backend/llm/ingestion.py` — `DocumentIngester`

Offline pipeline: file on disk → plain text → overlapping chunks → store.

```
DocumentIngester(vector_store)
│
├── ingest_file(file_path, doc_type, vendor, doc_name=None)
│     → {"doc_id": str, "chunks_stored": int, "doc_name": str}
│     1. _parse(path)            → list[{"text", "page"}]
│     2. _is_cli_doc(full_text)  → choose CHUNK_TOKENS (512 or 256)
│     3. _chunk(full_text, chunk_tokens)  → list[{"text", "page"}]
│     4. build metadatas[]
│     5. vector_store.upsert_chunks(doc_id, texts, metadatas)
│
├── ingest_directory(dir_path, doc_type, vendor)
│     iterates non-recursively, calls ingest_file() per file
│
├── _parse(path)                 dispatch by extension
│     .pdf   → pdfplumber        (preserves page numbers)
│     .docx  → python-docx
│     .html  → BeautifulSoup + lxml (strips script/style/nav)
│     .txt   → open() + read
│     .md    → same as .txt
│
└── _chunk(text, chunk_tokens)   paragraph → sentence → word splitting
      1. Split on \n\n paragraphs
      2. Para > chunk_tokens → split on ". " sentence boundary
      3. Sentence > chunk_tokens → hard word-count split
      4. OVERLAP_TOKENS (64) tail carried forward as prefix of next chunk
```

**CLI detection heuristic**: if > 15% of non-empty lines start with `#` or `!` (typical Cisco/Juniper config snippets), the document is classified as a CLI reference and uses `CHUNK_TOKENS=256` to avoid fragmenting individual commands.

```
Chunk size constants:
    CHUNK_TOKENS   = 512    (standard docs)
    CLI_CHUNK_TOKENS = 256  (CLI references)
    OVERLAP_TOKENS =  64    (both modes)
```

---

### `backend/llm/retriever.py` — `route()` + `KnowledgeRetriever`

Intent routing and formatted retrieval.

#### `route(query) → (use_live: bool, use_knowledge: bool)`

```
Priority order:
  1. Any LIVE_ONLY_TRIGGER phrase present?  → (True, False)
  2. Any KNOWLEDGE_TRIGGER phrase present?  → (True, True)
  3. query < 6 words?                       → (True, False)   short/terse
  4. Default (longer conversational)        → (True, True)

use_live is ALWAYS True — live topology is always injected.

LIVE_ONLY_TRIGGERS (examples):
  "right now", "currently", "which devices", "list all",
  "current status", "is it online", "last crawl"

KNOWLEDGE_TRIGGERS (examples):
  "how to", "configure", "troubleshoot", "best practice",
  "ospf", "bgp", "vlan", "stp", "command", "why is"
```

#### `KnowledgeRetriever`

```
KnowledgeRetriever(vector_store, embedder)
│
├── retrieve(query, k=5, vendor_filter=None, doc_type_filter=None)
│     1. embedder.embed_query(query)
│     2. Build ChromaDB where-filter from vendor/doc_type args
│     3. vector_store.query(embedding, k, where, min_score)
│     4. Enrich results with flattened metadata fields
│     returns list[{text, score, doc_name, doc_type, vendor, page_number, ...}]
│
└── retrieve_as_text(query, k=5) → str
      Formats top-k results as a markdown block:

      ## Relevant Knowledge Base
      The following excerpts from your network documentation may help...

      **Source**: cisco_ospf_guide.pdf · page 12 [vendor_doc / cisco]  relevance: 0.76
      <chunk text>

      **Source**: ...
```

---

### `backend/rag/__init__.py` + `backend/rag/api.py` — REST API

Admin endpoints for managing the knowledge base.

```
Router prefix: /api/v1/rag
Auth:          Depends(get_current_user) on all endpoints

POST   /rag/documents/upload
       Accepts: multipart/form-data
         file      UploadFile    (PDF/DOCX/HTML/TXT/MD)
         doc_type  str           vendor_doc|cli_reference|sop|troubleshooting|best_practice
         vendor    str           cisco|huawei|juniper|aruba|general
         doc_name  str?          human-readable override
       Validates extension, doc_type, vendor
       Saves to backend/knowledge_base/<safe_name>
       Calls ingester.ingest_file()
       Returns: {doc_id, doc_name, chunks_stored, message}

GET    /rag/documents
       Returns: list of DocumentInfo
         {doc_id, doc_name, doc_type, vendor, ingested_at, chunk_count}

DELETE /rag/documents/{doc_id}
       Calls vector_store.delete_document(doc_id)
       Returns: {doc_id, chunks_deleted, message}
       Note: source file in knowledge_base/ is NOT deleted
             so rebuild() will re-ingest it

POST   /rag/documents/rebuild
       Re-ingests every file in backend/knowledge_base/
       Metadata override via .meta.json sidecar files
       Returns: {files_processed, total_chunks, errors[]}

GET    /rag/status
       Always responds (even when ChromaDB unavailable)
       Returns: {available, total_chunks, total_documents, chroma_path}
```

**DI pattern**: `setup_rag(ingester, vector_store)` populates module-level globals at startup — no need for FastAPI `Depends` injection of these heavy objects.

---

## Modified Files

### `backend/llm/context.py` — `ContextBuilder`

Added one method after `build_full_context()`:

```python
def build_knowledge_context(self, query: str, retriever, k: int = 5) -> str:
    """
    Retrieve semantically relevant knowledge chunks for query.
    Returns a formatted markdown block or "" when unavailable.
    """
    if retriever is None:
        return ""
    return retriever.retrieve_as_text(query, k=k)
```

`build_full_context()` and `build_topology_context()` are **unchanged** — M5 behaviour is unaffected.

---

### `backend/llm/agent.py` — `NetworkAgent`

**Change 1**: `__init__` gains a `retriever=None` parameter:

```python
def __init__(self, db_manager=None, provider=None, api_key=None,
             model=None, retriever=None):
    ...
    self._retriever = retriever     # KnowledgeRetriever | None
```

**Change 2**: In `chat()`, before building `messages`, add hybrid routing:

```python
# --- M21: Hybrid RAG routing ---
try:
    from llm.retriever import route
    use_live, use_knowledge = route(user_message)
except Exception:
    use_live, use_knowledge = True, False

per_turn_system = self._system_prompt   # always includes live topology (M5)
if use_knowledge and self._retriever is not None:
    knowledge_ctx = self._context.build_knowledge_context(
        user_message, self._retriever
    )
    if knowledge_ctx:
        per_turn_system = self._system_prompt + "\n\n" + knowledge_ctx

messages = [
    LLMMessage(role="system", content=per_turn_system),
    *self._history,
]
```

Everything from `# Get tool declarations` onward (tool-calling loop, proposal detection) is **unchanged**.

---

### `backend/backend.py` — App Lifespan + Router Mount

**New imports** (after `from ssh_terminal.session import cleanup_idle_sessions`):

```python
from llm.embedder import GeminiEmbedder
from llm.vector_store import KnowledgeVectorStore
from llm.retriever import KnowledgeRetriever
from llm.ingestion import DocumentIngester
from rag.api import router as rag_router, setup_rag
```

**Lifespan init** (replaces `agent = NetworkAgent(db)`):

```python
# M21 — Vector-Embedded RAG stack
embedder     = GeminiEmbedder(api_key=os.getenv("GEMINI_API_KEY"))
vector_store = KnowledgeVectorStore(embedder)
retriever    = KnowledgeRetriever(vector_store, embedder)
ingester     = DocumentIngester(vector_store)
logger.info("Vector knowledge store ready (chunks=%d)", vector_store.count())
setup_rag(ingester, vector_store)

agent = NetworkAgent(db, retriever=retriever)
```

**Router mount** (after `ssh_router`):

```python
app.include_router(rag_router, prefix="/api/v1", tags=["RAG Knowledge Base"])
```

---

## Data Flow — End-to-End Example

```
Scenario: "How do I configure OSPF area 0 on a Cisco router?"

1. Frontend sends POST /api/v1/llm/chat
   {"message": "How do I configure OSPF area 0 on a Cisco router?"}

2. NetworkAgent.chat() called
   │
   ├─ route("How do I configure OSPF...")
   │    "configure" ∈ KNOWLEDGE_TRIGGERS → (True, True)
   │
   ├─ GeminiEmbedder.embed_query("How do I configure OSPF...")
   │    → [0.021, -0.134, 0.782, ...]   768-dim vector
   │
   ├─ KnowledgeVectorStore.query(vec, k=5, min_score=0.35)
   │    ChromaDB cosine-distance search
   │    distance=0.41 → score = 1.0 - (0.41/2) = 0.795
   │    returns top 3 chunks from cisco_ospf_guide.pdf (pages 4, 7, 12)
   │
   ├─ KnowledgeRetriever.retrieve_as_text()
   │    formats as:
   │    "## Relevant Knowledge Base
   │     **Source**: cisco_ospf_guide.pdf · page 4 [vendor_doc / cisco] relevance: 0.80
   │     router ospf 1
   │      network 10.0.0.0 0.0.0.255 area 0
   │     ..."
   │
   ├─ per_turn_system = _system_prompt + "\n\n" + knowledge_ctx
   │    (live topology still present from M5 — untouched)
   │
   └─ GeminiProvider.generate(messages=[system+knowledge+history])
        LLM answers with accurate Cisco IOS commands
        sourced from the actual PDF, not hallucinated
```

---

## Directory Structure

```
backend/
├── chroma_db/                    ← auto-created at startup
│   └── chroma.sqlite3            ← ChromaDB persistent store
├── knowledge_base/               ← uploaded source files persisted here
│   ├── cisco_ospf_guide.pdf
│   ├── huawei_vrp_cli.pdf
│   └── *.meta.json               ← optional sidecar: {"doc_type", "vendor"}
├── rag/
│   ├── __init__.py
│   └── api.py                    ← FastAPI router (5 endpoints)
└── llm/
    ├── embedder.py               ← GeminiEmbedder (NEW)
    ├── vector_store.py           ← KnowledgeVectorStore (NEW)
    ├── ingestion.py              ← DocumentIngester (NEW)
    ├── retriever.py              ← route() + KnowledgeRetriever (NEW)
    ├── context.py                ← + build_knowledge_context() (MODIFIED)
    └── agent.py                  ← + retriever param + per-turn inject (MODIFIED)
```

---

## Dependencies Added

```
# requirements.txt — M21 additions
chromadb          # vector DB (embedded, SQLite-backed, no server required)
pdfplumber        # PDF text extraction with page numbers
python-docx       # DOCX paragraph extraction
beautifulsoup4    # HTML parsing (strips nav/script/style)
lxml              # HTML parser backend for BeautifulSoup
```

All 5 packages installed into `backend/venv/`. No new API keys or services required — `GEMINI_API_KEY` already present for M5 is reused for embeddings.

---

## Routing Logic — Decision Table

```
Query                                        use_live  use_knowledge  Reason
───────────────────────────────────────────────────────────────────────────────
"which devices are online right now?"          ✅        ❌           LIVE_ONLY_TRIGGER
"list all devices"                             ✅        ❌           LIVE_ONLY_TRIGGER
"what is the current topology?"                ✅        ❌           LIVE_ONLY_TRIGGER
"how to configure OSPF area 0?"                ✅        ✅           KNOWLEDGE_TRIGGER
"troubleshoot BGP peering failure"             ✅        ✅           KNOWLEDGE_TRIGGER
"what is the best practice for VLAN design?"   ✅        ✅           KNOWLEDGE_TRIGGER
"configure VRF-Lite on Huawei switches"        ✅        ✅           KNOWLEDGE_TRIGGER
"show me R1 interfaces"                        ✅        ❌           short (<6 words)
"yes"                                          ✅        ❌           short (<6 words)
"compare OSPF vs EIGRP for campus design"      ✅        ✅           default (>6 words)
```

---

## Scoring & Thresholds

```
ChromaDB uses cosine DISTANCE metric [0.0 … 2.0]:
    0.0 = identical vectors
    2.0 = perfectly opposite vectors

Converted to similarity score for readability:
    score = 1.0 – (distance / 2.0)

    distance=0.00 → score=1.00  (exact match)
    distance=0.40 → score=0.80  (highly relevant)
    distance=1.00 → score=0.50  (marginally relevant)
    distance=1.30 → score=0.35  (threshold cutoff — filtered out)
    distance=2.00 → score=0.00  (irrelevant)

DEFAULT min_score = 0.35
    Chunks with score < 0.35 are dropped before returning to the agent.
    Prevents garbage context injection from dominating the system prompt.
```

---

## Frontend (Pending — Not Yet Implemented)

The following frontend screens are planned but not yet built:

```
/rag  (new route)
│
├── KnowledgeBaseStatus card
│     GET /api/v1/rag/status
│     Shows: available, total_chunks, total_documents, chroma_path
│
├── DocumentUpload form
│     POST /api/v1/rag/documents/upload
│     Fields: file picker, doc_type select, vendor select, doc_name input
│     Progress: chunk count on success toast
│
├── DocumentLibrary table
│     GET /api/v1/rag/documents
│     Columns: name, type, vendor, ingested_at, chunks, [delete icon]
│     DELETE /api/v1/rag/documents/{doc_id}
│
└── RebuildButton
      POST /api/v1/rag/documents/rebuild
      Wipes + re-ingests all files from knowledge_base/
      Shows: files_processed, total_chunks, errors[]
```

---

## Known Limitations & Future Work

| Limitation | Notes |
|---|---|
| No streaming retrieval scores in chat UI | Scores are private to the backend; frontend sees only the final answer |
| No per-user document isolation | All users share the same `network_knowledge` collection |
| Rebuild is not atomic | If rebuild fails mid-way, some old chunks may be gone and new chunks not yet present |
| CLI detection is heuristic | High `#`-line density could misclassify markdown docs as CLI references |
| No re-ranking / cross-encoder | Single-stage vector retrieval only; no second-pass reranker |
| Frontend admin panel | REST API is complete; UI component not yet built |
