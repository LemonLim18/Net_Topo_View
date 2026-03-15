"""
Driver Factory — auto-selects the correct driver based on device configuration.

Usage:
    # SSH driver for a legacy Cisco device
    driver = DriverFactory.create(
        driver_type="cisco_ios",
        host="192.168.1.1",
        username="admin",
        password="cisco",
    )

    # SDN controller driver for OpenDaylight
    driver = DriverFactory.create(
        driver_type="opendaylight",
        controller_url="http://192.168.1.100:8181",
        username="admin",
        password="admin",
    )

    # Use with context manager
    with DriverFactory.create("cisco_ios", host="10.0.0.1", ...) as driver:
        interfaces = driver.get_interfaces()
"""

import logging
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from drivers.base import BaseDriver

if TYPE_CHECKING:
    from db.models import Device
from drivers.cisco_ios import CiscoIOSDriver
from drivers.huawei_vrp import HuaweiVRPDriver
from drivers.juniper_junos import JuniperJunosDriver
from drivers.opendaylight import OpenDaylightDriver
from drivers.onos import ONOSDriver

logger = logging.getLogger("drivers.factory")


# Registry of all available drivers
_DRIVER_REGISTRY: Dict[str, type] = {
    # SSH drivers (legacy devices — direct access)
    "cisco_ios": CiscoIOSDriver,
    "cisco_xe": CiscoIOSDriver,       # IOS-XE uses same CLI as IOS
    "huawei_vrp": HuaweiVRPDriver,
    "juniper_junos": JuniperJunosDriver,

    # SDN controller drivers (modern devices — via controller API)
    "opendaylight": OpenDaylightDriver,
    "odl": OpenDaylightDriver,         # Alias
    "onos": ONOSDriver,
}

# Parameters expected by each driver category
_SSH_PARAMS = {"host", "username", "password", "key_file", "port", "hop_chain", "jump_host"}
_SDN_PARAMS = {"controller_url", "username", "password", "verify_ssl"}


class DriverFactory:
    """
    Factory for creating the appropriate driver based on device type.

    Supports both SSH-based (legacy) and SDN controller-based (modern) drivers.
    """

    @staticmethod
    def create(driver_type: str, **kwargs) -> BaseDriver:
        """
        Create a driver instance.

        Args:
            driver_type: One of: 'cisco_ios', 'cisco_xe', 'huawei_vrp',
                         'juniper_junos', 'opendaylight', 'odl', 'onos'
            **kwargs: Driver-specific parameters.
                SSH drivers: host, username, password, key_file, port, hop_chain, jump_host
                SDN drivers: controller_url, username, password, verify_ssl

        Returns:
            BaseDriver: Configured driver instance (not yet connected).

        Raises:
            ValueError: If driver_type is not recognized.
        """
        driver_type_lower = driver_type.lower().strip()

        if driver_type_lower not in _DRIVER_REGISTRY:
            available = ", ".join(sorted(_DRIVER_REGISTRY.keys()))
            raise ValueError(
                f"Unknown driver type: '{driver_type}'. "
                f"Available drivers: {available}"
            )

        driver_class = _DRIVER_REGISTRY[driver_type_lower]

        # Filter kwargs to only pass parameters the driver expects
        # This prevents TypeErrors from unexpected keyword arguments
        try:
            driver = driver_class(**kwargs)
        except TypeError as e:
            logger.error(f"Invalid parameters for {driver_type}: {e}")
            raise ValueError(
                f"Invalid parameters for driver '{driver_type}': {e}"
            ) from e

        logger.info(f"Created {driver_class.__name__} for {driver_type}")
        return driver

    @staticmethod
    def create_from_config(device_config: Dict[str, Any]) -> BaseDriver:
        """
        Create a driver from a device configuration dictionary.

        Expected format:
            {
                "driver": "cisco_ios",
                "host": "192.168.1.1",
                "username": "admin",
                "password": "cisco",
                ...
            }
        """
        config = dict(device_config)  # Don't mutate the original
        driver_type = config.pop("driver", None)

        if not driver_type:
            raise ValueError("Device config must include a 'driver' key")

        return DriverFactory.create(driver_type, **config)

    @staticmethod
    def get_available_drivers() -> List[str]:
        """Return list of all registered driver type names."""
        return sorted(_DRIVER_REGISTRY.keys())

    @staticmethod
    def get_ssh_drivers() -> List[str]:
        """Return driver types for SSH-based (legacy) devices."""
        return ["cisco_ios", "cisco_xe", "huawei_vrp", "juniper_junos"]

    @staticmethod
    def get_sdn_drivers() -> List[str]:
        """Return driver types for SDN controller-based devices."""
        return ["opendaylight", "odl", "onos"]

    @staticmethod
    def is_ssh_driver(driver_type: str) -> bool:
        """Check if a driver type is SSH-based."""
        return driver_type.lower() in DriverFactory.get_ssh_drivers()

    @staticmethod
    def is_sdn_driver(driver_type: str) -> bool:
        """Check if a driver type is SDN controller-based."""
        return driver_type.lower() in DriverFactory.get_sdn_drivers()

    @staticmethod
    def get_driver(device: "Device") -> BaseDriver:
        """
        Create an SSH driver for a Device from the inventory.
        Uses device.ssh_user / device.ssh_password; if missing, tries CredentialVault for device.id.
        Caller must call driver.connect() before use and driver.disconnect() when done.
        """
        host = (device.management_ip or device.hostname or "").strip()
        if not host:
            raise ValueError(f"Device {getattr(device, 'hostname', '?')} has no management_ip or hostname")

        username = (device.ssh_user or "").strip() if getattr(device, "ssh_user", None) else ""
        password = (device.ssh_password or "") if getattr(device, "ssh_password", None) else ""
        device_id = getattr(device, "id", None)

        if not username or not password:
            try:
                from auth.credentials import CredentialVault
                vault = CredentialVault()
                cred = vault.get(device_id, "SSH") if device_id else None
                if cred:
                    username = username or (cred.get("username") or "").strip()
                    password = password or (cred.get("password") or "")
            except Exception as e:
                logger.debug("CredentialVault not available or failed: %s", e)

        if not username or not password:
            raise ValueError(
                "SSH credentials not set. Set ssh_user and ssh_password in Device Inventory, "
                "or store credentials in the credential vault for this device."
            )

        driver_type = (device.vendor or "cisco_ios").strip().lower()
        if driver_type not in _DRIVER_REGISTRY:
            driver_type = "cisco_ios"
        port = getattr(device, "ssh_port", None) or 22
        return DriverFactory.create(
            driver_type,
            host=host,
            username=username,
            password=password,
            port=port,
        )

    @staticmethod
    def register_driver(name: str, driver_class: type) -> None:
        """
        Register a custom driver at runtime.
        Useful for plugins or extensions (e.g., adding Arista EOS support).
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Driver name must be a non-empty string")

        _DRIVER_REGISTRY[name.lower().strip()] = driver_class
        logger.info(f"Registered custom driver: {name} → {driver_class.__name__}")
