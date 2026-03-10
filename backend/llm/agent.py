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

import json
import logging
import re
from typing import Any, Dict, List, Optional

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
from llm.context_state import get_context_state_manager
from llm.milestone_baseline import build_milestone2_baseline, render_milestone2_baseline_prompt
from llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall
from llm.system_prompt import build_system_prompt
from llm.tools import ToolRegistry
from llm.context import ContextBuilder
from llm.proposal import ProposalManager, ConfigProposal
from llm import network_tools

logger = logging.getLogger("llm.agent")

# Maximum tool-calling rounds before forcing a text response
MAX_TOOL_ROUNDS = 5

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

        logger.info(
            "NetworkAgent initialized (model=%s, tools=%d)",
            self._provider.model_name(),
            len(self._tools.list_tools()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        # Add user message to history
        self._history.append(LLMMessage(role="user", content=user_message))

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
        tool_results: Dict[str, Any] = {}  # tool_name -> last result
        tool_args: Dict[str, Dict] = {}    # tool_name -> last args
        self_correction_state = build_self_correction_state()

        self._apply_prefetched_route_tools(messages, route_plan, tool_results, tool_args, self_correction_state, user_message)

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
                response_content = response.content
                if flow_action == "block":
                    response_content = self._render_controller_flow_block(flow_state)
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
        return heuristic_matches[:3]

    def _build_route_plan(
        self,
        user_message: str,
        query_class: str,
        hostnames: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        hostnames = hostnames or self._extract_candidate_hostnames(user_message)
        generic_read_tools = [
            "get_context_health",
            "get_audit_log",
            "list_templates",
            "get_template_schema",
            "get_digital_twin",
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
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
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
            if hostnames:
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
                prefetch_tools.extend(
                    [
                        ToolCall(name="get_digital_twin", arguments={"hostname": hostnames[0]}),
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
                "analyze_change_intelligence",
                "get_digital_twin",
                "plan_change",
                "list_templates",
                "get_template_schema",
                "get_inventory_device",
                "get_device_running_config",
                "get_recent_drift_events",
                "get_context_health",
            ]
            instruction = (
                "For this change-planning query, use intent parsing and policy validation first, then run change intelligence before planning so you surface intent memory, outcome risk, and what-if blockers instead of guessing."
            )
            prefetch_tools.append(ToolCall(name="get_context_health", arguments={}))
            if hostnames:
                prefetch_tools.extend(
                    [
                        ToolCall(name="get_digital_twin", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_inventory_device", arguments={"hostname": hostnames[0]}),
                        ToolCall(name="get_device_running_config", arguments={"hostname": hostnames[0]}),
                    ]
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
        result = self._tools.execute(tool_call.name, tool_call.arguments)
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
        result = self._tools.execute(name, arguments)
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

    def _apply_prefetched_route_tools(
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

        for tc in prefetch_tools:
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

    def _network_context_cache_key(
        self,
        user_message: Optional[str] = None,
        routing_decision=None,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        snapshot = get_context_state_manager().snapshot()
        decision = routing_decision
        return json.dumps(
            {
                "versions": snapshot.get("versions", {}),
                "query_class": getattr(decision, "query_class", None),
                "use_live_context": getattr(decision, "use_live_context", None),
                "include_inventory": getattr(decision, "include_inventory", None),
                "include_telemetry": getattr(decision, "include_telemetry", None),
                "include_sdn": getattr(decision, "include_sdn", None),
                "device_hostnames": sorted(device_hostnames or []),
                "query_hint": (user_message or "").strip().lower()[:160],
            },
            sort_keys=True,
        )

    def _build_network_context(
        self,
        user_message: Optional[str] = None,
        routing_decision=None,
        device_hostnames: Optional[List[str]] = None,
    ) -> str:
        cache_key = self._network_context_cache_key(
            user_message=user_message,
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
        self._network_context_cache = {cache_key: network_context}
        return network_context

    def _render_system_prompt(
        self,
        user_message: Optional[str] = None,
        routing_decision=None,
        device_hostnames: Optional[List[str]] = None,
        controller_policy: Optional[Dict[str, Any]] = None,
        route_instruction: str = "",
    ) -> str:
        return build_system_prompt(
            controller_scope=render_controller_scope_prompt(self.available_tools),
            milestone2_baseline=render_milestone2_baseline_prompt(self.available_tools),
            controller_policy=render_controller_policy_prompt(
                controller_policy or build_controller_policy("general", self.available_tools),
                route_instruction,
            ),
            network_context=self._build_network_context(
                user_message=user_message,
                routing_decision=routing_decision,
                device_hostnames=device_hostnames,
            ),
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
        grounding_meta["answer_policy"] = policy_status(answer_policy or {}, tool_args or {})
        if controller_flow is not None:
            grounding_meta["controller_flow"] = controller_flow
        if self_correction_state is not None:
            grounding_meta["self_correction"] = self_correction_state
        return grounding_meta

    def _render_grounded_content(self, content: str, grounding_meta: Dict[str, Any]) -> str:
        summary = format_grounding_summary(grounding_meta)
        conflict_summary = format_conflict_summary(grounding_meta)
        suffix = summary if not conflict_summary else f"{summary}\n{conflict_summary}"
        if not content:
            return suffix
        if summary in content:
            return content
        return f"{content}\n\n{suffix}"

    def refresh_context(self) -> None:
        """
        Invalidate cached environment context so the next turn rebuilds it.

        This preserves compatibility with existing refresh hooks while
        keeping per-turn prompt assembly authoritative.
        """
        self._network_context_cache.clear()
        logger.info("Cached network context invalidated")

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
