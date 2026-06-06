"""
M13a: Security Checks — Logging & Syslog
==========================================
CIS Cisco IOS Benchmark Section 4 (extended):
Syslog host, trap level, facility code, source interface, log buffering.
"""

import re
from security.models import AuditResult, SecurityRule

_DUMMY_RULE = SecurityRule(id="", category="", severity="", description="", vendor="", check_fn="")


def _result(status: str, evidence: str = "", remediation: str = "") -> AuditResult:
    return AuditResult(rule=_DUMMY_RULE, status=status, evidence=evidence, remediation=remediation)


# Syslog severity levels (lower number = more severe)
_SYSLOG_LEVELS = {
    "emergencies": 0, "alerts": 1, "critical": 2, "errors": 3,
    "warnings": 4, "notifications": 5, "informational": 6, "debugging": 7,
}


def check_syslog_host(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.2.1: Ensure a remote syslog host is configured."""
    match = re.search(r"logging (?:host )?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|\S+\.example\.)", config_text)
    if match or re.search(r"^logging \d{1,3}\.\d{1,3}", config_text, re.MULTILINE):
        return _result("pass", "Remote syslog host configured")
    # Check common patterns
    if re.search(r"^logging \d", config_text, re.MULTILINE):
        return _result("pass", "Remote syslog host configured")
    return _result("fail", "No remote syslog host configured",
                   "logging host <syslog-server-ip>\nlogging on")


def check_logging_trap_level(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.2.2: Ensure logging trap level is 'informational' or stricter."""
    match = re.search(r"logging trap (\S+)", config_text)
    if match:
        level = match.group(1).lower()
        level_num = _SYSLOG_LEVELS.get(level, 7)
        if level_num <= 6:  # informational or stricter
            return _result("pass", f"Logging trap level: {level}")
        return _result("fail", f"Logging trap level too verbose: {level}",
                       "logging trap informational")
    return _result("fail", "Logging trap level not configured",
                   "logging trap informational")


def check_logging_facility(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.2.3: Ensure syslog facility is configured."""
    if re.search(r"logging facility (local[0-7]|kern|user|mail|daemon|auth|syslog)", config_text, re.IGNORECASE):
        return _result("pass", "Logging facility configured")
    return _result("fail", "Logging facility not configured",
                   "logging facility local6")


def check_logging_source_interface(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.2.4: Ensure logging source-interface is set (for consistent log source IP)."""
    if "logging source-interface" in config_text:
        return _result("pass", "Logging source-interface configured")
    return _result("fail", "No logging source-interface configured",
                   "logging source-interface Loopback0")


def check_archive_logging(config_text: str, vendor: str) -> AuditResult:
    """CIS-4.3.1: Ensure archive logging (config change audit) is enabled."""
    if re.search(r"archive\s+\n.*log config", config_text, re.DOTALL):
        return _result("pass", "Archive log config enabled")
    return _result("fail", "Archive config-change logging not configured",
                   "archive\n log config\n  logging enable\n  notify syslog")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "check_syslog_host":              check_syslog_host,
    "check_logging_trap_level":       check_logging_trap_level,
    "check_logging_facility":         check_logging_facility,
    "check_logging_source_interface": check_logging_source_interface,
    "check_archive_logging":          check_archive_logging,
}
