"""
Poll a single network device using M1 drivers.

This replaces ``mini_nce.py:poll_device()`` with a version that uses
the Driver Factory (M1) instead of raw Paramiko SSH calls.

The return format is *deliberately* the same dict shape that the
legacy ``poll_device`` returns so the BFS engine builds an identical
topology JSON.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from drivers.factory import DriverFactory
from drivers.base import BaseDriver
from drivers.models import Neighbor, Interface, ARPEntry

logger = logging.getLogger("crawler.poll")

# Used by the BFS engine to detect loopback interfaces for mgmt_ip
RE_LOOPBACK = re.compile(r"^Loopback", re.IGNORECASE)


# -----------------------------------------------------------------------
# PollResult — typed wrapper around the legacy dict
# -----------------------------------------------------------------------

@dataclass
class PollResult:
    """Structured result from polling a single device."""
    hostname: Optional[str] = None
    down: bool = False
    interfaces: Dict[str, str] = field(default_factory=dict)
    lldp_summary: List[Dict] = field(default_factory=list)
    lldp_detail: List[Dict] = field(default_factory=list)
    arp_entries: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to the legacy dict format used by BFS engine."""
        d: Dict = {
            "hostname": self.hostname,
            "interfaces": self.interfaces,
            "lldp_summary": self.lldp_summary,
            "lldp_detail": self.lldp_detail,
            "arp_entries": self.arp_entries,
        }
        if self.down:
            d["down"] = True
        return d


def poll_device(
    host_ip: str,
    username: str,
    password: Optional[str],
    key_file: Optional[str] = None,
    driver_type: str = "cisco_ios",
    hop_chain: Optional[List[str]] = None,
    jump_host: Optional[Dict] = None,
) -> Dict:
    """
    Poll a single device using the M1 driver layer.

    Returns the **same dict shape** as ``mini_nce.py:poll_device()`` so
    the BFS engine can consume it identically::

        {
            "hostname": "R1",
            "interfaces": {"Ethernet0/0": "192.168.1.1", ...},
            "lldp_summary": [{"dev_id": "R2", "local_if": "Et0/0", ...}],
            "lldp_detail": [{"local_if": "Et0/0", "chassis": "...", ...}],
            "arp_entries": [{"ip": "...", "age": "...", "mac": "...", "intf": "..."}],
        }

    On failure, returns a dict with ``"down": True``.
    """
    driver: Optional[BaseDriver] = None
    try:
        driver = DriverFactory.create(
            driver_type,
            host=host_ip,
            username=username,
            password=password,
            key_file=key_file,
            hop_chain=hop_chain,
            jump_host=jump_host,
        )
        driver.connect()

        # --- Collect data via M1 driver ---
        hostname = driver.get_hostname()

        # Convert M1 Interface objects → legacy {name: ip} dict
        raw_interfaces: List[Interface] = driver.get_interfaces()
        ifaces: Dict[str, str] = {}
        for iface in raw_interfaces:
            if iface.ip_address:
                ifaces[iface.name] = iface.ip_address

        # Convert M1 Neighbor objects → legacy lldp_summary + lldp_detail
        raw_neighbors: List[Neighbor] = driver.get_neighbors()
        lldp_summary: List[Dict] = []
        lldp_detail: List[Dict] = []
        for n in raw_neighbors:
            lldp_summary.append({
                "dev_id": n.remote_device,
                "local_if": n.local_interface,
                "remote_port": n.remote_interface,
            })
            lldp_detail.append({
                "local_if": n.local_interface,
                "chassis": None,
                "remote_port": n.remote_interface,
                "system_name": n.remote_device,
                "remote_mgmt": n.remote_ip if n.remote_ip else None,
            })

        # Convert M1 ARPEntry objects → legacy arp dicts
        raw_arp: List[ARPEntry] = driver.get_arp_table()
        arp_entries: List[Dict] = []
        for a in raw_arp:
            arp_entries.append({
                "ip": a.ip,
                "age": a.age,
                "mac": a.mac,
                "intf": a.interface,
            })

        result = PollResult(
            hostname=hostname or host_ip,
            interfaces=ifaces,
            lldp_summary=lldp_summary,
            lldp_detail=lldp_detail,
            arp_entries=arp_entries,
        )
        logger.info("Polled %s (%s) — %d ifaces, %d neighbors, %d ARP",
                     hostname, host_ip, len(ifaces),
                     len(lldp_summary), len(arp_entries))
        return result.to_dict()

    except Exception as e:
        logger.warning("poll_device %s failed: %s", host_ip, e)
        return PollResult(hostname=host_ip, down=True).to_dict()

    finally:
        if driver is not None:
            try:
                driver.disconnect()
            except Exception:
                pass
