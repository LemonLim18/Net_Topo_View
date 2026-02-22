import logging
import difflib
from datetime import datetime
from typing import Optional, List

from db.manager import DatabaseManager
from db.models import Device, ConfigSnapshot
from config_engine.renderer import TemplateRenderer
from netbox.client import NetBoxClient
from drift.models import DriftEvent

logger = logging.getLogger("drift.comparator")

class DriftComparator:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.nb_client = NetBoxClient.get_client()
        self.renderer = TemplateRenderer()

    def check_drift(self, hostname: str) -> Optional[DriftEvent]:
        """
        Compares the latest Actual Config (DB) vs Intended Config (NetBox + M3).
        """
        device = self.db.get_device_by_hostname(hostname)
        if not device:
            logger.error(f"Device {hostname} not found in DB")
            return None

        # 1. Get Actual Config
        history = self.db.get_config_history(device.id, limit=1)
        if not history:
            logger.warning(f"No config history for {hostname}")
            return self._create_no_data_event(device, "No actual config snapshots found")
        
        actual_config = history[0].config_text

        # 2. Generate Intended Config
        # Strategy: 
        #   a. Fetch device context from NetBox (M7)
        #   b. Pass to M3 Renderer to generate full config text
        #   NOTE: Since we don't have full templates for every feature yet, 
        #   we will mock this step or use a simplified "intended" string for MVP.
        #   For true implementation, we would need: intended_config = self._render_golden_config(device)
        intended_config = self._fetch_intended_config(device)

        if not intended_config:
             # Fallback if we can't generate intent yet
             logger.info(f"No intended config source for {hostname}, skipping diff.")
             return None

        # 3. Compare
        has_drift, diff_text = self._compute_diff(intended_config, actual_config)

        # 4. Classify Severity (Basic rules for now)
        severity = "low"
        if has_drift:
            if "interface" in diff_text: severity = "medium"
            if "router ospf" in diff_text or "router bgp" in diff_text: severity = "high"

        return DriftEvent(
            device_id=device.id,
            hostname=device.hostname,
            timestamp=datetime.now().isoformat(),
            has_drift=has_drift,
            diff_text=diff_text,
            severity=severity
        )

    def _fetch_intended_config(self, device: Device) -> Optional[str]:
        """
        Retrieves the intended configuration.
         Ideally this calls NetBox -> M3 Renderer.
         For MVP, checking if we have a locally saved 'intended' snapshot or
         using NetBox 'render config' API if available.
        """
        # MVP: Attempt to render a basic template if vendor is known
        # In a real scenario, we'd fetch specific params from NetBox.
        if device.vendor in ["cisco_ios", "cisco_xe"]:
             # Example: Render a base template with hostname
             try:
                 return self.renderer.render("cisco_ios", "base", {"hostname": device.hostname})
             except Exception:
                 pass
        
        return None 

    def _compute_diff(self, intended: str, actual: str) -> (bool, str):
        intended_lines = intended.splitlines()
        actual_lines = actual.splitlines()
        
        # Simple difflib
        diff = list(difflib.unified_diff(
            intended_lines, 
            actual_lines, 
            fromfile="Intended", 
            tofile="Actual",
            lineterm=""
        ))
        
        if not diff:
            return False, ""
        
        return True, "\n".join(diff)

    def _create_no_data_event(self, device: Device, msg: str) -> DriftEvent:
        return DriftEvent(
             device_id=device.id,
             hostname=device.hostname,
             timestamp=datetime.now().isoformat(),
             has_drift=True, # Technically unknown, but flagging for attention
             diff_text=msg,
             severity="low"
        )
