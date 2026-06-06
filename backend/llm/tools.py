"""
Tool registry for the LLM Agent.

Tools are Python functions that the LLM can call to interact with the
network controller.  Each tool is registered with a JSON-schema
definition so Gemini knows *what* it can call and *how*.

Usage:
    from llm.tools import ToolRegistry

    registry = ToolRegistry()

    @registry.tool("Get the topology summary")
    def get_topology_summary() -> str:
        ...

    # Get all tool declarations for the LLM
    declarations = registry.get_declarations()

    # Execute a tool call
    result = registry.execute("get_topology_summary", {})
"""

import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Optional, get_type_hints

logger = logging.getLogger("llm.tools")


#: Maps Python types to JSON-schema types
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolRegistry:
    """
    Central registry of tools the LLM can invoke.

    Provides:
        - ``@tool()`` decorator to register functions
        - ``get_declarations()`` for Gemini function-calling schema
        - ``execute(name, args)`` to run a tool by name
    """

    def __init__(self):
        self._tools: Dict[str, Dict] = {}  # name → {fn, schema}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def tool(
        self,
        description: str = "",
        param_descriptions: Optional[Dict[str, str]] = None,
    ) -> Callable:
        """
        Decorator to register a function as an LLM-callable tool.

        Args:
            description:        Human-readable description of the tool.
            param_descriptions: Optional per-parameter descriptions.
        """
        param_descriptions = param_descriptions or {}

        def decorator(fn: Callable) -> Callable:
            name = fn.__name__
            schema = self._build_schema(fn, description, param_descriptions)
            self._tools[name] = {"fn": fn, "schema": schema}
            logger.debug("Registered tool: %s", name)
            return fn

        return decorator

    def register(
        self,
        fn: Callable,
        description: str = "",
        param_descriptions: Optional[Dict[str, str]] = None,
    ) -> None:
        """Programmatic registration (non-decorator)."""
        param_descriptions = param_descriptions or {}
        name = fn.__name__
        schema = self._build_schema(fn, description, param_descriptions)
        self._tools[name] = {"fn": fn, "schema": schema}
        logger.debug("Registered tool: %s", name)

    # ------------------------------------------------------------------
    # Schema generation
    # ------------------------------------------------------------------

    def _build_schema(
        self,
        fn: Callable,
        description: str,
        param_descriptions: Dict[str, str],
    ) -> Dict:
        """Build a JSON-schema tool definition from a function signature."""
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            py_type = hints.get(param_name, str)
            json_type = _TYPE_MAP.get(py_type, "string")

            prop: Dict[str, Any] = {"type": json_type}
            if param_name in param_descriptions:
                prop["description"] = param_descriptions[param_name]

            properties[param_name] = prop

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        schema: Dict[str, Any] = {
            "name": fn.__name__,
            "description": description or fn.__doc__ or "",
        }

        if properties:
            params: Dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                params["required"] = required
            schema["parameters"] = params

        return schema

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        Execute a registered tool by name.

        Args:
            name:       Tool function name.
            arguments:  Keyword arguments (from LLM).

        Returns:
            JSON-serialized result string.
        """
        if name not in self._tools:
            return json.dumps({"error": f"Unknown tool: {name}"})

        fn = self._tools[name]["fn"]
        try:
            result = fn(**arguments)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str, ensure_ascii=False)
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Declarations (for LLM)
    # ------------------------------------------------------------------

    def get_declarations(self, allowed_names: Optional[List[str]] = None) -> List[Dict]:
        """Return tool schemas for the LLM provider, optionally filtered by name."""
        if allowed_names is None:
            return [entry["schema"] for entry in self._tools.values()]

        allowed = set(allowed_names)
        return [
            entry["schema"]
            for name, entry in self._tools.items()
            if name in allowed
        ]

    def list_tools(self) -> List[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())
