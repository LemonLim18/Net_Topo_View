"""
Shared data models for the Multi-Vendor Driver Layer.

Every driver — whether SSH-based or SDN controller-based — returns these
same dataclasses. This guarantees that the rest of the system (crawler,
config engine, security module, LLM agent) can work with any vendor
without caring how the data was fetched.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class InterfaceStatus(Enum):
    """Normalized interface status across all vendors."""
    UP = "up"
    DOWN = "down"
    ADMIN_DOWN = "admin_down"
    UNKNOWN = "unknown"


class RouteProtocol(Enum):
    """Routing protocol type."""
    CONNECTED = "connected"
    STATIC = "static"
    OSPF = "ospf"
    BGP = "bgp"
    EIGRP = "eigrp"
    ISIS = "isis"
    RIP = "rip"
    LOCAL = "local"
    UNKNOWN = "unknown"


class DriverType(Enum):
    """How the driver connects to the device."""
    SSH = "ssh"             # Direct SSH / CLI
    NETCONF = "netconf"     # NETCONF (XML-based)
    RESTCONF = "restconf"   # RESTCONF (OpenDaylight)
    REST_API = "rest_api"   # Vendor REST API (ONOS, DNA Center)


class Vendor(Enum):
    """Supported vendors."""
    CISCO_IOS = "cisco_ios"
    CISCO_XE = "cisco_xe"
    HUAWEI_VRP = "huawei_vrp"
    JUNIPER_JUNOS = "juniper_junos"
    OPENDAYLIGHT = "opendaylight"
    ONOS = "onos"
    GENERIC = "generic"


# ---------------------------------------------------------------------------
# Dataclasses — The universal output contract
# ---------------------------------------------------------------------------

@dataclass
class Interface:
    """A single network interface on a device."""
    name: str                                       # "Ethernet0/1", "GigabitEthernet0/0/1"
    ip_address: str = ""                            # "192.168.1.1" or "" if unassigned
    subnet_mask: str = ""                           # "255.255.255.0" or ""
    status: InterfaceStatus = InterfaceStatus.UNKNOWN
    speed: str = ""                                 # "1Gbps", "100Mbps", ""
    mac_address: str = ""                           # "aa:bb:cc:dd:ee:ff" or ""
    description: str = ""                           # User-configured description
    mtu: int = 0                                    # 1500, 9000, etc.

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ip_address": self.ip_address,
            "subnet_mask": self.subnet_mask,
            "status": self.status.value,
            "speed": self.speed,
            "mac_address": self.mac_address,
            "description": self.description,
            "mtu": self.mtu,
        }


@dataclass
class Neighbor:
    """An LLDP/CDP neighbor relationship."""
    local_interface: str        # Our interface name
    remote_device: str          # Neighbor hostname or device ID
    remote_interface: str       # Neighbor's interface name
    remote_ip: str = ""         # Management IP if known
    discovery_protocol: str = "lldp"  # "lldp", "cdp", "sdn"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "local_interface": self.local_interface,
            "remote_device": self.remote_device,
            "remote_interface": self.remote_interface,
            "remote_ip": self.remote_ip,
            "discovery_protocol": self.discovery_protocol,
        }


@dataclass
class Route:
    """A single routing table entry."""
    prefix: str             # "10.0.0.0/24"
    next_hop: str           # "192.168.1.1" or "directly connected"
    protocol: RouteProtocol = RouteProtocol.UNKNOWN
    metric: int = 0
    administrative_distance: int = 0
    interface: str = ""     # Outgoing interface

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prefix": self.prefix,
            "next_hop": self.next_hop,
            "protocol": self.protocol.value,
            "metric": self.metric,
            "administrative_distance": self.administrative_distance,
            "interface": self.interface,
        }


@dataclass
class ARPEntry:
    """A single ARP table entry."""
    ip: str
    mac: str                # Normalized: "aa:bb:cc:dd:ee:ff"
    interface: str
    age: str = ""           # "-" or "5" (minutes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "mac": self.mac,
            "interface": self.interface,
            "age": self.age,
        }


@dataclass
class DeviceInfo:
    """Device identity and version information."""
    hostname: str
    vendor: Vendor = Vendor.GENERIC
    model: str = ""
    os_version: str = ""
    serial: str = ""
    uptime: str = ""
    management_ip: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hostname": self.hostname,
            "vendor": self.vendor.value,
            "model": self.model,
            "os_version": self.os_version,
            "serial": self.serial,
            "uptime": self.uptime,
            "management_ip": self.management_ip,
        }


@dataclass
class DriverResult:
    """
    Wrapper for any driver operation result.
    Includes success/failure status, data, and error details.
    """
    success: bool
    data: Any = None
    error: str = ""
    device_hostname: str = ""
    driver_type: DriverType = DriverType.SSH

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "device_hostname": self.device_hostname,
            "driver_type": self.driver_type.value,
        }
