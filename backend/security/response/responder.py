"""
M13c: Incident Responder
==========================
Generates remediation configurations for security findings and threats.
Integrates with M3 Config Engine for template-based config generation
and M4 Deploy Pipeline for automated remediation deployment.

Response actions include:
- Block source IP via ACL
- Isolate port (shutdown interface)
- Rate-limit interface
- Restore config from snapshot (for route hijack / config tampering)

Usage::

    responder = IncidentResponder()
    action = responder.respond(event)
    print(action.config_snippet)
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from security.models import SecurityEvent

logger = logging.getLogger("security.responder")


@dataclass
class ResponseAction:
    """A recommended or auto-generated response to a security event."""
    action_type: str        # block_ip | isolate_port | rate_limit | restore_config | alert_only
    severity: str           # matches event severity
    description: str        # human-readable summary
    config_snippet: str     # CLI commands to apply (vendor-specific)
    vendor: str = "cisco_ios"
    requires_approval: bool = True
    event_id: Optional[int] = None
    device_id: Optional[int] = None


class IncidentResponder:
    """
    Generates remediation responses for security events.

    Uses Cisco IOS CLI templates by default; extensible to other vendors.
    """

    def respond(self, event: SecurityEvent, vendor: str = "cisco_ios") -> ResponseAction:
        """
        Generate a response action for a security event.

        Args:
            event: The security event to respond to.
            vendor: Target device vendor (for config syntax).

        Returns:
            ResponseAction with the recommended remediation.
        """
        handler = self._RESPONSE_MAP.get(event.event_type)
        if handler:
            return handler(self, event, vendor)
        return self._default_response(event, vendor)

    # ------------------------------------------------------------------
    # Response generators per event type
    # ------------------------------------------------------------------

    def _respond_brute_force(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Block source IP via extended ACL."""
        src_ip = event.source_ip or "UNKNOWN"
        if vendor == "cisco_ios":
            config = (
                f"ip access-list extended SECURITY_BLOCK\n"
                f" deny ip host {src_ip} any log\n"
                f" permit ip any any\n"
                f"!\n"
                f"interface range GigabitEthernet0/0 - 3\n"
                f" ip access-group SECURITY_BLOCK in"
            )
        elif vendor == "huawei_vrp":
            config = (
                f"acl number 3999\n"
                f" rule 5 deny ip source {src_ip} 0 log\n"
                f" rule 1000 permit ip\n"
                f"#\n"
                f"interface GigabitEthernet0/0/0\n"
                f" traffic-filter inbound acl 3999"
            )
        else:
            config = f"# Block {src_ip} — vendor '{vendor}' not supported for auto-config"

        return ResponseAction(
            action_type="block_ip",
            severity=event.severity,
            description=f"Block brute force source {src_ip} via ACL",
            config_snippet=config,
            vendor=vendor,
            requires_approval=True,
            event_id=event.id,
        )

    def _respond_port_scan(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Rate-limit + ACL for port scanner."""
        src_ip = event.source_ip or "UNKNOWN"
        if vendor == "cisco_ios":
            config = (
                f"ip access-list extended RATE_LIMIT_SCAN\n"
                f" deny ip host {src_ip} any log\n"
                f" permit ip any any\n"
                f"!\n"
                f"! Consider adding to interface:\n"
                f"! ip access-group RATE_LIMIT_SCAN in"
            )
        else:
            config = f"# Rate-limit {src_ip} — vendor '{vendor}' template pending"

        return ResponseAction(
            action_type="block_ip",
            severity=event.severity,
            description=f"Block port scanner {src_ip}",
            config_snippet=config,
            vendor=vendor,
            requires_approval=True,
            event_id=event.id,
        )

    def _respond_rogue_device(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Isolate rogue device by shutting down the port."""
        hostname = ""
        if event.raw_data and isinstance(event.raw_data, dict):
            hostname = event.raw_data.get("hostname", "")

        if vendor == "cisco_ios":
            config = (
                f"! Isolate rogue device: {hostname or event.source_ip}\n"
                f"! Identify the connected interface first, then:\n"
                f"interface <connected-interface>\n"
                f" shutdown\n"
                f" description *** SECURITY: Rogue device isolated ***"
            )
        else:
            config = f"# Isolate rogue device '{hostname}' — manual intervention required"

        return ResponseAction(
            action_type="isolate_port",
            severity="high",
            description=f"Isolate rogue device: {hostname or event.source_ip}",
            config_snippet=config,
            vendor=vendor,
            requires_approval=True,  # Always require approval for port isolation
            event_id=event.id,
        )

    def _respond_malware(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Block C2 destination + quarantine source VLAN."""
        dst_ip = event.dest_ip or "UNKNOWN"
        src_ip = event.source_ip or "UNKNOWN"
        if vendor == "cisco_ios":
            config = (
                f"! Block malware C2 callback to {dst_ip}\n"
                f"ip access-list extended MALWARE_BLOCK\n"
                f" deny ip any host {dst_ip} log\n"
                f" deny ip host {src_ip} any log\n"
                f" permit ip any any\n"
                f"!\n"
                f"! Quarantine infected host:\n"
                f"! Move source port to quarantine VLAN\n"
                f"! interface <source-port>\n"
                f"!  switchport access vlan 999\n"
                f"!  description *** QUARANTINE: malware ***"
            )
        else:
            config = f"# Block C2 {dst_ip} + quarantine {src_ip} — manual config required"

        return ResponseAction(
            action_type="block_ip",
            severity="critical",
            description=f"Block malware C2 {dst_ip}, quarantine {src_ip}",
            config_snippet=config,
            vendor=vendor,
            requires_approval=True,
            event_id=event.id,
        )

    def _respond_suricata(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Block source from Suricata IDS alert."""
        src_ip = event.source_ip or "UNKNOWN"
        if vendor == "cisco_ios":
            config = (
                f"! Suricata IDS alert — block source\n"
                f"ip access-list extended IDS_BLOCK\n"
                f" deny ip host {src_ip} any log\n"
                f" permit ip any any"
            )
        else:
            config = f"# Block IDS source {src_ip} — vendor '{vendor}' not templated"

        return ResponseAction(
            action_type="block_ip",
            severity=event.severity,
            description=f"IDS alert: block {src_ip} — {event.description[:80]}",
            config_snippet=config,
            vendor=vendor,
            requires_approval=True,
            event_id=event.id,
        )

    def _respond_config_change(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Alert on unauthorized config change — suggest rollback."""
        return ResponseAction(
            action_type="restore_config",
            severity="high",
            description=f"Unauthorized config change detected: {event.description[:100]}",
            config_snippet=(
                "! Recommended: restore last known-good config snapshot\n"
                "! Use M4 Deploy Pipeline to rollback:\n"
                "!   1. Review last config snapshot in M14\n"
                "!   2. Generate diff using M3 Config Engine\n"
                "!   3. Deploy rollback via M4 Pipeline"
            ),
            vendor=vendor,
            requires_approval=True,
            event_id=event.id,
        )

    def _default_response(self, event: SecurityEvent, vendor: str) -> ResponseAction:
        """Fallback: alert-only response for unrecognized event types."""
        return ResponseAction(
            action_type="alert_only",
            severity=event.severity,
            description=f"Security event: {event.event_type} — {event.description[:100]}",
            config_snippet="! No auto-remediation available for this event type.\n! Manual investigation recommended.",
            vendor=vendor,
            requires_approval=False,
            event_id=event.id,
        )

    def respond_qos(self, event: SecurityEvent, vendor: str = "cisco_ios") -> ResponseAction:
        """
        Generate a 5-step QoS policy to remediate a traffic anomaly.

        Uses the M3 Config Engine to render the qos.j2 template with a
        campus-standard traffic profile:
          - VoIP  → DSCP EF,  LLQ priority 10%
          - Video → DSCP AF41, bandwidth 30%
          - Web   → DSCP BE (0), bandwidth 25%
          - Bulk  → DSCP CS1, policed to 50 Mbps
        """
        # Extract interface from event raw_data if available
        interface = "GigabitEthernet0/1"  # default
        if event.raw_data and isinstance(event.raw_data, dict):
            interface = event.raw_data.get("interface", interface)

        qos_params = {
            "policy_name": "CAMPUS_QOS_REMEDIATION",
            "interface": interface,
            "direction": "output",
            "class_maps": [
                {"name": "VOICE",     "matches": [{"type": "protocol", "value": "rtp"},
                                                   {"type": "protocol", "value": "sip"}]},
                {"name": "VIDEO",     "matches": [{"type": "dscp",     "value": "af41"}]},
                {"name": "WEB",       "matches": [{"type": "protocol", "value": "http"},
                                                   {"type": "protocol", "value": "https"}]},
                {"name": "BULK",      "matches": [{"type": "protocol", "value": "ftp"},
                                                   {"type": "protocol", "value": "bittorrent"}]},
            ],
            "policy_classes": [
                {"class_name": "VOICE", "dscp": "ef",   "priority_percent": 10},
                {"class_name": "VIDEO", "dscp": "af41", "bandwidth_percent": 30},
                {"class_name": "WEB",   "dscp": "0",    "bandwidth_percent": 25},
                {"class_name": "BULK",  "dscp": "cs1",  "police_rate": 50000000},
            ],
        }

        # Render using M3 Config Engine
        try:
            from config_engine.renderer import TemplateRenderer
            renderer = TemplateRenderer()
            config = renderer.render(vendor, "qos", qos_params)
        except Exception as e:
            logger.error("Failed to render QoS template: %s", e)
            config = f"! ERROR: Could not render QoS template for {vendor}: {e}"

        return ResponseAction(
            action_type="apply_qos",
            severity=event.severity,
            description=(
                f"5-step QoS remediation for {interface}: "
                f"VoIP=EF/LLQ10%, Video=AF41/30%, Web=BE/25%, Bulk=CS1/50Mbps cap"
            ),
            config_snippet=config,
            vendor=vendor,
            requires_approval=True,
            event_id=event.id,
        )

    # Dispatch map
    _RESPONSE_MAP = {
        "brute_force": _respond_brute_force,
        "port_scan": _respond_port_scan,
        "rogue_device": _respond_rogue_device,
        "malware": _respond_malware,
        "suricata": _respond_suricata,
        "config_change": _respond_config_change,
        "anomaly": respond_qos,
    }

    def get_supported_types(self) -> List[str]:
        """Return event types that have auto-response templates."""
        return list(self._RESPONSE_MAP.keys())
