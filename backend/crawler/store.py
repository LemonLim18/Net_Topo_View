"""
Bridge between the Crawler (M2) and the Database Layer (M14).

After a crawl completes, this module:
1.  Updates the in-memory topology cache in ``DatabaseManager`` (live map only).
2.  Does **not** sync discovered devices or links to the persistent DB — Device
    Inventory is manually registered (and/or NetBox sync) only; the crawler
    must not create or update devices in the devices table.
3.  Logs the crawl action in the ``audit_log``.
"""

import logging
from typing import Dict

from db.manager import DatabaseManager
from db.models import AuditLogEntry

logger = logging.getLogger("crawler.store")


def save_crawl_result(
    db_manager: DatabaseManager,
    topo_dict: Dict,
    triggered_by: str = "system",
) -> None:
    """
    Persist a crawl result to the in-memory topology cache only.

    Device Inventory is not modified: devices and links are not written to the
    persistent DB. Only the live topology cache is updated so the topology map
    can show current reachable nodes. Inventory remains manually registered
    (and/or NetBox sync) only.

    Args:
        db_manager:    The shared ``DatabaseManager`` instance.
        topo_dict:     The topology dict produced by ``crawl_topology()``.
        triggered_by:  Who/what triggered this crawl (for audit log).
    """
    # ----- 1. Update live cache only (frontend topology map) -----
    db_manager.update_topology(topo_dict)
    nodes = topo_dict.get("nodes", {})
    logger.info(
        "Live topology cache updated (%d nodes). Device Inventory unchanged (manual/NetBox only).",
        len(nodes),
    )

    # ----- 2. Do not sync devices to persistent SQL -----
    # Device Inventory is manually registered (and NetBox sync) only;
    # the live topology crawler must not create or update the devices table.

    # ----- 3. Do not sync links to persistent SQL -----
    # Links in the DB represent planned/manual topology; crawler data stays in cache only.

    # ----- 4. Audit log -----
    try:
        db_manager.log_action(AuditLogEntry(
            user_id=triggered_by,
            action="topology_crawl",
            target="network",
            details=f"Crawled {len(nodes)} nodes (live cache only; inventory unchanged)",
        ))
    except Exception as e:
        logger.warning("Failed to write audit log: %s", e)
