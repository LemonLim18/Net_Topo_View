"""
OpenDaylight SDN Controller Driver.

Communicates via RESTCONF (RFC 8040) to retrieve topology, device,
and link information from an OpenDaylight controller.

API Base: http://<controller>:8181/restconf/operational/
Auth: Basic HTTP (default admin:admin)

Key endpoints:
    - network-topology:network-topology     → all topologies
    - network-topology:network-topology/topology/{id}/node  → devices
    - network-topology:network-topology/topology/{id}/link  → links
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

logger = logging.getLogger("drivers.opendaylight")


class OpenDaylightDriver(SDNControllerDriver):
    """
    Driver for OpenDaylight SDN controller via RESTCONF API.

    Supports both legacy RESTCONF (/restconf/operational/) and
    RFC 8040 (/rests/data/) endpoints.

    Usage:
        with OpenDaylightDriver("http://192.168.1.100:8181") as odl:
            devices = odl.get_managed_devices()
            links = odl.get_topology_links()
    """

    # Default topology IDs in OpenDaylight
    FLOW_TOPOLOGY = "flow:1"
    NETCONF_TOPOLOGY = "topology-netconf"

    def __init__(
        self,
        controller_url: str,
        username: str = "admin",
        password: str = "admin",
        topology_id: str = "flow:1",
        use_rfc8040: bool = False,
        verify_ssl: bool = False,
    ):
        super().__init__(
            controller_url=controller_url,
            vendor=Vendor.OPENDAYLIGHT,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            driver_type=DriverType.RESTCONF,
        )
        self.topology_id = topology_id
        self.use_rfc8040 = use_rfc8040

    @property
    def _base_path(self) -> str:
        """Return the correct base path based on API version."""
        if self.use_rfc8040:
            return "/rests/data"
        return "/restconf/operational"

    # ------------------------------------------------------------------
    # SDN-specific: managed devices and topology
    # ------------------------------------------------------------------

    def get_managed_devices(self) -> List[DeviceInfo]:
        """Get all devices (nodes) from the OpenDaylight topology."""
        endpoint = (
            f"{self._base_path}/network-topology:network-topology/"
            f"topology/{self.topology_id}"
        )

        try:
            data = self._get(endpoint)
        except Exception as e:
            logger.error(f"Failed to get ODL topology: {e}")
            return []

        devices = []
        topology = data.get("topology", [{}])
        if isinstance(topology, list):
            topology = topology[0] if topology else {}

        nodes = topology.get("node", [])
        for node in nodes:
            node_id = node.get("node-id", "unknown")

            devices.append(DeviceInfo(
                hostname=node_id,
                vendor=Vendor.OPENDAYLIGHT,
                management_ip=self._extract_ip_from_node_id(node_id),
            ))

        return devices

    def get_topology_links(self) -> List[Neighbor]:
        """Get all links from the OpenDaylight topology."""
        endpoint = (
            f"{self._base_path}/network-topology:network-topology/"
            f"topology/{self.topology_id}"
        )

        try:
            data = self._get(endpoint)
        except Exception as e:
            logger.error(f"Failed to get ODL links: {e}")
            return []

        neighbors = []
        topology = data.get("topology", [{}])
        if isinstance(topology, list):
            topology = topology[0] if topology else {}

        links = topology.get("link", [])
        for link in links:
            source = link.get("source", {})
            dest = link.get("destination", {})

            src_node = source.get("source-node", "")
            src_tp = source.get("source-tp", "")
            dst_node = dest.get("dest-node", "")
            dst_tp = dest.get("dest-tp", "")

            neighbors.append(Neighbor(
                local_interface=self._clean_tp_name(src_tp),
                remote_device=dst_node,
                remote_interface=self._clean_tp_name(dst_tp),
                discovery_protocol="sdn",
            ))

        return neighbors

    # ------------------------------------------------------------------
    # BaseDriver interface — device-specific queries
    # ------------------------------------------------------------------

    def get_hostname(self) -> str:
        """Return controller hostname (not individual device)."""
        return f"ODL@{self.controller_url}"

    def get_device_info(self) -> DeviceInfo:
        """Return controller info."""
        return DeviceInfo(
            hostname=f"ODL@{self.controller_url}",
            vendor=Vendor.OPENDAYLIGHT,
        )

    def get_interfaces(self) -> List[Interface]:
        """
        Get all termination points (interfaces) across all nodes.
        In OpenDaylight, interfaces are represented as node termination points.
        """
        endpoint = (
            f"{self._base_path}/network-topology:network-topology/"
            f"topology/{self.topology_id}"
        )

        try:
            data = self._get(endpoint)
        except Exception as e:
            logger.error(f"Failed to get ODL interfaces: {e}")
            return []

        interfaces = []
        topology = data.get("topology", [{}])
        if isinstance(topology, list):
            topology = topology[0] if topology else {}

        nodes = topology.get("node", [])
        for node in nodes:
            tps = node.get("termination-point", [])
            for tp in tps:
                tp_id = tp.get("tp-id", "")
                interfaces.append(Interface(
                    name=self._clean_tp_name(tp_id),
                    status=InterfaceStatus.UP,  # ODL assumes UP if present
                ))

        return interfaces

    def get_neighbors(self) -> List[Neighbor]:
        """Alias for get_topology_links()."""
        return self.get_topology_links()

    def get_routing_table(self) -> List[Route]:
        """
        OpenDaylight can expose flow tables, not traditional routing tables.
        This would require parsing OpenFlow flow entries — advanced feature.
        """
        logger.info("Routing table via ODL requires OpenFlow flow parsing (not yet implemented)")
        return []

    def get_arp_table(self) -> List[ARPEntry]:
        """
        ARP entries may be available via host-tracker or L2switch modules.
        """
        logger.info("ARP table via ODL requires host-tracker integration (not yet implemented)")
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_ip_from_node_id(node_id: str) -> str:
        """
        Extract IP from ODL node IDs like 'openflow:1' or 'netconf:192.168.1.1'.
        """
        if ":" in node_id:
            parts = node_id.split(":")
            candidate = parts[-1]
            # Check if it looks like an IP
            if all(p.isdigit() for p in candidate.split(".")) and candidate.count(".") == 3:
                return candidate
        return ""

    @staticmethod
    def _clean_tp_name(tp_id: str) -> str:
        """
        Clean termination point names.
        ODL uses format like 'openflow:1:2' → 'port-2'
        """
        if ":" in tp_id:
            parts = tp_id.split(":")
            if len(parts) >= 3:
                return f"{parts[0]}:{parts[1]}/port-{parts[2]}"
        return tp_id
