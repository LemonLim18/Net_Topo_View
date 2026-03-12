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

from starlette.concurrency import run_in_threadpool

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

        Uses native ``system_instruction`` and structured ``Content``
        objects instead of string concatenation so Gemini can leverage
        its built-in multi-turn support and reduce token overhead.
        """
        system_text, contents = self._build_contents(messages)

        gemini_tools = self._build_tools(tools) if tools else None

        try:
            config_kwargs: Dict[str, Any] = {"temperature": temperature}
            if system_text:
                config_kwargs["system_instruction"] = system_text
            if gemini_tools:
                config_kwargs["tools"] = gemini_tools

            config = genai_types.GenerateContentConfig(**config_kwargs)

            response = await run_in_threadpool(
                self._client.models.generate_content,
                model=self._model,
                contents=contents,
                config=config,
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

    def _build_contents(
        self, messages: List[LLMMessage]
    ) -> tuple[str, list[genai_types.Content]]:
        """Convert LLMMessage list into Gemini-native structured objects.

        Returns ``(system_instruction_text, contents_list)`` where
        system messages are extracted into ``system_instruction`` (fed
        via ``GenerateContentConfig``) and user/assistant/tool messages
        become proper ``Content`` objects with the correct ``role``.
        """
        system_parts: List[str] = []
        contents: list[genai_types.Content] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            elif msg.role == "user":
                contents.append(
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=msg.content)],
                    )
                )
            elif msg.role == "assistant":
                parts: list[genai_types.Part] = []
                if msg.content:
                    parts.append(genai_types.Part.from_text(text=msg.content))
                for tc in msg.tool_calls:
                    parts.append(
                        genai_types.Part.from_function_call(
                            name=tc.name,
                            args=tc.arguments,
                        )
                    )
                if parts:
                    contents.append(
                        genai_types.Content(role="model", parts=parts)
                    )
            elif msg.role == "tool":
                contents.append(
                    genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part.from_function_response(
                                name=msg.name or "tool",
                                response={"result": msg.content},
                            )
                        ],
                    )
                )

        system_text = "\n\n".join(system_parts) if system_parts else ""
        return system_text, contents

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
