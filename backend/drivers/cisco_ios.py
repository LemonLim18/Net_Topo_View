"""
Cisco IOS SSH Driver.

Implements all parsing logic for Cisco IOS CLI output. The regex patterns
and parsing functions are extracted from the proven mini_nce.py crawler.

Supported commands:
    show running-config | include ^hostname
    show ip interface brief
    show lldp neighbor
    show lldp neighbors detail
    show ip arp
    show ip route
    show version
"""

import re
import logging
from typing import List, Dict, Tuple, Optional

from backend.drivers.ssh_driver import SSHDriver
from backend.drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    InterfaceStatus,
    RouteProtocol,
    Vendor,
)

logger = logging.getLogger("drivers.cisco_ios")


# ---------------------------------------------------------------------------
# Regexes (extracted from mini_nce.py)
# ---------------------------------------------------------------------------

RE_HOSTNAME = re.compile(
    r"^\s*hostname\s+(?P<h>\S+)", re.IGNORECASE | re.MULTILINE
)

RE_IF_BRIEF_LINE = re.compile(
    r"^(?P<intf>\S+)\s+(?P<ip>\d+\.\d+\.\d+\.\d+|\S+)\s+"
    r"(?P<ok>\S+)\s+(?P<method>\S+)\s+(?P<status>\S.*\S)\s+(?P<protocol>\S+)\s*$"
)

RE_IF_BRIEF_SIMPLE = re.compile(
    r"^(?P<intf>\S+)\s+(?P<ip>\d+\.\d+\.\d+\.\d+|\S+)\s+"
)

RE_LLDP_SUM_LINE = re.compile(
    r"^(?P<dev>\S+)\s+(?P<local_if>\S+)\s+\d+\s+\S+\s+(?P<port_id>\S+)",
    re.IGNORECASE,
)

RE_LLDP_DETAIL_SYSTEM_NAME = re.compile(
    r"System Name:\s*(?P<name>\S+)", re.IGNORECASE
)
RE_LLDP_DETAIL_LOCAL_INTF = re.compile(
    r"Local Intf:\s*(?P<intf>\S+)", re.IGNORECASE
)
RE_LLDP_DETAIL_PORT_ID = re.compile(
    r"Port id:\s*(?P<port>\S+)", re.IGNORECASE
)
RE_LLDP_DETAIL_MGMT_ADDR = re.compile(
    r"IP:\s*(?P<ip>\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
)

RE_ARP_LINE = re.compile(
    r"^\s*Internet\s+(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<age>\S+)\s+"
    r"(?P<mac>[\da-fA-F\.]+)\s+\S+\s+(?P<intf>\S+)",
    re.IGNORECASE,
)

RE_ROUTE_LINE = re.compile(
    r"^(?P<code>[A-Z\*]+)\s+"
    r"(?P<prefix>\d+\.\d+\.\d+\.\d+)"
    r"(?:/(?P<mask_len>\d+))?"
    r"(?:\s+(?P<subnet_mask>\d+\.\d+\.\d+\.\d+))?"
    r".*?(?:via\s+(?P<next_hop>\d+\.\d+\.\d+\.\d+))?"
    r".*?(?:(?P<intf>[A-Za-z]+[\d/\.]+))?"
)

RE_VERSION_MODEL = re.compile(
    r"[Cc]isco\s+(?P<model>\S+).*?(?:with\s+\d+K/\d+K\s+bytes)?",
    re.IGNORECASE,
)
RE_VERSION_IOS = re.compile(
    r"(?:IOS|Software).*?Version\s+(?P<version>\S+)",
    re.IGNORECASE,
)
RE_VERSION_SERIAL = re.compile(
    r"Processor board ID\s+(?P<serial>\S+)",
    re.IGNORECASE,
)
RE_VERSION_UPTIME = re.compile(
    r"uptime is\s+(?P<uptime>.+)",
    re.IGNORECASE,
)

# Maps Cisco route codes to our RouteProtocol
ROUTE_CODE_MAP = {
    "C": RouteProtocol.CONNECTED,
    "L": RouteProtocol.LOCAL,
    "S": RouteProtocol.STATIC,
    "O": RouteProtocol.OSPF,
    "B": RouteProtocol.BGP,
    "D": RouteProtocol.EIGRP,
    "i": RouteProtocol.ISIS,
    "R": RouteProtocol.RIP,
}


# ---------------------------------------------------------------------------
# Interface name normalizer
# ---------------------------------------------------------------------------

INTERFACE_SHORT_MAP = {
    "Et": "Ethernet",
    "Eth": "Ethernet",
    "Fa": "FastEthernet",
    "Gi": "GigabitEthernet",
    "Te": "TenGigabitEthernet",
    "Se": "Serial",
    "Lo": "Loopback",
    "Vl": "Vlan",
    "Tu": "Tunnel",
    "Po": "Port-channel",
}


def normalize_interface_name(short_if: str) -> str:
    """Expand abbreviated interface names: Et0/1 → Ethernet0/1."""
    for prefix, full in INTERFACE_SHORT_MAP.items():
        if short_if.startswith(prefix) and not short_if.startswith(full):
            return full + short_if[len(prefix):]
    return short_if


def normalize_mac(mac: str) -> str:
    """Convert Cisco MAC format (aabb.ccdd.eeff) to standard (aa:bb:cc:dd:ee:ff)."""
    mac_clean = mac.replace(".", "").replace(":", "").replace("-", "").lower()
    if len(mac_clean) == 12:
        return ":".join(mac_clean[i:i+2] for i in range(0, 12, 2))
    return mac.lower()


# ---------------------------------------------------------------------------
# CiscoIOSDriver
# ---------------------------------------------------------------------------

class CiscoIOSDriver(SSHDriver):
    """
    Driver for Cisco IOS and IOS-XE devices via SSH.

    Parsing logic is extracted from the proven mini_nce.py crawler.
    Supports legacy devices with older SSH ciphers.
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
            vendor=Vendor.CISCO_IOS,
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
        return "terminal length 0"

    def _get_config_mode_commands(self) -> Tuple[str, str]:
        return ("configure terminal", "end")

    def _get_show_commands(self) -> Dict[str, str]:
        return {
            "hostname": "show running-config | include ^hostname",
            "interfaces": "show ip interface brief",
            "neighbors_summary": "show lldp neighbor",
            "neighbors_detail": "show lldp neighbors detail",
            "arp": "show ip arp",
            "routes": "show ip route",
            "version": "show version",
            "running_config": "show running-config",
        }

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_hostname(self, raw_output: str) -> str:
        """Extract hostname from 'show running-config | include ^hostname'."""
        m = RE_HOSTNAME.search(raw_output)
        if m:
            return m.group("h")

        # Fallback: try to extract from prompt
        for line in reversed(raw_output.splitlines()):
            line = line.strip()
            if line.endswith("#") or line.endswith(">"):
                return line.rstrip("#>").strip()

        return "unknown"

    def _parse_interfaces(self, raw_output: str) -> List[Interface]:
        """Parse 'show ip interface brief' output."""
        interfaces = []

        for line in raw_output.splitlines():
            line = line.strip()

            # Skip header and irrelevant lines
            if not line or line.lower().startswith("interface") or line.lower().startswith("address"):
                continue

            # Try full format first (includes status/protocol columns)
            m = RE_IF_BRIEF_LINE.match(line)
            if m:
                intf_name = normalize_interface_name(m.group("intf"))
                ip = m.group("ip")
                status_str = m.group("status").strip().lower()
                protocol_str = m.group("protocol").strip().lower()

                # Determine interface status
                if "admin" in status_str:
                    status = InterfaceStatus.ADMIN_DOWN
                elif protocol_str == "up":
                    status = InterfaceStatus.UP
                else:
                    status = InterfaceStatus.DOWN

                ip_addr = ip if re.match(r"\d+\.\d+\.\d+\.\d+", ip) else ""

                interfaces.append(Interface(
                    name=intf_name,
                    ip_address=ip_addr,
                    status=status,
                ))
                continue

            # Fallback: simpler format
            m2 = RE_IF_BRIEF_SIMPLE.match(line)
            if m2:
                intf_name = normalize_interface_name(m2.group("intf"))
                ip = m2.group("ip")
                ip_addr = ip if re.match(r"\d+\.\d+\.\d+\.\d+", ip) else ""

                interfaces.append(Interface(
                    name=intf_name,
                    ip_address=ip_addr,
                    status=InterfaceStatus.UNKNOWN,
                ))

        return interfaces

    def _parse_neighbors(self, raw_output: str) -> List[Neighbor]:
        """
        Parse LLDP neighbor output (both summary and detail).
        Returns combined neighbor list.
        """
        neighbors = []
        seen = set()

        # --- Parse LLDP summary ---
        for line in raw_output.splitlines():
            line = line.rstrip()
            m = RE_LLDP_SUM_LINE.match(line)
            if m:
                dev = m.group("dev")
                local_if = m.group("local_if")

                # Filter false positives (IPs, "Internet" keyword)
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", dev):
                    continue
                if dev.lower() == "internet":
                    continue

                local_if_norm = normalize_interface_name(local_if)
                remote_port = normalize_interface_name(m.group("port_id"))
                key = (local_if_norm, dev, remote_port)

                if key not in seen:
                    seen.add(key)
                    neighbors.append(Neighbor(
                        local_interface=local_if_norm,
                        remote_device=dev,
                        remote_interface=remote_port,
                        discovery_protocol="lldp",
                    ))

        # --- Parse LLDP detail (enriches with management IPs) ---
        current_local_intf = ""
        current_system_name = ""
        current_port_id = ""
        current_mgmt_ip = ""

        for line in raw_output.splitlines():
            # Local interface
            m = RE_LLDP_DETAIL_LOCAL_INTF.search(line)
            if m:
                # Save previous entry if complete
                if current_local_intf and current_system_name:
                    self._enrich_neighbor(
                        neighbors, current_local_intf,
                        current_system_name, current_port_id, current_mgmt_ip
                    )

                current_local_intf = normalize_interface_name(m.group("intf"))
                current_system_name = ""
                current_port_id = ""
                current_mgmt_ip = ""
                continue

            m = RE_LLDP_DETAIL_SYSTEM_NAME.search(line)
            if m:
                current_system_name = m.group("name")
                continue

            m = RE_LLDP_DETAIL_PORT_ID.search(line)
            if m:
                current_port_id = normalize_interface_name(m.group("port"))
                continue

            m = RE_LLDP_DETAIL_MGMT_ADDR.search(line)
            if m:
                current_mgmt_ip = m.group("ip")
                continue

        # Don't forget the last entry
        if current_local_intf and current_system_name:
            self._enrich_neighbor(
                neighbors, current_local_intf,
                current_system_name, current_port_id, current_mgmt_ip
            )

        return neighbors

    def _enrich_neighbor(
        self,
        neighbors: List[Neighbor],
        local_intf: str,
        system_name: str,
        port_id: str,
        mgmt_ip: str,
    ) -> None:
        """Update existing neighbor entry with detail info, or add new one."""
        for n in neighbors:
            if n.local_interface == local_intf and n.remote_device == system_name:
                if mgmt_ip:
                    n.remote_ip = mgmt_ip
                if port_id and not n.remote_interface:
                    n.remote_interface = port_id
                return

        # Not found in summary — add new
        neighbors.append(Neighbor(
            local_interface=local_intf,
            remote_device=system_name,
            remote_interface=port_id,
            remote_ip=mgmt_ip,
            discovery_protocol="lldp",
        ))

    def _parse_routing_table(self, raw_output: str) -> List[Route]:
        """Parse 'show ip route' output."""
        routes = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line or line.startswith("Gateway") or line.startswith("Codes"):
                continue

            m = RE_ROUTE_LINE.match(line)
            if m:
                code = m.group("code").strip()
                prefix = m.group("prefix")
                mask_len = m.group("mask_len")
                next_hop = m.group("next_hop") or "directly connected"
                intf = m.group("intf") or ""

                # Determine protocol from code
                protocol = RouteProtocol.UNKNOWN
                for char in code:
                    if char in ROUTE_CODE_MAP:
                        protocol = ROUTE_CODE_MAP[char]
                        break

                # Build prefix with CIDR notation
                if mask_len:
                    prefix_cidr = f"{prefix}/{mask_len}"
                else:
                    prefix_cidr = prefix

                routes.append(Route(
                    prefix=prefix_cidr,
                    next_hop=next_hop,
                    protocol=protocol,
                    interface=intf,
                ))

        return routes

    def _parse_arp_table(self, raw_output: str) -> List[ARPEntry]:
        """Parse 'show ip arp' output."""
        entries = []
        for line in raw_output.splitlines():
            m = RE_ARP_LINE.match(line)
            if m:
                entries.append(ARPEntry(
                    ip=m.group("ip"),
                    mac=normalize_mac(m.group("mac")),
                    interface=m.group("intf"),
                    age=m.group("age"),
                ))
        return entries

    def _parse_device_info(self, raw_output: str) -> DeviceInfo:
        """Parse 'show version' output."""
        hostname = self._parse_hostname(raw_output)
        model = ""
        os_version = ""
        serial = ""
        uptime = ""

        m = RE_VERSION_MODEL.search(raw_output)
        if m:
            model = m.group("model")

        m = RE_VERSION_IOS.search(raw_output)
        if m:
            os_version = m.group("version")

        m = RE_VERSION_SERIAL.search(raw_output)
        if m:
            serial = m.group("serial")

        m = RE_VERSION_UPTIME.search(raw_output)
        if m:
            uptime = m.group("uptime").strip()

        return DeviceInfo(
            hostname=hostname,
            vendor=Vendor.CISCO_IOS,
            model=model,
            os_version=os_version,
            serial=serial,
            uptime=uptime,
            management_ip=self.host,
        )
