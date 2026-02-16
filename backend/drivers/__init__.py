"""
NetAutoController — Multi-Vendor Driver Layer

Provides a unified interface for talking to network devices regardless of
vendor (Cisco, Huawei, Juniper) or access method (SSH, RESTCONF, REST API).

Usage:
    from backend.drivers import DriverFactory, CiscoIOSDriver, ONOSDriver
    driver = DriverFactory.create("cisco_ios", host="192.168.1.1", ...)
    interfaces = driver.get_interfaces()
"""

from backend.drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    DriverResult,
)
from backend.drivers.base import BaseDriver
from backend.drivers.ssh_driver import SSHDriver
from backend.drivers.sdn_driver import SDNControllerDriver
from backend.drivers.cisco_ios import CiscoIOSDriver
from backend.drivers.huawei_vrp import HuaweiVRPDriver
from backend.drivers.juniper_junos import JuniperJunosDriver
from backend.drivers.opendaylight import OpenDaylightDriver
from backend.drivers.onos import ONOSDriver
from backend.drivers.factory import DriverFactory

__all__ = [
    # Dataclasses
    "Interface",
    "Neighbor",
    "Route",
    "ARPEntry",
    "DeviceInfo",
    "DriverResult",
    # Base classes
    "BaseDriver",
    "SSHDriver",
    "SDNControllerDriver",
    # SSH drivers
    "CiscoIOSDriver",
    "HuaweiVRPDriver",
    "JuniperJunosDriver",
    # SDN drivers
    "OpenDaylightDriver",
    "ONOSDriver",
    # Factory
    "DriverFactory",
]
