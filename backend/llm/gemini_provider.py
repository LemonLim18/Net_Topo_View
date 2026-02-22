"""
Google Gemini LLM Provider.

Uses the ``google-genai`` SDK (same as the existing ``backend/gemini.py``
prototype) but wraps it in the ``LLMProvider`` ABC so the agent can swap
backends.

Supports:
    - Multi-turn conversation
    - Tool / function calling
    - Streaming (future)
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall

logger = logging.getLogger("llm.gemini")

# ---------------------------------------------------------------------------
# Try to import google-genai SDK
# ---------------------------------------------------------------------------
try:
    from google import genai
    from google.genai import types as genai_types
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False
    logger.warning("google-genai SDK not installed — GeminiProvider unavailable")


class GeminiProvider(LLMProvider):
    """
    LLM provider backed by Google Gemini.

    Configuration:
        - ``api_key``:  Gemini API key (or set ``GEMINI_API_KEY`` env var)
        - ``model``:    Model name (default ``gemini-2.5-flash``)
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        if not _HAS_GENAI:
            raise ImportError(
                "google-genai SDK is required: pip install google-genai"
            )

        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        # Initialize client. standard sync client.
        if self._api_key:
            self._client = genai.Client(api_key=self._api_key)
        else:
            logger.warning("No Gemini API Key provided.")
            self._client = None
            
        logger.info("GeminiProvider initialized (model=%s)", self._model)

    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """
        Call Gemini with the conversation history and optional tools.
        """
        # Build contents for Gemini
        contents = self._build_contents(messages)

        # Build tool declarations
        gemini_tools = None
        if tools:
            gemini_tools = self._build_tools(tools)

        try:
            # Generate config
            gen_config = genai_types.GenerateContentConfig(
                temperature=temperature,
            )

            # Call Gemini (synchronous SDK — run in executor if needed)
            if gemini_tools:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        temperature=temperature,
                        tools=gemini_tools,
                    ),
                )
            else:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=gen_config,
                )

            return self._parse_response(response)

        except Exception as e:
            logger.error("Gemini API error: %s", e)
            return LLMResponse(
                content=f"Error calling Gemini API: {e}",
                finish_reason="error",
            )

    # ------------------------------------------------------------------
    # Message conversion
    # ------------------------------------------------------------------

    def _build_contents(self, messages: List[LLMMessage]) -> str:
        """Convert our LLMMessage list into a prompt string for Gemini."""
        parts = []
        for msg in messages:
            if msg.role == "system":
                parts.append(f"System instructions: {msg.content}\n")
            elif msg.role == "user":
                parts.append(f"User: {msg.content}\n")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}\n")
            elif msg.role == "tool":
                parts.append(
                    f"Tool result ({msg.name}): {msg.content}\n"
                )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Tool declaration conversion
    # ------------------------------------------------------------------

    def _build_tools(self, tools: List[Dict]) -> List:
        """Convert our tool definitions to Gemini function declarations."""
        declarations = []
        for tool in tools:
            fn_decl = genai_types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=tool.get("parameters"),
            )
            declarations.append(fn_decl)
        return [genai_types.Tool(function_declarations=declarations)]

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse Gemini response into our LLMResponse dataclass."""
        tool_calls: List[ToolCall] = []
        text_parts: List[str] = []

        try:
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        args = {}
                        if fc.args:
                            # Convert proto Map to dict
                            args = dict(fc.args)
                        tool_calls.append(ToolCall(
                            name=fc.name,
                            arguments=args,
                        ))
                    elif hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
        except Exception as e:
            logger.warning("Error parsing Gemini response: %s", e)
            # Fallback: try .text property
            try:
                text_parts.append(response.text)
            except Exception:
                text_parts.append(str(response))

        content = "\n".join(text_parts)
        finish = "tool_calls" if tool_calls else "stop"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
            raw=response,
        )
