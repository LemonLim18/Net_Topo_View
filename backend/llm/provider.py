"""
Abstract LLM Provider interface.

All LLM backends (Gemini, OpenRouter, Ollama) implement this ABC so that
the ``NetworkAgent`` can swap providers without changing business logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """Represents a tool/function call requested by the LLM."""
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMMessage:
    """A single message in the conversation history."""
    role: str              # "system", "user", "assistant", "tool"
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None   # for tool-response messages
    name: Optional[str] = None           # function name for tool results


@dataclass
class LLMResponse:
    """Response from the LLM provider."""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"          # "stop" | "tool_calls"
    raw: Any = None                      # provider-specific raw response


class LLMProvider(ABC):
    """
    Abstract interface for LLM backends.

    Subclasses must implement ``generate()`` which takes a conversation
    and returns an ``LLMResponse``.
    """

    @abstractmethod
    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """
        Generate a response from the LLM.

        Args:
            messages:     Full conversation history.
            tools:        Tool/function definitions (JSON-schema format).
            temperature:  Sampling temperature.

        Returns:
            An ``LLMResponse`` with either text content or tool calls.
        """
        ...

    @abstractmethod
    def model_name(self) -> str:
        """Return the human-readable model name."""
        ...
