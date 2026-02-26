"""
Bridge between the Crawler (M2) and the Database Layer (M14).

After a crawl completes, this module:
1.  Updates the in-memory topology cache in ``DatabaseManager``
2.  Syncs discovered devices to the persistent ``devices`` table
3.  Syncs discovered links to the persistent ``links`` table
4.  Logs the crawl action in the ``audit_log``
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from db.manager import DatabaseManager
from db.models import Device, AuditLogEntry
from db.repositories.device_repo import DeviceRepository
from db.repositories.topology_repo import TopologyRepository

logger = logging.getLogger("crawler.store")

TZ_UTC8 = timezone(timedelta(hours=8))


def save_crawl_result(
    db_manager: DatabaseManager,
    topo_dict: Dict,
    triggered_by: str = "system",
) -> None:
    """
    Persist a crawl result to **both** the in-memory cache and SQL.

    Args:
        db_manager:    The shared ``DatabaseManager`` instance.
        topo_dict:     The topology dict produced by ``crawl_topology()``.
        triggered_by:  Who/what triggered this crawl (for audit log).
    """
    # ----- 1. Update live cache (fastest path for frontend) -----
    db_manager.update_topology(topo_dict)
    logger.info("Live topology cache updated (%d nodes)",
                len(topo_dict.get("nodes", {})))

    # ----- 2. Sync devices to persistent SQL -----
    device_repo = DeviceRepository(db_manager)
    nodes = topo_dict.get("nodes", {})

    crawl_devices = []
    for hostname, node_data in nodes.items():
        if not isinstance(node_data, dict):
            continue
        status = "down" if node_data.get("down") else "active"
        crawl_devices.append(Device(
            hostname=hostname,
            management_ip=node_data.get("mgmt_ip"),
            vendor=node_data.get("vendor", "cisco_ios"),
            model=node_data.get("model"),
            os_version=node_data.get("os_version"),
            serial_number=node_data.get("serial"),
            status=status,
            last_polled=datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds"),
        ))

    if crawl_devices:
        for dev in crawl_devices:
            device_repo.save(dev)
        logger.info("Synced %d devices to persistent DB", len(crawl_devices))

    # ----- 3. Sync links to persistent SQL -----
    topo_repo = TopologyRepository(db_manager)
    crawl_links = []
    for hostname, node_data in nodes.items():
        if not isinstance(node_data, dict):
            continue
        edge_links = node_data.get("edgeLink", {})
        if not isinstance(edge_links, dict):
            continue
        for local_if, link_info in edge_links.items():
            if not isinstance(link_info, dict):
                continue
            remote_node = link_info.get("remote_node")
            if remote_node:
                crawl_links.append({
                    "src": hostname,
                    "src_interface": local_if,
                    "dst": remote_node,
                    "dst_interface": link_info.get("remote_interface"),
                })

    if crawl_links:
        topo_repo.sync_links_from_crawl(crawl_links)
        logger.info("Synced %d links to persistent DB", len(crawl_links))

    # ----- 4. Audit log -----
    try:
        db_manager.log_action(AuditLogEntry(
            user_id=triggered_by,
            action="topology_crawl",
            target="network",
            details=f"Crawled {len(nodes)} nodes, {len(crawl_links)} links",
        ))
    except Exception as e:
        logger.warning("Failed to write audit log: %s", e)
