"""
M14: Topology Repository
Manages link (adjacency) data and bridges the gap between
the persistent link table and the in-memory topology cache.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..models import Link
from .base import BaseRepository

logger = logging.getLogger("db.topology_repo")


class TopologyRepository(BaseRepository):
    """Domain-level operations on topology links and the live cache."""

    # ---------------------------------------------------------
    # Persistent link helpers
    # ---------------------------------------------------------
    def save_link(self, link: Link) -> int:
        return self._db.upsert_link(link)

    def get_links_for(self, device_id: int) -> List[Link]:
        return self._db.get_links_for_device(device_id)

    def get_all_links(self) -> List[Link]:
        return self._db.get_all_links()

    # ---------------------------------------------------------
    # Bulk sync from crawl  (edge links → persistent links)
    # ---------------------------------------------------------
    def sync_links_from_crawl(self, topo_nodes: Dict[str, dict]) -> None:
        """
        Walk the ``nodes`` dict from a crawl result and upsert
        every discovered link into the persistent store.

        Requires that devices already exist (call
        ``DeviceRepository.sync_from_crawl`` first).
        """
        saved = 0
        for hostname, data in topo_nodes.items():
            src_dev = self._db.get_device_by_hostname(hostname)
            if not src_dev:
                continue

            for local_if, edge_info in data.get("edgeLink", {}).items():
                remote_hostname = edge_info.get("remote_node")
                if not remote_hostname:
                    continue

                dst_dev = self._db.get_device_by_hostname(remote_hostname)
                if not dst_dev:
                    continue

                # Resolve interface ids (best effort)
                src_ifaces = self._db.get_interfaces_for_device(src_dev.id)
                src_if_id = None
                for iface in src_ifaces:
                    if iface.name == local_if:
                        src_if_id = iface.id
                        break

                remote_port = edge_info.get("remote_interface")
                dst_if_id = None
                if remote_port:
                    dst_ifaces = self._db.get_interfaces_for_device(dst_dev.id)
                    for iface in dst_ifaces:
                        if iface.name == remote_port:
                            dst_if_id = iface.id
                            break

                link = Link(
                    src_device_id=src_dev.id,
                    src_interface_id=src_if_id,
                    dst_device_id=dst_dev.id,
                    dst_interface_id=dst_if_id,
                    discovery_protocol="lldp",
                )
                self.save_link(link)
                saved += 1

        logger.info("Synced %d links from crawl into persistent store", saved)

    # ---------------------------------------------------------
    # Real-time cache proxies
    # ---------------------------------------------------------
    def update_live_topology(self, topo_dict: Dict[str, Any]) -> None:
        """Push a fresh crawl result into the in-memory cache."""
        self._db.update_topology(topo_dict)

    def get_live_topology(self) -> Dict[str, Any]:
        """Read the current in-memory topology (for the frontend)."""
        return self._db.get_topology()
