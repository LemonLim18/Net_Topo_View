"""
M13a: Security Checks — Management Plane Hardening
====================================================
CIS Cisco IOS Benchmark Section 1 (extended) and Section 10:
VTY access-class, Control Plane Policing, unnecessary services,
directed-broadcast, PAD, proxy ARP.
"""

import re
from security.models import AuditResult, SecurityRule

_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


# ---------------------------------------------------------------------------
# VTY Hardening (CIS-1.x extended)
# ---------------------------------------------------------------------------

def check_vty_transport_ssh_only(config_text: str, vendor: str) -> AuditResult:
    """CIS-1.2.1: Ensure VTY lines allow ONLY SSH (not 'all' or 'telnet')."""
    if re.search(r"transport input all", config_text):
        return _result("fail", "VTY transport input all (telnet + SSH both allowed)",
                       "line vty 0 15\n transport input ssh")
    if re.search(r"transport input telnet", config_text):
        return _result("fail", "VTY transport input telnet found",
                       "line vty 0 15\n transport input ssh")
    if re.search(r"transport input ssh", config_text):
        return _result("pass", "VTY transport input restricted to SSH")
    return _result("fail", "VTY transport input not explicitly configured",
                   "line vty 0 15\n transport input ssh")


def check_vty_login_local(config_text: str, vendor: str) -> AuditResult:
    """CIS-1.2.2: Ensure VTY lines use 'login local' or AAA for authentication."""
    if re.search(r"line vty.*\n(?:.*\n)*? login( local| authentication)", config_text, re.MULTILINE):
        return _result("pass", "VTY lines have login authentication configured")
    if "aaa authentication login default" in config_text:
        return _result("pass", "VTY lines covered by AAA authentication default")
    return _result("fail", "VTY lines may not require authentication",
                   "line vty 0 15\n login local   ! or: login authentication <aaa-list>")


# ---------------------------------------------------------------------------
# Control Plane Policing (CIS-1.3.x)
# ---------------------------------------------------------------------------

def check_control_plane_policing(config_text: str, vendor: str) -> AuditResult:
    """CIS-1.3.1: Ensure Control Plane Policing (CoPP) is configured."""
    if "control-plane" in config_text and "service-policy" in config_text:
        return _result("pass", "Control Plane Policing (CoPP) configured")
    return _result(
        "fail",
        "No Control Plane Policing found — router CPU vulnerable to flood attacks",
        "! Example CoPP snippet:\n"
        "policy-map COPP_POLICY\n class COPP_ICMP\n  police 8000 conform-action transmit exceed-action drop\n"
        "!\ncontrol-plane\n service-policy input COPP_POLICY",
    )


# ---------------------------------------------------------------------------
# Unnecessary Services (CIS-7.4.x)
# ---------------------------------------------------------------------------

def check_no_ip_directed_broadcast(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.4.3: Ensure IP directed-broadcast is disabled on all interfaces."""
    if "ip directed-broadcast" in config_text and "no ip directed-broadcast" not in config_text:
        return _result("fail", "ip directed-broadcast enabled (Smurf amplification risk)",
                       "interface <name>\n no ip directed-broadcast")
    return _result("pass", "IP directed-broadcast not enabled (disabled by default in IOS 12.0+)")


def check_no_proxy_arp(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.4.4: Ensure proxy ARP is disabled on interfaces not requiring it."""
    if re.search(r"ip proxy-arp", config_text) and "no ip proxy-arp" not in config_text:
        return _result("fail", "Proxy ARP is enabled",
                       "interface <name>\n no ip proxy-arp")
    return _result("pass", "Proxy ARP not enabled")


def check_no_pad(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.4.5: Ensure PAD commands are disabled."""
    if "no service pad" in config_text:
        return _result("pass", "PAD service disabled")
    if "service pad" in config_text:
        return _result("fail", "PAD service is enabled", "no service pad")
    return _result("pass", "PAD not configured (disabled by default on modern IOS)")


def check_no_ip_gratuitous_arps(config_text: str, vendor: str) -> AuditResult:
    """CIS-7.4.6: Ensure gratuitous ARP is suppressed."""
    if "no ip gratuitous-arps" in config_text:
        return _result("pass", "Gratuitous ARP suppressed")
    return _result("fail", "Gratuitous ARP not suppressed (ARP cache poisoning risk)",
                   "no ip gratuitous-arps")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "check_vty_transport_ssh_only":   check_vty_transport_ssh_only,
    "check_vty_login_local":          check_vty_login_local,
    "check_control_plane_policing":   check_control_plane_policing,
    "check_no_ip_directed_broadcast": check_no_ip_directed_broadcast,
    "check_no_proxy_arp":             check_no_proxy_arp,
    "check_no_pad":                   check_no_pad,
    "check_no_ip_gratuitous_arps":    check_no_ip_gratuitous_arps,
}
