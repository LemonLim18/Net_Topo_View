"""
M13b: Syslog Receiver
======================
UDP syslog listener that parses network device syslog messages
and converts them into SecurityEvent objects.

Supports:
- Cisco IOS syslog format:  ``%FACILITY-SEVERITY-MNEMONIC: Message``
- Common security-relevant mnemonics:
  - ``%SEC-6-IPACCESSLOGP``  — ACL deny logs
  - ``%SYS-5-CONFIG_I``      — Configuration changes
  - ``%SSH-5-SSH2_USERAUTH``  — SSH login attempts
  - ``%AUTHMGR-5-FAIL``      — Authentication failures

Usage::

    receiver = SyslogReceiver(port=514, callback=handle_event)
    receiver.start()  # runs in background thread
"""

import logging
import re
import socketserver
import threading
from typing import Callable, Optional, Tuple

from security.models import SecurityEvent

logger = logging.getLogger("security.syslog")


# ---------------------------------------------------------------------------
# Cisco IOS syslog pattern matchers
# ---------------------------------------------------------------------------

# General format: %FACILITY-SEVERITY-MNEMONIC: message text
_SYSLOG_PATTERN = re.compile(
    r"%(?P<facility>\w+)-(?P<severity>\d)-(?P<mnemonic>\w+):\s*(?P<message>.*)"
)

# ACL deny: e.g. "list 101 denied tcp 192.168.1.100(12345) -> 10.0.0.5(80), 1 packet"
_ACL_DENY_PATTERN = re.compile(
    r"list\s+(\S+)\s+denied\s+(\w+)\s+([\d.]+)\((\d+)\)\s*->\s*([\d.]+)\((\d+)\)"
)

# SSH auth: e.g. "User 'admin' authenticated from 192.168.1.100"
_SSH_AUTH_PATTERN = re.compile(
    r"(?:User|user)\s+'?(\w+)'?\s+.*(?:authenticated|failed).*?from\s+([\d.]+)",
    re.IGNORECASE,
)

# Config change: "Configured from console by admin on vty0 (192.168.1.100)"
_CONFIG_CHANGE_PATTERN = re.compile(
    r"[Cc]onfigured\s+from\s+(\w+)\s+by\s+(\w+)",
)


# Severity mapping: Cisco severity level → our severity
_CISCO_SEVERITY_MAP = {
    "0": "critical",   # Emergency
    "1": "critical",   # Alert
    "2": "critical",   # Critical
    "3": "high",       # Error
    "4": "high",       # Warning
    "5": "medium",     # Notification
    "6": "low",        # Informational
    "7": "info",       # Debugging
}

_CRITICAL_PATTERNS = (
    "aaa-3-serverdown",
    "radius-3-all_dead",
    "auth_service_failed",
    "ui_commit_failure",
    "bgp",
    "ospf",
)

_HIGH_PATTERNS = (
    "loopguard_block",
    "loop_detected",
    "fanfail",
    "power_supply_failed",
    "env_danger",
    "if_died",
    "snmp_trap_link_down",
    "fatal:",
    "exited, status 255",
    "no such file or directory",
)

_LOW_PATTERNS = (
    "logginghost_startstop",
    "clock sync enabled",
    "kernel time sync enabled",
    "ntp_log",
    "logging to host",
)


class SyslogMessageParser:
    """Parses raw syslog messages into SecurityEvent objects."""

    def parse(self, raw_message: str, source_addr: str = "") -> Optional[SecurityEvent]:
        """
        Parse a raw syslog message.

        Args:
            raw_message: The raw syslog text.
            source_addr: IP address of the sender.

        Returns:
            SecurityEvent if security-relevant, None otherwise.
        """
        raw_lower = raw_message.lower()
        match = _SYSLOG_PATTERN.search(raw_message)
        if match:
            facility = match.group("facility")
            severity_num = match.group("severity")
            mnemonic = match.group("mnemonic")
            message = match.group("message")
            severity = _CISCO_SEVERITY_MAP.get(severity_num, "info")
        else:
            # Juniper/Huawei and generic syslog lines may not include Cisco token structure.
            facility = "syslog"
            mnemonic = "GENERIC"
            message = raw_message
            severity = "info"

        # --- ACL deny ---
        if mnemonic == "IPACCESSLOGP" or "denied" in message.lower():
            event = self._parse_acl_deny(message, severity, source_addr, raw_message)
            if event.raw_data is None:
                event.raw_data = {}
            event.raw_data["category"] = "FIREWALL_ACL"
            event.raw_data["notify"] = self._notify_policy(event.severity)
            return event

        # --- SSH auth events ---
        if facility == "SSH" or mnemonic in ("SSH2_USERAUTH", "SSH2_SESSION"):
            event = self._parse_ssh_event(message, severity, mnemonic, source_addr, raw_message)
            if event and event.raw_data is not None:
                event.raw_data["category"] = "AUTH_SESSION_ANOMALY" if event.severity in ("high", "critical") else "SYSTEM_INFRA"
                event.raw_data["notify"] = self._notify_policy(event.severity)
            return event

        # --- Authentication failures ---
        if mnemonic in ("FAIL", "AUTHMGR_FAIL", "LOGIN_FAILED"):
            event = SecurityEvent(
                event_type="brute_force",
                severity="high",
                source_ip=source_addr,
                description=f"Authentication failure: {message}",
                raw_data={
                    "facility": facility,
                    "mnemonic": mnemonic,
                    "message": message,
                    "category": "AUTH_SESSION_ANOMALY",
                    "notify": self._notify_policy("high"),
                },
            )
            return event

        # --- Config change ---
        if mnemonic in ("CONFIG_I", "CONFIG"):
            event = SecurityEvent(
                event_type="config_change",
                severity="medium",
                source_ip=source_addr,
                description=f"Configuration changed: {message}",
                raw_data={
                    "facility": facility,
                    "mnemonic": mnemonic,
                    "message": message,
                    "category": "CONFIG_AUDIT",
                    "notify": self._notify_policy("medium"),
                },
            )
            return event

        event_type, sev, category = self._classify_operational_event(
            raw_message=raw_message,
            raw_lower=raw_lower,
            facility=facility,
            mnemonic=mnemonic,
            message=message,
            default_severity=severity,
        )
        return SecurityEvent(
            event_type=event_type,
            severity=sev,
            source_ip=source_addr,
            description=message,
            raw_data={
                "facility": facility,
                "mnemonic": mnemonic,
                "message": message,
                "raw": raw_message,
                "category": category,
                "notify": self._notify_policy(sev),
            },
        )

    def _classify_operational_event(
        self,
        raw_message: str,
        raw_lower: str,
        facility: str,
        mnemonic: str,
        message: str,
        default_severity: str,
    ) -> Tuple[str, str, str]:
        if any(p in raw_lower for p in _CRITICAL_PATTERNS):
            return "routing_or_auth_failure", "critical", "ROUTING_ADJACENCY_DOWN"

        if any(p in raw_lower for p in _HIGH_PATTERNS):
            if "snmp_trap_link_down" in raw_lower and "ifadminstatus down" in raw_lower:
                return "link_state_change", "medium", "LINK_STATE_EDGE"
            if "no such file or directory" in raw_lower:
                return "system_daemon_fault", "high", "SYSTEM_DAEMON_FAULT"
            if "fatal:" in raw_lower or "status 255" in raw_lower:
                return "auth_session_anomaly", "medium", "AUTH_SESSION_ANOMALY"
            return "link_or_system_fault", "high", "HARDWARE_ENV_FAULT"

        # Link state handling with direction-aware severity.
        if "changed state to" in raw_lower or "line protocol on interface" in raw_lower:
            is_core_like = any(x in raw_lower for x in ("port-channel", "bundle-ether", "ten", "hundred", "ethernet0/0"))
            if "administratively down" in raw_lower:
                return "link_state_change", "medium", "LINK_STATE_EDGE"
            if " to down" in raw_lower:
                return "link_state_change", ("critical" if is_core_like else "high"), ("LINK_STATE_CORE" if is_core_like else "LINK_STATE_EDGE")
            if " to up" in raw_lower:
                return "link_state_change", "low", ("LINK_STATE_CORE" if is_core_like else "LINK_STATE_EDGE")
            return "link_state_change", "medium", ("LINK_STATE_CORE" if is_core_like else "LINK_STATE_EDGE")

        if any(p in raw_lower for p in _LOW_PATTERNS):
            return "system_infra", "low", "LOG_PIPELINE_STATUS"

        if "login_event" in raw_lower or "logout_event" in raw_lower or "entering configuration mode" in raw_lower:
            return "config_audit", "medium", "CONFIG_AUDIT"

        if "commit" in raw_lower and ("success" in raw_lower or "progress" in raw_lower):
            return "config_audit", "medium", "CONFIG_AUDIT"

        if "time sync" in raw_lower or "clock has been updated" in raw_lower or "time reset" in raw_lower:
            return "time_sync_event", "low", "TIME_SYNC_EVENT"

        if "fwdd_cos_outq_sched_subunit_add" in raw_lower:
            return "system_infra", "info", "SYSTEM_INFRA"

        return "syslog", default_severity, "SYSTEM_INFRA"

    def _notify_policy(self, severity: str) -> str:
        if severity == "critical":
            return "page_immediate"
        if severity == "high":
            return "notify_immediate"
        if severity == "medium":
            return "ticket_and_digest"
        if severity == "low":
            return "digest_only"
        return "store_only"

    def _parse_acl_deny(self, message: str, severity: str, source_addr: str, raw: str) -> SecurityEvent:
        """Parse ACL deny log entry."""
        acl_match = _ACL_DENY_PATTERN.search(message)
        src_ip = ""
        dst_ip = ""
        if acl_match:
            src_ip = acl_match.group(3)
            dst_ip = acl_match.group(5)

        return SecurityEvent(
            event_type="port_scan" if "denied" in message else "acl_deny",
            severity=severity,
            source_ip=src_ip or source_addr,
            dest_ip=dst_ip,
            description=f"ACL deny: {message}",
            raw_data={"raw": raw},
        )

    def _parse_ssh_event(self, message: str, severity: str, mnemonic: str,
                          source_addr: str, raw: str) -> Optional[SecurityEvent]:
        """Parse SSH authentication events."""
        ssh_match = _SSH_AUTH_PATTERN.search(message)
        remote_ip = source_addr
        if ssh_match:
            remote_ip = ssh_match.group(2)

        is_failure = "fail" in message.lower() or "invalid" in message.lower()

        return SecurityEvent(
            event_type="brute_force" if is_failure else "ssh_login",
            severity="high" if is_failure else "info",
            source_ip=remote_ip,
            description=f"SSH {'failure' if is_failure else 'success'}: {message}",
            raw_data={"mnemonic": mnemonic, "raw": raw},
        )


class SyslogHandler(socketserver.BaseRequestHandler):
    """Handler for incoming UDP syslog datagrams."""

    def handle(self):
        data = self.request[0].strip()
        try:
            raw_message = data.decode("utf-8", errors="replace")
        except Exception:
            return

        source_addr = self.client_address[0]
        if hasattr(self.server, "raw_callback") and self.server.raw_callback:
            try:
                self.server.raw_callback(source_addr, raw_message)
            except Exception as e:
                logger.error("Syslog raw callback error: %s", e)

        parser = getattr(self.server, "parser", None)
        if parser is None:
            parser = SyslogMessageParser()
            self.server.parser = parser
        event = parser.parse(raw_message, source_addr)

        if event and hasattr(self.server, "event_callback") and self.server.event_callback:
            try:
                self.server.event_callback(event)
            except Exception as e:
                logger.error("Syslog callback error: %s", e)


class SyslogReceiver:
    """
    UDP syslog listener that runs in a background thread.

    Usage::

        def on_event(event: SecurityEvent):
            print(f"Threat: {event.event_type} from {event.source_ip}")

        receiver = SyslogReceiver(port=5514, callback=on_event)
        receiver.start()
    """

    def __init__(
        self,
        port: int = 5514,
        callback: Optional[Callable] = None,
        raw_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self._port = port
        self._callback = callback
        self._raw_callback = raw_callback
        self._server: Optional[socketserver.UDPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start listening in a background thread."""
        try:
            self._server = socketserver.UDPServer(("0.0.0.0", self._port), SyslogHandler)
            self._server.event_callback = self._callback  # type: ignore[attr-defined]
            self._server.raw_callback = self._raw_callback  # type: ignore[attr-defined]
            self._server.parser = SyslogMessageParser()  # type: ignore[attr-defined]
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("Syslog receiver started on UDP port %d", self._port)
        except OSError as e:
            logger.error("Failed to start syslog receiver on port %d: %s", self._port, e)

    def stop(self):
        """Stop the syslog listener."""
        if self._server:
            self._server.shutdown()
            logger.info("Syslog receiver stopped")
