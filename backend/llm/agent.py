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
from llm.controller_scope import (
    build_controller_implementation_status,
    build_controller_scope_contract,
    render_controller_scope_prompt,
)
from llm.context_contract import classify_query
from llm.context_state import get_context_state_manager
from llm.milestone_baseline import build_milestone2_baseline, render_milestone2_baseline_prompt
from llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall
from llm.tools import ToolRegistry
from llm.context import ContextBuilder
from llm.proposal import ProposalManager, ConfigProposal
from llm import network_tools

logger = logging.getLogger("llm.agent")

# Maximum tool-calling rounds before forcing a text response
MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = """\
You are **NetAutoController AI** — an expert network reliability engineer
embedded inside an enterprise-grade campus network automation controller.

## Architecture — Intent-Based Pipeline
You operate within a controlled 5-stage translation pipeline:

  Human Language → Structured Intent → Policy Validation → Change Plan → Execution → Verification

**You are responsible for Stages 1–3.  You NEVER execute directly.**

## MANDATORY: Configuration Request Workflow
For ANY request that involves creating, modifying, or deleting network 
configuration, you MUST follow this EXACT 3-step sequence:

### Step 1 — Parse Intent
Call `parse_intent(user_message)` to convert the request into a structured 
intent object.  This extracts: action, feature, vendor, parameters, target 
devices, and risk level.  If the intent requires disambiguation (e.g. no 
target device specified), relay the disambiguation_message to the user.

### Step 2 — Validate Policy
Call `validate_intent(intent_json, user_role)` with the JSON from Step 1.
This checks RBAC, change window, naming conventions, blast radius, VLAN/IP 
conflicts, and device reachability.  If approved=false, explain the 
violations to the user and do NOT proceed to Step 3.

### Step 3 — Generate Change Plan
Call `plan_change(intent_json)` with the validated intent.  This generates 
CLI config, rollback commands, syntax validation, diff preview, execution 
engine choice, and auto-verification checks for each target device.

**NEVER call `generate_config` directly for user requests.**  It is an 
internal tool used by the pipeline.  Always go through the 3-step sequence.

## Safety Principle
- The LLM (you) only outputs structured data and reasoning.
- The system enforces policy, generates deterministic configs, and executes.
- You NEVER generate raw CLI for direct execution.
- You NEVER talk directly to SSH.  All changes go through the approval gate.

{controller_scope}

{milestone2_baseline}

## Read-Only & Diagnostic Tools
For information requests (show topology, list devices, check status, find path, 
view audit logs, security scans), use the appropriate query tools directly.
No intent pipeline is needed for read-only operations.

## Available Query Tools
- `get_topology_summary` — overview of all devices and links
- `get_device_details(hostname)` — interfaces, neighbors, edge links
- `list_all_devices(scope)` — device listing for `live`, `inventory`, or `both` scope with explicit source labeling
- `find_path(source, destination)` — shortest path between devices
- `get_device_running_config(hostname)` — last known running-config snapshot
- `get_audit_log(limit)` — recent configuration change history
- `run_security_audit(hostname)` — CIS benchmark security scan
- `get_security_events(limit)` — recent threats and alerts
- `analyze_security_alert(event_id)` — threat analysis + response plan
- `get_template_schema(feature)` — see required parameters for a config template
- `list_templates(vendor)` — available Jinja2 config templates

## Rules
- Always query real data.  Never invent device names, IPs, or topology.
- Be concise but thorough.  Use bullet points and tables.
- If you don't know something, say so.  Never hallucinate network state.
- Treat the agent-supplied grounding metadata as authoritative. Do not contradict whether evidence is planned, live, mixed, or inferred.
- After generating a change plan, summarize: what changes, which devices, risk level, blast radius.

## Current Network State
{network_context}
"""


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
        self._proposals = ProposalManager()

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

        per_turn_system = self._render_system_prompt(
            user_message=user_message,
            routing_decision=routing_decision,
            device_hostnames=candidate_hostnames,
        )
        if route_plan["instruction"]:
            per_turn_system = per_turn_system + "\n\n" + route_plan["instruction"]
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

        self._apply_prefetched_route_tools(messages, route_plan, tool_results, tool_args)

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
                if self._enforce_required_tool_policy(messages, route_plan, tool_results, tool_args):
                    continue
                grounding_meta = self._build_grounding_meta(
                    routing_decision.query_class,
                    tool_results,
                    tool_args,
                    route_plan.get("answer_policy"),
                    knowledge_used=bool(knowledge_ctx),
                )
                final_content = self._render_grounded_content(response.content, grounding_meta)
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
                result = self._tools.execute(tc.name, tc.arguments)

                # Track for proposal detection
                tool_results[tc.name] = result
                tool_args[tc.name] = tc.arguments

                messages.append(LLMMessage(
                    role="tool",
                    content=result,
                    name=tc.name,
                ))

        # If we exhausted all rounds, ensure required policy tools have run, then return whatever we have
        self._enforce_required_tool_policy(messages, route_plan, tool_results, tool_args)
        final_response = await self._provider.generate(
            messages=messages,
            tools=None,  # Force text response
            temperature=0.3,
        )

        self._history.append(
            LLMMessage(
                role="assistant",
                content=self._render_grounded_content(
                    final_response.content,
                    self._build_grounding_meta(
                        routing_decision.query_class,
                        tool_results,
                        tool_args,
                        route_plan.get("answer_policy"),
                        knowledge_used=bool(knowledge_ctx),
                    ),
                ),
            )
        )

        grounding_meta = self._build_grounding_meta(
            routing_decision.query_class,
            tool_results,
            tool_args,
            route_plan.get("answer_policy"),
            knowledge_used=bool(knowledge_ctx),
        )
        final_content = self._render_grounded_content(final_response.content, grounding_meta)

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

            # Aggregate configs across all devices in the plan
            device_plans = plan.get("device_plans", [])
            if not device_plans:
                return None

            # Primary device = first device in plan
            first = device_plans[0]
            target_device = first.get("hostname", "unknown")
            vendor = intent_data.get("vendor") or first.get("vendor", "unknown")
            feature = intent_data.get("feature") or first.get("feature", "unknown")

            # Merge generated configs / rollbacks / diffs for ALL devices
            config_blocks = []
            rollback_blocks = []
            diff_blocks = []
            for dp in device_plans:
                hn = dp.get("hostname", "?")
                config_blocks.append(f"! ----- {hn} -----\n{dp.get('config', '')}")
                rollback_blocks.append(f"! ----- {hn} -----\n{dp.get('rollback', '')}")
                if dp.get("diff"):
                    diff_blocks.append(f"--- {hn} ---\n{dp['diff']}")

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

        return {
            "prefetch_tools": prefetch_tools,
            "allowed_tools": allowed_tools,
            "instruction": instruction,
            "answer_policy": answer_policy,
        }

    def _apply_prefetched_route_tools(
        self,
        messages: List[LLMMessage],
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
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

    def _enforce_required_tool_policy(
        self,
        messages: List[LLMMessage],
        route_plan: Dict[str, Any],
        tool_results: Dict[str, Any],
        tool_args: Dict[str, Dict[str, Any]],
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

        return True

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
    ) -> str:
        return SYSTEM_PROMPT.format(
            controller_scope=render_controller_scope_prompt(self.available_tools),
            milestone2_baseline=render_milestone2_baseline_prompt(self.available_tools),
            network_context=self._build_network_context(
                user_message=user_message,
                routing_decision=routing_decision,
                device_hostnames=device_hostnames,
            )
        )

    def _build_grounding_meta(
        self,
        query_class: str,
        tool_results: Dict[str, Any],
        tool_args: Optional[Dict[str, Dict[str, Any]]] = None,
        answer_policy: Optional[Dict[str, Any]] = None,
        knowledge_used: bool = False,
    ) -> Dict[str, Any]:
        grounding_meta = build_answer_grounding(
            query_class=query_class,
            tool_results=tool_results,
            tool_args=tool_args,
            knowledge_used=knowledge_used,
        )
        grounding_meta["answer_policy"] = policy_status(answer_policy or {}, tool_args or {})
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
