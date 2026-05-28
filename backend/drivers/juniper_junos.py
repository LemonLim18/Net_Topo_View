"""
Juniper Junos SSH Driver — Full implementation.

Implements CLI command mapping and output parsing for Juniper Junos devices.
Uses Netmiko's 'juniper_junos' device type for reliable SSH handling.

Key Junos differences from Cisco IOS / Huawei VRP:
    - CLI has TWO modes: operational (>) and configuration (# in [edit])
    - Commands use 'show' (like Cisco) but output format is very different
    - Config mode: 'configure' / 'commit and-quit'  (not 'conf t' / 'end')
    - Rollback: 'rollback N' is built-in (unlike Cisco which needs undo)
    - Paging: 'set cli screen-length 0'
    - Hostname: 'host-name' under [edit system] (not 'hostname')
    - Interface format: ge-0/0/0, xe-0/0/1, et-0/0/0, lo0, irb

Supported commands:
    show configuration system host-name
    show interfaces terse
    show lldp neighbors
    show arp no-resolve
    show route
    show version
    show configuration
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

logger = logging.getLogger("drivers.juniper_junos")


# ---------------------------------------------------------------------------
# Regexes for Juniper Junos CLI output
# ---------------------------------------------------------------------------

# show configuration system host-name → "host-name R1;"
RE_JUNOS_HOSTNAME = re.compile(
    r"host-name\s+(?P<hostname>\S+);?", re.IGNORECASE
)

# Fallback: extract from prompt like "user@R1>"
RE_JUNOS_PROMPT = re.compile(
    r"\w+@(?P<hostname>\S+)[>#]"
)

# show interfaces terse:
# Interface               Admin Link Proto    Local                 Remote
# ge-0/0/0                up    up
# ge-0/0/0.0              up    up   inet     10.1.1.1/24
#                                    inet6    fe80::1/64
# ge-0/0/1                up    down
# lo0.0                   up    up   inet     192.168.1.1/32
# dsc                     up    up
RE_JUNOS_IF_TERSE = re.compile(
    r"^(?P<intf>\S+)\s+"
    r"(?P<admin>up|down)\s+"
    r"(?P<link>up|down)"
    r"(?:\s+(?P<proto>\S+))?"
    r"(?:\s+(?P<local>\S+))?"
    r"(?:\s+(?P<remote>\S+))?",
    re.IGNORECASE,
)

# show lldp neighbors:
# Local Interface    Parent Interface    Chassis Id          Port info          System Name
# ge-0/0/0           -                   aa:bb:cc:dd:ee:ff   ge-0/0/1           R2
# ge-0/0/1           -                   11:22:33:44:55:66   ge-0/0/0           SW1
RE_JUNOS_LLDP = re.compile(
    r"^(?P<local_if>\S+)\s+"
    r"(?P<parent>\S+)\s+"
    r"(?P<chassis>[0-9a-fA-F:.\-]+)\s+"
    r"(?P<port_info>\S+)\s+"
    r"(?P<system_name>\S+)",
    re.IGNORECASE,
)

# show arp no-resolve:
# MAC Address       Address         Interface       Flags
# aa:bb:cc:dd:ee:ff 10.1.1.2        ge-0/0/0.0      none
# 11:22:33:44:55:66 10.1.2.1        ge-0/0/1.0      none
RE_JUNOS_ARP = re.compile(
    r"^(?P<mac>[0-9a-fA-F:]+)\s+"
    r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+"
    r"(?P<intf>\S+)\s+"
    r"(?P<flags>\S+)",
    re.IGNORECASE,
)

# show route:
# inet.0: 12 destinations, 12 routes (12 active, 0 holddown, 0 hidden)
# + = Active Route, - = Last Active, * = Both
#
# 10.0.0.0/24        *[OSPF/10] 00:05:23, metric 2
#                     >  to 10.1.1.2 via ge-0/0/0.0
# 10.1.1.0/24        *[Direct/0] 01:23:45
#                     >  via ge-0/0/0.0
# 192.168.1.1/32     *[Local/0] 01:23:45
#                        Local via lo0.0
RE_JUNOS_ROUTE_PREFIX = re.compile(
    r"^(?P<prefix>\d+\.\d+\.\d+\.\d+/\d+)\s+"
    r"\*?\[(?P<proto>\w+)/(?P<pref>\d+)\]"
    r"(?:\s+(?P<age>\S+))?"
    r"(?:,\s*metric\s+(?P<metric>\d+))?",
)

RE_JUNOS_ROUTE_NEXTHOP = re.compile(
    r"^\s+>?\s+(?:to\s+)?(?P<next_hop>\d+\.\d+\.\d+\.\d+)?\s*"
    r"(?:via\s+(?P<intf>\S+))?",
)

# show version:
# Hostname: R1
# Model: vmx
# Junos: 21.4R3-S5
# JUNOS OS Kernel 64-bit  [20231215....]
RE_JUNOS_VER_HOSTNAME = re.compile(
    r"^Hostname:\s+(?P<hostname>\S+)", re.IGNORECASE | re.MULTILINE
)
RE_JUNOS_VER_MODEL = re.compile(
    r"^Model:\s+(?P<model>\S+)", re.IGNORECASE | re.MULTILINE
)
RE_JUNOS_VER_JUNOS = re.compile(
    r"^Junos:\s+(?P<version>\S+)", re.IGNORECASE | re.MULTILINE
)
# Some versions show it differently
RE_JUNOS_VER_ALT = re.compile(
    r"JUNOS\s+.*?\[(?P<version>[^\]]+)\]", re.IGNORECASE
)
RE_JUNOS_VER_SERIAL = re.compile(
    r"(?:Chassis|Serial number)\s*(?:number)?\s*:?\s*(?P<serial>\S+)",
    re.IGNORECASE | re.MULTILINE,
)
RE_JUNOS_VER_UPTIME = re.compile(
    r"System booted:\s+(?P<uptime>.+)", re.IGNORECASE | re.MULTILINE
)

# Maps Junos route protocol names to our RouteProtocol
JUNOS_PROTO_MAP = {
    "direct": RouteProtocol.CONNECTED,
    "local": RouteProtocol.LOCAL,
    "static": RouteProtocol.STATIC,
    "ospf": RouteProtocol.OSPF,
    "bgp": RouteProtocol.BGP,
    "isis": RouteProtocol.ISIS,
    "rip": RouteProtocol.RIP,
    "aggregate": RouteProtocol.STATIC,
}


def normalize_junos_mac(mac: str) -> str:
    """Normalize MAC to standard format (aa:bb:cc:dd:ee:ff)."""
    clean = mac.replace(":", "").replace("-", "").replace(".", "").lower()
    if len(clean) == 12:
        return ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return mac.lower()


# ---------------------------------------------------------------------------
# JuniperJunosDriver
# ---------------------------------------------------------------------------

class JuniperJunosDriver(SSHDriver):
    """
    Driver for Juniper Junos devices via SSH.

    Supports:
        - SSH via Paramiko (inherited) or Netmiko 'juniper_junos'
        - Full parsing of operational-mode show commands
        - Configuration via 'configure' / 'commit and-quit'
        - Built-in rollback support ('rollback N')

    Future (stretch goal):
        - NETCONF via ncclient for structured XML output
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
            vendor=Vendor.JUNIPER_JUNOS,
            password=password,
            key_file=key_file,
            port=port,
            hop_chain=hop_chain,
            jump_host=jump_host,
        )

    # ------------------------------------------------------------------
    # SSHDriver abstract methods — command definitions
    # ------------------------------------------------------------------

    def _get_disable_paging_command(self) -> str:
        return "set cli screen-length 0"

    def _get_config_mode_commands(self) -> Tuple[str, str]:
        return ("configure", "commit and-quit")

    def _get_show_commands(self) -> Dict[str, str]:
        return {
            "hostname": "show configuration system host-name",
            "interfaces": "show interfaces terse",
            "neighbors_summary": "show lldp neighbors",
            "neighbors_detail": "show lldp neighbors",
            "arp": "show arp no-resolve",
            "routes": "show route",
            "version": "show version",
            "running_config": "show configuration",
        }

    # ------------------------------------------------------------------
    # Parsers — fully implemented
    # ------------------------------------------------------------------

    def _parse_hostname(self, raw_output: str) -> str:
        """
        Extract hostname from Junos output.

        Tries (in order):
        1. 'host-name xxx;' from show configuration
        2. 'Hostname: xxx' from show version
        3. Prompt pattern 'user@hostname>'
        """
        m = RE_JUNOS_HOSTNAME.search(raw_output)
        if m:
            name = m.group("hostname").rstrip(";").strip()
            if name.lower() != "host-name":
                return name

        m = RE_JUNOS_VER_HOSTNAME.search(raw_output)
        if m:
            name = m.group("hostname").strip()
            if name.lower() != "host-name":
                return name

        # Fallback: try to extract from prompt
        for line in reversed(raw_output.splitlines()):
            line = line.strip()
            m2 = RE_JUNOS_PROMPT.search(line)
            if m2:
                name = m2.group("hostname").strip()
                if name.lower() != "host-name":
                    return name

        return "unknown"

    def _parse_interfaces(self, raw_output: str) -> List[Interface]:
        """
        Parse 'show interfaces terse' output.

        Junos format:
            Interface               Admin Link Proto    Local            Remote
            ge-0/0/0                up    up
            ge-0/0/0.0              up    up   inet     10.1.1.1/24
            lo0.0                   up    up   inet     192.168.1.1/32
        """
        interfaces = []
        seen_header = False

        for line in raw_output.splitlines():
            # Skip until we see the terse header
            if "Interface" in line and "Admin" in line and "Link" in line:
                seen_header = True
                continue

            if not seen_header:
                continue

            line_stripped = line.rstrip()
            if not line_stripped:
                continue

            # Lines starting with whitespace are continuation lines
            # (additional protocol families on the same interface)
            if line_stripped[0].isspace():
                if interfaces:
                    # Check if this continuation has an IP
                    parts = line_stripped.split()
                    for p in parts:
                        if re.match(r"\d+\.\d+\.\d+\.\d+(/\d+)?", p):
                            # Only set if the interface doesn't already have an IP
                            if not interfaces[-1].ip_address:
                                ip_part = p.split("/")[0]
                                interfaces[-1].ip_address = ip_part
                                if "/" in p:
                                    interfaces[-1].subnet_mask = p.split("/")[1]
                            break
                continue

            m = RE_JUNOS_IF_TERSE.match(line_stripped)
            if m:
                intf_name = m.group("intf")
                admin = m.group("admin").lower()
                link = m.group("link").lower()
                local = m.group("local") or ""

                # Determine status
                if admin == "down":
                    status = InterfaceStatus.ADMIN_DOWN
                elif link == "up":
                    status = InterfaceStatus.UP
                else:
                    status = InterfaceStatus.DOWN

                # Extract IP from Local column (format: 10.1.1.1/24)
                ip_address = ""
                subnet_mask = ""
                if local and re.match(r"\d+\.\d+\.\d+\.\d+", local):
                    if "/" in local:
                        ip_address = local.split("/")[0]
                        subnet_mask = local.split("/")[1]
                    else:
                        ip_address = local

                interfaces.append(Interface(
                    name=intf_name,
                    ip_address=ip_address,
                    subnet_mask=subnet_mask,
                    status=status,
                ))

        return interfaces

    def _parse_neighbors(self, raw_output: str) -> List[Neighbor]:
        """
        Parse 'show lldp neighbors' output.

        Junos format:
            Local Interface    Parent Interface    Chassis Id          Port info    System Name
            ge-0/0/0           -                   aa:bb:cc:dd:ee:ff   ge-0/0/1     R2
        """
        neighbors = []
        seen_header = False

        for line in raw_output.splitlines():
            line_stripped = line.strip()

            # Detect header line
            if "Local Interface" in line and "System Name" in line:
                seen_header = True
                continue

            if not seen_header or not line_stripped:
                continue

            # Stop at summary lines like "X lldp neighbors"
            if re.match(r"^\d+\s+lldp", line_stripped, re.IGNORECASE):
                break

            m = RE_JUNOS_LLDP.match(line_stripped)
            if m:
                local_if = m.group("local_if")
                system_name = m.group("system_name")
                port_info = m.group("port_info")

                # Skip header-like false positives
                if local_if.lower() == "local" or system_name.lower() == "system":
                    continue

                neighbors.append(Neighbor(
                    local_interface=local_if,
                    remote_device=system_name,
                    remote_interface=port_info,
                    discovery_protocol="lldp",
                ))

        return neighbors

    def _parse_routing_table(self, raw_output: str) -> List[Route]:
        """
        Parse 'show route' output.

        Junos format:
            10.0.0.0/24        *[OSPF/10] 00:05:23, metric 2
                                >  to 10.1.1.2 via ge-0/0/0.0
            10.1.1.0/24        *[Direct/0] 01:23:45
                                >  via ge-0/0/0.0
        """
        routes = []
        current_prefix = None
        current_proto = RouteProtocol.UNKNOWN
        current_pref = 0
        current_metric = 0

        for line in raw_output.splitlines():
            line_stripped = line.rstrip()

            # Skip headers and table names like "inet.0: ..."
            if not line_stripped or line_stripped.startswith("inet"):
                continue
            if line_stripped.startswith("+") or line_stripped.startswith("-"):
                continue

            # Try to match a route prefix line
            m = RE_JUNOS_ROUTE_PREFIX.match(line_stripped)
            if m:
                current_prefix = m.group("prefix")
                proto_str = m.group("proto").lower()
                current_proto = JUNOS_PROTO_MAP.get(proto_str, RouteProtocol.UNKNOWN)
                current_pref = int(m.group("pref"))
                current_metric = int(m.group("metric")) if m.group("metric") else 0
                continue

            # Try to match a next-hop line (indented, belongs to previous prefix)
            if current_prefix:
                m2 = RE_JUNOS_ROUTE_NEXTHOP.match(line_stripped)
                if m2:
                    next_hop = m2.group("next_hop") or "directly connected"
                    intf = m2.group("intf") or ""

                    routes.append(Route(
                        prefix=current_prefix,
                        next_hop=next_hop,
                        protocol=current_proto,
                        administrative_distance=current_pref,
                        metric=current_metric,
                        interface=intf,
                    ))
                    current_prefix = None  # Reset after consuming

        return routes

    def _parse_arp_table(self, raw_output: str) -> List[ARPEntry]:
        """
        Parse 'show arp no-resolve' output.

        Junos format:
            MAC Address       Address         Interface       Flags
            aa:bb:cc:dd:ee:ff 10.1.1.2        ge-0/0/0.0      none
        """
        entries = []
        seen_header = False

        for line in raw_output.splitlines():
            line_stripped = line.strip()

            # Detect header
            if "MAC Address" in line and "Address" in line:
                seen_header = True
                continue

            if not seen_header or not line_stripped:
                continue

            # Stop at summary line like "Total entries: 5"
            if line_stripped.lower().startswith("total"):
                break

            m = RE_JUNOS_ARP.match(line_stripped)
            if m:
                entries.append(ARPEntry(
                    ip=m.group("ip"),
                    mac=normalize_junos_mac(m.group("mac")),
                    interface=m.group("intf"),
                ))

        return entries

    def _parse_device_info(self, raw_output: str) -> DeviceInfo:
        """
        Parse 'show version' output.

        Junos format:
            Hostname: R1
            Model: vmx
            Junos: 21.4R3-S5
        """
        hostname = self._parse_hostname(raw_output)

        model = ""
        m = RE_JUNOS_VER_MODEL.search(raw_output)
        if m:
            model = m.group("model")

        os_version = ""
        m = RE_JUNOS_VER_JUNOS.search(raw_output)
        if m:
            os_version = m.group("version")
        else:
            m = RE_JUNOS_VER_ALT.search(raw_output)
            if m:
                os_version = m.group("version")

        serial = ""
        m = RE_JUNOS_VER_SERIAL.search(raw_output)
        if m:
            serial = m.group("serial")

        uptime = ""
        m = RE_JUNOS_VER_UPTIME.search(raw_output)
        if m:
            uptime = m.group("uptime").strip()

        return DeviceInfo(
            hostname=hostname,
            vendor=Vendor.JUNIPER_JUNOS,
            model=model,
            os_version=os_version,
            serial=serial,
            uptime=uptime,
            management_ip=self.host,
        )
