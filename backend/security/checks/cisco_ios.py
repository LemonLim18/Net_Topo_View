"""
M13a: Security Checks — Access Control, Authentication, Encryption, Console
=============================================================================
Each function takes (config_text, vendor) and returns an AuditResult.
"""

import re
from security.models import AuditResult, SecurityRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    """Shortcut — the caller will set the actual rule afterwards."""
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


# ---------------------------------------------------------------------------
# CIS-1.x — Access Control
# ---------------------------------------------------------------------------

def check_telnet_disabled(config_text: str, vendor: str) -> AuditResult:
    """CIS-1.1.1: Ensure Telnet is disabled on VTY lines."""
    if "transport input telnet" in config_text:
        return _result("fail", "Found: 'transport input telnet'",
                        "line vty 0 15\n transport input ssh")
    if "transport input all" in config_text:
        return _result("fail", "Found: 'transport input all' (includes telnet)",
                        "line vty 0 15\n transport input ssh")
    return _result("pass", "Telnet not enabled on VTY lines")


def check_ssh_enabled(config_text: str, vendor: str) -> AuditResult:
    """CIS-1.1.2: Ensure SSH is enabled."""
    if "transport input ssh" in config_text or "ip ssh version" in config_text:
        return _result("pass", "SSH is enabled")
    return _result("fail", "SSH not explicitly configured",
                    "ip ssh version 2\nline vty 0 15\n transport input ssh")


def check_vty_acl(config_text: str, vendor: str) -> AuditResult:
    """CIS-1.1.3: Ensure ACL is applied to VTY lines."""
    if re.search(r"line vty.*\n.*access-class", config_text, re.MULTILINE):
        return _result("pass", "ACL applied to VTY lines")
    return _result("fail", "No access-class on VTY lines",
                    "ip access-list standard VTY_ACCESS\n permit <mgmt-subnet>\nline vty 0 15\n access-class VTY_ACCESS in")


# ---------------------------------------------------------------------------
# CIS-2.x — Authentication
# ---------------------------------------------------------------------------

def check_enable_secret(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.1.1: Ensure enable secret is configured."""
    if "enable secret" in config_text:
        return _result("pass", "Enable secret is configured")
    if "enable password" in config_text:
        return _result("fail", "'enable password' used instead of 'enable secret' (weak hash)",
                        "enable secret <strong-password>")
    return _result("fail", "No enable secret configured",
                    "enable secret <strong-password>")


def check_password_encryption(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.1.2: Ensure service password-encryption is enabled."""
    if "service password-encryption" in config_text:
        return _result("pass", "Password encryption is enabled")
    return _result("fail", "service password-encryption not found",
                    "service password-encryption")


def check_local_auth(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.1.3: Ensure local authentication is configured."""
    if "username" in config_text and ("privilege" in config_text or "secret" in config_text):
        return _result("pass", "Local user accounts configured")
    return _result("fail", "No local user accounts found",
                    "username admin privilege 15 secret <password>")


# ---------------------------------------------------------------------------
# CIS-3.x — Banner
# ---------------------------------------------------------------------------

def check_login_banner(config_text: str, vendor: str) -> AuditResult:
    """CIS-3.1.1: Ensure login banner is configured."""
    if "banner login" in config_text or "banner motd" in config_text:
        return _result("pass", "Login banner is configured")
    return _result("fail", "No login banner configured",
                    "banner login ^Authorized access only. Disconnect immediately if not authorized.^")


# ---------------------------------------------------------------------------
# CIS-4.x — Logging
# ---------------------------------------------------------------------------

def check_logging_enabled(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.1.1: Ensure logging is enabled."""
    if "no logging" in config_text and "no logging console" not in config_text:
        return _result("fail", "Logging is explicitly disabled", "logging on")
    if "logging" in config_text:
        return _result("pass", "Logging is enabled")
    return _result("fail", "No logging configuration found", "logging on\nlogging buffered 8192")


def check_logging_buffer(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.1.2: Ensure logging buffer >= 4096."""
    match = re.search(r"logging buffered (\d+)", config_text)
    if match:
        size = int(match.group(1))
        if size >= 4096:
            return _result("pass", f"Logging buffer: {size} bytes")
        return _result("fail", f"Logging buffer too small: {size} bytes",
                        "logging buffered 8192")
    return _result("fail", "No logging buffer configured", "logging buffered 8192")


def check_logging_timestamps(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.1.3: Ensure timestamps on log messages."""
    if "service timestamps log" in config_text:
        return _result("pass", "Timestamps enabled on logs")
    return _result("fail", "No timestamps on log messages",
                    "service timestamps log datetime msec localtime show-timezone")


# ---------------------------------------------------------------------------
# CIS-5.x — NTP
# ---------------------------------------------------------------------------

def check_ntp_configured(config_text: str, vendor: str) -> AuditResult:
    """CIS-5.1.1: Ensure NTP is configured."""
    if "ntp server" in config_text:
        return _result("pass", "NTP server configured")
    return _result("fail", "NTP not configured",
                    "ntp server <ntp-server-ip>")


# ---------------------------------------------------------------------------
# CIS-6.x — SNMP
# ---------------------------------------------------------------------------

def check_snmp_no_public(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.1.1: No SNMP community 'public'."""
    if re.search(r"snmp-server community public", config_text, re.IGNORECASE):
        return _result("fail", "SNMP community 'public' found",
                        "no snmp-server community public")
    return _result("pass", "SNMP community 'public' not used")


def check_snmp_no_private(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.1.2: No SNMP community 'private'."""
    if re.search(r"snmp-server community private", config_text, re.IGNORECASE):
        return _result("fail", "SNMP community 'private' found",
                        "no snmp-server community private")
    return _result("pass", "SNMP community 'private' not used")


# ---------------------------------------------------------------------------
# CIS-7.x — Encryption / SSH
# ---------------------------------------------------------------------------

def check_ssh_v2(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.1.1: Ensure SSH version 2."""
    if "ip ssh version 2" in config_text:
        return _result("pass", "SSH version 2 configured")
    if "ip ssh version 1" in config_text:
        return _result("fail", "SSH version 1 found (insecure)", "ip ssh version 2")
    return _result("fail", "SSH version not explicitly set", "ip ssh version 2")


def check_no_http_server(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.1.2: Ensure HTTP server is disabled."""
    if "no ip http server" in config_text:
        return _result("pass", "HTTP server is disabled")
    if "ip http server" in config_text:
        return _result("fail", "HTTP server is enabled (insecure)",
                        "no ip http server\nno ip http secure-server")
    return _result("pass", "HTTP server not configured")


# ---------------------------------------------------------------------------
# CIS-8.x — Routing
# ---------------------------------------------------------------------------

def check_no_source_route(config_text: str, vendor: str) -> AuditResult:
    """CIS-8.1.1: Ensure no IP source routing."""
    if "no ip source-route" in config_text:
        return _result("pass", "IP source routing disabled")
    if "ip source-route" in config_text:
        return _result("fail", "IP source routing enabled", "no ip source-route")
    return _result("fail", "IP source routing not explicitly disabled", "no ip source-route")


# ---------------------------------------------------------------------------
# CIS-9.x — Interface Hardening
# ---------------------------------------------------------------------------

def check_cdp_disabled(config_text: str, vendor: str) -> AuditResult:
    """CIS-9.1.1: Ensure CDP is disabled globally or on external interfaces."""
    if "no cdp run" in config_text:
        return _result("pass", "CDP disabled globally")
    return _result("fail", "CDP is running (info leak risk)",
                    "no cdp run")


def check_unused_shutdown(config_text: str, vendor: str) -> AuditResult:
    """CIS-9.1.2: Check for unused interfaces that are not shut down."""
    # Simple heuristic: look for interfaces with no IP and no shutdown
    interfaces = re.findall(r"interface (\S+)\n((?:.*\n)*?)!", config_text)
    open_unused = []
    for name, block in interfaces:
        if "shutdown" not in block and "ip address" not in block and "no ip address" in block:
            open_unused.append(name)
    if open_unused:
        return _result("fail",
                        f"Unused interfaces without shutdown: {', '.join(open_unused[:5])}",
                        "interface <name>\n shutdown")
    return _result("pass", "No unused open interfaces found")


# ---------------------------------------------------------------------------
# CIS-10.x — Console
# ---------------------------------------------------------------------------

def check_exec_timeout(config_text: str, vendor: str) -> AuditResult:
    """CIS-10.1.1: Ensure exec-timeout is set on console."""
    if re.search(r"line con.*\n.*exec-timeout", config_text, re.MULTILINE):
        return _result("pass", "Exec-timeout set on console")
    return _result("fail", "No exec-timeout on console line",
                    "line con 0\n exec-timeout 5 0")


def check_vty_timeout(config_text: str, vendor: str) -> AuditResult:
    """CIS-10.1.2: Ensure exec-timeout is set on VTY lines."""
    if re.search(r"line vty.*\n.*exec-timeout", config_text, re.MULTILINE):
        return _result("pass", "Exec-timeout set on VTY lines")
    return _result("fail", "No exec-timeout on VTY lines",
                    "line vty 0 15\n exec-timeout 10 0")


# ---------------------------------------------------------------------------
# Registry — maps function names to callables
# ---------------------------------------------------------------------------
CHECK_REGISTRY = {
    "check_telnet_disabled": check_telnet_disabled,
    "check_ssh_enabled": check_ssh_enabled,
    "check_vty_acl": check_vty_acl,
    "check_enable_secret": check_enable_secret,
    "check_password_encryption": check_password_encryption,
    "check_local_auth": check_local_auth,
    "check_login_banner": check_login_banner,
    "check_logging_enabled": check_logging_enabled,
    "check_logging_buffer": check_logging_buffer,
    "check_logging_timestamps": check_logging_timestamps,
    "check_ntp_configured": check_ntp_configured,
    "check_snmp_no_public": check_snmp_no_public,
    "check_snmp_no_private": check_snmp_no_private,
    "check_ssh_v2": check_ssh_v2,
    "check_no_http_server": check_no_http_server,
    "check_no_source_route": check_no_source_route,
    "check_cdp_disabled": check_cdp_disabled,
    "check_unused_shutdown": check_unused_shutdown,
    "check_exec_timeout": check_exec_timeout,
    "check_vty_timeout": check_vty_timeout,
}
