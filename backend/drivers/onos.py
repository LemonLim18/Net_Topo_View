"""
ONOS SDN Controller Driver.

Communicates via ONOS REST API to retrieve devices, links, hosts,
and topology information.

API Base: http://<controller>:8181/onos/v1/
Auth: Basic HTTP (default onos:rocks)

Key endpoints:
    GET /devices            → all infrastructure devices
    GET /devices/{id}/ports → ports of a device
    GET /links              → all infrastructure links
    GET /hosts              → all end-station hosts
    GET /topology           → topology overview
    GET /flows/{deviceId}   → flow rules on a device
"""

import logging
from typing import List, Optional, Dict

from drivers.sdn_driver import SDNControllerDriver
from drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    DriverResult,
    DriverType,
    Vendor,
    InterfaceStatus,
)

logger = logging.getLogger("drivers.onos")


class ONOSDriver(SDNControllerDriver):
    """
    Driver for ONOS SDN controller via REST API.

    ONOS uses a clean RESTful API at /onos/v1/ with separate endpoints
    for devices, links, hosts, and topology.

    Usage:
        with ONOSDriver("http://192.168.1.100:8181") as onos:
            devices = onos.get_managed_devices()
            links = onos.get_topology_links()
            hosts = onos.get_hosts()
    """

    API_PREFIX = "/onos/v1"

    def __init__(
        self,
        controller_url: str,
        username: str = "onos",
        password: str = "rocks",
        verify_ssl: bool = False,
    ):
        super().__init__(
            controller_url=controller_url,
            vendor=Vendor.ONOS,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            driver_type=DriverType.REST_API,
        )

    # ------------------------------------------------------------------
    # SDN-specific: managed devices and topology
    # ------------------------------------------------------------------

    def get_managed_devices(self) -> List[DeviceInfo]:
        """Get all infrastructure devices from ONOS."""
        try:
            data = self._get(f"{self.API_PREFIX}/devices")
        except Exception as e:
            logger.error(f"Failed to get ONOS devices: {e}")
            return []

        devices = []
        for device in data.get("devices", []):
            device_id = device.get("id", "unknown")
            annotations = device.get("annotations", {})

            devices.append(DeviceInfo(
                hostname=annotations.get("name", device_id),
                vendor=Vendor.ONOS,
                model=device.get("hw", ""),
                os_version=device.get("sw", ""),
                serial=device.get("serial", ""),
                management_ip=annotations.get("managementAddress", ""),
            ))

        return devices

    def get_topology_links(self) -> List[Neighbor]:
        """Get all infrastructure links from ONOS."""
        try:
            data = self._get(f"{self.API_PREFIX}/links")
        except Exception as e:
            logger.error(f"Failed to get ONOS links: {e}")
            return []

        neighbors = []
        for link in data.get("links", []):
            src = link.get("src", {})
            dst = link.get("dst", {})

            neighbors.append(Neighbor(
                local_interface=f"{src.get('device', '')}:{src.get('port', '')}",
                remote_device=dst.get("device", ""),
                remote_interface=f"{dst.get('device', '')}:{dst.get('port', '')}",
                discovery_protocol="sdn",
            ))

        return neighbors

    # ------------------------------------------------------------------
    # ONOS-specific methods
    # ------------------------------------------------------------------

    def get_hosts(self) -> List[Dict]:
        """
        Get all end-station hosts discovered by ONOS.
        Returns raw host data including MAC, IP, VLAN, and location.
        """
        try:
            data = self._get(f"{self.API_PREFIX}/hosts")
        except Exception as e:
            logger.error(f"Failed to get ONOS hosts: {e}")
            return []

        return data.get("hosts", [])

    def get_device_ports(self, device_id: str) -> List[Interface]:
        """Get all ports for a specific device."""
        try:
            data = self._get(f"{self.API_PREFIX}/devices/{device_id}/ports")
        except Exception as e:
            logger.error(f"Failed to get ports for {device_id}: {e}")
            return []

        interfaces = []
        for port in data.get("ports", []):
            annotations = port.get("annotations", {})

            # Determine status
            is_enabled = port.get("isEnabled", False)
            status = InterfaceStatus.UP if is_enabled else InterfaceStatus.DOWN

            # Port speed
            speed_mbps = port.get("portSpeed", 0)
            speed_str = ""
            if speed_mbps >= 1000:
                speed_str = f"{speed_mbps // 1000}Gbps"
            elif speed_mbps > 0:
                speed_str = f"{speed_mbps}Mbps"

            interfaces.append(Interface(
                name=annotations.get("portName", f"port-{port.get('port', '')}"),
                status=status,
                speed=speed_str,
                mac_address=annotations.get("portMac", ""),
            ))

        return interfaces

    def get_device_flows(self, device_id: str) -> List[Dict]:
        """Get flow rules installed on a device."""
        try:
            data = self._get(f"{self.API_PREFIX}/flows/{device_id}")
        except Exception as e:
            logger.error(f"Failed to get flows for {device_id}: {e}")
            return []

        return data.get("flows", [])

    def get_topology_overview(self) -> Dict:
        """Get topology overview (cluster count, device count, link count)."""
        try:
            return self._get(f"{self.API_PREFIX}/topology")
        except Exception as e:
            logger.error(f"Failed to get ONOS topology overview: {e}")
            return {}

    def get_topology_clusters(self) -> List[Dict]:
        """Get topology cluster information."""
        try:
            data = self._get(f"{self.API_PREFIX}/topology/clusters")
        except Exception as e:
            logger.error(f"Failed to get topology clusters: {e}")
            return []

        return data.get("clusters", [])

    # ------------------------------------------------------------------
    # BaseDriver interface
    # ------------------------------------------------------------------

    def get_hostname(self) -> str:
        """Return controller hostname."""
        return f"ONOS@{self.controller_url}"

    def get_device_info(self) -> DeviceInfo:
        """Return ONOS controller info."""
        overview = self.get_topology_overview()
        return DeviceInfo(
            hostname=f"ONOS@{self.controller_url}",
            vendor=Vendor.ONOS,
            model=f"ONOS Controller ({overview.get('deviceCount', '?')} devices)",
        )

    def get_interfaces(self) -> List[Interface]:
        """Get interfaces across all ONOS-managed devices."""
        all_interfaces = []
        devices = self.get_managed_devices()

        for device in devices:
            # Use the ONOS device ID format (e.g., "of:0000000000000001")
            device_id = device.hostname
            ports = self.get_device_ports(device_id)
            all_interfaces.extend(ports)

        return all_interfaces

    def get_neighbors(self) -> List[Neighbor]:
        """Alias for get_topology_links()."""
        return self.get_topology_links()

    def get_routing_table(self) -> List[Route]:
        """ONOS manages flows, not traditional routing tables."""
        logger.info("Routing table via ONOS requires flow rule parsing (not yet implemented)")
        return []

    def get_arp_table(self) -> List[ARPEntry]:
        """Extract ARP-like entries from ONOS host discovery."""
        hosts = self.get_hosts()
        entries = []

        for host in hosts:
            mac = host.get("mac", "")
            ips = host.get("ipAddresses", [])
            locations = host.get("locations", [{}])
            location = locations[0] if locations else {}
            intf = f"{location.get('elementId', '')}:{location.get('port', '')}"

            for ip in ips:
                entries.append(ARPEntry(
                    ip=ip,
                    mac=mac,
                    interface=intf,
                ))

        return entries
