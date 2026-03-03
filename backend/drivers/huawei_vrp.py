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
# Local Interface         Exptime(s) Neighbor Interface      Neighbor Device
# GE1/0/0                       108  GE1/0/5                 Acc_Huawei_1
RE_VRP_LLDP_BRIEF = re.compile(
    r"^(?P<local_if>\S+)\s+(?P<exptime>\d+)\s+(?P<remote_if>\S+)\s+(?P<neighbor>\S+)",
    re.IGNORECASE,
)

# display lldp neighbor (detail) field extractors
RE_VRP_LLDP_LOCAL_INTF = re.compile(
    r"^(?P<intf>\S+)\s+has\s+\d+\s+neighbor", re.IGNORECASE
)
RE_VRP_LLDP_SYSTEM_NAME = re.compile(
    r"System name\s*:\s*(?P<name>\S+)", re.IGNORECASE
)
RE_VRP_LLDP_PORT_ID = re.compile(
    r"Port ID\s*:\s*(?P<port>\S+)", re.IGNORECASE
)
RE_VRP_LLDP_MGMT_ADDR = re.compile(
    r"Management address\s*:\s*(?P<ip>\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
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
    # Connection — handle VRP initial password-change prompt
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to a Huawei VRP device with password-change handling.

        Huawei VRP devices with factory-default passwords display a
        mandatory ``Change now? [Y/N]:`` dialog immediately after SSH
        login.  The parent ``SSHDriver.connect()`` would send the paging
        command *into* that dialog, corrupting the shell state.

        This override manages the full sequence:
            1. SSH connect (with legacy algorithm fallback)
            2. Open interactive shell
            3. Read banner — if password-change prompt present, send ``N``
            4. **Then** send the paging-disable command
        """
        import time
        import paramiko

        if self._client is not None:
            return  # Already connected

        # --- Step 1: SSH transport (same as parent) ---
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._connect_with_algorithms()
            logger.info("SSH connected to %s (modern algorithms)", self.host)
        except (paramiko.SSHException, EOFError):
            logger.debug("Modern SSH failed for %s, trying legacy...", self.host)
            try:
                self._client = paramiko.SSHClient()
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self._connect_with_algorithms(use_legacy=True)
                logger.info("SSH connected to %s (legacy algorithms)", self.host)
            except Exception as e:
                self._client = None
                raise ConnectionError(f"Cannot SSH to {self.host}: {e}") from e

        # --- Step 2: Open interactive shell ---
        try:
            self._channel = self._client.invoke_shell()
            time.sleep(1.0)  # wait for banner
        except Exception as e:
            logger.warning("Failed to open shell on %s: %s", self.host, e)
            self._channel = None
            return

        # --- Step 3: Handle password-change prompt ---
        banner = self._drain_channel(self._channel, wait=0.5)
        if "Change now" in banner or "change the password" in banner.lower():
            logger.info("Huawei %s: declining initial password change", self.host)
            self._channel.send("N\n")
            time.sleep(1.5)
            self._drain_channel(self._channel, wait=0.5)

        # --- Step 4: Disable paging (AFTER password-change is handled) ---
        paging_cmd = self._get_disable_paging_command()
        if paging_cmd:
            self._channel.send(paging_cmd + "\n")
            time.sleep(0.5)
            self._drain_channel(self._channel)

        logger.debug("Huawei VRP shell ready on %s", self.host)

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
        """Parse 'display lldp neighbor brief' and 'display lldp neighbor' output."""
        neighbors = []
        seen = set()

        # --- Parse LLDP brief table ---
        in_brief_section = False
        for line in raw_output.splitlines():
            line = line.strip()

            # Detect the brief table header
            if "Local Interface" in line and "Neighbor" in line:
                in_brief_section = True
                continue

            # Skip separator lines (dashes)
            if in_brief_section and line.startswith("---"):
                continue

            # End of brief section on empty line or new prompt
            if in_brief_section and (not line or line.startswith("<")):
                in_brief_section = False
                continue

            if not in_brief_section:
                continue

            m = RE_VRP_LLDP_BRIEF.match(line)
            if m:
                local_if = m.group("local_if")
                remote_if = m.group("remote_if")
                neighbor = m.group("neighbor")
                key = (local_if, neighbor, remote_if)
                if key not in seen:
                    seen.add(key)
                    neighbors.append(Neighbor(
                        local_interface=local_if,
                        remote_device=neighbor,
                        remote_interface=remote_if,
                        discovery_protocol="lldp",
                    ))

        # --- Parse LLDP detail (enriches with management IPs) ---
        current_local_intf = ""
        current_system_name = ""
        current_port_id = ""
        current_mgmt_ip = ""

        for line in raw_output.splitlines():
            line = line.strip()

            # Local interface header: "GE1/0/0 has 1 neighbor(s):"
            m = RE_VRP_LLDP_LOCAL_INTF.match(line)
            if m:
                # Save previous entry if complete
                if current_local_intf and current_system_name:
                    self._enrich_neighbor(
                        neighbors, current_local_intf,
                        current_system_name, current_port_id, current_mgmt_ip
                    )
                current_local_intf = m.group("intf")
                current_system_name = ""
                current_port_id = ""
                current_mgmt_ip = ""
                continue

            m = RE_VRP_LLDP_SYSTEM_NAME.search(line)
            if m:
                current_system_name = m.group("name")
                continue

            m = RE_VRP_LLDP_PORT_ID.search(line)
            if m and "subtype" not in line.lower():
                current_port_id = m.group("port")
                continue

            m = RE_VRP_LLDP_MGMT_ADDR.search(line)
            if m:
                current_mgmt_ip = m.group("ip")
                continue

        # Don't forget the last detail entry
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
        # Not found in brief — add new
        neighbors.append(Neighbor(
            local_interface=local_intf,
            remote_device=system_name,
            remote_interface=port_id,
            remote_ip=mgmt_ip,
            discovery_protocol="lldp",
        ))

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
