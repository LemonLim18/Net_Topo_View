from typing import Dict, Any, List

async def fetch_local_inventory_cache(db, device_name: str) -> Dict[str, Any]:
    """
    Fetch device hardware specifications and interfaces from the local SQL cache.
    """
    device = db.get_device_by_hostname(device_name)
    if not device:
        device = db.get_device_by_management_ip(device_name)
        if not device:
            raise ValueError(f"Device '{device_name}' not found in local inventory cache.")
            
    interfaces = db.get_interfaces_for_device(device.id)
    
    return {
        "id": device.id,
        "name": device.hostname,
        "platform": device.vendor,
        "model": device.model,
        "role": device.role,
        "serial": device.serial_number,
        "status": device.status,
        "interfaces": [
            {
                "name": iface.name,
                "ip": iface.ip_address,
                "subnet": iface.subnet_mask,
                "mac": iface.mac_address,
                "speed": iface.speed,
                "status": iface.status
            }
            for iface in interfaces
        ]
    }
