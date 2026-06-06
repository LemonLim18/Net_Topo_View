"""
M14: Device Repository
Higher-level helpers for managing devices and their interfaces.
Delegates actual SQL to DatabaseManager but adds domain logic
(e.g. bulk sync from a crawl result, lookup by IP).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..models import Device, Interface
from .base import BaseRepository

logger = logging.getLogger("db.device_repo")


class DeviceRepository(BaseRepository):
    """Domain-level operations on the device inventory."""

    # ---------------------------------------------------------
    # Lookups
    # ---------------------------------------------------------
    def find_by_hostname(self, hostname: str) -> Optional[Device]:
        return self._db.get_device_by_hostname(hostname)

    def find_by_ip(self, ip: str) -> Optional[Device]:
        """Search inventory for a device with the given management IP."""
        for dev in self._db.get_all_devices():
            if dev.management_ip == ip:
                return dev
        return None

    def list_all(self) -> List[Device]:
        return self._db.get_all_devices()

    # ---------------------------------------------------------
    # Write helpers
    # ---------------------------------------------------------
    def save(self, device: Device) -> int:
        """Insert or update a single device. Returns its row id."""
        return self._db.upsert_device(device)

    def save_interface(self, iface: Interface) -> int:
        return self._db.upsert_interface(iface)

    def get_interfaces(self, device_id: int) -> List[Interface]:
        return self._db.get_interfaces_for_device(device_id)

    # ---------------------------------------------------------
    # Bulk sync  (used after a crawl)
    # ---------------------------------------------------------
    def sync_from_crawl(self, topo_nodes: Dict[str, dict]) -> None:
        """
        Accept the ``nodes`` dict from a crawl result and upsert
        every device + its interfaces into the persistent store.

        ``topo_nodes`` has the shape::

            { "R1": { "interfaceList": [{"Gi0/0": "10.0.0.1", ...}],
                       "mgmt_ip": "10.0.0.1", ... }, ... }
        """
        for hostname, data in topo_nodes.items():
            device = Device(
                hostname=hostname,
                management_ip=data.get("mgmt_ip"),
                status="up" if not data.get("down") else "down",
            )
            dev_id = self.save(device)

            # Sync interfaces
            iface_dict = {}
            iface_list = data.get("interfaceList", [{}])
            if iface_list and isinstance(iface_list[0], dict):
                iface_dict = iface_list[0]

            for if_name, ip_addr in iface_dict.items():
                iface = Interface(
                    device_id=dev_id,
                    name=if_name,
                    ip_address=ip_addr if isinstance(ip_addr, str) else None,
                    status="up",
                )
                self.save_interface(iface)

        logger.info("Synced %d devices from crawl into persistent store",
                     len(topo_nodes))

    def remove(self, device_id: int) -> bool:
        """Delete a device (cascades to interfaces and links)."""
        return self._db.delete_device(device_id)
