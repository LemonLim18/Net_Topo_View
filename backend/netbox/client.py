import os
import pynetbox
import logging
import requests
import urllib3

# Disable insecure request warnings for lab self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logger = logging.getLogger("netbox.client")

# M11: UI Role Key -> NetBox Role Name/Slug mapping
ROLE_MAPPING = {
    "core": "Core Switch / Router",
    "distribution": "Distribution Switch",
    "access": "Access Switch",
    "firewall": "Firewall",
    "network-device": "Network Device",
}


class NetBoxClient:
    _instance = None

    def __init__(self, url=None, token=None):
        # Read env vars lazily (at instantiation time, AFTER load_dotenv)
        resolved_url = url or os.environ.get("NETBOX_URL", "http://localhost:8000")
        resolved_token = token or os.environ.get("NETBOX_TOKEN", "")

        if not resolved_token:
            logger.warning(
                "NETBOX_TOKEN env var not set — NetBox API calls will fail "
                "unless a real token is configured."
            )

        self.url = resolved_url
        self.token = resolved_token
        self.api = None
        self._connect()

    def _connect(self):
        try:
            self.api = pynetbox.api(self.url, token=self.token)
            
            # Disable SSL verification for lab environment
            session = requests.Session()
            session.verify = False
            self.api.http_session = session

            logger.info(f"Initialized NetBox client at {self.url} (SSL verification disabled)")
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
    #  Auto-create prerequisite helpers
    # ------------------------------------------------------------------

    def _ensure_site(self, slug: str = "default", name: str = "Default Site"):
        """Get or create a NetBox Site."""
        try:
            site = self.api.dcim.sites.get(slug=slug)
            if site:
                return getattr(site, "id", None)
            result = self.api.dcim.sites.create({"name": name, "slug": slug})
            res_id = getattr(result, "id", None)
            if res_id:
                logger.info(f"Auto-created site '{slug}' (id={res_id})")
            return res_id
        except Exception as e:
            logger.error(f"Failed to ensure site '{slug}': {e} (type={type(e)})")
            return None

    def _ensure_manufacturer(self, vendor: str):
        """Get or create a Manufacturer from our vendor string."""
        # Map our vendor strings to friendly names
        vendor_map = {
            "cisco_ios":     ("Cisco",   "cisco"),
            "cisco_xe":      ("Cisco",   "cisco"),
            "huawei_vrp":    ("Huawei",  "huawei"),
            "juniper_junos": ("Juniper", "juniper"),
        }
        name, slug = vendor_map.get(vendor, (vendor.replace("_", " ").title(), vendor.replace("_", "-")))
        try:
            mfr = self.api.dcim.manufacturers.get(slug=slug)
            if mfr:
                return getattr(mfr, "id", None)
            result = self.api.dcim.manufacturers.create({"name": name, "slug": slug})
            res_id = getattr(result, "id", None)
            if res_id:
                logger.info(f"Auto-created manufacturer '{name}' (id={res_id})")
            return res_id
        except Exception as e:
            logger.error(f"Failed to ensure manufacturer '{slug}': {e} (type={type(e)})")
            return None

    def _ensure_device_type(self, vendor: str, model: str = None):
        """Get or create a DeviceType for the given vendor+model."""
        model_name = model or "Generic"
        slug = f"{vendor.replace('_', '-')}-{model_name.lower().replace(' ', '-')}"
        slug = slug[:50]  # NetBox slug max length
        try:
            dt = self.api.dcim.device_types.get(slug=slug)
            if dt:
                return getattr(dt, "id", None)
            mfr_id = self._ensure_manufacturer(vendor)
            if not mfr_id:
                return None
            result = self.api.dcim.device_types.create({
                "model": model_name,
                "slug": slug,
                "manufacturer": mfr_id,
            })
            res_id = getattr(result, "id", None)
            if res_id:
                logger.info(f"Auto-created device_type '{slug}' (id={res_id})")
            return res_id
        except Exception as e:
            logger.error(f"Failed to ensure device_type '{slug}': {e} (type={type(e)})")
            return None

    def _ensure_device_role(self, role: str = "network-device"):
        """Get or create a DeviceRole."""
        # Use mapping or fallback to title-cased slug
        name = ROLE_MAPPING.get(role, role.replace("-", " ").title())
        slug = role.lower().replace(" ", "-")
        try:
            dr = self.api.dcim.device_roles.get(slug=slug)
            if dr:
                return getattr(dr, "id", None)
            result = self.api.dcim.device_roles.create({
                "name": name,
                "slug": slug,
                "color": "4caf50",
            })
            res_id = getattr(result, "id", None)
            if res_id:
                logger.info(f"Auto-created device_role '{name}' (id={res_id})")
            return res_id
        except Exception as e:
            logger.error(f"Failed to ensure device_role '{role}': {e} (type={type(e)})")
            return None

    def _ensure_platform(self, vendor: str):
        """Get or create a Platform for the vendor."""
        platform_map = {
            "cisco_ios":     ("Cisco IOS",      "cisco-ios"),
            "cisco_xe":      ("Cisco IOS-XE",   "cisco-xe"),
            "huawei_vrp":    ("Huawei VRP",     "huawei-vrp"),
            "juniper_junos": ("Juniper JunOS",  "juniper-junos"),
        }
        name, slug = platform_map.get(vendor, (vendor.replace("_", " ").title(), vendor.replace("_", "-")))
        try:
            plat = self.api.dcim.platforms.get(slug=slug)
            if plat:
                return getattr(plat, "id", None)
            mfr_id = self._ensure_manufacturer(vendor)
            result = self.api.dcim.platforms.create({
                "name": name,
                "slug": slug,
                "manufacturer": mfr_id,
            })
            res_id = getattr(result, "id", None)
            if res_id:
                logger.info(f"Auto-created platform '{slug}' (id={res_id})")
            return res_id
        except Exception as e:
            logger.error(f"Failed to ensure platform '{slug}': {e} (type={type(e)})")
            return None

    # ------------------------------------------------------------------
    #  Write helpers — best-effort push to NetBox
    # ------------------------------------------------------------------

    def create_device(self, name: str, mgmt_ip: str = None,
                      vendor: str = "cisco_ios",
                      model: str = None,
                      site: str = "default",
                      role: str = "network-device"):
        """Create a device in NetBox.

        Auto-creates all prerequisite objects (Site, Manufacturer,
        DeviceType, DeviceRole, Platform) if they don't exist yet.
        """
        if not self.api:
            logger.warning("NetBox API not connected — skipping create_device")
            return None
        try:
            # Check if device already exists
            existing = self.api.dcim.devices.get(name=name)
            if existing:
                logger.info(f"Device '{name}' already exists in NetBox (id={existing.id})")
                return existing

            # Auto-create all prerequisites
            site_id = self._ensure_site(slug=site)
            dt_id = self._ensure_device_type(vendor, model)
            role_id = self._ensure_device_role(role)
            platform_id = self._ensure_platform(vendor)

            if not all([site_id, dt_id, role_id]):
                logger.error(f"Cannot create device '{name}': missing prerequisites "
                             f"(site={site_id}, dt={dt_id}, role={role_id})")
                return None

            payload = {
                "name": name,
                "device_type": dt_id,
                "site": site_id,
                "role": role_id,
                "status": "planned",
            }
            if platform_id:
                payload["platform"] = platform_id

            result = self.api.dcim.devices.create(payload)
            logger.info(f"Created device '{name}' in NetBox (id={result.id})")

            # Optionally assign management IP
            if mgmt_ip and result:
                self._assign_management_ip(result, mgmt_ip)

            return result
        except Exception as e:
            logger.error(f"Error creating device in NetBox: {e}")
            return None

    def update_device(self, name: str, mgmt_ip: str = None,
                      vendor: str = None,
                      model: str = None,
                      site: str = None,
                      role: str = None):
        """Update an existing device in NetBox.
        
        Args:
            name: Current hostname in NetBox (the key).
            mgmt_ip: New management IP.
            vendor/model/site/role: Updated metadata.
        """
        if not self.api:
            return None
        try:
            device = self.api.dcim.devices.get(name=name)
            if not device:
                # If it doesn't exist, maybe it was renamed locally?
                # For now, we fallback to create if missing and status is registered.
                return self.create_device(name, mgmt_ip, vendor, model, site, role)

            # Update prerequisites if they changed
            updates = {}
            if site:
                s_id = self._ensure_site(slug=site)
                if s_id: updates["site"] = s_id
            if vendor and model:
                dt_id = self._ensure_device_type(vendor, model)
                if dt_id: updates["device_type"] = dt_id
            if role:
                r_id = self._ensure_device_role(role)
                if r_id: updates["role"] = r_id
            if vendor:
                p_id = self._ensure_platform(vendor)
                if p_id: updates["platform"] = p_id

            if updates:
                device.update(updates)
                device.save()
                logger.info(f"Updated device '{name}' in NetBox with {list(updates.keys())}")

            # Update management IP if provided
            if mgmt_ip:
                self._assign_management_ip(device, mgmt_ip)

            return device
        except Exception as e:
            logger.error(f"Error updating device in NetBox: {e}")
            return None

    def _assign_management_ip(self, device, mgmt_ip: str):
        """Create a management interface + IP and assign as primary."""
        try:
            # Ensure IP has CIDR notation
            if "/" not in mgmt_ip:
                mgmt_ip = f"{mgmt_ip}/24"

            # Create a management interface
            iface = self.api.dcim.interfaces.create({
                "device": device.id,
                "name": "Management",
                "type": "virtual",
            })

            # Create the IP address
            ip = self.api.ipam.ip_addresses.create({
                "address": mgmt_ip,
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": iface.id,
            })

            # Set as primary IPv4
            device.primary_ip4 = ip.id
            device.save()
            logger.info(f"Assigned management IP {mgmt_ip} to device '{device.name}'")
        except Exception as e:
            logger.warning(f"Could not assign management IP: {e}")

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
