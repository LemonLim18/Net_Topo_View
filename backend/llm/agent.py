"""
NetworkAgent — the main AI agent orchestrator.

Ties together:
    - LLM Provider (Gemini)
    - Tool Registry (network tools)
    - Context Builder (topology RAG)

Implements a tool-calling loop: the agent calls tools, gets results,
and continues reasoning until it produces a final answer.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from backend.llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall
from backend.llm.tools import ToolRegistry
from backend.llm.context import ContextBuilder
from backend.llm import network_tools

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
- When asked to configure something, show the generated config AND the rollback commands.

## Current Network State
{network_context}
"""


class NetworkAgent:
    """
    Main agent that handles multi-turn conversations with tool calling.

    Usage::

        agent = NetworkAgent(db_manager=db)
        response = await agent.chat("Show me all devices")
        # response = {"role": "assistant", "content": "Here are..."}

        # Multi-turn
        response2 = await agent.chat("Tell me more about R1")
    """

    def __init__(
        self,
        db_manager=None,
        provider: Optional[LLMProvider] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Args:
            db_manager:  DatabaseManager instance for live topology.
            provider:    Custom LLMProvider (default: GeminiProvider).
            api_key:     Gemini API key (if using default provider).
            model:       Model name override.
        """
        # Initialize LLM provider
        if provider:
            self._provider = provider
        else:
            from backend.llm.gemini_provider import GeminiProvider
            self._provider = GeminiProvider(api_key=api_key, model=model)

        # Initialize context builder
        self._context = ContextBuilder(db_manager=db_manager)

        # Initialize and populate tool registry
        self._tools = ToolRegistry()
        network_tools.set_db_manager(db_manager)
        network_tools.register_all_tools(self._tools)

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

    async def chat(self, user_message: str) -> Dict[str, str]:
        """
        Process a user message and return the agent's response.

        This implements a tool-calling loop:
        1. Send user message + history to LLM
        2. If LLM requests tool calls, execute them
        3. Feed tool results back to LLM
        4. Repeat until LLM produces a text response

        Returns:
            {"role": "assistant", "content": "..."}
        """
        # Add user message to history
        self._history.append(LLMMessage(role="user", content=user_message))

        # Build full message list (system + history)
        messages = [
            LLMMessage(role="system", content=self._system_prompt),
            *self._history,
        ]

        # Get tool declarations
        tool_declarations = self._tools.get_declarations()

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
                return {"role": "assistant", "content": response.content}

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
        return {"role": "assistant", "content": final_response.content}

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
