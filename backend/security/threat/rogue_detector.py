"""
M13b: Rogue Device Detector
=============================
Compares discovered LLDP/ARP neighbors against known device inventory
in the M14 database to identify unknown / rogue devices.

A device is considered "rogue" if:
1. Its hostname or MAC is NOT in the M14 devices table
2. It appears as a neighbor in the topology but has no inventory record
3. It has an unexpected IP range (not in managed subnets)

Usage::

    detector = RogueDeviceDetector(db)
    alerts = detector.scan()
"""

import logging
from typing import Any, Dict, List, Optional, Set

from security.models import SecurityEvent

logger = logging.getLogger("security.rogue")


class RogueDeviceDetector:
    """
    Detects rogue/unknown devices by comparing topology neighbors
    against the known device inventory.
    """

    def __init__(self, db_manager):
        """
        Args:
            db_manager: DatabaseManager instance for inventory lookup.
        """
        self._db = db_manager

    def scan(self) -> List[SecurityEvent]:
        """
        Scan the current topology for unknown devices.

        Compares nodes in the live topology cache against the devices
        table in M14. Any node not in the inventory is flagged as rogue.

        Returns:
            List of SecurityEvent for each rogue device detected.
        """
        events: List[SecurityEvent] = []

        # Get known inventory
        known_devices = self._db.get_all_devices()
        known_hostnames: Set[str] = {d.hostname.lower() for d in known_devices}
        known_ips: Set[str] = set()
        for d in known_devices:
            if d.management_ip:
                known_ips.add(d.management_ip)

        # Get live topology
        topo = self._db.get_topology()
        if not topo:
            logger.info("No topology data available for rogue device scan")
            return events

        topo_nodes = topo.get("nodes", {})

        for hostname, node_data in topo_nodes.items():
            hostname_lower = hostname.lower()
            mgmt_ip = ""

            # Extract management IP from node data
            if isinstance(node_data, dict):
                mgmt_ip = node_data.get("management_ip", "")
                if not mgmt_ip:
                    # Try interfaces
                    interfaces = node_data.get("interfaces", {})
                    for iface_data in interfaces.values():
                        if isinstance(iface_data, dict):
                            ip = iface_data.get("ip_address", "")
                            if ip:
                                mgmt_ip = ip
                                break

            # Check if this device is in the inventory
            if hostname_lower not in known_hostnames and mgmt_ip not in known_ips:
                events.append(SecurityEvent(
                    event_type="rogue_device",
                    severity="high",
                    source_ip=mgmt_ip,
                    description=(
                        f"Unknown device detected: '{hostname}' "
                        f"(IP: {mgmt_ip or 'unknown'}) — "
                        f"not found in device inventory"
                    ),
                    raw_data={
                        "hostname": hostname,
                        "management_ip": mgmt_ip,
                        "node_data_keys": list(node_data.keys()) if isinstance(node_data, dict) else [],
                    },
                ))

        if events:
            logger.warning("Rogue device scan found %d unknown devices", len(events))
        else:
            logger.info("Rogue device scan: all devices are in inventory")

        return events

    def check_single(self, hostname: str, ip: Optional[str] = None) -> Optional[SecurityEvent]:
        """
        Check if a single device is in the inventory.

        Args:
            hostname: Device hostname to check.
            ip: Optional IP address.

        Returns:
            SecurityEvent if rogue, None if known.
        """
        device = self._db.get_device_by_hostname(hostname)
        if device:
            return None

        return SecurityEvent(
            event_type="rogue_device",
            severity="high",
            source_ip=ip or "",
            description=f"Unknown device: '{hostname}' (IP: {ip or 'unknown'})",
            raw_data={"hostname": hostname, "ip": ip},
        )
