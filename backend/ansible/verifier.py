"""
Post-change verification via the existing Context-Injection RAG.

Queries the NetworkAgent (which holds live topology + telemetry in its context)
to confirm whether the desired state is now observed on the device.

The agent is NEVER modified — this only calls agent.chat() as a read-only probe.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("ansible.verifier")

# Structured questions per feature type
_VERIFY_QUESTIONS: dict[str, str] = {
    "vlan": (
        "Has VLAN {vlan_id} been successfully applied to interface {interface} "
        "on {host}? Is the interface in access mode and no-shutdown? "
        "Answer YES or NO and explain in one sentence."
    ),
    "ospf": (
        "Is OSPF neighbour adjacency on {host} still established after the recent "
        "configuration change? Any neighbour drops? Answer YES or NO."
    ),
    "bgp": (
        "Is BGP peer count on {host} unchanged after the recent change? "
        "Any sessions went down? Answer YES or NO."
    ),
    "interface": (
        "Is interface {interface} on {host} currently up/up after the configuration "
        "change? Answer YES or NO."
    ),
    "default": (
        "After the recent configuration change ({description}), confirm the desired "
        "state is now active on {host}. Answer YES or NO and explain briefly."
    ),
}


class PostChangeVerifier:
    """
    Uses the Context-Injection RAG (NetworkAgent) to verify post-change state.
    Adds no new logic to the agent — only calls .chat() with targeted questions.
    """

    def __init__(self, agent) -> None:
        # Accepts the existing NetworkAgent instance — type not imported to
        # avoid circular dependency.
        self._agent = agent

    async def verify(
        self,
        intent: dict,
        host: str,
    ) -> tuple[bool, str]:
        """
        Ask the agent whether the change converged successfully.

        Args:
            intent:  Change intent dict (description, feature, params).
            host:    Device hostname to query about.

        Returns:
            (passed: bool, explanation: str)
        """
        feature = intent.get("feature", "default")
        template = _VERIFY_QUESTIONS.get(feature, _VERIFY_QUESTIONS["default"])

        query = template.format(
            host=host,
            vlan_id=intent.get("params", {}).get("vlan_id", "?"),
            interface=intent.get("params", {}).get("interface", "?"),
            description=intent.get("description", "configuration change"),
        )

        try:
            response = await self._agent.chat(query)
            text = response.strip() if isinstance(response, str) else str(response)
            passed = text.upper().startswith("YES")
            logger.info(
                "Post-verify host=%s feature=%s passed=%s | %s",
                host, feature, passed, text[:200],
            )
            return passed, text
        except Exception as exc:
            msg = f"Post-change verification query failed: {exc}"
            logger.error(msg)
            return False, msg

    async def verify_all(
        self,
        intent:   dict,
        hosts:    list[str],
    ) -> dict[str, tuple[bool, str]]:
        """Run verification for multiple hosts in parallel."""
        tasks = {h: self.verify(intent, h) for h in hosts}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            host: (False, str(res)) if isinstance(res, Exception) else res
            for host, res in zip(tasks.keys(), results)
        }


def check_rag_state_matches(agent, intent: dict, host: str) -> bool:
    """
    Synchronous pre-flight check: ask the agent whether the desired state
    is ALREADY present so we can short-circuit to NO_OP.

    Runs in a new event loop if called from sync context (e.g. pipeline).
    """
    async def _ask():
        feature = intent.get("feature", "default")
        template = _VERIFY_QUESTIONS.get(feature, _VERIFY_QUESTIONS["default"])
        query = "PREFLIGHT STATE CHECK: " + template.format(
            host=host,
            vlan_id=intent.get("params", {}).get("vlan_id", "?"),
            interface=intent.get("params", {}).get("interface", "?"),
            description=intent.get("description", "planned change"),
        )
        try:
            response = await agent.chat(query)
            text = str(response).strip()
            return text.upper().startswith("YES")
        except Exception:
            return False  # if RAG fails, proceed with change

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't nest loops — schedule as a task (from async context use verify() directly)
            return False
        return loop.run_until_complete(_ask())
    except RuntimeError:
        return asyncio.run(_ask())
