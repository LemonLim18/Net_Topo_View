"""
Huawei VRP SSH Driver.

Implements CLI command mapping and output parsing for Huawei VRP devices.
Uses Netmiko's 'huawei_vrp' device type for reliable SSH handling.

Key differences from Cisco IOS:
    - Commands use 'display' instead of 'show'
    - Config mode: 'system-view' / 'return' (not 'configure terminal' / 'end')
    - Undo syntax: 'undo <cmd>' (not 'no <cmd>')
    - Paging: 'screen-length 0 temporary'
    - Hostname: 'sysname' (not 'hostname')
"""

import re
import logging
from typing import List, Dict, Tuple, Optional

from drivers.ssh_driver import SSHDriver
from drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    InterfaceStatus,
    RouteProtocol,
    Vendor,
)

logger = logging.getLogger("drivers.huawei_vrp")


# ---------------------------------------------------------------------------
# Regexes for Huawei VRP output
# ---------------------------------------------------------------------------

RE_SYSNAME = re.compile(
    r"^\s*sysname\s+(?P<name>\S+)", re.IGNORECASE | re.MULTILINE
)

# display ip interface brief:
# Interface                         IP Address/Mask      Physical   Protocol
# GigabitEthernet0/0/0              10.1.1.1/24          up         up
RE_VRP_IF_BRIEF = re.compile(
    r"^(?P<intf>\S+)\s+"
    r"(?P<ip>\d+\.\d+\.\d+\.\d+)"
    r"(?:/(?P<mask>\d+))?\s+"
    r"(?P<physical>\S+)\s+"
    r"(?P<protocol>\S+)",
    re.IGNORECASE,
)

# Simpler fallback: interfaces with "unassigned" or no IP
RE_VRP_IF_BRIEF_SIMPLE = re.compile(
    r"^(?P<intf>\S+)\s+(?P<ip>\S+)\s+(?P<physical>\S+)\s+(?P<protocol>\S+)"
)

# display lldp neighbor brief:
# Local Intf    Neighbor Dev         Neighbor Intf
# GE0/0/1       R2                    GE0/0/1
RE_VRP_LLDP = re.compile(
    r"^(?P<local_if>\S+)\s+(?P<neighbor>\S+)\s+(?P<remote_if>\S+)",
    re.IGNORECASE,
)

# display arp:
# IP Address      MAC Address     VLAN  Interface       Aging
# 10.1.1.2        a0b1-c2d3-e4f5  --    GE0/0/1         20
RE_VRP_ARP = re.compile(
    r"^(?P<ip>\d+\.\d+\.\d+\.\d+)\s+"
    r"(?P<mac>[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})\s+"
    r"(?P<vlan>\S+)\s+"
    r"(?P<intf>\S+)\s*"
    r"(?P<age>\S*)",
    re.IGNORECASE,
)

# display ip routing-table:
# Route Flags: R - relay, D - download to fib
# Destination/Mask    Proto   Pre  Cost   Flags  NextHop         Interface
# 10.0.0.0/24         OSPF    10   2             10.1.1.2        GE0/0/1
RE_VRP_ROUTE = re.compile(
    r"^(?P<prefix>\d+\.\d+\.\d+\.\d+/\d+)\s+"
    r"(?P<proto>\S+)\s+"
    r"(?P<pref>\d+)\s+"
    r"(?P<cost>\d+)\s+"
    r"(?P<flags>\S*)\s+"
    r"(?P<next_hop>\d+\.\d+\.\d+\.\d+|\S+)\s+"
    r"(?P<intf>\S+)",
    re.IGNORECASE,
)

# display version:
RE_VRP_MODEL = re.compile(
    r"HUAWEI\s+(?P<model>\S+)\s+uptime", re.IGNORECASE
)
RE_VRP_VERSION = re.compile(
    r"VRP.*?Version\s+(?P<version>\S+)", re.IGNORECASE
)
RE_VRP_UPTIME = re.compile(
    r"uptime is\s+(?P<uptime>.+)", re.IGNORECASE
)

# Protocol mapping
VRP_PROTO_MAP = {
    "direct": RouteProtocol.CONNECTED,
    "static": RouteProtocol.STATIC,
    "ospf": RouteProtocol.OSPF,
    "bgp": RouteProtocol.BGP,
    "isis": RouteProtocol.ISIS,
    "rip": RouteProtocol.RIP,
}


def normalize_huawei_mac(mac: str) -> str:
    """Convert Huawei MAC (a0b1-c2d3-e4f5) to standard (a0:b1:c2:d3:e4:f5)."""
    clean = mac.replace("-", "").lower()
    if len(clean) == 12:
        return ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return mac.lower()


# ---------------------------------------------------------------------------
# HuaweiVRPDriver
# ---------------------------------------------------------------------------

class HuaweiVRPDriver(SSHDriver):
    """
    Driver for Huawei VRP devices via SSH.

    Can use either Paramiko (inherited) or Netmiko for connections.
    Netmiko's 'huawei_vrp' device type is recommended for production
    as it handles VRP quirks automatically.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
        port: int = 22,
        hop_chain: Optional[List[str]] = None,
        jump_host: Optional[Dict] = None,
    ):
        super().__init__(
            host=host,
            username=username,
            vendor=Vendor.HUAWEI_VRP,
            password=password,
            key_file=key_file,
            port=port,
            hop_chain=hop_chain,
            jump_host=jump_host,
        )

    # ------------------------------------------------------------------
    # SSHDriver abstract methods
    # ------------------------------------------------------------------

    def _get_disable_paging_command(self) -> str:
        return "screen-length 0 temporary"

    def _get_config_mode_commands(self) -> Tuple[str, str]:
        return ("system-view", "return")

    def _get_show_commands(self) -> Dict[str, str]:
        return {
            "hostname": "display current-configuration | include sysname",
            "interfaces": "display ip interface brief",
            "neighbors_summary": "display lldp neighbor brief",
            "neighbors_detail": "display lldp neighbor",
            "arp": "display arp",
            "routes": "display ip routing-table",
            "version": "display version",
            "running_config": "display current-configuration",
        }

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_hostname(self, raw_output: str) -> str:
        """Extract sysname from config output."""
        m = RE_SYSNAME.search(raw_output)
        if m:
            return m.group("name")

        # Fallback: try prompt
        for line in reversed(raw_output.splitlines()):
            line = line.strip()
            if line.startswith("<") and line.endswith(">"):
                return line.strip("<>")
            if line.startswith("[") and line.endswith("]"):
                return line.strip("[]")

        return "unknown"

    def _parse_interfaces(self, raw_output: str) -> List[Interface]:
        """Parse 'display ip interface brief' output."""
        interfaces = []

        for line in raw_output.splitlines():
            line = line.strip()
            if not line or "Interface" in line and "IP Address" in line:
                continue

            m = RE_VRP_IF_BRIEF.match(line)
            if m:
                intf_name = m.group("intf")
                ip = m.group("ip")
                mask = m.group("mask") or ""
                physical = m.group("physical").lower()
                protocol = m.group("protocol").lower()

                if "admin" in physical or "*down" in physical:
                    status = InterfaceStatus.ADMIN_DOWN
                elif physical == "up" and protocol == "up":
                    status = InterfaceStatus.UP
                else:
                    status = InterfaceStatus.DOWN

                interfaces.append(Interface(
                    name=intf_name,
                    ip_address=ip if re.match(r"\d+\.\d+\.\d+\.\d+", ip) else "",
                    subnet_mask=mask,
                    status=status,
                ))
                continue

            # Fallback simpler match
            m2 = RE_VRP_IF_BRIEF_SIMPLE.match(line)
            if m2:
                interfaces.append(Interface(
                    name=m2.group("intf"),
                    ip_address=m2.group("ip") if re.match(r"\d+\.\d+\.\d+\.\d+", m2.group("ip")) else "",
                    status=InterfaceStatus.UNKNOWN,
                ))

        return interfaces

    def _parse_neighbors(self, raw_output: str) -> List[Neighbor]:
        """Parse 'display lldp neighbor brief' output."""
        neighbors = []
        in_lldp_section = False

        for line in raw_output.splitlines():
            line = line.strip()

            # LLDP table header typically contains "Local Intf"
            if "Local" in line and ("Neighbor" in line or "Intf" in line):
                in_lldp_section = True
                continue

            if not in_lldp_section or not line:
                continue

            m = RE_VRP_LLDP.match(line)
            if m:
                neighbors.append(Neighbor(
                    local_interface=m.group("local_if"),
                    remote_device=m.group("neighbor"),
                    remote_interface=m.group("remote_if"),
                    discovery_protocol="lldp",
                ))

        return neighbors

    def _parse_routing_table(self, raw_output: str) -> List[Route]:
        """Parse 'display ip routing-table' output."""
        routes = []

        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue

            m = RE_VRP_ROUTE.match(line)
            if m:
                proto_str = m.group("proto").lower()
                protocol = VRP_PROTO_MAP.get(proto_str, RouteProtocol.UNKNOWN)

                routes.append(Route(
                    prefix=m.group("prefix"),
                    next_hop=m.group("next_hop"),
                    protocol=protocol,
                    administrative_distance=int(m.group("pref")),
                    metric=int(m.group("cost")),
                    interface=m.group("intf"),
                ))

        return routes

    def _parse_arp_table(self, raw_output: str) -> List[ARPEntry]:
        """Parse 'display arp' output."""
        entries = []
        for line in raw_output.splitlines():
            m = RE_VRP_ARP.match(line)
            if m:
                entries.append(ARPEntry(
                    ip=m.group("ip"),
                    mac=normalize_huawei_mac(m.group("mac")),
                    interface=m.group("intf"),
                    age=m.group("age") or "",
                ))
        return entries

    def _parse_device_info(self, raw_output: str) -> DeviceInfo:
        """Parse 'display version' output."""
        hostname = self._parse_hostname(raw_output)
        model = ""
        os_version = ""
        uptime = ""

        m = RE_VRP_MODEL.search(raw_output)
        if m:
            model = m.group("model")

        m = RE_VRP_VERSION.search(raw_output)
        if m:
            os_version = m.group("version")

        m = RE_VRP_UPTIME.search(raw_output)
        if m:
            uptime = m.group("uptime").strip()

        return DeviceInfo(
            hostname=hostname,
            vendor=Vendor.HUAWEI_VRP,
            model=model,
            os_version=os_version,
            uptime=uptime,
            management_ip=self.host,
        )
