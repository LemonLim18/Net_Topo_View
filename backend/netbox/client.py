import os
import pynetbox
import logging

# Configure logging
logger = logging.getLogger("netbox.client")

# Defaults for development - in production uses env vars
DEFAULT_NETBOX_URL = os.environ.get("NETBOX_URL", "http://localhost:8000")
DEFAULT_NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "0123456789abcdef0123456789abcdef01234567")

class NetBoxClient:
    _instance = None

    def __init__(self, url=None, token=None):
        self.url = url or DEFAULT_NETBOX_URL
        self.token = token or DEFAULT_NETBOX_TOKEN
        self.api = None
        self._connect()

    def _connect(self):
        try:
            self.api = pynetbox.api(self.url, token=self.token)
            # Check connection (optional, lightweight call)
            # self.api.status() 
            logger.info(f"Initialized NetBox client at {self.url}")
        except Exception as e:
            logger.error(f"Failed to connect to NetBox: {e}")
            self.api = None

    @classmethod
    def get_client(cls):
        """Singleton accessor"""
        if cls._instance is None:
            cls._instance = NetBoxClient()
        return cls._instance

    def get_devices(self):
        """Fetch all devices from NetBox"""
        if not self.api: return []
        try:
            # Return a generator or list
            return list(self.api.dcim.devices.all())
        except Exception as e:
            logger.error(f"Error fetching devices: {e}")
            return []

    def get_cables(self):
        """Fetch all cables (connections)"""
        if not self.api: return []
        try:
            return list(self.api.dcim.cables.all())
        except Exception as e:
            logger.error(f"Error fetching cables: {e}")
            return []
