"""
M13a: CIS-style Security Rules
================================
Defines all security checks organized by category.
Each rule maps to a check function in the checks/ sub-package.
"""

from security.models import SecurityRule

# ---------------------------------------------------------------------------
# Rule Definitions — 10 categories, ~25 checks
# ---------------------------------------------------------------------------

SECURITY_RULES = [
    # ── Access Control ────────────────────────────────────────
    SecurityRule(
        id="CIS-1.1.1", category="access_control", severity="critical",
        description="Ensure Telnet is disabled on VTY lines",
        vendor="cisco_ios", check_fn="check_telnet_disabled",
    ),
    SecurityRule(
        id="CIS-1.1.2", category="access_control", severity="high",
        description="Ensure SSH is enabled for remote management",
        vendor="cisco_ios", check_fn="check_ssh_enabled",
    ),
    SecurityRule(
        id="CIS-1.1.3", category="access_control", severity="medium",
        description="Ensure ACL is applied to VTY lines",
        vendor="cisco_ios", check_fn="check_vty_acl",
    ),

    # ── Authentication ────────────────────────────────────────
    SecurityRule(
        id="CIS-2.1.1", category="authentication", severity="critical",
        description="Ensure enable secret is configured",
        vendor="cisco_ios", check_fn="check_enable_secret",
    ),
    SecurityRule(
        id="CIS-2.1.2", category="authentication", severity="high",
        description="Ensure password encryption is enabled",
        vendor="cisco_ios", check_fn="check_password_encryption",
    ),
    SecurityRule(
        id="CIS-2.1.3", category="authentication", severity="high",
        description="Ensure local authentication is configured",
        vendor="cisco_ios", check_fn="check_local_auth",
    ),

    # ── Banner ────────────────────────────────────────────────
    SecurityRule(
        id="CIS-3.1.1", category="banner", severity="medium",
        description="Ensure login banner is configured",
        vendor="cisco_ios", check_fn="check_login_banner",
    ),

    # ── Logging ───────────────────────────────────────────────
    SecurityRule(
        id="CIS-4.1.1", category="logging", severity="high",
        description="Ensure logging is enabled",
        vendor="cisco_ios", check_fn="check_logging_enabled",
    ),
    SecurityRule(
        id="CIS-4.1.2", category="logging", severity="medium",
        description="Ensure logging buffer size is adequate (>=4096)",
        vendor="cisco_ios", check_fn="check_logging_buffer",
    ),
    SecurityRule(
        id="CIS-4.1.3", category="logging", severity="high",
        description="Ensure timestamps are on log messages",
        vendor="cisco_ios", check_fn="check_logging_timestamps",
    ),

    # ── NTP ───────────────────────────────────────────────────
    SecurityRule(
        id="CIS-5.1.1", category="ntp", severity="medium",
        description="Ensure NTP is configured",
        vendor="cisco_ios", check_fn="check_ntp_configured",
    ),

    # ── SNMP ──────────────────────────────────────────────────
    SecurityRule(
        id="CIS-6.1.1", category="snmp", severity="critical",
        description="Ensure SNMP community 'public' is not used",
        vendor="cisco_ios", check_fn="check_snmp_no_public",
    ),
    SecurityRule(
        id="CIS-6.1.2", category="snmp", severity="critical",
        description="Ensure SNMP community 'private' is not used",
        vendor="cisco_ios", check_fn="check_snmp_no_private",
    ),

    # ── Encryption / SSH ──────────────────────────────────────
    SecurityRule(
        id="CIS-7.1.1", category="encryption", severity="critical",
        description="Ensure SSH version 2 is configured",
        vendor="cisco_ios", check_fn="check_ssh_v2",
    ),
    SecurityRule(
        id="CIS-7.1.2", category="encryption", severity="high",
        description="Ensure HTTP server is disabled",
        vendor="cisco_ios", check_fn="check_no_http_server",
    ),

    # ── Routing ───────────────────────────────────────────────
    SecurityRule(
        id="CIS-8.1.1", category="routing", severity="medium",
        description="Ensure no IP source routing",
        vendor="cisco_ios", check_fn="check_no_source_route",
    ),

    # ── Interface Hardening ───────────────────────────────────
    SecurityRule(
        id="CIS-9.1.1", category="interface", severity="medium",
        description="Ensure CDP is disabled on external interfaces",
        vendor="cisco_ios", check_fn="check_cdp_disabled",
    ),
    SecurityRule(
        id="CIS-9.1.2", category="interface", severity="low",
        description="Ensure unused interfaces are shutdown",
        vendor="cisco_ios", check_fn="check_unused_shutdown",
    ),

    # ── Console ───────────────────────────────────────────────
    SecurityRule(
        id="CIS-10.1.1", category="console", severity="high",
        description="Ensure exec-timeout is set on console",
        vendor="cisco_ios", check_fn="check_exec_timeout",
    ),
    SecurityRule(
        id="CIS-10.1.2", category="console", severity="high",
        description="Ensure exec-timeout is set on VTY lines",
        vendor="cisco_ios", check_fn="check_vty_timeout",
    ),
]


def get_rules_for_vendor(vendor: str):
    """Return rules applicable to a vendor (vendor-specific + 'all')."""
    return [r for r in SECURITY_RULES if r.vendor in (vendor, "all")]
