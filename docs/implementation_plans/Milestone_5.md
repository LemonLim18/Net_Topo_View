# Milestone 5: Controller Latency And Meta-Planning Routing

## Objective

Reduce avoidable controller latency for simple and meta-architecture prompts by fixing the heaviest backend path first:

1. Route platform-architecture and implementation-planning prompts away from the compliance path.
2. Avoid building operational network context when the prompt does not require live or planned network evidence.
3. Eliminate duplicate deterministic prefetch execution between streaming and non-streaming controller flows.

## Major Fixes

### M5.1 Architecture Planning Query Class

- Add `architecture_planning` to the query taxonomy.
- Match prompts that combine platform-design language with LLM/RAG/MCP/controller-performance terms.
- Keep this route out of live inventory, telemetry, drift, and compliance prefetch unless the user explicitly asks for operational state.

Expected outcome:
- Long design prompts stop being misrouted as `compliance`.
- The controller avoids unnecessary `get_context_health`, config, drift, and inventory reads for platform-planning requests.

### M5.2 Lazy Operational Context Injection

- Only inject the `Current Network State` section when the route actually needs operational or inventory context.
- Allow architecture-planning turns to run with controller policy and milestone baseline only.

Expected outcome:
- Meta prompts no longer pay the token and assembly cost of empty or irrelevant network context.

### M5.3 Shared Prepared Turn State

- Build turn routing state once per request.
- Reuse prefetched tool results when the streamed controller hands off to the full chat loop.
- Preserve controller reroutes for stale evidence, but stop re-running the original deterministic prefetch tools.

Expected outcome:
- Streaming requests avoid duplicated route classification and duplicated prefetch execution.

## Validation

- Add a classifier regression proving platform architecture prompts resolve to `architecture_planning`.
- Add a streaming regression proving the initial prefetched tools are executed once per turn.

## Status

Completed in this milestone slice:

- `architecture_planning` classifier route added.
- Empty network-context injection removed for non-operational meta prompts.
- Streaming handoff now reuses prepared prefetch state.
- Focused backend regressions added and passing.

## Next Follow-On Work

1. Add a compact meta-planning answer policy so architecture prompts produce shorter, more execution-oriented responses by default.
2. Push the same lazy-loading discipline into knowledge retrieval so architecture prompts do not trigger vector lookup unless explicitly requested.
3. Add controller timing telemetry per phase so future regressions show whether latency came from routing, prefetch, retrieval, or model generation.