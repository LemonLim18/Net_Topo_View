"""
M13c: Incident Response Playbooks
====================================
Pre-defined automated response workflows for common threat types.
Each playbook defines a sequence of steps to execute when a specific
threat is detected.

A playbook can be:
- **Auto**: Executes immediately without human approval
- **Semi-auto**: Generates the config, but waits for approval before deploy
- **Manual**: Only alerts and provides investigation guidance

Usage::

    from security.response.playbooks import PlaybookEngine, get_playbook

    engine = PlaybookEngine()
    result = engine.execute(playbook_name="brute_force_response", event=event)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from security.models import SecurityEvent
from security.response.responder import IncidentResponder, ResponseAction

logger = logging.getLogger("security.playbooks")


@dataclass
class PlaybookStep:
    """A single step in a response playbook."""
    order: int
    action: str         # log | alert | generate_acl | block_ip | isolate_port | notify | escalate | generate_qos | apply_qos
    description: str
    auto_execute: bool = False
    config: str = ""    # generated config if applicable


@dataclass
class Playbook:
    """Definition of a response playbook."""
    name: str
    description: str
    trigger_event_types: List[str]
    severity_threshold: str     # minimum severity to trigger: critical | high | medium | low
    mode: str                   # auto | semi_auto | manual
    steps: List[PlaybookStep] = field(default_factory=list)


@dataclass
class PlaybookResult:
    """Result of executing a playbook."""
    playbook_name: str
    event_id: Optional[int]
    steps_executed: List[Dict[str, Any]]
    response_action: Optional[ResponseAction] = None
    status: str = "completed"  # completed | pending_approval | error


# ---------------------------------------------------------------------------
# Playbook Definitions
# ---------------------------------------------------------------------------

PLAYBOOKS: Dict[str, Playbook] = {
    "brute_force_response": Playbook(
        name="brute_force_response",
        description="Respond to brute force login attempts by blocking the source IP",
        trigger_event_types=["brute_force"],
        severity_threshold="high",
        mode="semi_auto",
        steps=[
            PlaybookStep(1, "log", "Log brute force detection to audit trail", auto_execute=True),
            PlaybookStep(2, "generate_acl", "Generate ACL to block source IP", auto_execute=True),
            PlaybookStep(3, "alert", "Send alert to security team", auto_execute=True),
            PlaybookStep(4, "block_ip", "Deploy ACL to edge devices (requires approval)", auto_execute=False),
        ],
    ),

    "port_scan_response": Playbook(
        name="port_scan_response",
        description="Respond to port scanning by rate-limiting and alerting",
        trigger_event_types=["port_scan"],
        severity_threshold="medium",
        mode="semi_auto",
        steps=[
            PlaybookStep(1, "log", "Log port scan detection", auto_execute=True),
            PlaybookStep(2, "generate_acl", "Generate rate-limit ACL for scanner IP", auto_execute=True),
            PlaybookStep(3, "alert", "Alert security team for investigation", auto_execute=True),
            PlaybookStep(4, "block_ip", "Deploy block ACL (requires approval)", auto_execute=False),
        ],
    ),

    "rogue_device_response": Playbook(
        name="rogue_device_response",
        description="Isolate unknown devices detected on the network",
        trigger_event_types=["rogue_device"],
        severity_threshold="high",
        mode="manual",
        steps=[
            PlaybookStep(1, "log", "Log rogue device detection", auto_execute=True),
            PlaybookStep(2, "alert", "Alert network security team — urgent", auto_execute=True),
            PlaybookStep(3, "isolate_port", "Shutdown connecting port (manual approval required)", auto_execute=False),
            PlaybookStep(4, "notify", "Notify NOC for physical investigation", auto_execute=False),
        ],
    ),

    "malware_response": Playbook(
        name="malware_response",
        description="Quarantine malware-infected hosts and block C2 communication",
        trigger_event_types=["malware"],
        severity_threshold="critical",
        mode="semi_auto",
        steps=[
            PlaybookStep(1, "log", "Log malware detection event", auto_execute=True),
            PlaybookStep(2, "generate_acl", "Generate ACL to block C2 destination", auto_execute=True),
            PlaybookStep(3, "isolate_port", "Move infected host to quarantine VLAN", auto_execute=False),
            PlaybookStep(4, "alert", "Escalate to SOC team", auto_execute=True),
            PlaybookStep(5, "notify", "Notify affected user / device owner", auto_execute=False),
        ],
    ),

    "suricata_alert_response": Playbook(
        name="suricata_alert_response",
        description="Respond to Suricata IDS alerts based on severity",
        trigger_event_types=["suricata"],
        severity_threshold="high",
        mode="semi_auto",
        steps=[
            PlaybookStep(1, "log", "Log Suricata alert", auto_execute=True),
            PlaybookStep(2, "generate_acl", "Generate block ACL for attacker IP", auto_execute=True),
            PlaybookStep(3, "alert", "Alert security team with alert details", auto_execute=True),
            PlaybookStep(4, "block_ip", "Deploy block rule (requires approval)", auto_execute=False),
        ],
    ),

    "config_tamper_response": Playbook(
        name="config_tamper_response",
        description="Respond to unauthorized configuration changes — rollback to last known good",
        trigger_event_types=["config_change"],
        severity_threshold="high",
        mode="manual",
        steps=[
            PlaybookStep(1, "log", "Log unauthorized config change", auto_execute=True),
            PlaybookStep(2, "alert", "Alert network engineering — config tamper suspected", auto_execute=True),
            PlaybookStep(3, "notify", "Generate config diff for review", auto_execute=True),
            PlaybookStep(4, "escalate", "Rollback via M4 pipeline (manual approval required)", auto_execute=False),
        ],
    ),

    "qos_remediation_response": Playbook(
        name="qos_remediation_response",
        description=(
            "Auto-remediate bandwidth congestion by deploying a 5-step QoS policy: "
            "Classify traffic → Mark with DSCP → Police/Shape heavy flows → "
            "Queue by priority → Schedule with LLQ for VoIP"
        ),
        trigger_event_types=["anomaly"],
        severity_threshold="high",
        mode="semi_auto",
        steps=[
            PlaybookStep(1, "log", "Log severe traffic anomaly (bandwidth spike)", auto_execute=True),
            PlaybookStep(2, "alert", "Alert Network Engineering: congestion detected on interface", auto_execute=True),
            PlaybookStep(3, "generate_qos",
                         "Generate 5-step QoS policy — protect VoIP (EF/LLQ), "
                         "prioritize Video (AF41), police Downloads (CS1, 50Mbps cap)",
                         auto_execute=True),
            PlaybookStep(4, "apply_qos",
                         "Deploy QoS policy to congested interface via M4 Deployment Pipeline (requires approval)",
                         auto_execute=False),
        ],
    ),
}


class PlaybookEngine:
    """Executes playbooks against security events."""

    def __init__(self):
        self._responder = IncidentResponder()

    def get_playbook(self, event_type: str) -> Optional[Playbook]:
        """Find the best playbook for an event type."""
        for pb in PLAYBOOKS.values():
            if event_type in pb.trigger_event_types:
                return pb
        return None

    def execute(
        self,
        playbook_name: str,
        event: SecurityEvent,
        vendor: str = "cisco_ios",
    ) -> PlaybookResult:
        """
        Execute a playbook against a security event.

        Args:
            playbook_name: Name of the playbook to execute.
            event: The triggering security event.
            vendor: Target device vendor for config generation.

        Returns:
            PlaybookResult with executed steps and generated response.
        """
        playbook = PLAYBOOKS.get(playbook_name)
        if not playbook:
            return PlaybookResult(
                playbook_name=playbook_name,
                event_id=event.id,
                steps_executed=[],
                status="error",
            )

        steps_executed = []
        response_action = None

        for step in playbook.steps:
            step_result = {"order": step.order, "action": step.action, "description": step.description}

            if step.auto_execute:
                step_result["status"] = "executed"

                # If this step generates config, use the responder
                if step.action in ("generate_acl", "block_ip", "isolate_port"):
                    response_action = self._responder.respond(event, vendor)
                    step_result["config_generated"] = bool(response_action.config_snippet)
                elif step.action == "generate_qos":
                    response_action = self._responder.respond_qos(event, vendor)
                    step_result["config_generated"] = bool(response_action.config_snippet)
            else:
                step_result["status"] = "pending_approval"

            steps_executed.append(step_result)

        return PlaybookResult(
            playbook_name=playbook_name,
            event_id=event.id,
            steps_executed=steps_executed,
            response_action=response_action,
            status="pending_approval" if playbook.mode != "auto" else "completed",
        )

    def list_playbooks(self) -> List[Dict[str, Any]]:
        """Return summary of all available playbooks."""
        return [
            {
                "name": pb.name,
                "description": pb.description,
                "trigger_types": pb.trigger_event_types,
                "mode": pb.mode,
                "steps": len(pb.steps),
            }
            for pb in PLAYBOOKS.values()
        ]
