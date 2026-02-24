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
from typing import Any, Dict, List, Optional

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
embedded inside a campus network automation controller.

## Your Capabilities
You have access to tools that let you:
1. **Query the live network topology** — see all devices, interfaces, links, and status.
2. **Generate device configurations** — create CLI commands for Cisco IOS, Huawei VRP, and Juniper Junos using Jinja2 templates.
3. **Generate rollback commands** — create undo commands to reverse any configuration change.
4. **Trace network paths** — find the shortest path between any two devices.
5. **View audit logs** — see what configuration changes have been made.

## Rules
- Always use your tools to fetch real data. Never invent device names, IPs, or topology.
- When generating configs, first check the template schema with `get_template_schema` to understand required parameters.
- Present configurations in code blocks for readability.
- If a device is not found, suggest the correct hostname from your tools.
- Be concise but thorough. Use bullet points and tables when appropriate.
- If you don't know something, say so. Never hallucinate network state.
- When asked to configure something, ALWAYS use `generate_config` to create the config AND `generate_rollback` for the undo commands AND `diff_configs` to show changes.
- After generating config, provide a clear explanation of what the change will do and its risk level (low/medium/high).

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
        network_tools.register_all_tools(self._tools)

        # M21: optional knowledge retriever for hybrid RAG
        self._retriever = retriever

        # Conversation history (persists across chat() calls)
        self._history: List[LLMMessage] = []

        # Build system prompt with current network context
        self._system_prompt = SYSTEM_PROMPT.format(
            network_context=self._context.build_full_context()
        )

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

        # --- M21: Hybrid RAG routing ---
        try:
            from llm.retriever import route
            use_live, use_knowledge = route(user_message)
        except Exception:
            use_live, use_knowledge = True, False

        per_turn_system = self._system_prompt  # always includes baked live topology
        if use_knowledge and self._retriever is not None:
            knowledge_ctx = self._context.build_knowledge_context(
                user_message, self._retriever
            )
            if knowledge_ctx:
                per_turn_system = self._system_prompt + "\n\n" + knowledge_ctx

        # Build full message list (system + history)
        messages = [
            LLMMessage(role="system", content=per_turn_system),
            *self._history,
        ]

        # Get tool declarations
        tool_declarations = self._tools.get_declarations()

        # Track tool calls for proposal detection
        tool_results: Dict[str, Any] = {}  # tool_name -> last result
        tool_args: Dict[str, Dict] = {}    # tool_name -> last args

        # Tool-calling loop
        for round_num in range(MAX_TOOL_ROUNDS):
            response = await self._provider.generate(
                messages=messages,
                tools=tool_declarations if tool_declarations else None,
                temperature=0.3,
            )

            # If no tool calls, we have a final answer
            if not response.tool_calls:
                self._history.append(
                    LLMMessage(role="assistant", content=response.content)
                )

                # --- M19: Proposal Detection ---
                proposal = self._maybe_create_proposal(
                    user_message, response.content, tool_results, tool_args
                )
                if proposal:
                    return {
                        "role": "assistant",
                        "content": response.content,
                        "type": "proposal",
                        "proposal": proposal.model_dump(),
                    }

                return {
                    "role": "assistant",
                    "content": response.content,
                    "type": "text",
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

        # If we exhausted all rounds, return whatever we have
        final_response = await self._provider.generate(
            messages=messages,
            tools=None,  # Force text response
            temperature=0.3,
        )

        self._history.append(
            LLMMessage(role="assistant", content=final_response.content)
        )

        # Check for proposal even after exhausting rounds
        proposal = self._maybe_create_proposal(
            user_message, final_response.content, tool_results, tool_args
        )
        if proposal:
            return {
                "role": "assistant",
                "content": final_response.content,
                "type": "proposal",
                "proposal": proposal.model_dump(),
            }

        return {
            "role": "assistant",
            "content": final_response.content,
            "type": "text",
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
        If generate_config was called during this chat turn,
        automatically package the results into a ConfigProposal.
        """
        if "generate_config" not in tool_results:
            return None

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

    def refresh_context(self) -> None:
        """
        Refresh the network context in the system prompt.
        Call this after a topology crawl completes.
        """
        self._system_prompt = SYSTEM_PROMPT.format(
            network_context=self._context.build_full_context()
        )
        logger.info("System prompt refreshed with latest topology")

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
    def available_tools(self) -> List[str]:
        """Return the list of available tool names."""
        return self._tools.list_tools()
