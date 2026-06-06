"""
Abstract SDN Controller driver — shared logic for REST API-based controllers.

Handles HTTP client setup, authentication, JSON parsing, and error handling.
Vendor-specific subclasses (OpenDaylight, ONOS) implement the actual API
endpoint mapping and response parsing.

Inheritance:
    BaseDriver → SDNControllerDriver → OpenDaylightDriver
                                      → ONOSDriver
"""

import logging
from abc import abstractmethod
from typing import List, Optional, Dict, Any

import requests
from requests.auth import HTTPBasicAuth

from drivers.base import BaseDriver
from drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    DriverResult,
    DriverType,
    Vendor,
)

logger = logging.getLogger("drivers.sdn")


class SDNControllerDriver(BaseDriver):
    """
    Abstract REST API-based driver for SDN controllers.

    Provides shared HTTP client logic. Subclasses implement endpoint
    mapping and response parsing for their specific controller.

    SDN controller drivers manage MULTIPLE devices through one connection.
    Methods like get_interfaces() accept an optional device_id parameter
    to query a specific device managed by the controller.
    """

    DEFAULT_TIMEOUT = 10  # seconds

    def __init__(
        self,
        controller_url: str,
        vendor: Vendor,
        username: str = "admin",
        password: str = "admin",
        verify_ssl: bool = False,
        driver_type: DriverType = DriverType.REST_API,
    ):
        super().__init__(vendor=vendor, driver_type=driver_type)
        # Normalize URL (strip trailing slash)
        self.controller_url = controller_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl

        # Session for connection pooling
        self._session: Optional[requests.Session] = None

    # ------------------------------------------------------------------
    # HTTP client methods — shared by all SDN drivers
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initialize HTTP session with auth."""
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(self.username, self.password)
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._session.verify = self.verify_ssl
        logger.info(f"SDN session created for {self.controller_url}")

    def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            self._session.close()
            self._session = None
        logger.debug(f"SDN session closed for {self.controller_url}")

    def is_alive(self) -> bool:
        """Check if the controller is reachable."""
        if self._session is None:
            return False
        try:
            resp = self._session.get(
                self.controller_url,
                timeout=self.DEFAULT_TIMEOUT,
            )
            return resp.status_code < 500
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """
        Send GET request to controller API.
        Returns parsed JSON response.
        Raises requests.HTTPError on failure.
        """
        if self._session is None:
            raise ConnectionError("Not connected. Call connect() first.")

        url = f"{self.controller_url}{endpoint}"
        logger.debug(f"GET {url}")

        resp = self._session.get(url, params=params, timeout=self.DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return resp.json() if resp.content else {}

    def _post(self, endpoint: str, data: Any = None) -> Dict:
        """Send POST request to controller API."""
        if self._session is None:
            raise ConnectionError("Not connected. Call connect() first.")

        url = f"{self.controller_url}{endpoint}"
        logger.debug(f"POST {url}")

        resp = self._session.post(url, json=data, timeout=self.DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return resp.json() if resp.content else {}

    def _put(self, endpoint: str, data: Any = None) -> Dict:
        """Send PUT request to controller API."""
        if self._session is None:
            raise ConnectionError("Not connected. Call connect() first.")

        url = f"{self.controller_url}{endpoint}"
        logger.debug(f"PUT {url}")

        resp = self._session.put(url, json=data, timeout=self.DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return resp.json() if resp.content else {}

    def _delete(self, endpoint: str) -> bool:
        """Send DELETE request. Returns True on success."""
        if self._session is None:
            raise ConnectionError("Not connected. Call connect() first.")

        url = f"{self.controller_url}{endpoint}"
        logger.debug(f"DELETE {url}")

        resp = self._session.delete(url, timeout=self.DEFAULT_TIMEOUT)
        resp.raise_for_status()

        return True

    # ------------------------------------------------------------------
    # SDN-specific: list all managed devices
    # ------------------------------------------------------------------

    @abstractmethod
    def get_managed_devices(self) -> List[DeviceInfo]:
        """Return all devices managed by this SDN controller."""
        ...

    @abstractmethod
    def get_topology_links(self) -> List[Neighbor]:
        """Return all links in the controller's topology view."""
        ...

    # ------------------------------------------------------------------
    # BaseDriver overrides — SDN controllers don't have running configs
    # ------------------------------------------------------------------

    def get_running_config(self) -> str:
        """SDN controllers don't expose running configs in the same way."""
        logger.warning(
            f"{self.__class__.__name__}: get_running_config() not applicable "
            "for SDN controllers. Use get_device_config(device_id) instead."
        )
        return ""

    def push_config(self, commands: List[str]) -> DriverResult:
        """
        SDN controllers use REST API operations, not CLI commands.
        Subclasses should override with controller-specific API calls.
        """
        return DriverResult(
            success=False,
            error="push_config() via CLI not supported for SDN controllers. "
                  "Use controller-specific API methods instead.",
            driver_type=self._driver_type,
        )
