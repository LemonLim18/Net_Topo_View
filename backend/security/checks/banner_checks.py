"""
M13a: Security Checks — Banners
=================================
CIS Cisco IOS Benchmark Section 3 (extended):
MOTD, login, exec, and incoming banners — presence and content safety.
"""

import re
from security.models import AuditResult, SecurityRule

_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


# Patterns that should NOT appear in banners (information disclosure)
_DISALLOWED_PATTERNS = [
    (re.compile(r"\bIOS\b", re.IGNORECASE),         "IOS version string"),
    (re.compile(r"version \d+\.\d+", re.IGNORECASE),"software version"),
    (re.compile(r"\bCisco\b", re.IGNORECASE),        "vendor name 'Cisco'"),
    (re.compile(r"\brouter\b", re.IGNORECASE),       "device role 'router'"),
    (re.compile(r"\bswitch\b", re.IGNORECASE),       "device role 'switch'"),
    (re.compile(r"welcome", re.IGNORECASE),          "welcoming language"),
    (re.compile(r"password", re.IGNORECASE),         "word 'password'"),
]

_REQUIRED_PHRASES = [
    re.compile(r"authorized", re.IGNORECASE),
    re.compile(r"unauthorized.*prohibited|prohibited.*unauthorized", re.IGNORECASE),
]


def _extract_banner_text(config_text: str, banner_type: str) -> str:
    """Extract banner content between the delimiter characters."""
    match = re.search(
        rf"banner {banner_type} (\S)(.*?)\1",
        config_text,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(2).strip() if match else ""


def check_motd_banner(config_text: str, vendor: str) -> AuditResult:
    """CIS-3.1.1: Ensure an MOTD banner is configured."""
    if "banner motd" in config_text:
        return _result("pass", "MOTD banner is configured")
    return _result("fail", "No 'banner motd' found",
                   'banner motd ^C\nUnauthorized access is prohibited.\nAll activities are logged.\n^C')


def check_login_banner(config_text: str, vendor: str) -> AuditResult:
    """CIS-3.1.2: Ensure a login banner is configured."""
    if "banner login" in config_text:
        return _result("pass", "Login banner is configured")
    return _result("fail", "No 'banner login' found",
                   'banner login ^C\nAuthorized users only.\n^C')


def check_exec_banner(config_text: str, vendor: str) -> AuditResult:
    """CIS-3.1.3: Ensure an exec banner is configured (post-login reminder)."""
    if "banner exec" in config_text:
        return _result("pass", "Exec banner is configured")
    return _result("fail", "No 'banner exec' found (post-login warning missing)",
                   'banner exec ^C\n*** This system is for authorized use only. ***\n^C')


def check_banner_no_info_disclosure(config_text: str, vendor: str) -> AuditResult:
    """CIS-3.2.1: Ensure banners do not disclose sensitive device/OS information."""
    # Collect all banner blocks
    banners_combined = ""
    for btype in ("motd", "login", "exec", "incoming"):
        banners_combined += _extract_banner_text(config_text, btype) + " "

    if not banners_combined.strip():
        return _result("pass", "No banners to check for info disclosure")

    violations = []
    for pattern, label in _DISALLOWED_PATTERNS:
        if pattern.search(banners_combined):
            violations.append(label)

    if violations:
        return _result(
            "fail",
            f"Banner contains potentially disclosing information: {', '.join(violations)}",
            "Remove OS version, vendor name, device roles, and welcoming phrases from all banners.",
        )
    return _result("pass", "Banners do not appear to disclose sensitive information")


def check_banner_legal_language(config_text: str, vendor: str) -> AuditResult:
    """CIS-3.2.2: Ensure banners contain required legal/authorization language."""
    banners_combined = ""
    for btype in ("motd", "login", "exec"):
        banners_combined += _extract_banner_text(config_text, btype) + " "

    if not banners_combined.strip():
        return _result("fail", "No banners configured — legal warning missing",
                       'banner motd ^C\nUnauthorized access is prohibited.\n^C')

    for pattern in _REQUIRED_PHRASES:
        if pattern.search(banners_combined):
            return _result("pass", "Banner contains authorization/legal language")

    return _result(
        "fail",
        "Banners do not include 'authorized' or 'unauthorized access is prohibited' language",
        "Add explicit unauthorized-access warning to banner motd or banner login.",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "check_motd_banner":               check_motd_banner,
    "check_login_banner":              check_login_banner,
    "check_exec_banner":               check_exec_banner,
    "check_banner_no_info_disclosure": check_banner_no_info_disclosure,
    "check_banner_legal_language":     check_banner_legal_language,
}
