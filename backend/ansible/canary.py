"""
Canary host selector.

Picks a small subset of least-critical devices to run a playbook against
before a wide rollout.  Canary devices are identified by:

  1. `canary: true` in their host_vars (inventory metadata).
  2. Hostnames containing the word 'canary', 'lab', or 'test'.
  3. Simple fallback: first N hosts alphabetically per vendor group.

Usage in pipeline:
    from ansible.canary import select_canary_hosts

    canary_hosts = select_canary_hosts(inventory, n=1)
    # Run playbook with limit=",".join(canary_hosts) first
    # Verify → then run remaining hosts
"""

import logging
from typing import Optional

logger = logging.getLogger("ansible.canary")

_CANARY_KEYWORDS = {"canary", "lab", "test", "dev", "poc"}


def select_canary_hosts(
    inventory: dict,
    n: int = 1,
    vendor_group: Optional[str] = None,
) -> list[str]:
    """
    Return up to `n` canary hosts from the inventory.

    Args:
        inventory:     Ansible inventory dict (from inventory.build_inventory()).
        n:             How many canary hosts to select.
        vendor_group:  If set, restrict to a specific vendor group
                       (e.g. "vendor_cisco_ios").

    Returns:
        List of hostnames to target for the canary run.
    """
    all_hosts: dict = inventory.get("all", {}).get("hosts", {})
    children:  dict = inventory.get("all", {}).get("children", {})

    candidate_pool: dict = {}

    if vendor_group and vendor_group in children:
        # Restrict to a single vendor group
        candidate_pool = children[vendor_group].get("hosts", {})
        # Enrich with full host vars
        candidate_pool = {h: all_hosts.get(h, {}) for h in candidate_pool}
    else:
        candidate_pool = all_hosts

    if not candidate_pool:
        logger.warning("Empty inventory — no canary hosts available")
        return []

    # Priority 1: explicit canary flag in host vars
    flagged = [
        h for h, v in candidate_pool.items()
        if v.get("canary") is True
    ]
    if flagged:
        selected = flagged[:n]
        logger.info("Canary selection (flagged): %s", selected)
        return selected

    # Priority 2: keywords in hostname
    keyword_matches = [
        h for h in candidate_pool
        if any(kw in h.lower() for kw in _CANARY_KEYWORDS)
    ]
    if keyword_matches:
        selected = keyword_matches[:n]
        logger.info("Canary selection (keyword match): %s", selected)
        return selected

    # Priority 3: first N hosts alphabetically
    selected = sorted(candidate_pool.keys())[:n]
    logger.info("Canary selection (alphabetical fallback): %s", selected)
    return selected


def remaining_hosts(inventory: dict, canary: list[str]) -> list[str]:
    """Return all hosts except the canary set."""
    all_hosts = list(inventory.get("all", {}).get("hosts", {}).keys())
    return [h for h in all_hosts if h not in canary]
