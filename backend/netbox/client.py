import os
import pynetbox
import logging
import requests
import urllib3

# Disable insecure request warnings for lab self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logger = logging.getLogger("netbox.client")


class _TimeoutSession(requests.Session):
    def __init__(self, timeout_seconds: float):
        super().__init__()
        self._timeout_seconds = timeout_seconds

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout_seconds)
        return super().request(*args, **kwargs)

# Custom field slugs for SSH credentials and OS Version in NetBox (must match Customization → Custom Fields)
def get_ssh_custom_field_slugs():
    return {
        "username": os.environ.get("NETBOX_CUSTOM_FIELD_SSH_USERNAME", "ssh_user"),
        "password": os.environ.get("NETBOX_CUSTOM_FIELD_SSH_PASSWORD", "ssh_password"),
    }


def get_os_version_custom_field_slug():
    return os.environ.get("NETBOX_CUSTOM_FIELD_OS_VERSION", "os_version")

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
        resolved_url = url or os.environ.get("NETBOX_URL", "")
        resolved_token = token or os.environ.get("NETBOX_TOKEN", "")

        if not resolved_token:
            logger.warning(
                "NETBOX_TOKEN env var not set — NetBox API calls will fail "
                "unless a real token is configured."
            )

        self.url = resolved_url
        self.token = resolved_token
        self.timeout_seconds = float(os.environ.get("NETBOX_HTTP_TIMEOUT_SECONDS", "10.0"))
        self.api = None
        self._connect()

    def _connect(self):
        # Prevent recursive loops to local backend (usually localhost:8000 / 127.0.0.1:8000)
        is_loopback = False
        if self.url:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(self.url)
                hostname = parsed.hostname or ""
                port = parsed.port or 80
                # Default backend port is 8000. If port is 8000 and matches loopback/localhost hostnames
                if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0") and port == 8000:
                    is_loopback = True
            except Exception:
                pass

        if not self.url or is_loopback:
            if is_loopback:
                logger.warning(
                    "NetBox URL '%s' loops back to the local backend itself. "
                    "Disabling NetBox connection to prevent recursive loopback / 404 errors.",
                    self.url,
                )
            else:
                logger.warning("NetBox URL is empty/not configured. NetBox API integration is disabled.")
            self.api = None
            return

        try:
            self.api = pynetbox.api(self.url, token=self.token)
            
            # Disable SSL verification for lab environment
            session = _TimeoutSession(self.timeout_seconds)
            session.verify = False
            self.api.http_session = session

            logger.info(
                "Initialized NetBox client at %s (SSL verification disabled, timeout=%ss)",
                self.url,
                self.timeout_seconds,
            )
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

    def get_device_by_name(self, name: str):
        """Fetch devices by exact NetBox name filter."""
        if not self.api or not name:
            return []
        try:
            return list(self.api.dcim.devices.filter(name=name))
        except Exception as e:
            logger.error(f"Error fetching device '{name}': {e}")
            return []

    def get_device_by_id(self, device_id: int):
        """Fetch a single device by ID (detail endpoint). Use this to get custom_fields (ssh_user, ssh_password, os_version)."""
        if not self.api or device_id is None:
            return None
        try:
            return self.api.dcim.devices.get(device_id)
        except Exception as e:
            logger.debug("Error fetching device id %s: %s", device_id, e)
            return None

    def get_cables(self):
        """Fetch all cables (connections)"""
        if not self.api: return []
        try:
            return list(self.api.dcim.cables.all())
        except Exception as e:
            logger.error(f"Error fetching cables: {e}")
            return []

    def get_interfaces_for_device(self, nb_device_id: int):
        """Fetch all interfaces for a NetBox device, with assigned IPs.

        Returns a list of dicts:
          [{"name": "GE0/0/1", "ip_address": "10.0.1.1", "subnet_mask": "255.255.255.0", ...}, ...]
        """
        if not self.api:
            return []
        try:
            ifaces = list(self.api.dcim.interfaces.filter(device_id=nb_device_id))
            ip_map = {}
            try:
                ips = list(self.api.ipam.ip_addresses.filter(device_id=nb_device_id))
                for ip_obj in ips:
                    assigned = getattr(ip_obj, "assigned_object", None)
                    iface_id = None
                    if assigned is not None:
                        iface_id = getattr(assigned, "id", None)
                    if iface_id is None:
                        iface_id = getattr(ip_obj, "assigned_object_id", None)
                    if iface_id is None:
                        continue
                    address_str = str(getattr(ip_obj, "address", "") or "")
                    ip_address = None
                    subnet_mask = None
                    if "/" in address_str:
                        ip_address, prefix = address_str.split("/")
                        subnet_mask = self._cidr_to_mask(int(prefix))
                    elif address_str:
                        ip_address = address_str
                    existing = ip_map.get(iface_id)
                    if existing is None or (not existing.get("ip_address") and ip_address):
                        ip_map[iface_id] = {"ip_address": ip_address, "subnet_mask": subnet_mask}
            except Exception:
                ip_map = {}

            result = []
            for iface in ifaces:
                iface_obj = iface
                iface_ip = ip_map.get(getattr(iface_obj, "id", None), {})
                ip_address = iface_ip.get("ip_address")
                subnet_mask = iface_ip.get("subnet_mask")

                # Fetch link connectivity details
                connected_endpoint = None
                link_status = None
                if getattr(iface_obj, "link_peers", None):
                    peers = iface_obj.link_peers
                    if peers:
                        peer = peers[0]
                        connected_endpoint = {
                            "device": getattr(getattr(peer, "device", None), "name", "Unknown"),
                            "interface": getattr(peer, "name", "Unknown")
                        }
                        # Find cable status if possible
                        if getattr(iface_obj, "cable", None):
                            link_status = str(getattr(iface_obj.cable, "status", "unknown"))

                result.append({
                    "name": iface_obj.name,
                    "ip_address": ip_address,
                    "subnet_mask": subnet_mask,
                    "mac_address": self._extract_interface_mac(iface_obj),
                    "type": str(getattr(iface_obj, "type", "other")),
                    "connected_endpoint": connected_endpoint,
                    "link_status": link_status,
                })
            return result
        except Exception as e:
            logger.error(f"Error fetching interfaces for device {nb_device_id}: {e}")
            return []

    def _extract_interface_mac(self, iface) -> str | None:
        try:
            primary = getattr(iface, "primary_mac_address", None)
            if primary is not None:
                if hasattr(primary, "mac_address"):
                    mac = str(getattr(primary, "mac_address", "") or "").strip()
                    if mac:
                        return mac
                # Some NetBox versions may return an ID/value directly.
                if isinstance(primary, int) and self.api:
                    mac_obj = self.api.dcim.mac_addresses.get(primary)
                    mac = str(getattr(mac_obj, "mac_address", "") or "").strip() if mac_obj else ""
                    if mac:
                        return mac
                if isinstance(primary, str) and primary.strip():
                    return primary.strip()
            raw = getattr(iface, "mac_address", None)
            return str(raw).strip() if raw else None
        except Exception:
            raw = getattr(iface, "mac_address", None)
            return str(raw).strip() if raw else None

    def _patch_interface(self, interface_id: int, payload: dict) -> bool:
        if not self.api:
            logger.warning("Cannot PATCH interface %s: NetBox API not connected", interface_id)
            return False
        try:
            base = (self.url or "").rstrip("/")
            url = f"{base}/api/dcim/interfaces/{int(interface_id)}/"
            headers = {}
            if self.token:
                headers["Authorization"] = f"Token {self.token}"
            response = self.api.http_session.patch(url, json=payload, headers=headers, timeout=self.timeout_seconds)
            if response.status_code in (200, 201):
                return True
            logger.warning("PATCH interface %s failed: %s %s", interface_id, response.status_code, response.text[:200])
            return False
        except Exception as e:
            logger.warning("PATCH interface %s exception: %s", interface_id, e)
            return False

    @staticmethod
    def _normalize_mac(mac: str | None) -> str | None:
        raw = str(mac or "").strip().lower()
        if not raw:
            return None
        hex_only = "".join(ch for ch in raw if ch in "0123456789abcdef")
        if len(hex_only) != 12:
            return None
        return ":".join(hex_only[idx:idx + 2] for idx in range(0, 12, 2))

    def _cidr_to_mask(self, prefix: int) -> str:
        """Convert a CIDR prefix length (0-32) to a dotted-decimal mask."""
        try:
            mask = (0xffffffff >> (32 - prefix)) << (32 - prefix)
            return (
                f"{(mask >> 24) & 0xff}.{(mask >> 16) & 0xff}."
                f"{(mask >> 8) & 0xff}.{mask & 0xff}"
            )
        except Exception:
            return "255.255.255.0" # Fallback

    # ------------------------------------------------------------------
    #  Auto-create prerequisite helpers
    # ------------------------------------------------------------------

    def _ensure_site(self, slug: str = "default", name: str = "Default Site"):
        """Get or create a NetBox Site."""
        # Defensive check for null or 'None' string
        if not slug or str(slug).lower() == "none": slug = "default"
        if not name or str(name).lower() == "none": name = "Default Site"
        
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
            logger.error(f"Failed to ensure site '{slug}': {e}")
            return None

    def _ensure_manufacturer(self, vendor: str):
        """Get or create a Manufacturer from our vendor string."""
        if not vendor or str(vendor).lower() == "none": vendor = "unknown"
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
            logger.error(f"Failed to ensure manufacturer '{slug}': {e}")
            return None

    def _ensure_device_type(self, vendor: str, model: str = None):
        """Get or create a DeviceType for the given vendor+model."""
        if not vendor or str(vendor).lower() == "none": vendor = "unknown"
        model_name = model or "Generic"
        if str(model_name).lower() == "none": model_name = "Generic"
        
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
            logger.error(f"Failed to ensure device_type '{slug}': {e}")
            return None

    def _ensure_device_role(self, role: str = "network-device"):
        """Get or create a DeviceRole."""
        if not role or str(role).lower() == "none": role = "network-device"
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

    def _build_device_custom_fields(self, ssh_user: str = None, ssh_password: str = None, os_version: str = None):
        """Build the NetBox custom_fields payload for device credential metadata."""
        slugs = get_ssh_custom_field_slugs()
        os_version_slug = get_os_version_custom_field_slug()
        cf = {}
        # Only send ssh_user/ssh_password when non-empty; NetBox may mark them required and reject empty
        if ssh_user is not None and str(ssh_user).strip() != "":
            cf[slugs["username"]] = str(ssh_user).strip()
        if ssh_password is not None and str(ssh_password).strip() != "":
            cf[slugs["password"]] = str(ssh_password).strip()
        if os_version is not None and str(os_version).strip() != "":
            cf[os_version_slug] = str(os_version).strip()
        return cf

    def _set_device_custom_fields(self, device, ssh_user: str = None, ssh_password: str = None, os_version: str = None):
        """Write custom fields (ssh_user, ssh_password, os_version) to NetBox device via direct PATCH so they persist."""
        if not device or not self.api:
            return
        cf = self._build_device_custom_fields(ssh_user, ssh_password, os_version)
        if not cf:
            return
        try:
            # Build merged custom_fields: start from current so we don't wipe other custom fields
            current = getattr(device, "custom_fields", None)
            if current is not None and hasattr(current, "items"):
                merged = dict(current)
            elif isinstance(current, dict):
                merged = dict(current)
            else:
                merged = {}
            merged.update(cf)

            # Direct PATCH so custom_fields are always sent (pynetbox Record.save() can omit them)
            device_id = getattr(device, "id", None)
            url = getattr(device, "url", None)
            if not url and device_id is not None:
                base = (self.url or "").rstrip("/")
                if not base.endswith("/"):
                    base += "/"
                url = f"{base}api/dcim/devices/{device_id}/"
            if not url:
                logger.warning("Device has no url/id; cannot PATCH custom fields.")
                return
            headers = {}
            if self.token:
                headers["Authorization"] = f"Token {self.token}"
            resp = self.api.http_session.patch(
                url, json={"custom_fields": merged}, headers=headers, timeout=self.timeout_seconds
            )
            if resp.status_code in (200, 201):
                logger.info("Updated custom fields on device '%s' (%s)", getattr(device, "name", device), list(cf.keys()))
            else:
                logger.warning("PATCH custom fields failed for device '%s': %s %s", getattr(device, "name", device), resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("Could not set custom fields on device: %s", e)

    def create_device(self, name: str, mgmt_ip: str = None,
                      mgmt_mask: str = None,
                      vendor: str = "cisco_ios",
                      model: str = None,
                      site: str = "default",
                      role: str = "network-device",
                      ssh_user: str = None,
                      ssh_password: str = None,
                      serial: str = None,
                      os_version: str = None):
        """Create a device in NetBox.

        Auto-creates all prerequisite objects (Site, Manufacturer,
        DeviceType, DeviceRole, Platform) if they don't exist yet.
        Optionally syncs SSH username/password to device custom fields if defined in NetBox.
        """
        if not self.api:
            logger.warning("NetBox API not connected — skipping create_device")
            return None
        try:
            # Check if device already exists — still update management IP so GUI changes reflect
            existing = self.api.dcim.devices.get(name=name)
            if existing:
                logger.info(f"Device '{name}' already exists in NetBox (id={existing.id})")
                if mgmt_ip:
                    self._assign_management_ip(existing, mgmt_ip, mgmt_mask)
                
                # Update serial if provided
                if serial:
                    existing.serial = serial
                    existing.save()

                self._set_device_custom_fields(existing, ssh_user, ssh_password, os_version)
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
                "serial": serial or "",
            }
            if platform_id:
                payload["platform"] = platform_id
            custom_fields = self._build_device_custom_fields(ssh_user, ssh_password, os_version)
            if custom_fields:
                payload["custom_fields"] = custom_fields

            result = self.api.dcim.devices.create(payload)
            logger.info(f"Created device '{name}' in NetBox (id={result.id})")

            # Optionally assign management IP
            if mgmt_ip and result:
                self._assign_management_ip(result, mgmt_ip, mgmt_mask)
            self._set_device_custom_fields(result, ssh_user, ssh_password, os_version)
            return result
        except Exception as e:
            logger.error(f"Error creating device in NetBox: {e}")
            raise  # Let the caller handle the error message

    def update_device(self, name: str, mgmt_ip: str = None,
                      mgmt_mask: str = None,
                      vendor: str = None,
                      model: str = None,
                      site: str = None,
                      role: str = None,
                      original_name: str = None,
                      ssh_user: str = None,
                      ssh_password: str = None,
                      serial: str = None,
                      os_version: str = None):
        """Update an existing device in NetBox. Optionally sync SSH credentials to custom fields."""
        if not self.api: return None
        try:
            device = self.api.dcim.devices.get(name=original_name or name)
            if not device:
                return self.create_device(name, mgmt_ip, mgmt_mask, vendor, model, site, role, ssh_user, ssh_password, serial, os_version)

            updates = {}
            if site:
                s_id = self._ensure_site(slug=site)
                if s_id: updates["site"] = s_id
            if vendor:
                dt_id = self._ensure_device_type(vendor, model)
                if dt_id: updates["device_type"] = dt_id
            if role:
                r_id = self._ensure_device_role(role)
                if r_id: updates["role"] = r_id
            if name and name != (original_name or device.name):
                updates["name"] = name
            if vendor:
                p_id = self._ensure_platform(vendor)
                if p_id: updates["platform"] = p_id
            if serial is not None:
                updates["serial"] = serial

            if updates:
                # Use attribute assignment for serial to be safer, standard fields in update()
                if "serial" in updates:
                    device.serial = updates.pop("serial")
                
                if updates:
                    device.update(updates)
                device.save()
                logger.info(f"Updated device '{name}' in NetBox with {list(updates.keys())}")

            if mgmt_ip:
                self._assign_management_ip(device, mgmt_ip, mgmt_mask)
            self._set_device_custom_fields(device, ssh_user, ssh_password, os_version)
            return device
        except Exception as e:
            logger.error(f"Error updating device in NetBox: {e}")
            raise  # Let the caller handle the error message

    def get_or_create_interface(self, device_name: str, name: str, iface_type: str = "other"):
        """Ensure an interface exists on a device in NetBox."""
        if not self.api: return None
        try:
            device = self.api.dcim.devices.get(name=device_name)
            if not device:
                logger.warning(f"Device '{device_name}' not found in NetBox — cannot create interface '{name}'")
                return None
            
            iface = self.api.dcim.interfaces.get(device_id=device.id, name=name)
            if iface:
                return iface
            
            new_iface = self.api.dcim.interfaces.create({
                "device": device.id,
                "name": name,
                "type": iface_type
            })
            logger.info(f"Created interface '{name}' on device '{device_name}' in NetBox")
            return new_iface
        except Exception as e:
            logger.error(f"Error ensuring interface '{name}' on '{device_name}': {e}")
            return None

    def set_interface_mac_address(self, device_name: str, interface_name: str, mac_address: str):
        """Assign MAC address to interface and set it as primary in NetBox.

        Uses dcim.mac-addresses + interface.primary_mac_address when available.
        Falls back to direct interface.mac_address update for older NetBox versions.
        """
        if not self.api:
            return None

        normalized_mac = self._normalize_mac(mac_address)
        if not normalized_mac:
            logger.warning("Invalid MAC address '%s' for %s:%s", mac_address, device_name, interface_name)
            return None

        try:
            iface = self.get_or_create_interface(device_name, interface_name)
            if not iface:
                return None

            # Preferred approach (NetBox 4.x): create/reuse mac-address object, then set primary.
            mac_obj = None
            try:
                mac_obj = self.api.dcim.mac_addresses.get(mac_address=normalized_mac)
                if mac_obj:
                    assigned_obj_id = getattr(mac_obj, "assigned_object_id", None)
                    assigned_obj_type = str(getattr(mac_obj, "assigned_object_type", "") or "")
                    if assigned_obj_id != iface.id or "dcim.interface" not in assigned_obj_type:
                        mac_obj.update({
                            "assigned_object_type": "dcim.interface",
                            "assigned_object_id": iface.id,
                        })
                        mac_obj.save()
                if not mac_obj:
                    mac_obj = self.api.dcim.mac_addresses.create({
                        "mac_address": normalized_mac,
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": iface.id,
                    })
                if mac_obj:
                    primary_id = getattr(mac_obj, "id", None)
                    patched = self._patch_interface(iface.id, {"primary_mac_address": primary_id}) if primary_id else False
                    if not patched:
                        iface.update({"primary_mac_address": primary_id})
                        iface.save()
                    logger.info("Set primary MAC '%s' on %s:%s", normalized_mac, device_name, interface_name)
                    return mac_obj
            except Exception as e:
                logger.debug("NetBox mac-address object flow failed for %s:%s: %s", device_name, interface_name, e)

            # Compatibility fallback (older NetBox): direct field on interface.
            try:
                iface.update({"mac_address": normalized_mac})
                iface.save()
                logger.info("Set interface MAC '%s' on %s:%s using legacy field", normalized_mac, device_name, interface_name)
                return iface
            except Exception as e:
                logger.error("Failed setting MAC on %s:%s: %s", device_name, interface_name, e)
                return None
        except Exception as e:
            logger.error("Error setting MAC for %s:%s: %s", device_name, interface_name, e)
            return None

    def assign_ip_to_interface(self, device_name: str, interface_name: str, ip_address: str, 
                               subnet_mask: str = None, set_as_primary: bool = False):
        """Assign an IP address to a specific interface on a device."""
        if not self.api: return None
        try:
            device = self.api.dcim.devices.get(name=device_name)
            if not device: return None

            iface = self.get_or_create_interface(device_name, interface_name)
            if not iface: return None

            # Ensure IP has CIDR notation
            if "/" not in ip_address:
                # Convert dot-decimal mask to prefix length if provided
                prefix = 24
                if subnet_mask:
                    try:
                        # Simple mask-to-prefix conversion for 255.255.255.0 etc.
                        octets = [int(o) for o in subnet_mask.split(".")]
                        binary_str = "".join([bin(o).lstrip("0b").zfill(8) for o in octets])
                        prefix = binary_str.find("0")
                        if prefix == -1: prefix = 32
                    except:
                        pass
                ip_with_cidr = f"{ip_address}/{prefix}"
            else:
                ip_with_cidr = ip_address

            # Check if IP exists
            ip = self.api.ipam.ip_addresses.get(address=ip_with_cidr)
            if ip:
                # Update assignment if needed
                if ip.assigned_object_id != iface.id:
                    ip.update({
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": iface.id
                    })
                    ip.save()
            else:
                ip = self.api.ipam.ip_addresses.create({
                    "address": ip_with_cidr,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": iface.id
                })

            if set_as_primary:
                device.primary_ip4 = ip.id
                device.save()
            
            logger.info(f"Assigned IP {ip_with_cidr} to '{interface_name}' on '{device_name}'")
            return ip
        except Exception as e:
            logger.error(f"Error assigning IP {ip_address} to '{interface_name}' on '{device_name}': {e}")
            return None

    def create_cable(self, src_device_name: str, src_interface_name: str, 
                     dst_device_name: str, dst_interface_name: str):
        """Create a cable (link) between two device interfaces."""
        if not self.api: return None
        try:
            src_iface = self.get_or_create_interface(src_device_name, src_interface_name)
            dst_iface = self.get_or_create_interface(dst_device_name, dst_interface_name)
            
            if not src_iface or not dst_iface:
                logger.warning(f"Prerequisite interfaces for cable '{src_device_name}:{src_interface_name} -> {dst_device_name}:{dst_interface_name}' missing")
                return None

            # Check if cable already exists on either side
            # NetBox cables are unique per termination point
            if src_iface.cable or dst_iface.cable:
                logger.info(f"Cable already exists for termination point on '{src_device_name}' or '{dst_device_name}'")
                return src_iface.cable or dst_iface.cable

            cable = self.api.dcim.cables.create({
                "a_terminations": [{"object_type": "dcim.interface", "object_id": src_iface.id}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": dst_iface.id}],
                "status": "connected"
            })
            logger.info(f"Created cable from {src_device_name}:{src_interface_name} to {dst_device_name}:{dst_interface_name}")
            return cable
        except Exception as e:
            logger.error(f"Error creating cable: {e}")
            return None

    def _assign_management_ip(self, device, mgmt_ip: str, mgmt_mask: str = None):
        """Legacy helper for setting primary management IP."""
        return self.assign_ip_to_interface(device.name, "Management", mgmt_ip, subnet_mask=mgmt_mask, set_as_primary=True)

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

    def delete_interface_by_name(self, device_name: str, interface_name: str):
        """Delete an interface from NetBox by device + interface name (best-effort)."""
        if not self.api:
            return False
        try:
            device = self.api.dcim.devices.get(name=device_name)
            if not device:
                return False
            iface = self.api.dcim.interfaces.get(device_id=device.id, name=interface_name)
            if iface:
                iface.delete()
                logger.info(f"Deleted interface '{interface_name}' from device '{device_name}' in NetBox")
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting interface from NetBox: {e}")
            return False
