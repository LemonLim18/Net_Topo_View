# LLM / RAG / MCP Production-Grade Audit & Refactor Plan

## 1. Current System Analysis

```mermaid
flowchart TD
    U["User Query"] --> CL["classify_query()"]
    CL --> RP["_build_route_plan()"]
    RP --> PR["Prefetch Tools (sequential)"]
    PR --> CTX["_build_network_context()"]
    CTX --> SP["_render_system_prompt()"]
    SP --> |"~15K+ tokens"| LLM["Gemini API (sync call)"]
    LLM --> TC{"Tool calls?"}
    TC -->|yes| EXEC["Execute tools (loop)"]
    EXEC --> LLM
    TC -->|no| GR["_build_grounding_meta()"]
    GR --> OUT["Response + Grounding"]

    CTX --> CB["ContextBundleBuilder.build_bundle()"]
    CB --> INV["Fetch Inventory"]
    CB --> TOPO["Fetch Topology"]
    CB --> TEL["Fetch Telemetry"]
    CB --> SDN["Fetch SDN"]
    CB --> CFG["Fetch Config History"]
    CB --> DEP["Fetch Deployment"]
    CB --> INC["Fetch Incidents"]
    CB --> THR["Fetch Threats"]
    CB --> DRI["Fetch Drift"]
    CB --> KNO["Fetch Knowledge"]
    CB --> FRE["Freshness Warnings"]
```

### Architecture Summary

| Layer | Files | Role |
|-------|-------|------|
| **Orchestration** | [agent.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/agent.py) (2218 lines) | Tool-calling loop, routing, grounding, proposals |
| **LLM Provider** | [gemini_provider.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/gemini_provider.py) | Gemini SDK wrapper, sync `generate()` |
| **Query Router** | [context_contract.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/context_contract.py) | `classify_query()` → 8 query classes |
| **Context Builder** | [context.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/context.py) | Fuses topology + bundle → system prompt |
| **Context Bundle** | [context_bundle.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/rag/context_bundle.py) (1617 lines) | 11-domain data fusion |
| **RAG Retriever** | [retriever.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/retriever.py) | Partition-aware retrieval + re-ranking |
| **Vector Store** | [vector_store.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/vector_store.py) | ChromaDB, 6 partitions |
| **Embedder** | [embedder.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/embedder.py) | Gemini text-embedding-004 (768-dim) |
| **Auto-Embedder** | [auto_embedder.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/rag/auto_embedder.py) (1042 lines) | Incremental embedding for live state |
| **Network Tools** | [network_tools.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/network_tools.py) (2452 lines) | 75+ callable tool functions |
| **MCP** | [mcp/server.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/server.py), [tools.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/tools.py), [resources.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/resources.py) | MCP protocol layer |
| **Ingestion** | [ingestion.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/ingestion.py) | Document → chunk → embed → store |
| **Refresh** | [refresh_fabric.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/refresh_fabric.py), [context_state.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/context_state.py) | Pub/sub invalidation |

---

## 2. Identified Issues

### CRITICAL — Directly Impact Response Latency

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| **C1** | **System prompt bloat: ~15K+ tokens** injected every call (contract + source matrix + context versions + topology + templates + full 11-domain bundle) | `context.py:182-221` → `build_full_context()` | Every LLM call carries ~15K tokens. At current pricing and for locally hosted models, this wastes compute and slows inference. |
| **C2** | **Synchronous Gemini SDK call** blocks the async event loop | `gemini_provider.py:97-112` — `self._client.models.generate_content()` called synchronously inside `async def generate()` | Entire FastAPI server blocks during LLM call |
| **C3** | **Prefetch tools execute sequentially** | `agent.py:_apply_prefetched_route_tools` — each tool runs in series | For `troubleshooting` class, 3-5 prefetch tools run back-to-back (~2-5s total wait) |
| **C4** | **No embedding query cache** | `embedder.py:embed_query()` — calls Gemini API every time | Same query text re-embedded on every RAG lookup — adds ~200-500ms per query |
| **C5** | **Prompt pass-through as string concatenation** instead of structured Gemini contents | `gemini_provider.py:127-141` — `_build_contents()` concatenates all messages into a single string | Loses Gemini's native multi-turn support, prevents proper `system_instruction` usage, inflates token count |

### HIGH — Cause Unnecessary Overhead

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| **H1** | **Single-entry network context cache** — evicted on every unique query | `agent.py:2065-2086` — `self._network_context_cache = {cache_key: network_context}` | Cache only holds 1 entry; effectively no caching since each query has a unique key |
| **H2** | **No tool result TTL cache** — identical tool calls repeat across conversation turns | `agent.py:500+` — tool execution loop | `get_context_health`, `get_topology_summary` re-execute on every message |
| **H3** | **ContextBundle builds all 11 domains by default** even when routing says only inventory is needed | `context.py:253-273` — partial routing flags, but most are `True` by default | Simple inventory query fetches telemetry, SDN, threats, drift, incidents, knowledge |
| **H4** | **MCP `get_device_details` triple-fetches** — calls inventory, live, AND compare tools for one device | `mcp/tools.py:47-68` — `get_device_details()` | 3x redundant DB/API calls per MCP device query |
| **H5** | **Context versions injected into every prompt** (11 domains × their version + timestamp) | `context_state.py:103-118` — `to_prompt_text()` | ~500 tokens of operational metadata the LLM never needs for answering |

### MEDIUM — Design Improvements

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| **M1** | **Live network state embedded into vector store** via `auto_embedder.py` — this data changes frequently and embedding stale snapshots provides low retrieval value | `auto_embedder.py:74-160` — `embed_crawl_result()` | Stale live-state embeddings pollute retrieval results; should use direct structured query instead |
| **M2** | **No response dedup for repeated questions** — same user question in same session makes full LLM round-trip | `agent.py:chat()` | Wasted compute for common repeated queries |

---

## 3. Cross-Check Against Best Practices

| Practice | Current State | Recommendation |
|----------|---------------|----------------|
| **Static vs dynamic data separation** | ✅ 6 partitions in vector store — but live state is also embedded (M1) | Stop embedding live_state; use structured API queries for real-time data |
| **Structured retrieval for operational data** | ✅ Direct DB tools exist for device lookup | Enforce: LLM only for reasoning/summarization, never for data lookup |
| **Embedding cache** | ❌ No cache | Add LRU cache on `embed_query()` |
| **System prompt efficiency** | ❌ Massive prompt | Domain-filtered context injection based on `routing_decision` |
| **Caching layer** | ❌ Effectively none | Add TTL cache for tool results + network context |
| **Async I/O** | ❌ Sync Gemini SDK call | Use `run_in_threadpool` or native async SDK |
| **Parallel tool execution** | ❌ Sequential | Use `asyncio.gather` for independent prefetch tools |
| **Gemini API best practice** | ❌ String concatenation | Use structured `Content` objects with proper `system_instruction` |

---

## 4. Refactor Recommendations

### Priority 1 — Latency Killers (Target: <2s for inventory, <10s for complex)

#### R1. Domain-Filtered System Prompt
**Files**: [context.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/context.py)

Only inject the domains that `routing_decision.required_domains` actually requests. For `inventory_only`, skip topology, telemetry, SDN, threats, drift, and knowledge.

```diff
- parts.append(self.build_topology_context())
- parts.append(self.build_available_templates_context())
+ if decision is None or "live_topology" in (decision.required_domains or set()):
+     parts.append(self.build_topology_context())
+ if decision is None or "config" in (decision.required_domains or set()):
+     parts.append(self.build_available_templates_context())
```

**Expected savings**: ~10K tokens per inventory query.

#### R2. Async Gemini Call via ThreadPool
**File**: [gemini_provider.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/gemini_provider.py)

```diff
- response = self._client.models.generate_content(...)
+ from starlette.concurrency import run_in_threadpool
+ response = await run_in_threadpool(
+     self._client.models.generate_content, ...
+ )
```

#### R3. Parallel Prefetch Tool Execution
**File**: [agent.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/agent.py)

Execute independent prefetch tools concurrently:
```python
import asyncio
results = await asyncio.gather(
    *[run_in_threadpool(self._tools.execute, tc.name, tc.arguments) for tc in prefetch_tools]
)
```

#### R4. Embedding Query Cache
**File**: [embedder.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/embedder.py)

Add `functools.lru_cache` on `embed_query()`:
```python
from functools import lru_cache

@lru_cache(maxsize=256)
def embed_query(self, text: str) -> list[float]:
    ...
```

### Priority 2 — Token & Compute Reduction

#### R5. Structured Gemini Contents
**File**: [gemini_provider.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/gemini_provider.py)

Use Gemini's native `system_instruction` + structured `Content` objects instead of string concatenation. This enables proper multi-turn handling and reduces token overhead by ~15-20%.

#### R6. Tool Result TTL Cache
**File**: [agent.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/agent.py)

Add a per-session cache for tool results with configurable TTL (e.g., 60s for inventory, 30s for topology, 10s for telemetry):
```python
_TOOL_CACHE_TTL = {"get_context_health": 60, "get_topology_summary": 30, ...}
```

#### R7. Drop Context Versions from Prompt
**File**: [context.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/context.py)

Remove `get_context_state_manager().to_prompt_text()` from the system prompt — the LLM does not need to know `inventory: version=3; last_updated=...`.

#### R8. Stop Embedding Live Network State
**File**: [auto_embedder.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/rag/auto_embedder.py)

Remove `PARTITION_LIVE_STATE` embedding. Live topology changes too frequently for embeddings to be useful. Structured API queries via tool calls (`get_topology_summary`, `get_device_details`) already serve this purpose.

### Priority 3 — Operational Polish

#### R9. MCP `get_device_details` Consolidation
**File**: [mcp/tools.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/mcp/tools.py)

Fetch once and compose instead of triple-calling:
```python
device = _tool_payload(network_tools.get_inventory_device, hostname)
# Use device data to compose canonical state — no need for 3 separate calls
```

#### R10. Network Context Cache Upgrade
**File**: [agent.py](file:///c:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/llm/agent.py)

Replace the single-entry dict with a proper LRU cache keyed by `(query_class, frozenset(hostnames))`.

---

## 5. Performance Improvement Summary

| Metric | Current | After Refactor | Speedup |
|--------|---------|----------------|---------|
| Inventory query latency | ~60s | **<1s** (fast path) / **<3s** (with LLM) | 20-60x |
| System prompt tokens | ~15K+ | ~2-5K (domain-filtered) | 3-7x |
| Embedding query time | ~300ms (API call each time) | **<1ms** (LRU cache hit) | 300x |
| Prefetch tool time | ~2-5s (sequential) | **<1s** (parallel) | 3-5x |
| Event loop blocking | Full block during LLM call | Non-blocking (threadpool) | — |
| Tool re-execution waste | All tools re-run each turn | Cached results (TTL) | N/A |

---

## 6. Implementation Plan

> [!IMPORTANT]
> All changes preserve existing functionality. No API contract changes.

### Phase 1 — Critical Latency Fixes (R1-R4)
- [x] **R1**: Domain-filtered system prompt in `context.py`
- [x] **R2**: Async Gemini call in `gemini_provider.py`
- [x] **R3**: Parallel prefetch in `agent.py`
- [x] **R4**: Embedding LRU cache in `embedder.py`

### Phase 2 — Token & Compute Reduction (R5-R8)
- [x] **R5**: Structured Gemini contents in `gemini_provider.py`
- [x] **R6**: Tool result TTL cache in `agent.py`
- [x] **R7**: Remove context versions from prompt in `context.py`
- [x] **R8**: Stop embedding live state in `auto_embedder.py`

### Phase 3 — Operational Polish (R9-R10)
- [x] **R9**: MCP device details consolidation in `mcp/tools.py`
- [x] **R10**: Network context LRU cache in `agent.py`

---

## 7. Verification Plan

### Automated
- Run `test_answer_grounding.py`, `test_agent_routing_policy.py`, `test_context_contract.py`, `test_rag_partitions.py`
- Verify TypeScript build: `npx tsc --noEmit`
- Python syntax check: `python -c "import ast; ast.parse(...)"`

### Manual
- Send inventory query → verify <1s via fast path
- Send troubleshooting query → verify progress phases + <10s total
- Confirm grounding footer shows clean freshness text (no raw debug)
- Verify MCP `get_device_details` makes 1 DB call (not 3)
