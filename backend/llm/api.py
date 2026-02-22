"""
FastAPI chat router for the LLM Agent.

Provides REST endpoints for the frontend chat interface to communicate
with the NetworkAgent.

Endpoints:
    POST /api/chat           — Send a message, get AI response
    POST /api/chat/clear     — Clear conversation history
    GET  /api/chat/history   — Get conversation history
    GET  /api/chat/tools     — List available tools
    GET  /api/chat/info      — Get agent info (model, status)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("llm.api")

router = APIRouter(prefix="/llm/chat", tags=["Chat (M5)"])


# -----------------------------------------------------------------------
# Request / response models
# -----------------------------------------------------------------------

class ChatRequest(BaseModel):
    """User chat message."""
    message: str = Field(..., description="The user's message", min_length=1)


class ChatResponse(BaseModel):
    """Agent chat response."""
    role: str = "assistant"
    content: str = ""
    model: str = ""


class InfoResponse(BaseModel):
    """Agent info."""
    model: str = ""
    tools: list = []
    history_length: int = 0
    status: str = "ready"


# -----------------------------------------------------------------------
# Shared agent instance (set by setup_agent)
# -----------------------------------------------------------------------
_agent = None


def setup_agent(agent) -> None:
    """
    Called once at app startup to inject the NetworkAgent instance.

    Usage (in backend.py or main.py):
        from llm.api import router, setup_agent
        from llm.agent import NetworkAgent

        agent = NetworkAgent(db_manager=db)
        setup_agent(agent)
        app.include_router(router)
    """
    global _agent
    _agent = agent
    logger.info("Chat API initialized with agent (model=%s)", agent.model_name)


def _get_agent():
    """Get the global agent instance or raise 503."""
    if _agent is None:
        raise HTTPException(
            status_code=503,
            detail="AI Agent not initialized. Set GEMINI_API_KEY or configure the LLM provider.",
        )
    return _agent


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a chat message to the AI agent and get a response.

    The agent has access to the live network topology and can:
    - Answer questions about devices, interfaces, and links
    - Generate Cisco/Huawei/Juniper configurations
    - Find network paths between devices
    - Show audit history
    """
    agent = _get_agent()

    try:
        result = await agent.chat(request.message)
        return ChatResponse(
            role=result.get("role", "assistant"),
            content=result.get("content", ""),
            model=agent.model_name,
        )
    except Exception as e:
        logger.error("Chat error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear")
async def clear_history():
    """Clear the conversation history."""
    agent = _get_agent()
    agent.clear_history()
    return {"status": "ok", "message": "Conversation history cleared"}


@router.get("/history")
async def get_history():
    """Get the current conversation history."""
    agent = _get_agent()
    return {"history": agent.get_history()}


@router.get("/tools")
async def list_tools():
    """List all tools available to the AI agent."""
    agent = _get_agent()
    return {"tools": agent.available_tools}


@router.get("/info", response_model=InfoResponse)
async def get_info():
    """Get information about the AI agent's status and configuration."""
    agent = _get_agent()
    return InfoResponse(
        model=agent.model_name,
        tools=agent.available_tools,
        history_length=len(agent.get_history()),
        status="ready",
    )


@router.post("/refresh")
async def refresh_context():
    """
    Refresh the agent's network context.
    Call this after a topology crawl completes.
    """
    agent = _get_agent()
    agent.refresh_context()
    return {"status": "ok", "message": "Network context refreshed"}
