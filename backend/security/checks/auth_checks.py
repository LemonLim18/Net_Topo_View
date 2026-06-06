"""
M13a: Security Checks — Authentication & AAA
=============================================
CIS Cisco IOS Benchmark Section 2 (extended):
AAA new-model, RADIUS/TACACS+, password policies, login failure handling.
"""

import re
from security.models import AuditResult, SecurityRule

_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


# ---------------------------------------------------------------------------
# AAA Framework
# ---------------------------------------------------------------------------

def check_aaa_new_model(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.2.1: Ensure AAA new-model is enabled."""
    if "aaa new-model" in config_text:
        return _result("pass", "AAA new-model is enabled")
    return _result("fail", "aaa new-model not found (AAA framework disabled)",
                   "aaa new-model")


def check_aaa_authentication_login(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.2.2: Ensure 'aaa authentication login default' is configured."""
    if re.search(r"aaa authentication login default", config_text):
        return _result("pass", "AAA authentication login default configured")
    return _result("fail", "No 'aaa authentication login default' found",
                   "aaa authentication login default group tacacs+ local\n"
                   "aaa authentication login default group radius local")


def check_aaa_authorization(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.2.3: Ensure AAA authorization is configured for privileged commands."""
    if re.search(r"aaa authorization (exec|commands)", config_text):
        return _result("pass", "AAA authorization configured for exec/commands")
    return _result("fail", "No AAA authorization for exec or commands",
                   "aaa authorization exec default group tacacs+ local\n"
                   "aaa authorization commands 15 default group tacacs+ local")


def check_aaa_accounting(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.2.4: Ensure AAA accounting is configured."""
    if re.search(r"aaa accounting (exec|commands|system)", config_text):
        return _result("pass", "AAA accounting configured")
    return _result("fail", "No AAA accounting found",
                   "aaa accounting exec default start-stop group tacacs+")


# ---------------------------------------------------------------------------
# Password Policies
# ---------------------------------------------------------------------------

def check_minimum_password_length(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.3.1: Ensure minimum password length >= 10."""
    match = re.search(r"security passwords min-length (\d+)", config_text)
    if match:
        length = int(match.group(1))
        if length >= 10:
            return _result("pass", f"Minimum password length: {length}")
        return _result("fail", f"Password minimum length too short: {length}",
                       "security passwords min-length 10")
    return _result("fail", "No minimum password length configured",
                   "security passwords min-length 10")


def check_login_failure_lockout(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.3.2: Ensure login block-for is configured to lock out after failures."""
    if re.search(r"login block-for \d+ attempts \d+ within \d+", config_text):
        return _result("pass", "Login failure lockout configured")
    return _result("fail", "No login failure lockout (brute-force protection missing)",
                   "login block-for 120 attempts 3 within 60")


def check_login_quiet_mode(config_text: str, vendor: str) -> AuditResult:
    """CIS-2.3.3: Ensure login quiet-mode ACL is configured."""
    if re.search(r"login quiet-mode access-class", config_text):
        return _result("pass", "Login quiet-mode ACL configured")
    return _result("fail", "No login quiet-mode access-class",
                   "login quiet-mode access-class PERMIT_MGMT_ONLY")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "check_aaa_new_model":          check_aaa_new_model,
    "check_aaa_authentication_login": check_aaa_authentication_login,
    "check_aaa_authorization":      check_aaa_authorization,
    "check_aaa_accounting":         check_aaa_accounting,
    "check_minimum_password_length": check_minimum_password_length,
    "check_login_failure_lockout":  check_login_failure_lockout,
    "check_login_quiet_mode":       check_login_quiet_mode,
}
