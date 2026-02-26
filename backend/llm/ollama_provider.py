"""
M5 LLM Agent — Ollama Provider (self-hosted)
=============================================
Implements the ``LLMProvider`` ABC using Ollama's local REST API
(``POST /api/chat``).  Supports multi-turn conversations and the
OpenAI-compatible tool/function calling format that Ollama exposes
on models such as ``llama3``, ``mistral``, ``qwen2.5``, etc.

Configuration
-------------
Set these environment variables (or pass to constructor):

    OLLAMA_BASE_URL  → e.g. http://192.168.1.251:11434  (default: http://localhost:11434)
    OLLAMA_MODEL     → e.g. llama3.2:3b                  (default: llama3.2)
    OLLAMA_TIMEOUT   → request timeout in seconds          (default: 120)

Usage::

    provider = OllamaProvider()
    response = await provider.generate([
        LLMMessage(role="user", content="What is BGP?")
    ])
    print(response.content)
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from llm.provider import LLMProvider, LLMMessage, LLMResponse, ToolCall

logger = logging.getLogger("llm.ollama")

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL    = "llama3.2"
_DEFAULT_TIMEOUT  = 120


class OllamaProvider(LLMProvider):
    """
    LLM provider backed by a locally running Ollama server.

    Parameters
    ----------
    base_url : Ollama server URL.  Defaults to ``OLLAMA_BASE_URL`` env var
               or ``http://localhost:11434``.
    model    : Model tag to use.  Defaults to ``OLLAMA_MODEL`` env var
               or ``llama3.2``.
    timeout  : HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self._base_url = (base_url or os.getenv("OLLAMA_BASE_URL", _DEFAULT_BASE_URL)).rstrip("/")
        self._model    = model   or os.getenv("OLLAMA_MODEL",    _DEFAULT_MODEL)
        self._timeout  = int(timeout or os.getenv("OLLAMA_TIMEOUT", _DEFAULT_TIMEOUT))
        logger.info("OllamaProvider initialised (url=%s model=%s)", self._base_url, self._model)

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
        Send a chat request to the Ollama server and return an LLMResponse.

        Supports tool calling if the selected model declares tool support
        (Ollama will ignore the tools parameter silently for models that
        don't support function calling).
        """
        payload = self._build_payload(messages, tools, temperature)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._base_url}. "
                "Is the server running? (OLLAMA_BASE_URL env var)"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama API error {exc.response.status_code}: {exc.response.text}"
            ) from exc

        return self._parse_response(data)

    # ------------------------------------------------------------------
    # Non-async convenience wrapper (for modules using the sync generate())
    # ------------------------------------------------------------------

    def generate_sync(
        self,
        prompt: str,
        temperature: float = 0.3,
    ) -> str:
        """
        Synchronous single-turn generation for callers that use the simple
        ``llm.generate(prompt)`` pattern (e.g. DriftClassifier).
        """
        import httpx as _httpx

        payload = {
            "model":    self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream":   False,
            "options":  {"temperature": temperature},
        }

        try:
            with _httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self._base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except _httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._base_url}."
            ) from exc

        msg = data.get("message", {})
        return msg.get("content", "")

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the Ollama server is reachable and the model is loaded."""
        try:
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            tags = resp.json().get("models", [])
            # Check if our model is in the list (partial match on name)
            model_base = self._model.split(":")[0]
            return any(model_base in m.get("name", "") for m in tags)
        except Exception:
            return False

    def list_local_models(self) -> List[str]:
        """Return the list of model tags available on this Ollama instance."""
        try:
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except Exception as exc:
            logger.warning("Could not list Ollama models: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Payload building
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]],
        temperature: float,
    ) -> Dict[str, Any]:
        """Convert LLMMessage objects to Ollama API format."""
        ollama_messages = []
        for msg in messages:
            if msg.role == "tool":
                # Tool result: Ollama expects role="tool" with tool_call_id
                ollama_messages.append({
                    "role":         "tool",
                    "content":      msg.content,
                    "tool_call_id": msg.tool_call_id or "",
                })
            elif msg.tool_calls:
                # Assistant message requesting tool calls
                ollama_messages.append({
                    "role":       "assistant",
                    "content":    msg.content or "",
                    "tool_calls": [
                        {"function": {"name": tc.name, "arguments": tc.arguments}}
                        for tc in msg.tool_calls
                    ],
                })
            else:
                ollama_messages.append({
                    "role":    msg.role,
                    "content": msg.content,
                })

        payload: Dict[str, Any] = {
            "model":    self._model,
            "messages": ollama_messages,
            "stream":   False,
            "options":  {"temperature": temperature},
        }

        if tools:
            # Ollama accepts OpenAI-compatible tool definitions
            payload["tools"] = tools

        return payload

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(data: Dict[str, Any]) -> LLMResponse:
        """Convert Ollama's /api/chat response into an LLMResponse."""
        msg = data.get("message", {})
        content = msg.get("content", "")
        finish_reason = "stop"
        tool_calls: List[ToolCall] = []

        raw_tool_calls = msg.get("tool_calls", [])
        if raw_tool_calls:
            finish_reason = "tool_calls"
            for tc in raw_tool_calls:
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw=data,
        )
