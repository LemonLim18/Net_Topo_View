from datetime import datetime
import logging
from db.manager import DatabaseManager
from db.models import Device, Interface
from llm.context_contract import CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY
from llm.context_state import get_context_state_manager
from llm.refresh_state import get_refresh_state_manager
from netbox.client import NetBoxClient

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
        
        for nb_dev in nb_devices:
            try:
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
                existing = self.db.get_device_by_hostname(hostname)
                source = existing.source if existing else "netbox"
                
                device = Device(
                    hostname=hostname,
                    management_ip=mgmt_ip,
                    vendor=vendor,
                    model=model,
                    status=status,
                    role=role,
                    source=source,
                )
                
                device_id = self.db.upsert_device(device)
                synced_count += 1
                logger.debug(f"Synced device: {hostname} (status={status}, model={model}, role={role}, source={source})")

                # --- Sync interfaces for this device ---
                nb_device_id = nb_dev.id
                nb_ifaces = self.nb_client.get_interfaces_for_device(nb_device_id)
                for nb_iface in nb_ifaces:
                    iface = Interface(
                        device_id=device_id,
                        name=nb_iface["name"],
                        ip_address=nb_iface.get("ip_address"),
                        subnet_mask=nb_iface.get("subnet_mask"),
                        mac_address=nb_iface.get("mac_address"),
                        status="active" if nb_iface.get("ip_address") else "unknown",
                    )
                    self.db.upsert_interface(iface)
                    iface_count += 1

            except Exception as e:
                logger.error(f"Failed to sync device {nb_dev.name}: {e}")

        logger.info(f"NetBox Sync Complete. Synced {synced_count} devices, {iface_count} interfaces.")
        state = get_context_state_manager()
        state.invalidate(
            domain=CONTEXT_DOMAIN_INVENTORY,
            source="netbox_sync",
            summary=f"synced {synced_count} devices from NetBox",
            event_type="refresh",
            metadata={"synced_count": synced_count, "interface_count": iface_count},
        )
        state.invalidate(
            domain=CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
            source="netbox_sync",
            summary="inventory sync may have changed planned topology context",
            event_type="refresh",
            metadata={"synced_count": synced_count},
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
