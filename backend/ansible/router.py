"""
Execution routing decision tree.

Given a change intent and device metadata, returns the best
execution strategy:

  ANSIBLE_NATIVE  — vendor resource modules (idempotent, structured diff)
  ANSIBLE_CLI     — Ansible network_cli / raw (no modules, but still AWX/runner)
  SSH_FALLBACK    — existing Netmiko/Paramiko driver (hop-chain or unreachable)
  NO_OP           — RAG confirms state already converged; skip entirely

Does NOT execute anything — only returns an ExecutionPlan.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("ansible.router")


class ExecutionAction(str, Enum):
    ANSIBLE_NATIVE = "ansible_native"
    ANSIBLE_CLI    = "ansible_cli"
    SSH_FALLBACK   = "ssh_fallback"
    NO_OP          = "no_op"


@dataclass
class ExecutionPlan:
    action:   ExecutionAction
    reason:   str = ""
    playbook: Optional[str] = None          # YAML string — set for ANSIBLE_* actions
    commands: list[str]      = field(default_factory=list)  # set for SSH_FALLBACK
    driver_type: str         = "cisco_ios"  # for SSH_FALLBACK


# ---------------------------------------------------------------------------
# Vendor capability tables
# ---------------------------------------------------------------------------

# Vendors that have native Ansible resource modules
_NATIVE_MODULE_VENDORS: set[str] = {
    "cisco_ios", "cisco_nxos", "cisco_xr", "juniper_junos",
}

# Vendors that work with Ansible network_cli but have no resource modules
_CLI_VENDORS: set[str] = {
    "huawei_vrp", "aruba",
}

# Vendors that require hop-chain SSH (BFS traversal) — always SSH fallback
_HOPCHAIN_ONLY_VENDORS: set[str] = set()  # extend as needed


def route_execution(
    intent: dict,
    device_vars: dict,
    rag_state_matches: bool = False,
) -> ExecutionPlan:
    """
    Determine the best execution path for a change intent.

    Args:
        intent:             Change intent dict with keys:
                              description, target_hosts, vendor, feature,
                              params, rendered_commands (for SSH fallback)
        device_vars:        Host vars dict from inventory.py (nettopo_vendor,
                              nettopo_has_modules, nettopo_hop_chain)
        rag_state_matches:  True if Context-Injection RAG reports the desired
                              state is already converged → returns NO_OP.

    Returns:
        ExecutionPlan with the chosen action and reason.
    """

    # ── Gate 0: Idempotency check via Context-Injection RAG ──────────────────
    if rag_state_matches:
        return ExecutionPlan(
            action=ExecutionAction.NO_OP,
            reason="Context-Injection RAG confirms desired state already converged — skipping",
        )

    vendor: str = (
        device_vars.get("nettopo_vendor")
        or intent.get("vendor", "")
    ).lower()

    hop_chain: list = device_vars.get("nettopo_hop_chain") or []
    has_modules: bool = device_vars.get("nettopo_has_modules", False) or (vendor in _NATIVE_MODULE_VENDORS)

    # ── Gate 1: Hop-chain devices cannot be reached by Ansible controller ────
    if hop_chain:
        return ExecutionPlan(
            action=ExecutionAction.SSH_FALLBACK,
            reason=f"Device requires hop-chain via {hop_chain} — Ansible cannot reach it directly",
            commands=intent.get("rendered_commands", []),
            driver_type=vendor,
        )

    # ── Gate 2: Vendor not in any Ansible support list ───────────────────────
    if vendor not in _NATIVE_MODULE_VENDORS and vendor not in _CLI_VENDORS:
        logger.warning(
            "Vendor '%s' has no Ansible support profile — routing to SSH fallback", vendor
        )
        return ExecutionPlan(
            action=ExecutionAction.SSH_FALLBACK,
            reason=f"No Ansible support registered for vendor '{vendor}'",
            commands=intent.get("rendered_commands", []),
            driver_type=vendor,
        )

    # ── Gate 3: Native module path (preferred) ────────────────────────────────
    if has_modules:
        return ExecutionPlan(
            action=ExecutionAction.ANSIBLE_NATIVE,
            reason=f"Vendor '{vendor}' has native resource modules — using idempotent module path",
        )

    # ── Gate 4: Ansible CLI path (network_cli, no resource modules) ──────────
    return ExecutionPlan(
        action=ExecutionAction.ANSIBLE_CLI,
        reason=f"Vendor '{vendor}' supported via network_cli connection plugin",
    )
