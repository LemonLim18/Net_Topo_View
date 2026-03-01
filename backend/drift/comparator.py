import logging
import difflib
from datetime import datetime
from typing import Optional, List, Tuple

from db.manager import DatabaseManager
from db.models import Device, ConfigSnapshot
from config_engine.renderer import TemplateRenderer
from netbox.client import NetBoxClient
from drift.models import DriftEvent

logger = logging.getLogger("drift.comparator")

# ── Vendor-to-template mapping ──────────────────────────────────────
_VENDOR_MAP = {
    "cisco_ios":      "cisco_ios",
    "cisco_xe":       "cisco_ios",     # IOS-XE uses same template base
    "cisco_iosxe":    "cisco_ios",
    "cisco_nxos":     "cisco_ios",
    "juniper_junos":  "juniper_junos",
    "juniper":        "juniper_junos",
    "junos":          "juniper_junos",
    "huawei_vrp":     "huawei_vrp",
    "huawei":         "huawei_vrp",
    "vrp":            "huawei_vrp",
    "arista_eos":     "cisco_ios",     # EOS syntax is largely IOS-like
}

# Base features to try rendering for golden config comparison
_BASE_FEATURES = ["base", "ntp", "logging", "ssh_harden", "banner"]

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
        Retrieves the intended (golden) configuration for drift comparison.

        Strategy (Phase 15 — Multi-Vendor):
          1. Map device vendor to template vendor key.
          2. Try rendering a composite golden config from base features
             (ntp, logging, ssh_harden, banner) using M3 Renderer.
          3. If vendor is unknown or templates fail, return None (skip drift).

        Supports: cisco_ios, cisco_xe, cisco_nxos, juniper_junos, huawei_vrp, arista_eos
        """
        # Resolve vendor alias
        raw_vendor = (device.vendor or "").lower().strip()
        template_vendor = _VENDOR_MAP.get(raw_vendor)

        if not template_vendor:
            logger.info(
                "Unsupported vendor '%s' for drift comparison on %s — skipping",
                device.vendor, device.hostname,
            )
            return None

        # Build composite golden config from multiple features
        sections: list[str] = []
        params = {"hostname": device.hostname}

        for feature in _BASE_FEATURES:
            try:
                rendered = self.renderer.render(template_vendor, feature, params)
                if rendered and rendered.strip():
                    sections.append(f"! --- {feature} ---")
                    sections.append(rendered.strip())
            except Exception:
                # Template doesn't exist for this feature — skip silently
                pass

        if not sections:
            logger.info(
                "No renderable templates for vendor '%s' (%s) — falling back to last-known-good",
                template_vendor, device.hostname,
            )
            # Fallback: use second-to-last config snapshot as intended
            return self._get_last_known_good(device)

        return "\n".join(sections)

    def _get_last_known_good(self, device: Device) -> Optional[str]:
        """
        Fallback intended config: use the second-to-last config snapshot
        (i.e. the previously captured config before the latest one).
        """
        try:
            history = self.db.get_config_history(device.id, limit=2)
            if len(history) >= 2:
                return history[1].config_text
        except Exception as e:
            logger.debug("Could not fetch last-known-good for %s: %s", device.hostname, e)
        return None

    def _compute_diff(self, intended: str, actual: str) -> Tuple[bool, str]:
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
