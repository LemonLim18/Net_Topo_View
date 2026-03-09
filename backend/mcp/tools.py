try:
    from .server import mcp, db, agent, pipeline
except ImportError:  # pragma: no cover - direct script/test fallback
    from mcp.server import mcp, db, agent, pipeline

try:
    from deploy.models import ChangeRequest
except ImportError:  # pragma: no cover - package import path
    from ..deploy.models import ChangeRequest
import json

@mcp.tool()
def list_devices() -> str:
    """List all devices in the network with their management IP and status."""
    devices = db.get_all_devices()
    result = []
    for d in devices:
        result.append(f"{d.hostname} ({d.management_ip}) - {d.platform} [{d.status}]")
    return "\n".join(result)

@mcp.tool()
def get_device_details(hostname: str) -> str:
    """Get detailed information about a specific device."""
    device = db.get_device_by_hostname(hostname)
    if not device:
        return "Device not found."
    return json.dumps(device.dict(), indent=2, default=str)

@mcp.tool()
def run_command(hostname: str, command: str) -> str:
    """Run a show command on a network device. 
    WARNING: Use only for read-only commands (show, display).
    """
    # We can reuse the agent's tool or call driver directly
    # For consistency, let's use the agent's tool functionality if exposed, 
    # or direct driver call via a helper.
    # Since agent has network_tools, let's see if we can use them.
    # But network_tools might expect an Agent context.
    # Let's direct call for now using M1 logic via a helper in future, 
    # but for this MVP, we will simulate or use the generic 'run_show_command' if M1 is ready.
    # The 'NetworkAgent' has 'tools'. Let's use `agent.tools.run_show_command`.
    
    # We need to find the tool instance. 
    # M5 tools are classes.
    # Let's instantiate the tool? Or just use a direct driver call for simplicity here.
    # Actually, `backend.llm.network_tools.RunShowCommand` exists.
    
    from drivers.factory import DriverFactory
    from security.credentials import get_credentials # Mock/Real
    
    device = db.get_device_by_hostname(hostname)
    if not device:
        return "Device not found."
        
    # TODO: Real SSH call implementation
    # driver = DriverFactory.get_driver(device.platform)
    # session = driver.connect(device.management_ip, ...)
    # output = driver.send_command(session, command)
    return f"Mock Output for '{command}' on {hostname}\nInterface GigabitEthernet1 is UP..."

@mcp.tool()
def plan_deployment(description: str, devices: list[str]) -> str:
    """Plan a configuration deployment.
    Args:
        description: Natural language description of the change (e.g. 'Configure VLAN 10')
        devices: List of hostnames to apply the change to
    """
    # This would use M5 Agent to generate config and M4 to create a Change Request
    return f"Deployment plan created for '{description}' on {devices}. Change Request ID: #101 (Pending Approval)"
