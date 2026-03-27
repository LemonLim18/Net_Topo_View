import logging
import difflib
import re
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

_MANAGED_PREFIXES = (
    "hostname ",
    "ntp ",
    "logging ",
    "banner ",
    "snmp-server ",
    "ip name-server ",
    "ip domain",
    "service ",
    "clock ",
    "aaa ",
    "username ",
    "enable secret",
    "line vty",
    "transport input",
    "ip ssh ",
)

_VOLATILE_PATTERNS = (
    r"^!?\s*last configuration change\b",
    r"^!?\s*ntp clock-period\b",
    r"^!?\s*current configuration\b",
    r"^!?\s*time-source\b",
    r"^!?\s*uptime is\b",
)

_CRITICAL_PATTERNS = (
    r"\bno aaa\b",
    r"\btransport input telnet\b",
    r"\bno ip ssh\b",
    r"\benable password\b",
    r"\bno service password-encryption\b",
)

_HIGH_PATTERNS = (
    r"\brouter (bgp|ospf)\b",
    r"\bip route\b",
    r"\baccess-list\b",
    r"\bip access-group\b",
    r"\bpolicy-map\b",
    r"\bclass-map\b",
)

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

        # 3. Compare (managed compliance directives + risk-aware severity)
        has_drift, diff_text, severity = self._compute_compliance_delta(
            intended_config=intended_config,
            actual_config=actual_config,
        )

        return DriftEvent(
            device_id=device.id,
            hostname=device.hostname,
            timestamp=datetime.now().isoformat(),
            has_drift=has_drift,
            diff_text=diff_text,
            severity=severity,
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

    def _compute_compliance_delta(self, intended_config: str, actual_config: str) -> Tuple[bool, str, str]:
        intended_lines = self._normalize_config_lines(intended_config)
        actual_lines = self._normalize_config_lines(actual_config)
        actual_set = set(actual_lines)

        managed_intended = [line for line in intended_lines if self._is_managed_line(line)]
        if not managed_intended:
            managed_intended = list(intended_lines)

        missing_in_actual = [line for line in managed_intended if line not in actual_set]

        # Extra risky commands present live but not in intended baseline.
        risky_extras = [
            line for line in actual_lines
            if line not in set(managed_intended) and self._matches_any(line, _CRITICAL_PATTERNS + _HIGH_PATTERNS)
        ]

        has_drift = bool(missing_in_actual or risky_extras)
        if not has_drift:
            return False, "", "low"

        severity = self._classify_severity(missing_in_actual, risky_extras)
        summary = [
            f"Drift Summary: missing_baseline={len(missing_in_actual)} risky_extra={len(risky_extras)} severity={severity}",
        ]

        if missing_in_actual:
            summary.append("Missing intended baseline commands:")
            summary.extend(f"+ {line}" for line in missing_in_actual[:80])
            if len(missing_in_actual) > 80:
                summary.append(f"... ({len(missing_in_actual) - 80} more)")

        if risky_extras:
            summary.append("")
            summary.append("Risky live commands not in baseline:")
            summary.extend(f"- {line}" for line in risky_extras[:40])
            if len(risky_extras) > 40:
                summary.append(f"... ({len(risky_extras) - 40} more)")

        # Keep a compact unified diff for deeper inspection/debugging.
        unified = list(difflib.unified_diff(
            managed_intended,
            [line for line in actual_lines if self._is_managed_line(line)],
            fromfile="intended_managed",
            tofile="actual_managed",
            lineterm="",
        ))
        if unified:
            summary.append("")
            summary.append("Managed unified diff:")
            summary.extend(unified[:120])

        return True, "\n".join(summary), severity

    def _normalize_config_lines(self, config_text: str) -> List[str]:
        lines: List[str] = []
        for raw in (config_text or "").splitlines():
            line = re.sub(r"\s+", " ", str(raw or "").strip())
            if not line:
                continue
            if line.startswith("!") or line.startswith("#"):
                continue
            if any(re.search(pat, line, re.IGNORECASE) for pat in _VOLATILE_PATTERNS):
                continue
            lines.append(line.lower())
        return lines

    def _is_managed_line(self, line: str) -> bool:
        return any(line.startswith(prefix) for prefix in _MANAGED_PREFIXES)

    def _matches_any(self, line: str, patterns: Tuple[str, ...]) -> bool:
        return any(re.search(pat, line, re.IGNORECASE) for pat in patterns)

    def _classify_severity(self, missing_lines: List[str], risky_extras: List[str]) -> str:
        score = 0
        for line in missing_lines:
            if self._matches_any(line, _CRITICAL_PATTERNS):
                score += 5
            elif self._matches_any(line, _HIGH_PATTERNS):
                score += 3
            else:
                score += 1
        for line in risky_extras:
            if self._matches_any(line, _CRITICAL_PATTERNS):
                score += 5
            elif self._matches_any(line, _HIGH_PATTERNS):
                score += 3
            else:
                score += 1

        if score >= 8:
            return "high"
        if score >= 3:
            return "medium"
        return "low"

    def _create_no_data_event(self, device: Device, msg: str) -> DriftEvent:
        return DriftEvent(
             device_id=device.id,
             hostname=device.hostname,
             timestamp=datetime.now().isoformat(),
             has_drift=True, # Technically unknown, but flagging for attention
             diff_text=msg,
             severity="low"
        )
