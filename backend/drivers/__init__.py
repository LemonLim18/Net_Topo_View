"""
NetAutoController — Multi-Vendor Driver Layer

Provides a unified interface for talking to network devices regardless of
vendor (Cisco, Huawei, Juniper) or access method (SSH, RESTCONF, REST API).

Usage:
    from drivers import DriverFactory, CiscoIOSDriver, ONOSDriver
    driver = DriverFactory.create("cisco_ios", host="192.168.1.1", ...)
    interfaces = driver.get_interfaces()
"""

from drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    DriverResult,
)
from drivers.base import BaseDriver
from drivers.ssh_driver import SSHDriver
from drivers.sdn_driver import SDNControllerDriver
from drivers.cisco_ios import CiscoIOSDriver
from drivers.huawei_vrp import HuaweiVRPDriver
from drivers.juniper_junos import JuniperJunosDriver
from drivers.opendaylight import OpenDaylightDriver
from drivers.onos import ONOSDriver
from drivers.factory import DriverFactory

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
