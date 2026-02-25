import os
import pynetbox
import logging

# Configure logging
logger = logging.getLogger("netbox.client")

# Defaults for development - in production uses env vars
DEFAULT_NETBOX_URL = os.environ.get("NETBOX_URL", "http://localhost:8000")
DEFAULT_NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")

_FAKE_TOKEN = "0123456789abcdef0123456789abcdef01234567"

if not DEFAULT_NETBOX_TOKEN:
    DEFAULT_NETBOX_TOKEN = _FAKE_TOKEN
    logger.warning(
        "NETBOX_TOKEN env var not set — using placeholder token. "
        "NetBox API calls will fail unless a real token is configured."
    )

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

    # ------------------------------------------------------------------
    #  Write helpers — best-effort push to NetBox
    # ------------------------------------------------------------------

    def create_device(self, name: str, mgmt_ip: str = None,
                      platform_slug: str = None,
                      device_type: str = "generic",
                      site: str = "default"):
        """Create a device in NetBox (best-effort).

        Requires that the referenced *device_type* and *site* already exist
        in NetBox.  Falls back to slug-based lookup.
        """
        if not self.api:
            logger.warning("NetBox API not connected — skipping create_device")
            return None
        try:
            payload: dict = {"name": name}

            # Resolve device-type by slug
            dt = self.api.dcim.device_types.get(slug=device_type)
            if dt:
                payload["device_type"] = dt.id
            else:
                logger.warning(f"device_type slug '{device_type}' not found in NetBox")
                return None

            # Resolve site by slug
            s = self.api.dcim.sites.get(slug=site)
            if s:
                payload["site"] = s.id
            else:
                logger.warning(f"site slug '{site}' not found in NetBox")
                return None

            # Optional platform
            if platform_slug:
                plat = self.api.dcim.platforms.get(slug=platform_slug)
                if plat:
                    payload["platform"] = plat.id

            result = self.api.dcim.devices.create(payload)
            logger.info(f"Created device '{name}' in NetBox (id={result.id})")
            return result
        except Exception as e:
            logger.error(f"Error creating device in NetBox: {e}")
            return None

    def delete_device_by_name(self, name: str):
        """Delete a device from NetBox by name (best-effort)."""
        if not self.api:
            return False
        try:
            dev = self.api.dcim.devices.get(name=name)
            if dev:
                dev.delete()
                logger.info(f"Deleted device '{name}' from NetBox")
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting device from NetBox: {e}")
            return False
