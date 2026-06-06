from datetime import datetime
import logging
import os
from db.manager import DatabaseManager
from db.models import Device, Interface
from llm.context_contract import CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY
from llm.refresh_fabric import publish_context_refresh_event
from llm.refresh_state import get_refresh_state_manager
from netbox.client import NetBoxClient, get_ssh_custom_field_slugs, get_os_version_custom_field_slug

logger = logging.getLogger("netbox.sync_devices")

# NetBox status value -> local status mapping
# NetBox uses: planned, staged, active, decommissioning, offline
_STATUS_MAP = {
    "planned":          "planned",
    "staged":           "planned",
    "active":           "active",
    "decommissioning":  "down",
    "offline":          "down",
}

# Reverse role mapping: NetBox role slug -> local role key
# Covers both controller-created slugs ("distribution") and
# user/NetBox-created slugs ("distribution-switch")
_ROLE_SLUG_MAP = {
    "core":                  "core",
    "core-switch":           "core",
    "core-switch---router":  "core",
    "core-switch-router":    "core",
    "distribution":          "distribution",
    "distribution-switch":   "distribution",
    "access":                "access",
    "access-switch":         "access",
    "firewall":              "firewall",
    "network-device":        "network-device",
}


class DeviceSyncer:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.nb_client = NetBoxClient.get_client()

    def sync_all_devices(self):
        """
        Fetches all devices from NetBox and upserts them into the local DB.
        Also syncs interfaces and their IP assignments for each device.
        Preserves 'source' for devices that already exist locally.
        """
        refresh_state = get_refresh_state_manager()
        refresh_state.record_start("inventory", trigger="netbox_sync")

        if not self.nb_client.api:
            logger.warning("NetBox API not initialized. Skipping sync.")
            result = {"status": "skipped", "reason": "no_connection"}
            refresh_state.record_skipped("inventory", result=result, trigger="netbox_sync")
            return result

        nb_devices = self.nb_client.get_devices()
        synced_count = 0
        iface_count = 0
        
        fetch_device_details = str(os.getenv("NETBOX_SYNC_FETCH_DEVICE_DETAILS", "false")).strip().lower() in {"1", "true", "yes", "on"}

        for nb_dev in nb_devices:
            try:
                # Fetch full device by ID so we get custom_fields (list endpoint often omits them)
                full_dev = self.nb_client.get_device_by_id(nb_dev.id) if (fetch_device_details and getattr(nb_dev, "id", None)) else None
                nb_dev = full_dev if full_dev is not None else nb_dev

                hostname = nb_dev.name
                mgmt_ip = str(nb_dev.primary_ip4).split('/')[0] if nb_dev.primary_ip4 else None
                platform = str(nb_dev.platform.slug) if nb_dev.platform else "unknown"

                # Map platform slug to our internal vendor strings
                vendor = self._map_platform_to_vendor(platform)

                # Map NetBox status to local status
                nb_status = str(getattr(nb_dev, "status", "")).lower().strip()
                # pynetbox may return a Record object for status; extract its value
                if hasattr(nb_dev.status, "value"):
                    nb_status = str(nb_dev.status.value).lower().strip()
                status = _STATUS_MAP.get(nb_status, "planned")

                # Extract model from device_type
                model = None
                if nb_dev.device_type:
                    model = str(nb_dev.device_type.model) if hasattr(nb_dev.device_type, "model") else str(nb_dev.device_type)

                # Extract role from device role slug
                role = "network-device"
                if nb_dev.role:
                    role_slug = str(nb_dev.role.slug) if hasattr(nb_dev.role, "slug") else str(nb_dev.role)
                    role = _ROLE_SLUG_MAP.get(role_slug, role_slug)

                # Smart source tracking:
                # If the device already exists locally, keep its current source.
                # Only brand-new devices (not yet in local DB) get source="netbox".
                # Match by hostname first, then by management IP so that when a device
                # is renamed in NetBox (e.g. Acc_Huawei_SW3 -> Acc_Huawei_3), we update
                # the existing row instead of creating a duplicate.
                existing = self.db.get_device_by_hostname(hostname)
                if not existing and mgmt_ip:
                    existing = self.db.get_device_by_management_ip(mgmt_ip)
                source = existing.source if existing else "netbox"
                device_id_existing = existing.id if existing else None

                # Extract serial number from standard field
                serial = str(nb_dev.serial) if getattr(nb_dev, "serial", None) else None
                
                # OS Version is often not in a standard DCIM field, but may be in 'platform' or custom fields
                # We'll check platforms first
                os_version = None
                if nb_dev.platform and hasattr(nb_dev.platform, "name"):
                    # Sometimes the version is in the platform name or custom field
                    pass
                
                # Check for custom field os_version (slug from env or default "os_version")
                if hasattr(nb_dev, "custom_fields") and nb_dev.custom_fields:
                    os_version_slug = get_os_version_custom_field_slug()
                    os_version = nb_dev.custom_fields.get(os_version_slug) or os_version

                # Smart Merging: Only overwrite local fields if NetBox has a value
                # This prevents "clearing" manually entered data when NetBox is incomplete.
                final_vendor = vendor if vendor != "unknown" else (existing.vendor if existing else "unknown")
                final_model = model if model else (existing.model if existing else None)
                final_role = role if role != "network-device" else (existing.role if existing else "network-device")
                final_serial = serial if serial else (existing.serial_number if existing else None)
                final_os_version = os_version if os_version else (existing.os_version if existing else None)
                final_mgmt_ip = mgmt_ip if mgmt_ip else (existing.management_ip if existing else None)

                # Preserving local credentials or syncing from NetBox custom fields
                nb_ssh_user = None
                nb_ssh_password = None
                if hasattr(nb_dev, "custom_fields") and nb_dev.custom_fields:
                    slugs = get_ssh_custom_field_slugs()
                    nb_ssh_user = nb_dev.custom_fields.get(slugs["username"])
                    nb_ssh_password = nb_dev.custom_fields.get(slugs["password"])

                ssh_user = nb_ssh_user if nb_ssh_user else (existing.ssh_user if existing else None)
                ssh_password = nb_ssh_password if nb_ssh_password else (existing.ssh_password if existing else None)
                ssh_port = existing.ssh_port if existing else 22
                
                # Preserve local fields not stored in standard NetBox properties
                management_interface = existing.management_interface if existing else None
                oob_gateway_ip = existing.oob_gateway_ip if existing else None

                device = Device(
                    hostname=hostname,
                    management_ip=final_mgmt_ip,
                    management_interface=management_interface,
                    oob_gateway_ip=oob_gateway_ip,
                    vendor=final_vendor,
                    model=final_model,
                    status=status,
                    role=final_role,
                    source=source,
                    netbox_id=nb_dev.id,
                    id=device_id_existing,
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
                    ssh_port=ssh_port,
                    serial_number=final_serial,
                    os_version=final_os_version,
                )
                
                device_id = self.db.upsert_device(device)
                synced_count += 1
                logger.debug(f"Synced device: {hostname} (status={status}, model={model}, role={role}, source={source})")

                # --- Sync interfaces for this device ---
                nb_device_id = nb_dev.id
                nb_ifaces = self.nb_client.get_interfaces_for_device(nb_device_id)
                existing_interfaces = {
                    iface.name: iface
                    for iface in self.db.get_interfaces_for_device(device_id)
                    if iface.name
                }
                for nb_iface in nb_ifaces:
                    existing_iface = existing_interfaces.get(nb_iface["name"])
                    ip_address = nb_iface.get("ip_address")
                    subnet_mask = nb_iface.get("subnet_mask")
                    interface_type = "l3" if ip_address else (
                        existing_iface.interface_type if existing_iface else "l2"
                    )
                    is_management = bool(existing_iface.is_management) if existing_iface else False
                    iface = Interface(
                        device_id=device_id,
                        name=nb_iface["name"],
                        ip_address=ip_address,
                        subnet_mask=subnet_mask,
                        mac_address=nb_iface.get("mac_address"),
                        interface_type=interface_type,
                        is_management=is_management,
                        status="active" if nb_iface.get("ip_address") else "unknown",
                    )
                    self.db.upsert_interface(iface)
                    iface_count += 1

            except Exception as e:
                logger.error(f"Failed to sync device {nb_dev.name}: {e}")

        logger.info(f"NetBox Sync Complete. Synced {synced_count} devices, {iface_count} interfaces.")
        publish_context_refresh_event(
            source="netbox_sync",
            action="inventory_sync_completed",
            summary=f"synced {synced_count} devices from NetBox",
            domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY),
            event_type="refresh",
            refresh_mode="none",
            metadata={"synced_count": synced_count, "interface_count": iface_count},
        )
        result = {"status": "success", "synced_count": synced_count, "interface_count": iface_count}
        refresh_state.record_success("inventory", result=result, trigger="netbox_sync")
        return result

    def _map_platform_to_vendor(self, platform_slug: str) -> str:
        """Maps NetBox platform slugs to internal vendor codes."""
        mapping = {
            "cisco-ios": "cisco_ios",
            "cisco-xe": "cisco_ios",
            "juniper-junos": "juniper_junos",
            "huawei-vrp": "huawei_vrp"
        }
        return mapping.get(platform_slug, "unknown")
