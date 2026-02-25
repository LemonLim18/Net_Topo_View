"""
M25 Phase-2: Autonomous Agent Plan → Execute API.

Two-phase agentic workflow as defined in FRONTEND_CONTEXT.md:

  Phase 1 — Planning:
    POST /api/agent/plan
    AI analyses intent + network context, returns structured plan
    (context summary, proposed actions, expected outcomes, risk level)

  Phase 2 — Execution:
    POST /api/agent/execute
    User confirms, AI generates Ansible playbook
    Accepts action: "generate" | "revise" | "cancel"
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("agent.api")

router = APIRouter(prefix="/agent", tags=["Autonomous Agent (M25)"])

# ── Shared instances (injected by backend.py via setup_agent_api) ────────────
_agent    = None   # NetworkAgent
_builder  = None   # PlaybookBuilder


def setup_agent_api(agent, builder=None) -> None:
    global _agent, _builder
    _agent   = agent
    _builder = builder
    logger.info("Agent Plan/Execute API initialised (builder=%s)", bool(builder))


# ── In-process plan store (UUID → plan dict) ─────────────────────────────────
_plans: Dict[str, Dict[str, Any]] = {}

# ── Request / Response models ────────────────────────────────────────────────

class PlanContext(BaseModel):
    current_view:    Optional[str] = None
    selected_device: Optional[str] = None


class PlanRequest(BaseModel):
    domain:  str = "network"          # "network" | "security"
    message: str
    context: PlanContext = PlanContext()


class ContextSummary(BaseModel):
    topology:         str = ""
    current_state:    str = ""
    affected_devices: List[str] = []


class ExpectedOutcomes(BaseModel):
    positive: List[str] = []
    warnings: List[str] = []


class PlanResponse(BaseModel):
    plan_id:          str
    mode:             str = "planning"
    context_summary:  ContextSummary  = ContextSummary()
    proposed_actions: List[str]       = []
    expected_outcomes: ExpectedOutcomes = ExpectedOutcomes()
    risk_level:       str             = "medium"   # low | medium | high


class ExecuteRequest(BaseModel):
    plan_id: str
    action:  str = "generate"   # generate | revise | cancel


class PlaybookPayload(BaseModel):
    name:     str = ""
    filename: str = ""
    content:  str = ""   # YAML
    hosts:    List[str] = []
    tasks:    List[Dict[str, Any]] = []


class ExecuteResponse(BaseModel):
    mode:               str = "execution"
    plan_id:            str
    playbook:           PlaybookPayload = PlaybookPayload()
    estimated_duration: str = "2-5 minutes"
    rollback_plan:      Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_agent():
    if _agent is None:
        raise HTTPException(503, "AI agent not initialized — set GEMINI_API_KEY")
    return _agent


_PLAN_SYSTEM = """\
You are analysing a network change request for an autonomous agent workflow.
Return ONLY valid JSON matching this exact schema — no markdown, no prose:

{
  "proposed_actions": ["action 1", "action 2", ...],
  "context_summary": {
    "topology": "one-sentence summary of relevant topology",
    "current_state": "one-sentence description of current device/service state",
    "affected_devices": ["R1", "R2"]
  },
  "expected_outcomes": {
    "positive": ["outcome 1", "outcome 2"],
    "warnings": ["warning 1"]
  },
  "risk_level": "low"
}

risk_level must be one of: low | medium | high
"""


async def _llm_plan(agent, message: str, domain: str, ctx: PlanContext) -> dict:
    """Ask the LLM for a structured plan dict."""
    device_hint = f" The user currently has device '{ctx.selected_device}' selected." if ctx.selected_device else ""
    view_hint   = f" They are on the '{ctx.current_view}' view." if ctx.current_view else ""

    # Build a short prompt
    prompt = (
        f"Domain: {domain}.\n"
        f"Intent: {message}\n"
        f"{device_hint}{view_hint}\n\n"
        "Produce the structured plan JSON according to the schema."
    )

    # Use the agent's raw LLM provider for a single structured call
    from llm.provider import LLMMessage
    provider = agent._provider
    messages = [
        LLMMessage(role="system", content=_PLAN_SYSTEM),
        LLMMessage(role="user",   content=prompt),
    ]
    resp = await provider.generate(messages, tools=None, temperature=0.1)
    raw  = resp.content.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON plan, using fallback: %.200s", raw)
        return {
            "proposed_actions":  [message],
            "context_summary":   {"topology": "", "current_state": "", "affected_devices": []},
            "expected_outcomes": {"positive": [], "warnings": ["Could not parse LLM response"]},
            "risk_level":        "medium",
        }


async def _llm_or_builder_playbook(
    agent, builder, plan: dict, plan_id: str
) -> tuple[str, list[str]]:
    """
    Generate Ansible YAML for the plan.

    Prefers PlaybookBuilder (Ansible-native) if available, else
    falls back to a raw LLM call.
    """
    affected = plan.get("affected_devices", [])
    message  = plan.get("message", "")

    if builder is not None:
        try:
            intent = {
                "description":  message,
                "target_hosts": affected,
                "vendor":       plan.get("vendor", "cisco_ios"),
                "feature":      plan.get("feature", "adhoc"),
                "params":       plan.get("params", {}),
            }
            rag_ctx = ""
            if agent:
                try:
                    rag_ctx = agent._context.build_full_context()
                except Exception:
                    pass

            playbook_yaml = await builder.build(
                intent=intent,
                rag_context=rag_ctx,
                run_id=plan_id,
            )
            return playbook_yaml, affected
        except Exception as exc:
            logger.warning("PlaybookBuilder failed, falling back to LLM: %s", exc)

    # LLM fallback
    from llm.provider import LLMMessage
    provider = agent._provider
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are a network automation engineer. "
                "Generate a valid Ansible playbook in YAML only — no prose, no markdown fences. "
                f"Target hosts: {affected}. "
                "Use cisco.ios.ios_config module for Cisco IOS configuration tasks."
            ),
        ),
        LLMMessage(role="user", content=f"Write an Ansible playbook to: {message}"),
    ]
    resp = await provider.generate(messages, tools=None, temperature=0.1)
    raw  = resp.content.strip()
    # Strip fences
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            stripped = part.lstrip("yaml\nYAML\n").strip()
            if stripped.startswith("---") or stripped.startswith("- name"):
                raw = stripped
                break
    return raw, affected


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/plan", response_model=PlanResponse)
async def plan_action(req: PlanRequest):
    """
    Phase-1: AI analyses the intent and returns a structured action plan.

    The frontend displays the plan for user review before execution.
    """
    agent = _require_agent()

    plan_data = await _llm_plan(agent, req.message, req.domain, req.context)

    # Store plan for phase-2 retrieval
    plan_id = str(uuid.uuid4())
    _plans[plan_id] = {
        "id":               plan_id,
        "message":          req.message,
        "domain":           req.domain,
        "context":          req.context.model_dump(),
        "affected_devices": plan_data.get("context_summary", {}).get("affected_devices", []),
        "risk_level":       plan_data.get("risk_level", "medium"),
        "created_at":       datetime.utcnow().isoformat(),
    }

    ctx_raw  = plan_data.get("context_summary", {})
    outs_raw = plan_data.get("expected_outcomes", {})

    return PlanResponse(
        plan_id=plan_id,
        mode="planning",
        context_summary=ContextSummary(
            topology=ctx_raw.get("topology", ""),
            current_state=ctx_raw.get("current_state", ""),
            affected_devices=ctx_raw.get("affected_devices", []),
        ),
        proposed_actions=plan_data.get("proposed_actions", [req.message]),
        expected_outcomes=ExpectedOutcomes(
            positive=outs_raw.get("positive", []),
            warnings=outs_raw.get("warnings", []),
        ),
        risk_level=plan_data.get("risk_level", "medium"),
    )


@router.post("/execute", response_model=ExecuteResponse)
async def execute_plan(req: ExecuteRequest):
    """
    Phase-2: Given a planId, generate (or revise/cancel) the Ansible playbook.

    action:
      "generate" — produce playbook from the stored plan
      "revise"   — regenerate with a different approach
      "cancel"   — discard the plan, return empty response
    """
    plan = _plans.get(req.plan_id)
    if not plan:
        raise HTTPException(404, f"Plan '{req.plan_id}' not found or expired")

    if req.action == "cancel":
        _plans.pop(req.plan_id, None)
        return ExecuteResponse(
            mode="execution",
            plan_id=req.plan_id,
            playbook=PlaybookPayload(),
            estimated_duration="N/A",
            rollback_plan=None,
        )

    agent = _require_agent()

    playbook_yaml, hosts = await _llm_or_builder_playbook(
        agent, _builder, plan, req.plan_id
    )

    # Rough task-count from YAML (just count '- name:' lines)
    task_count = playbook_yaml.count("- name:")
    pb_name    = f"remediate_{plan['domain']}_{req.plan_id[:8]}"

    # Estimate duration: ~30s per task, minimum 1 min
    minutes = max(1, task_count // 2)

    return ExecuteResponse(
        mode="execution",
        plan_id=req.plan_id,
        playbook=PlaybookPayload(
            name=pb_name,
            filename=f"{pb_name}.yml",
            content=playbook_yaml,
            hosts=hosts,
            tasks=[],
        ),
        estimated_duration=f"{minutes}-{minutes + 3} minutes",
        rollback_plan="Re-apply previous config snapshot or run rollback playbook.",
    )
