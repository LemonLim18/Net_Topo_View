from backend.mcp.server import mcp, db
import json

@mcp.resource("network://topology")
def get_topology() -> str:
    """Returns the current network topology as a JSON string."""
    topo = db.get_topology()
    return json.dumps(topo, default=str)

@mcp.resource("network://devices")
def get_device_list() -> str:
    """Returns a list of all devices in the inventory."""
    devices = db.get_all_devices()
    return json.dumps([d.dict() for d in devices], default=str)

@mcp.resource("network://device/{hostname}/config")
def get_device_config(hostname: str) -> str:
    """Returns the latest running configuration for a specific device."""
    device = db.get_device_by_hostname(hostname)
    if not device:
        return "Device not found."
    
    # In a real scenario, we'd fetch the latest snapshot from config_snapshots table
    # For now, we'll confirm the device exists and return a placeholder or fetch if available
    # Assuming db.get_latest_snapshot(device.id) exists or similar
    # If not, let's just return device details for now to be safe
    return f"Configuration for {hostname} (Placeholder - Snapshot retrieval pending implementation)"
