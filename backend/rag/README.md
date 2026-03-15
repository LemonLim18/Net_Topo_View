# RAG (Retrieval-Augmented Generation) and vector store

## Where and how to run RAG Rebuild

- **In the app:** Open **Knowledge Base** from the sidebar (e.g. **BookOpen** icon or "Knowledge Base" menu). On that page, use the **"Rebuild"** button (next to "Rebuild Knowledge Base"). Rebuild runs in the background; when it finishes, the document list and chunk counts update.
- **Via API:** `POST /api/v1/rag/documents/rebuild` (requires auth). Returns `{"status": "accepted", "message": "Knowledge base rebuild started in background"}`. The job runs asynchronously; use `GET /api/v1/rag/status` and `GET /api/v1/rag/documents` to see chunk counts and ingested documents after it completes.

Rebuild does two things:

1. Clears the **static knowledge** partition and re-ingests every file in `backend/knowledge_base/` (uploaded PDFs, TXT, MD, DOCX, etc.).
2. Ingests the **Ansible reference library**: every `backend/ansible/reference/{feature}/{vendor}/reference.md` with metadata `doc_type=ansible_reference`, `vendor`, `applicable_feature`.

So after adding or changing files in `knowledge_base/` or in `ansible/reference/`, run a rebuild so the vector store is up to date.

## Vector database / RAG tech

- **Vector store:** **ChromaDB** (persistent).
- **Storage path:** `backend/chroma_db/` (SQLite-backed persistence).
- **Embeddings:** Provided by the app’s embedder (e.g. Gemini Embedding API), not stored in ChromaDB; embeddings are computed at ingest and at query time.
- **Partitions:** The static RAG knowledge used for plan-ansible and Q&A lives in the **static_knowledge** partition (`network_knowledge_static` collection). Other partitions exist for live state, inventory, incidents, etc.

To install ChromaDB: `pip install chromadb`.
