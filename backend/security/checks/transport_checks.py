"""
M13a: Security Checks — Transport & Encryption
===============================================
CIS Cisco IOS Benchmark Section 7 (extended):
SSH hardening, TLS, weak cipher removal, HTTP(S) management plane encryption.
"""

import re
from security.models import AuditResult, SecurityRule

_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


# ---------------------------------------------------------------------------
# SSH Hardening
# ---------------------------------------------------------------------------

def check_ssh_key_bits(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.2.1: Ensure crypto key modulus >= 2048 bits."""
    match = re.search(r"crypto key generate rsa.*modulus (\d+)", config_text)
    if match:
        bits = int(match.group(1))
        if bits >= 2048:
            return _result("pass", f"RSA key modulus: {bits} bits")
        return _result("fail", f"RSA key modulus too small: {bits} bits",
                       "crypto key generate rsa modulus 2048")
    # If no explicit modulus found, check for ip ssh rsa keypair-name entries
    if "crypto key generate rsa" in config_text:
        return _result("fail", "RSA key found but modulus not explicitly >= 2048",
                       "crypto key generate rsa modulus 2048")
    return _result("fail", "No crypto RSA key found",
                   "crypto key generate rsa modulus 2048\nip ssh version 2")


def check_ssh_timeout(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.2.2: Ensure SSH authentication timeout <= 60 seconds."""
    match = re.search(r"ip ssh time-out (\d+)", config_text)
    if match:
        timeout = int(match.group(1))
        if timeout <= 60:
            return _result("pass", f"SSH timeout: {timeout}s")
        return _result("fail", f"SSH timeout too long: {timeout}s",
                       "ip ssh time-out 60")
    return _result("fail", "SSH timeout not configured",
                   "ip ssh time-out 60")


def check_ssh_auth_retries(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.2.3: Ensure SSH authentication-retries <= 3."""
    match = re.search(r"ip ssh authentication-retries (\d+)", config_text)
    if match:
        retries = int(match.group(1))
        if retries <= 3:
            return _result("pass", f"SSH auth retries: {retries}")
        return _result("fail", f"SSH auth retries too high: {retries}",
                       "ip ssh authentication-retries 3")
    return _result("fail", "SSH authentication-retries not configured",
                   "ip ssh authentication-retries 3")


# ---------------------------------------------------------------------------
# HTTP(S) Management
# ---------------------------------------------------------------------------

def check_no_http_server(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.3.1: Ensure HTTP (plain) management server is disabled."""
    if "no ip http server" in config_text:
        return _result("pass", "HTTP server is disabled")
    if re.search(r"^ip http server$", config_text, re.MULTILINE):
        return _result("fail", "Plain HTTP server is enabled",
                       "no ip http server")
    return _result("pass", "HTTP server not configured")


def check_https_server(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.3.2: If HTTPS is used, ensure TLS 1.2+ is enforced."""
    if "ip http secure-server" in config_text:
        if re.search(r"ip http tls-version TLSv1\.[23]", config_text):
            return _result("pass", "HTTPS server configured with TLS 1.2+")
        return _result("fail", "HTTPS server enabled but TLS version not pinned to 1.2+",
                       "ip http tls-version TLSv1.2")
    return _result("pass", "HTTPS server not configured (HTTP management not in use)")


# ---------------------------------------------------------------------------
# Weak Service Removal
# ---------------------------------------------------------------------------

def check_no_tcp_small_servers(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.4.1: Ensure TCP/UDP small servers are disabled."""
    if "no service tcp-small-servers" in config_text and "no service udp-small-servers" in config_text:
        return _result("pass", "TCP/UDP small servers disabled")
    issues = []
    if "service tcp-small-servers" in config_text:
        issues.append("tcp-small-servers enabled")
    if "service udp-small-servers" in config_text:
        issues.append("udp-small-servers enabled")
    if issues:
        return _result("fail", ", ".join(issues),
                       "no service tcp-small-servers\nno service udp-small-servers")
    # Neither enabled nor disabled — modern IOS defaults to off
    return _result("pass", "TCP/UDP small servers not configured (disabled by default)")


def check_no_finger_service(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.4.2: Ensure Finger service is disabled."""
    if "service finger" in config_text or "ip finger" in config_text:
        return _result("fail", "Finger service is enabled",
                       "no service finger\nno ip finger")
    return _result("pass", "Finger service not enabled")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "check_ssh_key_bits":        check_ssh_key_bits,
    "check_ssh_timeout":         check_ssh_timeout,
    "check_ssh_auth_retries":    check_ssh_auth_retries,
    "check_no_http_server":      check_no_http_server,
    "check_https_server":        check_https_server,
    "check_no_tcp_small_servers": check_no_tcp_small_servers,
    "check_no_finger_service":   check_no_finger_service,
}
