"""
M5: LLM Agent — AI Brain for Network Automation.

Provides a natural-language interface that understands network topology,
generates device configurations, and answers operational questions.

Usage:
    from llm import NetworkAgent

    agent = NetworkAgent(db_manager=db)
    response = await agent.chat("Show me the topology summary")

Supported LLM backends:
    - Google Gemini (default)
    - OpenRouter (DeepSeek, etc.)
"""

from llm.agent import NetworkAgent  # noqa: F401

__all__ = ["NetworkAgent"]
