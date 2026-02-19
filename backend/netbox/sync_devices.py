from datetime import datetime
import logging
from backend.db.manager import DatabaseManager
from backend.db.models import Device
from backend.netbox.client import NetBoxClient

logger = logging.getLogger("netbox.sync_devices")

class DeviceSyncer:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.nb_client = NetBoxClient.get_client()

    def sync_all_devices(self):
        """
        Fetches all devices from NetBox and upserts them into the local DB.
        """
        if not self.nb_client.api:
            logger.warning("NetBox API not initialized. Skipping sync.")
            return {"status": "skipped", "reason": "no_connection"}

        nb_devices = self.nb_client.get_devices()
        synced_count = 0
        
        for nb_dev in nb_devices:
            # Map NetBox fields to local Device model
            # Note: Adjust field mapping based on actual NetBox data structure
            try:
                hostname = nb_dev.name
                mgmt_ip = str(nb_dev.primary_ip4).split('/')[0] if nb_dev.primary_ip4 else None
                platform = str(nb_dev.platform.slug) if nb_dev.platform else "unknown"
                
                # Map platform slug to our internal vendor strings
                vendor = self._map_platform_to_vendor(platform)
                
                device = Device(
                    hostname=hostname,
                    management_ip=mgmt_ip,
                    vendor=vendor,
                    status="active" if nb_dev.status.value == "active" else "down"
                )
                
                self.db.upsert_device(device)
                synced_count += 1
                logger.debug(f"Synced device: {hostname}")
                
            except Exception as e:
                logger.error(f"Failed to sync device {nb_dev.name}: {e}")

        logger.info(f"NetBox Sync Complete. Synced {synced_count} devices.")
        return {"status": "success", "synced_count": synced_count}

    def _map_platform_to_vendor(self, platform_slug: str) -> str:
        """Maps NetBox platform slugs to internal vendor codes."""
        mapping = {
            "cisco-ios": "cisco_ios",
            "cisco-xe": "cisco_ios",
            "juniper-junos": "juniper_junos",
            "huawei-vrp": "huawei_vrp"
        }
        return mapping.get(platform_slug, "unknown")
