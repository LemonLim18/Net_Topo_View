"""
M13a: Security Checks — SNMP
=============================
CIS Cisco IOS Benchmark Section 6 (extended):
SNMP version enforcement, weak community strings, ACL, contact/location.
"""

import re
from security.models import AuditResult, SecurityRule

_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


_WEAK_COMMUNITIES = ["public", "private", "cisco", "default", "snmp", "admin", "manager", "community"]


def check_snmp_v3_only(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.2.1: Ensure only SNMPv3 is used (no v1 or v2c communities)."""
    v1v2 = re.findall(r"snmp-server community (\S+)", config_text)
    if v1v2:
        return _result(
            "fail",
            f"SNMPv1/v2c communities found: {', '.join(v1v2)}",
            "Remove all 'snmp-server community' lines and use SNMPv3:\n"
            "snmp-server group MGMT v3 priv\n"
            "snmp-server user NMSUSER MGMT v3 auth sha <auth-pass> priv aes 128 <priv-pass>",
        )
    if "snmp-server group" in config_text and "v3" in config_text:
        return _result("pass", "SNMPv3 group configured, no v1/v2c communities found")
    if "snmp-server" not in config_text:
        return _result("pass", "SNMP not configured on this device")
    return _result("fail", "SNMP configured but no v3 group found",
                   "snmp-server group MGMT v3 priv")


def check_snmp_weak_communities(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.2.2: Ensure no commonly guessed SNMP community strings are used."""
    found = []
    for community in _WEAK_COMMUNITIES:
        if re.search(rf"snmp-server community {community}\b", config_text, re.IGNORECASE):
            found.append(community)
    if found:
        return _result(
            "fail",
            f"Weak SNMP community strings found: {', '.join(found)}",
            "\n".join(f"no snmp-server community {c}" for c in found),
        )
    return _result("pass", "No well-known weak community strings found")


def check_snmp_acl(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.2.3: Ensure SNMP access is restricted by an ACL."""
    if re.search(r"snmp-server community \S+ (RO|RW) \S+", config_text):
        return _result("pass", "SNMP community has ACL applied")
    if re.search(r"snmp-server group \S+ v3.*access", config_text):
        return _result("pass", "SNMPv3 group has access restriction configured")
    if "snmp-server" in config_text:
        return _result("fail", "SNMP configured without ACL restriction",
                       "snmp-server community <name> RO SNMP_ACL\n"
                       "ip access-list standard SNMP_ACL\n permit <nms-host>")
    return _result("pass", "SNMP not configured")


def check_snmp_contact(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.3.1: Ensure snmp-server contact is set."""
    if "snmp-server contact" in config_text:
        return _result("pass", "SNMP contact is configured")
    return _result("fail", "No snmp-server contact configured",
                   "snmp-server contact noc@example.com")


def check_snmp_location(config_text: str, vendor: str) -> AuditResult:
    """CIS-6.3.2: Ensure snmp-server location is set."""
    if "snmp-server location" in config_text:
        return _result("pass", "SNMP location is configured")
    return _result("fail", "No snmp-server location configured",
                   "snmp-server location DC1-Rack-A3")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "check_snmp_v3_only":          check_snmp_v3_only,
    "check_snmp_weak_communities": check_snmp_weak_communities,
    "check_snmp_acl":              check_snmp_acl,
    "check_snmp_contact":          check_snmp_contact,
    "check_snmp_location":         check_snmp_location,
}
