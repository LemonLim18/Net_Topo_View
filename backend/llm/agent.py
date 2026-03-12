"""
NetworkAgent — the main AI agent orchestrator.

Ties together:
    - LLM Provider (Gemini)
    - Tool Registry (network tools)
    - Context Builder (topology RAG)
    - Proposal Manager (M19 agentic workflow)

Implements a tool-calling loop: the agent calls tools, gets results,
and continues reasoning until it produces a final answer.

When the AI generates a configuration (via generate_config tool),
the agent automatically packages the result into a ConfigProposal
for human-in-the-loop approval before deployment.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from starlette.concurrency import run_in_threadpool

from config_engine.rollback_planner import build_rollback_plan
from llm.advanced_differentiators import build_advanced_differentiator_status
from config_engine.safety import validate_action_artifact, validate_cli_artifact
from llm.answer_release import build_answer_release_state
from llm.answer_policy import build_answer_policy, policy_status
from llm.answer_grounding import build_answer_grounding, format_conflict_summary, format_grounding_summary
from llm.controller_flow import build_controller_flow_state
from llm.controller_policy import build_controller_policy, render_controller_policy_prompt
from llm.controller_self_correction import build_self_correction_state, plan_self_correction
from llm.controller_scope import (
    build_controller_implementation_status,
    build_controller_scope_contract,
    render_controller_scope_prompt,
)
from llm.context_contract import classify_query

from llm.milestone_baseline import build_milestone2_baseline, render_milestone2_baseline_prompt
from llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall
from llm.system_prompt import build_system_prompt
from llm.semantics import AUTHORITY_MAP_VERSION, SEMANTICS_VERSION
from llm.tools import ToolRegistry
from llm.context import ContextBuilder
from llm.proposal import ProposalManager, ConfigProposal
from llm import network_tools

logger = logging.getLogger("llm.agent")

# Maximum tool-calling rounds before forcing a text response
MAX_TOOL_ROUNDS = 5

# Per-tool TTL (seconds) for the in-session result cache.
# Tools not listed here are always re-executed.
_TOOL_CACHE_TTL: Dict[str, int] = {
    "get_context_health": 60,
    "get_topology_summary": 30,
    "get_last_sync_status": 60,
    "get_refresh_job_status": 30,
    "list_inventory_devices": 60,
    "list_all_devices": 30,
    "list_templates": 120,
    "get_template_schema": 120,
    "get_audit_log": 60,
    "get_canonical_semantics_map": 300,
}

class NetworkAgent:
    """
    Main agent that handles multi-turn conversations with tool calling.

    When the AI calls generate_config during a conversation, the agent
    automatically creates a ConfigProposal that the user can approve or
    reject through the frontend — mimicking the Antigravity workflow.

    Usage::

        agent = NetworkAgent(db_manager=db)
        response = await agent.chat("Show me all devices")
        # response = {"role": "assistant", "content": "Here are...", "type": "text"}

        # Config request → returns a proposal
        response = await agent.chat("Configure OSPF on R1")
        # response = {"role": "assistant", "content": "...", "type": "proposal", "proposal": {...}}
    """

    def __init__(
        self,
        db_manager=None,
        provider: Optional[LLMProvider] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        retriever=None,
        proposal_manager: Optional[ProposalManager] = None,
    ):
        """
        Args:
            db_manager:  DatabaseManager instance for live topology.
            provider:    Custom LLMProvider (default: GeminiProvider).
            api_key:     Gemini API key (if using default provider).
            model:       Model name override.
            retriever:   Optional KnowledgeRetriever for M21 Vector RAG.
        """
        # Initialize LLM provider
        if provider:
            self._provider = provider
        else:
            from llm.gemini_provider import GeminiProvider
            self._provider = GeminiProvider(api_key=api_key, model=model)

        # Initialize context builder
        self._context = ContextBuilder(db_manager=db_manager)

        # Initialize proposal manager (M19)
        self._proposals = proposal_manager or ProposalManager()

        # Store db_manager for device lookups
        self._db = db_manager

        # Initialize and populate tool registry
        self._tools = ToolRegistry()
        network_tools.set_db_manager(db_manager)
        network_tools.set_intent_provider(self._provider)
        network_tools.register_all_tools(self._tools)
        self._controller_scope_contract = build_controller_scope_contract()

        # M21: optional knowledge retriever for hybrid RAG
        self._retriever = retriever

        # Conversation history (persists across chat() calls)
        self._history: List[LLMMessage] = []

        self._network_context_cache: Dict[str, str] = {}

        # Tool result TTL cache: {(tool_name, args_key): (result, timestamp)}
        self._tool_result_cache: Dict[str, tuple[str, float]] = {}

        logger.info(
            "NetworkAgent initialized (model=%s, tools=%d)",
            self._provider.model_name(),
            len(self._tools.list_tools()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Friendly labels for each query-class source used in SSE progress
    _SOURCE_LABELS: Dict[str, str] = {
        "inventory_only": "device inventory (NetBox)",
        "planned_vs_live": "planned vs. live state comparison",
        "live_only": "live network topology",
        "troubleshooting": "incidents, telemetry & live state",
        "change_planning": "intent pipeline & configuration engine",
        "architecture_planning": "controller architecture guidance",
        "compliance": "compliance & drift records",
        "knowledge_heavy": "knowledge base documents",
        "general": "network context",
    }

    def _prepare_turn_state(self, user_message: str) -> Dict[str, Any]:
        routing_decision = classify_query(user_message)
        logger.info(
            "Query classified as %s (%s)",
            routing_decision.query_class,
            routing_decision.reasoning,
        )

        candidate_hostnames = self._extract_candidate_hostnames(user_message)
        route_plan = self._build_route_plan(
            user_message,
            routing_decision.query_class,
            candidate_hostnames,
        )
        route_plan["query_class"] = routing_decision.query_class
        route_plan["required_domains"] = list(routing_decision.required_domains)
        route_plan["hostnames"] = list(candidate_hostnames)

        return {
            "routing_decision": routing_decision,
            "candidate_hostnames": candidate_hostnames,
            "route_plan": route_plan,
        }

    async def _prefetch_route_tools(
        self,
        route_plan: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        tool_results: Dict[str, Any] = {}
        tool_args: Dict[str, Dict[str, Any]] = {}
        prefetch_tools = route_plan.get("prefetch_tools", [])
        if not prefetch_tools:
            return tool_results, tool_args

        results = await asyncio.gather(
            *(run_in_threadpool(self._execute_tool_cached, tc.name, tc.arguments) for tc in prefetch_tools)
        )
        for tc, result in zip(prefetch_tools, results):
            tool_results[tc.name] = result
            tool_args[tc.name] = tc.arguments
        return tool_results, tool_args

    def _should_include_network_context(self, routing_decision=None) -> bool:
        if routing_decision is None:
            return True
        return any(
            (
                getattr(routing_decision, "use_live_context", False),
                getattr(routing_decision, "include_inventory", False),
                getattr(routing_decision, "include_telemetry", False),
                getattr(routing_decision, "include_sdn", False),
                bool(getattr(routing_decision, "required_domains", ())),
            )
        )

    def _try_inventory_fast_path(
        self,
        user_message: str,
        query_class: str,
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict],
        route_plan: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        For simple inventory_only queries where the prefetch already has the
        answer, compose a direct response WITHOUT calling the LLM.

        Returns a chat-compatible dict if fast path succeeded, else None.
        """
        if query_class != "inventory_only":
            return None

        # Check get_inventory_device result
        raw = tool_results.get("get_inventory_device")
        if raw is None:
            # Also check search_inventory_devices
            raw = tool_results.get("search_inventory_devices")
        if raw is None:
            return None

        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return None

        if payload.get("error"):
            return None

        # Single device lookup → compose direct answer
        device = payload.get("device")
        if isinstance(device, dict):
            hostname = device.get("hostname", "Unknown")
            mgmt_ip = device.get("management_ip")
            vendor = device.get("vendor", "")
            model = device.get("model", "")
            role = device.get("role", "")
            status = device.get("status", "")

            # Build a concise, natural-language answer
            parts = []
            q_lower = user_message.lower()
            if "management ip" in q_lower or "mgmt ip" in q_lower or "ip address" in q_lower:
                if mgmt_ip:
                    parts.append(f"The management IP address for **{hostname}** is **{mgmt_ip}**.")
                else:
                    parts.append(f"**{hostname}** does not have a management IP address configured in the inventory.")
            elif "vendor" in q_lower or "manufacturer" in q_lower:
                parts.append(f"**{hostname}** is a **{vendor or 'unknown vendor'}** device.")
            elif "model" in q_lower:
                parts.append(f"**{hostname}** is model **{model or 'unknown'}**.")
            elif "status" in q_lower:
                parts.append(f"**{hostname}** has status: **{status or 'unknown'}**.")
            elif "role" in q_lower:
                parts.append(f"**{hostname}** has role: **{role or 'unknown'}**.")
            else:
                # General device info
                info_parts = [f"**{hostname}**"]
                if mgmt_ip:
                    info_parts.append(f"management IP **{mgmt_ip}**")
                if vendor:
                    info_parts.append(f"vendor **{vendor}**")
                if model:
                    info_parts.append(f"model **{model}**")
                if role:
                    info_parts.append(f"role **{role}**")
                if status:
                    info_parts.append(f"status **{status}**")
                parts.append(f"Device {', '.join(info_parts)}.")

            content = "\n\n".join(parts)

            # Build grounding metadata
            grounding_meta = self._build_grounding_meta(
                query_class=query_class,
                tool_results=tool_results,
                tool_args=tool_args,
            )
            answer_release = build_answer_release_state(
                query_class=query_class,
                grounding=grounding_meta,
                tool_args=tool_args,
                allowed_tools=(route_plan or {}).get("allowed_tools") or [],
            )
            grounding_meta["answer_release"] = answer_release

            if str(answer_release.get("decision") or "") == "allow_limited":
                content = self._render_limited_answer(content, answer_release)

            # Add the freshness note if needed
            final_content = self._render_grounded_content(content, grounding_meta)

            self._history.append(LLMMessage(role="user", content=user_message))
            self._history.append(LLMMessage(role="assistant", content=final_content))

            return {
                "role": "assistant",
                "content": final_content,
                "type": "text",
                "grounding_meta": grounding_meta,
            }

        # Multi-device search result
        devices = payload.get("devices")
        if isinstance(devices, list) and devices:
            lines = []
            for dev in devices[:10]:
                if not isinstance(dev, dict):
                    continue
                hn = dev.get("hostname", "Unknown")
                ip = dev.get("management_ip", "N/A")
                v = dev.get("vendor", "")
                lines.append(f"- **{hn}**: {ip}" + (f" ({v})" if v else ""))
            content = f"Found **{len(devices)}** device(s) in inventory:\n\n" + "\n".join(lines)

            grounding_meta = self._build_grounding_meta(
                query_class=query_class,
                tool_results=tool_results,
                tool_args=tool_args,
            )
            final_content = self._render_grounded_content(content, grounding_meta)

            self._history.append(LLMMessage(role="user", content=user_message))
            self._history.append(LLMMessage(role="assistant", content=final_content))

            return {
                "role": "assistant",
                "content": final_content,
                "type": "text",
                "grounding_meta": grounding_meta,
            }

        return None

    async def chat_streaming(self, user_message: str) -> AsyncIterator[Dict[str, Any]]:
        """
        Streaming version of chat() that yields progress events.

        Yields dicts of the form:
            {"event": "progress", "phase": "...", "label": "...", "elapsed_ms": ...}
            {"event": "complete", ...full chat response...}
        """
        t0 = time.monotonic()

        def _elapsed() -> int:
            return int((time.monotonic() - t0) * 1000)

        # Phase 1: Classify
        yield {"event": "progress", "phase": "classifying", "label": "Understanding your request…", "elapsed_ms": _elapsed()}

        turn_state = self._prepare_turn_state(user_message)
        routing_decision = turn_state["routing_decision"]
        route_plan = turn_state["route_plan"]

        source_label = self._SOURCE_LABELS.get(routing_decision.query_class, "network context")

        # Phase 2: Prefetch
        yield {"event": "progress", "phase": "fetching", "label": f"Retrieving {source_label}…", "elapsed_ms": _elapsed()}

        tool_results, tool_args = await self._prefetch_route_tools(route_plan)

        # Phase 2b: Try fast path (skip LLM for simple inventory lookups)
        fast_result = self._try_inventory_fast_path(
            user_message,
            routing_decision.query_class,
            tool_results,
            tool_args,
            route_plan,
        )
        if fast_result is not None:
            fast_result["elapsed_ms"] = _elapsed()
            yield {"event": "complete", **fast_result}
            return

        # Phase 3: LLM reasoning
        yield {"event": "progress", "phase": "reasoning", "label": "Analyzing and composing response…", "elapsed_ms": _elapsed()}

        result = await self._chat_with_prepared_turn(
            user_message,
            turn_state,
            prefetched_tool_results=tool_results,
            prefetched_tool_args=tool_args,
        )
        result["elapsed_ms"] = _elapsed()
        yield {"event": "complete", **result}

    async def chat(self, user_message: str) -> Dict[str, Any]:
        """
        Process a user message and return the agent's response.

        This implements a tool-calling loop:
        1. Send user message + history to LLM
        2. If LLM requests tool calls, execute them
        3. Feed tool results back to LLM
        4. Repeat until LLM produces a text response
        5. If generate_config was called, package as a proposal

        Returns:
            {"role": "assistant", "content": "...", "type": "text"} or
            {"role": "assistant", "content": "...", "type": "proposal", "proposal": {...}}
        """
        turn_state = self._prepare_turn_state(user_message)
        return await self._chat_with_prepared_turn(user_message, turn_state)

    async def _chat_with_prepared_turn(
        self,
        user_message: str,
        turn_state: Dict[str, Any],
        prefetched_tool_results: Optional[Dict[str, Any]] = None,
        prefetched_tool_args: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self._history.append(LLMMessage(role="user", content=user_message))

        routing_decision = turn_state["routing_decision"]
        candidate_hostnames = turn_state["candidate_hostnames"]
        route_plan = turn_state["route_plan"]

        per_turn_system = self._render_system_prompt(
            user_message=user_message,
            routing_decision=routing_decision,
            device_hostnames=candidate_hostnames,
            controller_policy=route_plan.get("controller_policy"),
            route_instruction=route_plan.get("instruction", ""),
        )
        if routing_decision.use_knowledge and self._retriever is not None:
            knowledge_ctx = self._context.build_knowledge_context(
                user_message, self._retriever
            )
            if knowledge_ctx:
                per_turn_system = per_turn_system + "\n\n" + knowledge_ctx
        else:
            knowledge_ctx = ""

        # Build full message list (system + history)
        messages = [
            LLMMessage(role="system", content=per_turn_system),
            *self._history,
        ]

        # Track tool calls for proposal detection
        tool_results: Dict[str, Any] = dict(prefetched_tool_results or {})
        tool_args: Dict[str, Dict] = dict(prefetched_tool_args or {})
        self_correction_state = build_self_correction_state()

        await self._apply_prefetched_route_tools(messages, route_plan, tool_results, tool_args, self_correction_state, user_message)

        # Get tool declarations
        tool_declarations = self._tools.get_declarations(route_plan["allowed_tools"])

        # Tool-calling loop
        for round_num in range(MAX_TOOL_ROUNDS):
            response = await self._provider.generate(
                messages=messages,
                tools=tool_declarations if tool_declarations else None,
                temperature=0.3,
            )

            # If no tool calls, we have a final answer
            if not response.tool_calls:
                if self._enforce_controller_policy(
                    messages,
                    route_plan,
                    user_message,
                    tool_results,
                    tool_args,
                    self_correction_state,
                ):
                    continue
                if self._enforce_required_tool_policy(messages, route_plan, tool_results, tool_args, self_correction_state, user_message):
                    continue
                flow_state, flow_action = self._evaluate_controller_flow(
                    messages,
                    user_message,
                    route_plan,
                    tool_results,
                    tool_args,
                    self_correction_state,
                    knowledge_used=bool(knowledge_ctx),
                )
                if flow_action == "reroute":
                    continue
                grounding_meta = self._build_grounding_meta(
                    routing_decision.query_class,
                    tool_results,
                    tool_args,
                    route_plan.get("answer_policy"),
                    knowledge_used=bool(knowledge_ctx),
                    controller_flow=flow_state,
                    self_correction_state=self_correction_state,
                )
                answer_release, answer_release_action = self._evaluate_answer_release(
                    messages,
                    user_message,
                    route_plan,
                    grounding_meta,
                    tool_results,
                    tool_args,
                    self_correction_state,
                )
                grounding_meta["answer_release"] = answer_release
                if answer_release_action == "reroute":
                    continue
                response_content = response.content
                if flow_action == "block":
                    response_content = self._render_controller_flow_block(flow_state)
                elif answer_release_action == "allow_limited":
                    response_content = self._render_limited_answer(response_content, answer_release)
                final_content = self._render_grounded_content(response_content, grounding_meta)
                self._history.append(
                    LLMMessage(role="assistant", content=final_content)
                )

                # --- M19: Proposal Detection ---
                proposal = self._maybe_create_proposal(
                    user_message, response.content, tool_results, tool_args
                )
                if proposal:
                    resp = {
                        "role": "assistant",
                        "content": final_content,
                        "type": "proposal",
                        "proposal": proposal.model_dump(),
                        "grounding_meta": grounding_meta,
                    }
                    if hasattr(proposal, "_intent_meta"):
                        resp["type"] = "intent_plan"
                        resp["intent_meta"] = proposal._intent_meta  # type: ignore[attr-defined]
                    return resp

                return {
                    "role": "assistant",
                    "content": final_content,
                    "type": "text",
                    "grounding_meta": grounding_meta,
                }

            # Execute tool calls
            logger.info(
                "Round %d: %d tool calls",
                round_num + 1,
                len(response.tool_calls),
            )

            # Add assistant message with tool calls to context
            tool_call_summary = ", ".join(
                f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
                for tc in response.tool_calls
            )
            messages.append(LLMMessage(
                role="assistant",
                content=f"Calling tools: {tool_call_summary}",
                tool_calls=response.tool_calls,
            ))

            # Execute each tool and add results
            for tc in response.tool_calls:
                logger.info("Executing tool: %s(%s)", tc.name, tc.arguments)
                result, effective_tool_call = self._execute_controlled_tool_call(
                    tc,
                    route_plan,
                    messages,
                    tool_results,
                    tool_args,
                    user_message,
                    self_correction_state,
                )

                # Track for proposal detection
                tool_results[effective_tool_call.name] = result
                tool_args[effective_tool_call.name] = effective_tool_call.arguments

                messages.append(LLMMessage(
                    role="tool",
                    content=result,
                    name=tc.name,
                ))

        # If we exhausted all rounds, ensure controller and answer policies have run, then return whatever we have
        self._enforce_controller_policy(messages, route_plan, user_message, tool_results, tool_args, self_correction_state)
        self._enforce_required_tool_policy(messages, route_plan, tool_results, tool_args, self_correction_state, user_message)
        final_response = await self._provider.generate(
            messages=messages,
            tools=None,  # Force text response
            temperature=0.3,
        )

        flow_state = self._build_controller_flow_state(
            user_message,
            route_plan,
            tool_results,
            tool_args,
            self_correction_state,
            knowledge_used=bool(knowledge_ctx),
        )
        final_response_content = final_response.content
        if flow_state.get("release", {}).get("decision") == "block":
            final_response_content = self._render_controller_flow_block(flow_state)
        grounding_meta = self._build_grounding_meta(
            routing_decision.query_class,
            tool_results,
            tool_args,
            route_plan.get("answer_policy"),
            knowledge_used=bool(knowledge_ctx),
            controller_flow=flow_state,
            self_correction_state=self_correction_state,
        )
        answer_release, answer_release_action = self._evaluate_answer_release(
            messages,
            user_message,
            route_plan,
            grounding_meta,
            tool_results,
            tool_args,
            self_correction_state,
        )
        grounding_meta["answer_release"] = answer_release
        if flow_state.get("release", {}).get("decision") != "block" and answer_release_action == "allow_limited":
            final_response_content = self._render_limited_answer(final_response_content, answer_release)

        self._history.append(
            LLMMessage(
                role="assistant",
                content=self._render_grounded_content(final_response_content, grounding_meta),
            )
        )

        final_content = self._render_grounded_content(final_response_content, grounding_meta)

        # Check for proposal even after exhausting rounds
        proposal = self._maybe_create_proposal(
            user_message, final_response.content, tool_results, tool_args
        )
        if proposal:
            resp = {
                "role": "assistant",
                "content": final_content,
                "type": "proposal",
                "proposal": proposal.model_dump(),
                "grounding_meta": grounding_meta,
            }
            if hasattr(proposal, "_intent_meta"):
                resp["type"] = "intent_plan"
                resp["intent_meta"] = proposal._intent_meta  # type: ignore[attr-defined]
            return resp

        return {
            "role": "assistant",
            "content": final_content,
            "type": "text",
            "grounding_meta": grounding_meta,
        }

    # ------------------------------------------------------------------
    # M19: Proposal Detection
    # ------------------------------------------------------------------

    def _maybe_create_proposal(
        self,
        user_message: str,
        ai_response: str,
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict],
    ) -> Optional[ConfigProposal]:
        """
        If the intent pipeline (plan_change) or legacy generate_config was
        called during this chat turn, package the results into a ConfigProposal.

        Priority: intent pipeline > legacy flow.
        """
        # ---- Intent-pipeline path (plan_change) ----
        if "plan_change" in tool_results:
            return self._proposal_from_intent_pipeline(
                user_message, ai_response, tool_results, tool_args
            )

        # ---- Legacy path (generate_config) ----
        if "generate_config" in tool_results:
            return self._proposal_from_legacy(
                user_message, ai_response, tool_results, tool_args
            )

        return None

    def _build_device_action_artifacts(
        self,
        device_plans: List[Dict[str, Any]],
        feature: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for device_plan in device_plans:
            hostname = str(device_plan.get("hostname") or "unknown")
            vendor = str(device_plan.get("vendor") or "unknown")
            execution_engine = str(device_plan.get("execution_engine") or "ssh_fallback")
            syntax_warnings = list(device_plan.get("syntax_warnings") or [])
            verification_checks = list(device_plan.get("verification_checks") or [])
            rollback_plan = device_plan.get("rollback_plan") if isinstance(device_plan.get("rollback_plan"), dict) else None
            rollback_config = str(device_plan.get("rollback") or device_plan.get("rollback_config") or "")
            generated_config = str(device_plan.get("config") or device_plan.get("generated_config") or "")
            diff_text = str(device_plan.get("diff") or device_plan.get("diff_text") or "")
            dependencies = ["intent_validated", "human_approval"]
            if syntax_warnings:
                dependencies.append("syntax_review")
            if execution_engine in {"ansible_native", "ansible_cli"}:
                dependencies.append("automation_inventory_ready")
            else:
                dependencies.append("ssh_credentials_available")

            artifacts.append(
                {
                    "hostname": hostname,
                    "device_id": self._lookup_device_id(hostname),
                    "vendor": vendor,
                    "feature": feature,
                    "generated_config": generated_config,
                    "rollback_config": rollback_config,
                    "diff_text": diff_text,
                    "execution_engine": execution_engine,
                    "rollback_plan": rollback_plan,
                    "dependencies": dependencies,
                    "syntax_warnings": syntax_warnings,
                    "verification_criteria": verification_checks,
                    "line_count": int(device_plan.get("line_count") or len([line for line in generated_config.splitlines() if line.strip()])),
                    "error": device_plan.get("error"),
                    "safety_validation": device_plan.get("safety_validation")
                    or validate_cli_artifact(
                        vendor=vendor,
                        feature=feature,
                        params=dict(params or {}),
                        generated_config=generated_config,
                        rollback_config=rollback_config,
                        execution_engine=execution_engine,
                        rollback_plan=rollback_plan,
                        require_render_match=True,
                    ),
                }
            )
        return artifacts

    def _build_proposal_action_artifact(
        self,
        *,
        intent_data: Dict[str, Any],
        policy_data: Dict[str, Any],
        plan: Dict[str, Any],
        device_plans: List[Dict[str, Any]],
        risk_level: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        feature = str(intent_data.get("feature") or plan.get("feature") or (device_plans[0].get("feature") if device_plans else "unknown") or "unknown")
        vendor = str(intent_data.get("vendor") or (device_plans[0].get("vendor") if device_plans else "unknown") or "unknown")
        effective_params = dict(params or intent_data.get("parameters") or {})
        device_artifacts = self._build_device_action_artifacts(device_plans, feature, effective_params)
        resolved_devices = [item.get("hostname") for item in device_artifacts if item.get("hostname")]
        engines = sorted({str(item.get("execution_engine") or "ssh_fallback") for item in device_artifacts})
        primary_engine = engines[0] if len(engines) == 1 else "mixed"
        verification_criteria: List[Dict[str, Any]] = []
        for item in device_artifacts:
            for check in item.get("verification_criteria") or []:
                if isinstance(check, dict):
                    enriched = dict(check)
                    enriched.setdefault("hostname", item.get("hostname"))
                    verification_criteria.append(enriched)

        rollback_available = sum(1 for item in device_artifacts if item.get("rollback_config"))
        rollback_plans = [item.get("rollback_plan") for item in device_artifacts if isinstance(item.get("rollback_plan"), dict)]
        snapshot_backed = sum(1 for item in rollback_plans if item.get("runtime_restore_available"))
        strategies = sorted({str(item.get("strategy") or "unknown") for item in rollback_plans})
        action_type = "multi_device_playbook" if len(device_artifacts) > 1 else "single_device_proposal"
        dependencies = ["parse_intent", "validate_intent", "plan_change", "human_approval"]
        if len(device_artifacts) > 1:
            dependencies.append("coordinated_execution_review")

        artifact: Dict[str, Any] = {
            "version": "phase3_step9_v1",
            "action_type": action_type,
            "intent_id": intent_data.get("id") or plan.get("intent_id"),
            "scope": {
                "device_scope": intent_data.get("scope") or {},
                "resolved_devices": resolved_devices,
                "device_count": len(device_artifacts),
                "all_devices": bool((intent_data.get("scope") or {}).get("all_devices")),
            },
            "vendor": vendor,
            "feature": feature,
            "risk_level": risk_level,
            "execution": {
                "primary_engine": primary_engine,
                "execution_engines": engines,
                "requires_coordinated_execution": len(device_artifacts) > 1,
                "dependencies": dependencies,
            },
            "approval": {
                "status": "pending",
                "requires_human_approval": True,
                "policy_approved": bool(policy_data.get("approved", True)),
                "policy_violations": list(policy_data.get("violations") or []),
                "policy_warnings": list(policy_data.get("warnings") or []),
            },
            "rollback_intent": {
                "strategy": (
                    "snapshot_aware_diff_restore"
                    if snapshot_backed == len(device_artifacts) and device_artifacts
                    else ("mixed_snapshot_and_reverse" if snapshot_backed else ("known_reverse_operations" if rollback_available else "manual_reconstruction_required"))
                ),
                "strategy_chain": strategies,
                "available_device_count": rollback_available,
                "snapshot_backed_device_count": snapshot_backed,
                "summary": (
                    f"Snapshot-backed rollback is available for {snapshot_backed} of {len(device_artifacts)} device(s); targeted reverse commands are available for {rollback_available} device(s)."
                ),
            },
            "verification_criteria": verification_criteria,
            "device_artifacts": device_artifacts,
        }
        if action_type == "multi_device_playbook":
            artifact["playbook_spec"] = {
                "name": f"{feature}_multi_device_change",
                "target_hosts": resolved_devices,
                "device_count": len(device_artifacts),
                "execution_engines": engines,
                "serial": 1,
                "generated_yaml": False,
                "mode": "planned",
            }
        artifact["validation"] = validate_action_artifact(
            artifact,
            vendor=vendor,
            feature=feature,
            params=effective_params,
            require_render_match=True,
        )
        artifact["approval"]["safety_validated"] = bool(artifact["validation"].get("passed"))
        artifact["approval"]["safety_errors"] = list(artifact["validation"].get("errors") or [])
        return artifact

    # ---------- intent-pipeline proposal builder ----------

    def _proposal_from_intent_pipeline(
        self,
        user_message: str,
        ai_response: str,
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict],
    ) -> Optional[ConfigProposal]:
        """Build a rich ConfigProposal from the 3-stage intent pipeline."""
        try:
            raw_plan = tool_results["plan_change"]
            plan = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan

            # Extract intent metadata (from parse_intent result if available)
            intent_data: Dict = {}
            if "parse_intent" in tool_results:
                raw_intent = tool_results["parse_intent"]
                intent_data = json.loads(raw_intent) if isinstance(raw_intent, str) else raw_intent

            # Extract policy result (from validate_intent result if available)
            policy_data: Dict = {}
            if "validate_intent" in tool_results:
                raw_policy = tool_results["validate_intent"]
                policy_data = json.loads(raw_policy) if isinstance(raw_policy, str) else raw_policy

            # Aggregate configs across all devices in the plan.
            # Support both the legacy internal shape and the current
            # network_tools.plan_change payload shape.
            device_plans = plan.get("device_plans") or plan.get("devices") or []
            if not device_plans:
                return None

            # Primary device = first device in plan
            first = device_plans[0]
            target_device = first.get("hostname", "unknown")
            vendor = intent_data.get("vendor") or first.get("vendor", "unknown")
            feature = intent_data.get("feature") or plan.get("feature") or first.get("feature", "unknown")

            # Merge generated configs / rollbacks / diffs for ALL devices
            config_blocks = []
            rollback_blocks = []
            diff_blocks = []
            for dp in device_plans:
                hn = dp.get("hostname", "?")
                config_text = dp.get("config") or dp.get("generated_config", "")
                rollback_text = dp.get("rollback") or dp.get("rollback_config", "")
                diff_text = dp.get("diff") or dp.get("diff_text", "")

                if config_text:
                    config_blocks.append(f"! ----- {hn} -----\n{config_text}")
                if rollback_text:
                    rollback_blocks.append(f"! ----- {hn} -----\n{rollback_text}")
                if diff_text:
                    diff_blocks.append(f"--- {hn} ---\n{diff_text}")

            generated_config = "\n\n".join(config_blocks)
            rollback_config = "\n\n".join(rollback_blocks)
            diff_text = "\n\n".join(diff_blocks)

            # Params come from intent
            params = intent_data.get("parameters", {})
            target_device_id = self._lookup_device_id(target_device)

            # Risk from intent, fallback to heuristic
            risk_level = intent_data.get("risk_level") or self._assess_risk(feature, vendor)

            action_artifact = self._build_proposal_action_artifact(
                intent_data=intent_data,
                policy_data=policy_data,
                plan=plan,
                device_plans=device_plans,
                risk_level=risk_level,
                params=params,
            )

            # Build extra metadata to attach to the proposal
            intent_meta = {
                "pipeline": "intent",
                "intent_id": intent_data.get("id"),
                "intent_type": intent_data.get("intent_type"),
                "scope": intent_data.get("scope"),
                "resolved_devices": intent_data.get("resolved_devices", []),
                "blast_radius": policy_data.get("blast_radius"),
                "policy_approved": policy_data.get("approved", True),
                "policy_violations": policy_data.get("violations", []),
                "policy_warnings": policy_data.get("warnings", []),
                "execution_engine": first.get("execution_engine"),
                "verification_checks": first.get("verification_checks", []),
                "device_count": len(device_plans),
                "action_type": action_artifact.get("action_type"),
            }

            proposal = self._proposals.create(
                user_prompt=user_message,
                target_device=target_device,
                target_device_id=target_device_id,
                vendor=vendor,
                feature=feature,
                params=params,
                generated_config=generated_config,
                rollback_config=rollback_config,
                diff_text=diff_text,
                risk_level=risk_level,
                ai_explanation=ai_response,
                action_artifact=action_artifact,
            )

            # Attach intent metadata as extra field (non-Pydantic, OK for dict dump)
            proposal_dict = proposal.model_dump()
            proposal_dict["intent_meta"] = intent_meta

            logger.info(
                "Created intent-pipeline proposal %s: %s/%s on %d device(s)",
                proposal.id, vendor, feature, len(device_plans),
            )
            # Return a thin wrapper so callers get both the proposal + meta
            proposal._intent_meta = intent_meta  # type: ignore[attr-defined]
            return proposal

        except Exception as e:
            logger.error("Failed to create intent-pipeline proposal: %s", e, exc_info=True)
            return None

    # ---------- legacy proposal builder ----------

    def _proposal_from_legacy(
        self,
        user_message: str,
        ai_response: str,
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict],
    ) -> Optional[ConfigProposal]:
        """
        Legacy flow: If generate_config was called during this chat turn,
        automatically package the results into a ConfigProposal.
        """

        try:
            config_result = tool_results["generate_config"]
            config_args = tool_args.get("generate_config", {})

            # Parse the generated config from the tool result
            generated_config = ""
            if isinstance(config_result, str):
                try:
                    parsed = json.loads(config_result)
                    generated_config = parsed.get("config", config_result)
                except (json.JSONDecodeError, AttributeError):
                    generated_config = config_result

            # Get rollback config if available
            rollback_config = ""
            if "generate_rollback" in tool_results:
                rollback_result = tool_results["generate_rollback"]
                if isinstance(rollback_result, str):
                    try:
                        parsed = json.loads(rollback_result)
                        rollback_config = parsed.get("rollback_config", rollback_result)
                    except (json.JSONDecodeError, AttributeError):
                        rollback_config = rollback_result

            # Get diff if available
            diff_text = ""
            if "diff_configs" in tool_results:
                diff_result = tool_results["diff_configs"]
                if isinstance(diff_result, str):
                    try:
                        parsed = json.loads(diff_result)
                        diff_text = parsed.get("unified", diff_result)
                    except (json.JSONDecodeError, AttributeError):
                        diff_text = diff_result

            # Extract device info
            vendor = config_args.get("vendor", "unknown")
            feature = config_args.get("feature", "unknown")
            params_str = config_args.get("params", "{}")
            if isinstance(params_str, str):
                try:
                    params = json.loads(params_str)
                except json.JSONDecodeError:
                    params = {}
            else:
                params = params_str if isinstance(params_str, dict) else {}

            # Try to determine target device from context
            target_device = self._extract_target_device(user_message, params)
            target_device_id = self._lookup_device_id(target_device)

            # Determine risk level based on feature
            risk_level = self._assess_risk(feature, vendor)

            action_artifact = {
                "version": "phase3_step11_v1",
                "action_type": "single_device_proposal",
                "scope": {
                    "device_scope": {"device_hostnames": [target_device], "all_devices": False},
                    "resolved_devices": [target_device],
                    "device_count": 1,
                    "all_devices": False,
                },
                "vendor": vendor,
                "feature": feature,
                "risk_level": risk_level,
                "execution": {
                    "primary_engine": "ssh_fallback",
                    "execution_engines": ["ssh_fallback"],
                    "requires_coordinated_execution": False,
                    "dependencies": ["human_approval", "ssh_credentials_available"],
                },
                "approval": {
                    "status": "pending",
                    "requires_human_approval": True,
                    "policy_approved": True,
                    "policy_violations": [],
                    "policy_warnings": [],
                },
                "rollback_intent": {
                    "strategy": "manual_reconstruction_required",
                    "available_device_count": 1 if rollback_config else 0,
                    "summary": "Rollback planning metadata has not been computed yet.",
                },
                "verification_criteria": [],
                "device_artifacts": [
                    {
                        "hostname": target_device,
                        "device_id": target_device_id,
                        "vendor": vendor,
                        "feature": feature,
                        "generated_config": generated_config,
                        "rollback_config": rollback_config,
                        "diff_text": diff_text,
                        "execution_engine": "ssh_fallback",
                        "rollback_plan": build_rollback_plan(
                            db_manager=self._db,
                            hostname=target_device,
                            vendor=vendor,
                            feature=feature,
                            params=dict(params),
                            generated_config=generated_config,
                            template_rollback_config=rollback_config,
                        ),
                        "dependencies": ["human_approval", "ssh_credentials_available"],
                        "syntax_warnings": [],
                        "verification_criteria": [],
                        "line_count": len([line for line in generated_config.splitlines() if line.strip()]),
                        "error": None,
                        "safety_validation": validate_cli_artifact(
                            vendor=vendor,
                            feature=feature,
                            params=dict(params),
                            generated_config=generated_config,
                            rollback_config=rollback_config,
                            execution_engine="ssh_fallback",
                            rollback_plan=build_rollback_plan(
                                db_manager=self._db,
                                hostname=target_device,
                                vendor=vendor,
                                feature=feature,
                                params=dict(params),
                                generated_config=generated_config,
                                template_rollback_config=rollback_config,
                            ),
                            require_render_match=True,
                        ),
                    }
                ],
            }
            rollback_plan = action_artifact["device_artifacts"][0]["rollback_plan"]
            action_artifact["rollback_intent"] = {
                "strategy": rollback_plan.get("strategy"),
                "strategy_chain": list(rollback_plan.get("strategy_chain") or []),
                "available_device_count": 1 if rollback_config else 0,
                "snapshot_backed_device_count": 1 if rollback_plan.get("runtime_restore_available") else 0,
                "summary": rollback_plan.get("summary", ""),
            }
            action_artifact["validation"] = validate_action_artifact(
                action_artifact,
                vendor=vendor,
                feature=feature,
                params=dict(params),
                require_render_match=True,
            )
            action_artifact["approval"]["safety_validated"] = bool(action_artifact["validation"].get("passed"))
            action_artifact["approval"]["safety_errors"] = list(action_artifact["validation"].get("errors") or [])

            # Create the proposal
            proposal = self._proposals.create(
                user_prompt=user_message,
                target_device=target_device,
                target_device_id=target_device_id,
                vendor=vendor,
                feature=feature,
                params=params,
                generated_config=generated_config,
                rollback_config=rollback_config,
                diff_text=diff_text,
                risk_level=risk_level,
                ai_explanation=ai_response,
                action_artifact=action_artifact,
            )

            logger.info(
                "Created proposal %s: %s/%s on %s",
                proposal.id, vendor, feature, target_device,
            )
            return proposal

        except Exception as e:
            logger.error("Failed to create proposal: %s", e)
            return None

    def _extract_target_device(self, user_message: str, params: Dict) -> str:
        """Try to identify the target device from the user message or params."""
        # Check params first (some templates include hostname)
        for key in ("hostname", "device", "host", "device_name"):
            if key in params:
                return str(params[key])

        # Simple heuristic: look for common device name patterns in the message
        words = user_message.split()
        for word in words:
            clean = word.strip(".,!?;:'\"")
            # Match patterns like R1, SW1, Router-1, etc.
            if clean and (
                clean[0].upper() in "RS" and any(c.isdigit() for c in clean)
            ):
                return clean.upper()

        return "unknown"

    def _lookup_device_id(self, hostname: str) -> int:
        """Look up the device ID from the database."""
        if not self._db or hostname == "unknown":
            return 0
        try:
            topo = self._db.get_topology()
            if topo and "nodes" in topo:
                # Try to find device in topology
                for name, data in topo["nodes"].items():
                    if name.lower() == hostname.lower():
                        # Get device_id from DB if available
                        return data.get("device_id", 0)
            return 0
        except Exception:
            return 0

    def _assess_risk(self, feature: str, vendor: str) -> str:
        """Simple risk assessment based on the feature type."""
        high_risk = {"acl", "firewall", "nat", "bgp", "routing"}
        medium_risk = {"ospf", "eigrp", "vlan", "stp", "interface"}
        low_risk = {"ntp", "logging", "banner", "snmp"}

        feature_lower = feature.lower()
        if feature_lower in high_risk:
            return "high"
        elif feature_lower in medium_risk:
            return "medium"
        elif feature_lower in low_risk:
            return "low"
        return "medium"

    # ------------------------------------------------------------------
    # Proposal access
    # ------------------------------------------------------------------

    @property
    def proposal_manager(self) -> ProposalManager:
        """Expose the proposal manager for use by the API layer."""
        return self._proposals

    # ------------------------------------------------------------------
    # Existing public methods
    # ------------------------------------------------------------------

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self._history.clear()
        logger.info("Conversation history cleared")

    def set_provider(self, provider: LLMProvider, reset_history: bool = True) -> None:
        """Swap the active LLM provider used by this agent."""
        self._provider = provider
        network_tools.set_intent_provider(provider)
        self._network_context_cache.clear()
        self._tool_result_cache.clear()
        if reset_history:
            self.clear_history()
        logger.info(
            "NetworkAgent provider switched (provider=%s, model=%s)",
            self.provider_name,
            self.model_name,
        )

    def _known_hostnames(self) -> List[str]:
        hostnames: List[str] = []
        try:
            if self._db is not None:
                for device in self._db.get_all_devices():
                    if getattr(device, "hostname", None):
                        hostnames.append(str(device.hostname))
                topo = self._db.get_topology()
                if topo and topo.get("nodes"):
                    for hostname in topo["nodes"].keys():
                        hostnames.append(str(hostname))
        except Exception:
            return []

        deduped: List[str] = []
        seen = set()
        for hostname in hostnames:
            key = hostname.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hostname)
        return deduped

    def _extract_candidate_hostnames(self, user_message: str) -> List[str]:
        query_lower = user_message.lower()
        matches: List[tuple[int, str]] = []
        for hostname in self._known_hostnames():
            start = query_lower.find(hostname.lower())
            if start >= 0:
                matches.append((start, hostname))

        if matches:
            return [hostname for _, hostname in sorted(matches, key=lambda item: item[0])]

        heuristic_matches = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", user_message)
        if heuristic_matches:
            return heuristic_matches[:3]

        fuzzy_matches = network_tools.suggest_device_hostnames(user_message, limit=3)
        if fuzzy_matches:
            return fuzzy_matches

        return []

    def _advanced_differentiator_enabled(self, feature_name: str) -> bool:
        status = build_advanced_differentiator_status(
            self.controller_implementation_status,
            feature_name=feature_name,
        )
        return status["availability"] == "enabled"

    def _build_route_plan(
        self,
        user_message: str,
        query_class: str,
        hostnames: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        hostnames = hostnames or self._extract_candidate_hostnames(user_message)
        digital_twin_enabled = self._advanced_differentiator_enabled("digital_twin")
        change_intelligence_enabled = self._advanced_differentiator_enabled("change_intelligence")
        generic_read_tools = [
            "get_context_health",
            "get_last_sync_status",
            "trigger_context_refresh",
            "get_refresh_job_status",
            "get_audit_log",
            "list_templates",
            "get_template_schema",
        ]

        instruction = ""
        prefetch_tools: List[ToolCall] = []
        allowed_tools: Optional[List[str]] = None
        answer_policy = build_answer_policy(user_message, query_class, hostnames)

        if query_class == "inventory_only":
            allowed_tools = generic_read_tools + [
                "list_inventory_devices",
                "get_inventory_device",
                "search_inventory_devices",
                "get_planned_topology",
                "compare_planned_vs_live",
            ]
            instruction = (
                "For this inventory-focused query, treat source-of-truth inventory as authoritative. "
                "Do not answer from live topology unless the prefetched inventory data is missing, and say so explicitly if fallback is needed."
            )
            if hostnames:
                prefetch_tools.append(ToolCall(name="get_inventory_device", arguments={"hostname": hostnames[0]}))
            else:
                prefetch_tools.append(ToolCall(name="search_inventory_devices", arguments={"query": user_message, "limit": 5}))

        elif query_class == "planned_vs_live":
            allowed_tools = generic_read_tools + [
                "compare_planned_vs_live",
                "get_inventory_device",
                "list_inventory_devices",
                "get_planned_topology",
                "get_topology_summary",
                "get_device_details",
                "list_all_devices",
            ]
            instruction = (
                "For this planned-versus-live query, compare authoritative planned state against live discovery. "
                "Do not collapse disagreements; surface mismatches explicitly."
            )
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            prefetch_tools.append(
                ToolCall(
                    name="compare_planned_vs_live",
                    arguments={"hostname": hostnames[0]} if hostnames else {},
                )
            )
            if digital_twin_enabled and hostnames:
                allowed_tools.append("get_digital_twin")
                prefetch_tools.append(ToolCall(name="get_digital_twin", arguments={"hostname": hostnames[0]}))

        elif query_class == "live_only":
            allowed_tools = generic_read_tools + [
                "get_topology_summary",
                "get_device_details",
                "get_device_telemetry_snapshot",
                "list_all_devices",
                "find_path",
                "get_recent_incidents",
                "get_recent_drift_events",
            ]
            instruction = (
                "For this live-state query, prioritize current topology and operational state. "
                "Do not substitute planned inventory for live facts."
            )
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            if hostnames:
                prefetch_tools.extend(
                    [
                        ToolCall(name="get_device_details", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_device_telemetry_snapshot", arguments={"hostname": hostnames[0], "limit": 10}),
                    ]
                )
            else:
                prefetch_tools.append(ToolCall(name="get_topology_summary", arguments={}))

        elif query_class == "troubleshooting":
            allowed_tools = generic_read_tools + [
                "get_inventory_device",
                "get_device_details",
                "get_device_telemetry_snapshot",
                "get_device_running_config",
                "get_recent_incidents",
                "get_recent_drift_events",
                "get_security_events",
                "run_security_audit",
                "analyze_security_alert",
                "find_path",
                "list_all_devices",
                "get_topology_summary",
            ]
            instruction = (
                "For this troubleshooting query, combine live topology, recent incidents, drift, and config evidence. "
                "If the sources disagree, state the conflict instead of guessing."
            )
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            prefetch_tools.append(ToolCall(name="get_recent_incidents", arguments={"limit": 5}))
            if hostnames:
                if digital_twin_enabled:
                    allowed_tools.append("get_digital_twin")
                    prefetch_tools.append(ToolCall(name="get_digital_twin", arguments={"hostname": hostnames[0]}))
                prefetch_tools.extend(
                    [
                        ToolCall(name="get_inventory_device", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_device_details", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_device_telemetry_snapshot", arguments={"hostname": hostnames[0], "limit": 10}),
                        ToolCall(name="get_device_running_config", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_recent_drift_events", arguments={"hostname": hostnames[0], "limit": 5}),
                    ]
                )

        elif query_class == "change_planning":
            allowed_tools = [
                "parse_intent",
                "validate_intent",
                "plan_change",
                "list_templates",
                "get_template_schema",
                "get_inventory_device",
                "get_device_running_config",
                "get_recent_drift_events",
                "get_context_health",
                "get_last_sync_status",
                "trigger_context_refresh",
                "get_refresh_job_status",
            ]
            instruction = (
                "For this change-planning query, use intent parsing and policy validation first, then run change intelligence before planning so you surface intent memory, outcome risk, and what-if blockers instead of guessing."
            )
            if not change_intelligence_enabled:
                instruction += " Advanced change-intelligence remains reserved until Milestone 3 stability is proven, so rely on validated intent, authoritative inventory, drift, and current config evidence instead."
            if not digital_twin_enabled:
                instruction += " Formal digital-twin reasoning remains reserved until Milestone 3 stability is proven, so do not require it for this route."
            if change_intelligence_enabled:
                allowed_tools.append("analyze_change_intelligence")
            if digital_twin_enabled:
                allowed_tools.append("get_digital_twin")
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            if hostnames:
                if digital_twin_enabled:
                    prefetch_tools.append(ToolCall(name="get_digital_twin", arguments={"hostname": hostnames[0]}))
                prefetch_tools.extend(
                    [
                        ToolCall(name="get_inventory_device", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_device_running_config", arguments={"hostname": hostnames[0]}),
                    ]
                )

        elif query_class == "architecture_planning":
            allowed_tools = [
                "get_knowledge_base_status",
                "list_templates",
                "get_template_schema",
            ]
            instruction = (
                "For this architecture-planning query, treat the request as platform or controller design work. "
                "Do not fetch live network evidence unless the user explicitly asks for current operational state. "
                "Focus on implementation tradeoffs, latency bottlenecks, routing behavior, and milestone sequencing."
            )

        elif query_class == "compliance":
            allowed_tools = generic_read_tools + [
                "get_inventory_device",
                "get_device_running_config",
                "get_recent_drift_events",
                "get_recent_incidents",
                "run_security_audit",
                "get_knowledge_base_status",
            ]
            instruction = (
                "For this compliance query, prefer config evidence, drift state, audit findings, and knowledge documents."
            )
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            prefetch_tools.append(ToolCall(name="get_knowledge_base_status", arguments={"limit": 5}))
            if hostnames:
                prefetch_tools.extend(
                    [
                        ToolCall(name="get_inventory_device", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_device_running_config", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_recent_drift_events", arguments={"hostname": hostnames[0], "limit": 5}),
                    ]
                )

        elif query_class == "knowledge_heavy":
            allowed_tools = generic_read_tools + [
                "get_knowledge_base_status",
                "list_templates",
                "get_template_schema",
            ]
            instruction = (
                "For this knowledge-heavy query, treat curated knowledge as primary. If live state is needed and missing, say so explicitly."
            )
            prefetch_tools.append(ToolCall(name="get_knowledge_base_status", arguments={"limit": 5}))

        else:
            allowed_tools = generic_read_tools + [
                "get_inventory_device",
                "search_inventory_devices",
                "compare_planned_vs_live",
                "get_topology_summary",
                "get_device_details",
                "list_all_devices",
                "get_recent_incidents",
                "get_recent_drift_events",
            ]
            instruction = (
                "For this general network query, ground the answer with authoritative device or topology evidence before answering. "
                "If you rely only on prompt context, say so explicitly."
            )
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            if hostnames:
                prefetch_tools.append(
                    ToolCall(name="compare_planned_vs_live", arguments={"hostname": hostnames[0]})
                )
            else:
                prefetch_tools.append(ToolCall(name="get_topology_summary", arguments={}))

        controller_policy = build_controller_policy(query_class, allowed_tools)

        return {
            "prefetch_tools": prefetch_tools,
            "allowed_tools": allowed_tools,
            "instruction": instruction,
            "answer_policy": answer_policy,
            "controller_policy": controller_policy,
        }

    def _json_payload(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw}
        return {}

    def _controller_error_result(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> str:
        payload = {
            "error": message,
            "controller_policy_error": code,
        }
        if details:
            payload["details"] = details
        return json.dumps(payload, ensure_ascii=False)

    def _record_tool_message(
        self,
        messages: List[LLMMessage],
        tool_call: ToolCall,
        result: str,
        note: str,
    ) -> None:
        messages.append(
            LLMMessage(
                role="assistant",
                content=note,
                tool_calls=[tool_call],
            )
        )
        messages.append(
            LLMMessage(
                role="tool",
                content=result,
                name=tool_call.name,
            )
        )

    def _validate_tool_call_contract(
        self,
        tool_call: ToolCall,
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
    ) -> Optional[str]:
        allowed_tools = set(route_plan.get("allowed_tools") or [])
        controller_policy = route_plan.get("controller_policy") or {}

        if allowed_tools and tool_call.name not in allowed_tools:
            return self._controller_error_result(
                "tool_not_allowed",
                f"Tool '{tool_call.name}' is not allowed for this query class.",
                {"allowed_tools": sorted(allowed_tools)},
            )

        blocked_direct_tools = set(controller_policy.get("blocked_direct_tools") or [])
        if tool_call.name in blocked_direct_tools:
            return self._controller_error_result(
                "direct_generation_blocked",
                f"Tool '{tool_call.name}' is blocked. Use parse_intent -> validate_intent -> plan_change instead.",
            )

        if controller_policy.get("requires_change_workflow"):
            if tool_call.name == "validate_intent" and "parse_intent" not in tool_results:
                return self._controller_error_result(
                    "missing_intent_parse",
                    "validate_intent cannot run before parse_intent completes.",
                )
            if tool_call.name == "plan_change":
                if "parse_intent" not in tool_results:
                    return self._controller_error_result(
                        "missing_intent_parse",
                        "plan_change cannot run before parse_intent completes.",
                    )
                if "validate_intent" not in tool_results:
                    return self._controller_error_result(
                        "missing_policy_validation",
                        "plan_change cannot run before validate_intent approves the request.",
                    )
                parse_payload = self._json_payload(tool_results.get("parse_intent"))
                if parse_payload.get("requires_disambiguation"):
                    return self._controller_error_result(
                        "intent_requires_disambiguation",
                        "plan_change is blocked until the request is disambiguated.",
                        {"disambiguation_message": parse_payload.get("disambiguation_message", "")},
                    )
                validation_payload = self._json_payload(tool_results.get("validate_intent"))
                if validation_payload.get("approved") is not True:
                    return self._controller_error_result(
                        "policy_not_approved",
                        "plan_change is blocked because validate_intent did not approve the request.",
                        {"violations": validation_payload.get("violations", [])},
                    )

        return None

    def _execute_tool_cached(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool, returning a cached result when the TTL has not expired."""
        ttl = _TOOL_CACHE_TTL.get(name)
        if ttl is not None:
            cache_key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
            cached = self._tool_result_cache.get(cache_key)
            if cached is not None:
                result, ts = cached
                if (time.monotonic() - ts) < ttl:
                    logger.debug("Tool cache HIT for %s (ttl=%ds)", name, ttl)
                    return result

            result = self._tools.execute(name, arguments)
            self._tool_result_cache[cache_key] = (result, time.monotonic())
            return result

        return self._tools.execute(name, arguments)

    def _execute_controlled_tool_call(
        self,
        tool_call: ToolCall,
        route_plan: Dict[str, Any],
        messages: List[LLMMessage],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        user_message: str,
        self_correction_state: Dict[str, Any],
    ) -> tuple[str, ToolCall]:
        violation = self._validate_tool_call_contract(tool_call, route_plan, tool_results)
        if violation is not None:
            self._record_tool_message(
                messages,
                tool_call,
                violation,
                f"Controller policy blocked tool call: {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})",
            )
            return violation, tool_call
        result = self._execute_tool_cached(tool_call.name, tool_call.arguments)
        tool_results[tool_call.name] = result
        tool_args[tool_call.name] = tool_call.arguments
        messages.append(
            LLMMessage(
                role="tool",
                content=result,
                name=tool_call.name,
            )
        )
        return self._apply_self_correction(
            tool_call,
            result,
            route_plan,
            messages,
            tool_results,
            tool_args,
            user_message,
            self_correction_state,
        )

    def _execute_controller_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        route_plan: Dict[str, Any],
        messages: List[LLMMessage],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        note: str,
        self_correction_state: Dict[str, Any],
        user_message: str,
    ) -> str:
        tool_call = ToolCall(name=name, arguments=arguments)
        result = self._execute_tool_cached(name, arguments)
        tool_results[tool_call.name] = result
        tool_args[tool_call.name] = tool_call.arguments
        self._record_tool_message(messages, tool_call, result, note)
        result, _effective_tool_call = self._apply_self_correction(
            tool_call,
            result,
            route_plan=route_plan,
            messages=messages,
            tool_results=tool_results,
            tool_args=tool_args,
            user_message=user_message,
            self_correction_state=self_correction_state,
        )
        return result

    def _apply_self_correction(
        self,
        tool_call: ToolCall,
        result: str,
        route_plan: Dict[str, Any],
        messages: List[LLMMessage],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        user_message: str,
        self_correction_state: Dict[str, Any],
    ) -> tuple[str, ToolCall]:
        current_tool = tool_call
        current_result = result

        while True:
            plan = plan_self_correction(
                user_message=user_message,
                tool_name=current_tool.name,
                arguments=current_tool.arguments,
                raw_result=current_result,
                route_plan=route_plan,
                tool_results=tool_results,
                state=self_correction_state,
            )
            if plan is None:
                return current_result, current_tool

            entry = plan["entry"]
            if plan["decision"] == "escalate":
                self_correction_state.setdefault("attempts", []).append(entry)
                self_correction_state.setdefault("escalations", []).append(
                    {
                        "tool_name": current_tool.name,
                        "failure_kind": entry["failure_kind"],
                        "reason": plan["escalation_reason"],
                    }
                )
                return current_result, current_tool

            revised_tool_calls = plan.get("revised_tool_calls", [])
            tool_call_summary = ", ".join(
                f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
                for tc in revised_tool_calls
            )
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=(
                        f"Controller self-correction retry for {current_tool.name}: {tool_call_summary}"
                    ),
                    tool_calls=revised_tool_calls,
                )
            )

            retry_success = False
            final_retry_result = current_result
            final_retry_tool = current_tool
            for revised_call in revised_tool_calls:
                retry_result = self._tools.execute(revised_call.name, revised_call.arguments)
                tool_results[revised_call.name] = retry_result
                tool_args[revised_call.name] = revised_call.arguments
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=retry_result,
                        name=revised_call.name,
                    )
                )
                final_retry_result = retry_result
                final_retry_tool = revised_call
                retry_payload = self._json_payload(retry_result)
                if not retry_payload.get("error") and not retry_payload.get("controller_policy_error"):
                    retry_success = True
                    break

            entry["outcome"] = "retry_succeeded" if retry_success else "retry_failed"
            self_correction_state.setdefault("attempts", []).append(entry)

            if retry_success:
                return final_retry_result, final_retry_tool

            current_tool = final_retry_tool
            current_result = final_retry_result

    def _execute_recovery_tool_calls(
        self,
        messages: List[LLMMessage],
        tool_calls: List[ToolCall],
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        note: str,
        self_correction_state: Dict[str, Any],
        user_message: str,
    ) -> None:
        if not tool_calls:
            return

        tool_call_summary = ", ".join(
            f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
            for tc in tool_calls
        )
        messages.append(
            LLMMessage(
                role="assistant",
                content=f"{note}: {tool_call_summary}",
                tool_calls=tool_calls,
            )
        )
        for tc in tool_calls:
            self._execute_controlled_tool_call(
                tc,
                route_plan,
                messages,
                tool_results,
                tool_args,
                user_message,
                self_correction_state,
            )

    def _enforce_controller_policy(
        self,
        messages: List[LLMMessage],
        route_plan: Dict[str, Any],
        user_message: str,
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        self_correction_state: Dict[str, Any],
    ) -> bool:
        controller_policy = route_plan.get("controller_policy") or {}
        if not controller_policy.get("requires_change_workflow"):
            return False

        executed = False

        if "parse_intent" not in tool_results:
            self._execute_controller_tool(
                "parse_intent",
                {"user_message": user_message},
                route_plan,
                messages,
                tool_results,
                tool_args,
                "Controller policy enforcing change workflow step: parse_intent",
                self_correction_state,
                user_message,
            )
            executed = True

        parse_payload = self._json_payload(tool_results.get("parse_intent"))
        if parse_payload.get("error"):
            return executed
        if parse_payload.get("requires_disambiguation"):
            return executed

        intent_json = tool_results.get("parse_intent")
        if isinstance(intent_json, dict):
            intent_json = json.dumps(intent_json, ensure_ascii=False)
        if not isinstance(intent_json, str):
            intent_json = json.dumps(parse_payload, ensure_ascii=False)

        if "validate_intent" not in tool_results:
            self._execute_controller_tool(
                "validate_intent",
                {"intent_json": intent_json, "user_role": controller_policy.get("default_user_role", "operator")},
                route_plan,
                messages,
                tool_results,
                tool_args,
                "Controller policy enforcing change workflow step: validate_intent",
                self_correction_state,
                user_message,
            )
            executed = True

        validation_payload = self._json_payload(tool_results.get("validate_intent"))
        if validation_payload.get("error"):
            return executed
        if validation_payload.get("approved") is not True:
            return executed

        plan_payload = self._json_payload(tool_results.get("plan_change")) if "plan_change" in tool_results else {}
        if "plan_change" not in tool_results or plan_payload.get("error") or plan_payload.get("controller_policy_error"):
            self._execute_controller_tool(
                "plan_change",
                {"intent_json": intent_json},
                route_plan,
                messages,
                tool_results,
                tool_args,
                "Controller policy enforcing change workflow step: plan_change",
                self_correction_state,
                user_message,
            )
            executed = True

        return executed

    async def _apply_prefetched_route_tools(
        self,
        messages: List[LLMMessage],
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        self_correction_state: Dict[str, Any],
        user_message: str,
    ) -> None:
        prefetch_tools: List[ToolCall] = route_plan.get("prefetch_tools", [])
        if not prefetch_tools:
            return

        tool_call_summary = ", ".join(
            f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
            for tc in prefetch_tools
        )
        messages.append(
            LLMMessage(
                role="assistant",
                content=f"Deterministically prefetched authoritative tools: {tool_call_summary}",
                tool_calls=prefetch_tools,
            )
        )

        pending_tools: List[ToolCall] = []
        for tc in prefetch_tools:
            if tc.name in tool_results and tool_args.get(tc.name) == tc.arguments:
                continue
            pending_tools.append(tc)

        executed_results: List[Any] = []
        if pending_tools:
            executed_results = await asyncio.gather(
                *(run_in_threadpool(self._execute_tool_cached, tc.name, tc.arguments) for tc in pending_tools)
            )

        results: List[Any] = []
        executed_iter = iter(executed_results)
        for tc in prefetch_tools:
            if tc.name in tool_results and tool_args.get(tc.name) == tc.arguments:
                results.append(tool_results[tc.name])
            else:
                results.append(next(executed_iter))

        for tc, result in zip(prefetch_tools, results):
            tool_results[tc.name] = result
            tool_args[tc.name] = tc.arguments
            messages.append(
                LLMMessage(
                    role="tool",
                    content=result,
                    name=tc.name,
                )
            )
            self._apply_self_correction(
                tc,
                result,
                route_plan,
                messages,
                tool_results,
                tool_args,
                user_message,
                self_correction_state,
            )

    def _enforce_required_tool_policy(
        self,
        messages: List[LLMMessage],
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        self_correction_state: Dict[str, Any],
        user_message: str,
    ) -> bool:
        policy = route_plan.get("answer_policy") or {}
        status = policy_status(policy, tool_args)
        if status["satisfied"]:
            return False

        required_calls: List[ToolCall] = list(policy.get("required_tool_calls") or [])
        missing_calls = [
            call
            for call in required_calls
            if call.name in status["missing_required_tools"]
        ]
        if not missing_calls:
            return False

        tool_call_summary = ", ".join(
            f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
            for tc in missing_calls
        )
        messages.append(
            LLMMessage(
                role="assistant",
                content=(
                    "Enforcing authoritative answer policy before final answer: "
                    f"{tool_call_summary}"
                ),
                tool_calls=missing_calls,
            )
        )

        for tc in missing_calls:
            result = self._tools.execute(tc.name, tc.arguments)
            tool_results[tc.name] = result
            tool_args[tc.name] = tc.arguments
            messages.append(
                LLMMessage(
                    role="tool",
                    content=result,
                    name=tc.name,
                )
            )
            self._apply_self_correction(
                tc,
                result,
                route_plan,
                messages,
                tool_results,
                tool_args,
                user_message,
                self_correction_state,
            )

        return True

    def _build_controller_flow_state(
        self,
        user_message: str,
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        self_correction_state: Dict[str, Any],
        knowledge_used: bool = False,
    ) -> Dict[str, Any]:
        return build_controller_flow_state(
            user_message=user_message,
            query_class=route_plan.get("query_class", "general"),
            declared_required_domains=route_plan.get("required_domains") or [],
            hostnames=route_plan.get("hostnames") or [],
            tool_results=tool_results,
            tool_args=tool_args,
            allowed_tools=route_plan.get("allowed_tools") or [],
            answer_policy=route_plan.get("answer_policy"),
            controller_policy=route_plan.get("controller_policy"),
            self_correction_state=self_correction_state,
            knowledge_used=knowledge_used,
        )

    def _evaluate_controller_flow(
        self,
        messages: List[LLMMessage],
        user_message: str,
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        self_correction_state: Dict[str, Any],
        knowledge_used: bool = False,
    ) -> tuple[Dict[str, Any], str]:
        flow_state = self._build_controller_flow_state(
            user_message,
            route_plan,
            tool_results,
            tool_args,
            self_correction_state,
            knowledge_used=knowledge_used,
        )
        release = flow_state.get("release", {})
        decision = release.get("decision", "allow")
        if decision == "reroute":
            recovery_tool_calls = [
                ToolCall(name=item["name"], arguments=item.get("arguments", {}))
                for item in release.get("recovery_tool_calls", [])
                if item.get("name")
            ]
            if recovery_tool_calls:
                self._execute_recovery_tool_calls(
                    messages,
                    recovery_tool_calls,
                    route_plan,
                    tool_results,
                    tool_args,
                    "Controller flow enforcing missing authoritative evidence",
                    self_correction_state,
                    user_message,
                )
                return flow_state, "reroute"
            return flow_state, "block"
        return flow_state, decision

    def _render_controller_flow_block(self, flow_state: Dict[str, Any]) -> str:
        release = flow_state.get("release", {})
        missing_domains = ", ".join(release.get("missing_domains") or []) or "authoritative evidence"
        missing_tools = ", ".join(release.get("missing_required_tools") or [])
        missing_steps = " -> ".join(release.get("missing_change_steps") or [])
        details = [f"Missing evidence domains: {missing_domains}."]
        if missing_tools:
            details.append(f"Missing required tool reads: {missing_tools}.")
        if missing_steps:
            details.append(f"Incomplete change workflow: {missing_steps}.")
        details.append("The controller blocked release rather than guessing from incomplete evidence.")
        return "I can't provide a grounded answer yet. " + " ".join(details)

    def _evaluate_answer_release(
        self,
        messages: List[LLMMessage],
        user_message: str,
        route_plan: Dict[str, Any],
        grounding_meta: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
        self_correction_state: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        release_state = build_answer_release_state(
            query_class=route_plan.get("query_class", "general"),
            grounding=grounding_meta,
            tool_args=tool_args,
            allowed_tools=route_plan.get("allowed_tools") or [],
        )
        if release_state.get("decision") == "reroute":
            refresh_tool_calls = [
                ToolCall(name=item["name"], arguments=item.get("arguments", {}))
                for item in release_state.get("refresh_tool_calls", [])
                if item.get("name")
            ]
            if refresh_tool_calls:
                self._execute_recovery_tool_calls(
                    messages,
                    refresh_tool_calls,
                    route_plan,
                    tool_results,
                    tool_args,
                    "Answer release gate requesting fresher authoritative evidence",
                    self_correction_state,
                    user_message,
                )
                return release_state, "reroute"
            release_state["decision"] = "allow_limited"
            release_state["limited"] = True
        return release_state, str(release_state.get("decision") or "allow")

    def _render_limited_answer(self, content: str, answer_release: Dict[str, Any]) -> str:
        note = "Note: " + str(
            answer_release.get("reason") or "Grounded evidence is degraded, so this answer should not be treated as fully current."
        )
        if content.startswith("Limited answer:") or note in content:
            return content
        if not content:
            return note
        return f"{content}\n\n{note}"

    _CONTEXT_CACHE_MAXSIZE = 16

    @staticmethod
    def _network_context_cache_key(
        routing_decision=None,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        """Produce a compact, hashable cache key based on routing class and target hosts."""
        qc = getattr(routing_decision, "query_class", None) or ""
        hosts = ",".join(sorted(device_hostnames or []))
        use_live = getattr(routing_decision, "use_live_context", True)
        return f"{qc}|live={use_live}|{hosts}"

    def _build_network_context(
        self,
        user_message: Optional[str] = None,
        routing_decision=None,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        cache_key = self._network_context_cache_key(
            routing_decision=routing_decision,
            device_hostnames=device_hostnames,
        )
        cached = self._network_context_cache.get(cache_key)
        if cached is not None:
            return cached

        network_context = self._context.build_full_context(
            device_hostnames=device_hostnames,
            query=user_message,
            routing_decision=routing_decision,
        )
        self._network_context_cache[cache_key] = network_context
        if len(self._network_context_cache) > self._CONTEXT_CACHE_MAXSIZE:
            oldest = next(iter(self._network_context_cache))
            del self._network_context_cache[oldest]
        return network_context

    def _render_system_prompt(
        self,
        user_message: Optional[str] = None,
        routing_decision=None,
        device_hostnames: Optional[List[str]] = None,
        controller_policy: Optional[Dict[str, Any]] = None,
        route_instruction: str = "",
    ) -> str:
        network_context = ""
        if self._should_include_network_context(routing_decision):
            network_context = self._build_network_context(
                user_message=user_message,
                routing_decision=routing_decision,
                device_hostnames=device_hostnames,
            )
        return build_system_prompt(
            controller_scope=render_controller_scope_prompt(self.available_tools),
            milestone2_baseline=render_milestone2_baseline_prompt(self.available_tools),
            controller_policy=render_controller_policy_prompt(
                controller_policy or build_controller_policy("general", self.available_tools),
                route_instruction,
            ),
            network_context=network_context,
        )

    def _build_grounding_meta(
        self,
        query_class: str,
        tool_results: Dict[str, Any],
        tool_args: Optional[Dict[str, Dict[str, Any]]] = None,
        answer_policy: Optional[Dict[str, Any]] = None,
        knowledge_used: bool = False,
        controller_flow: Optional[Dict[str, Any]] = None,
        self_correction_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        grounding_meta = build_answer_grounding(
            query_class=query_class,
            tool_results=tool_results,
            tool_args=tool_args,
            knowledge_used=knowledge_used,
        )
        grounding_meta["authority_map"] = {
            "version": AUTHORITY_MAP_VERSION,
            "semantics_version": SEMANTICS_VERSION,
            "query_class": query_class,
        }
        grounding_meta["answer_policy"] = policy_status(answer_policy or {}, tool_args or {})
        if controller_flow is not None:
            grounding_meta["controller_flow"] = controller_flow
        if self_correction_state is not None:
            grounding_meta["self_correction"] = self_correction_state
        return grounding_meta

    def _render_grounded_content(self, content: str, grounding_meta: Dict[str, Any]) -> str:
        """Append a brief, human-readable freshness note only when data is stale or missing.

        The full grounding metadata is always available in the ``grounding_meta``
        field of the JSON response; the frontend renders it as a structured UI
        element.  We no longer append the raw debug-style grounding string.
        """
        freshness = grounding_meta.get("freshness_class", "unknown")
        source_age = grounding_meta.get("source_age") or []

        # Only add a note for stale / missing evidence
        note = ""
        if freshness == "stale" and source_age:
            # Find human-readable age
            max_age = max(
                (entry.get("age_seconds") or 0 for entry in source_age),
                default=0,
            )
            if max_age > 0:
                if max_age < 3600:
                    age_text = f"{max_age // 60} minutes"
                elif max_age < 86400:
                    age_text = f"{max_age // 3600} hours"
                else:
                    age_text = f"{max_age // 86400} days"
                note = f"ℹ️ *Based on inventory data last refreshed {age_text} ago.*"
        elif freshness == "missing":
            note = "⚠️ *Some required evidence was unavailable. Results may be incomplete.*"

        conflict_summary = format_conflict_summary(grounding_meta)
        if conflict_summary:
            note = (note + "\n\n" + conflict_summary) if note else conflict_summary

        if not content:
            return note or "No data available."
        if not note:
            return content
        return f"{content}\n\n{note}"

    def refresh_context(self) -> None:
        """
        Invalidate cached environment context so the next turn rebuilds it.

        This preserves compatibility with existing refresh hooks while
        keeping per-turn prompt assembly authoritative.
        """
        self._network_context_cache.clear()
        self._tool_result_cache.clear()
        logger.info("Cached network context and tool results invalidated")

    def get_history(self) -> List[Dict[str, str]]:
        """Return the conversation history as a list of dicts."""
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self._history
        ]

    @property
    def model_name(self) -> str:
        """Return the LLM model name."""
        return self._provider.model_name()

    @property
    def provider_name(self) -> str:
        """Return the normalized provider name."""
        provider_class = self._provider.__class__.__name__.lower()
        if "ollama" in provider_class:
            return "ollama"
        return "gemini"

    @property
    def controller_scope_contract(self) -> Dict[str, Any]:
        """Return the frozen controller scope contract used by the runtime."""
        return dict(self._controller_scope_contract)

    @property
    def controller_implementation_status(self) -> Dict[str, Any]:
        """Return the current baseline of implemented, partial, and missing controller capabilities."""
        return build_controller_implementation_status(self.available_tools)

    @property
    def milestone2_baseline(self) -> Dict[str, Any]:
        """Return the Milestone 2 foundation baseline derived from the current backend surface."""
        return build_milestone2_baseline(self.available_tools)

    @property
    def available_tools(self) -> List[str]:
        """Return the list of available tool names."""
        return self._tools.list_tools()
