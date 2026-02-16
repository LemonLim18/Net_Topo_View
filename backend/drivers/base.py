"""
Abstract base driver — the interface every vendor driver must implement.

This is the root of the driver hierarchy:
    BaseDriver (ABC)
    ├── SSHDriver        → for legacy CLI-based devices
    └── SDNControllerDriver → for devices managed via SDN controller APIs
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from backend.drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    DriverResult,
    DriverType,
    Vendor,
)


class BaseDriver(ABC):
    """
    Abstract base class for all network device drivers.

    Every driver — whether SSH, NETCONF, or REST API — implements this
    interface so that the rest of the system (crawler, config engine,
    LLM agent, security module) can work with any vendor identically.
    """

    def __init__(self, vendor: Vendor, driver_type: DriverType):
        self._vendor = vendor
        self._driver_type = driver_type

    @property
    def vendor(self) -> Vendor:
        """The vendor this driver handles."""
        return self._vendor

    @property
    def driver_type(self) -> DriverType:
        """How this driver connects (SSH, RESTCONF, REST API)."""
        return self._driver_type

    # ------------------------------------------------------------------
    # Read-only operations (safe, no changes to device)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_hostname(self) -> str:
        """Return the device hostname."""
        ...

    @abstractmethod
    def get_device_info(self) -> DeviceInfo:
        """Return device identity: hostname, vendor, model, OS version, serial."""
        ...

    @abstractmethod
    def get_interfaces(self) -> List[Interface]:
        """Return all interfaces with IP, status, speed."""
        ...

    @abstractmethod
    def get_neighbors(self) -> List[Neighbor]:
        """Return LLDP/CDP/SDN-discovered neighbors."""
        ...

    @abstractmethod
    def get_routing_table(self) -> List[Route]:
        """Return the full routing table."""
        ...

    @abstractmethod
    def get_arp_table(self) -> List[ARPEntry]:
        """Return the ARP/neighbor table."""
        ...

    @abstractmethod
    def get_running_config(self) -> str:
        """Return the full running configuration as text."""
        ...

    # ------------------------------------------------------------------
    # Write operations (modifies device — requires caution)
    # ------------------------------------------------------------------

    @abstractmethod
    def push_config(self, commands: List[str]) -> DriverResult:
        """
        Push configuration commands to the device.
        Returns a DriverResult with success/failure and any output.
        """
        ...

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the device or controller."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection."""
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if the connection is still active."""
        ...

    # ------------------------------------------------------------------
    # Context manager support — use drivers with `with` statement
    # ------------------------------------------------------------------

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} vendor={self._vendor.value} type={self._driver_type.value}>"
