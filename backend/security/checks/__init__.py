"""
M13a: Security Checks — unified check registry.

Merges all sub-module registries so the engine only needs to import
from this package.  New check modules are registered by adding them
to the import list below.
"""

from security.checks.cisco_ios      import CHECK_REGISTRY as _reg_ios
from security.checks.auth_checks    import CHECK_REGISTRY as _reg_auth
from security.checks.transport_checks import CHECK_REGISTRY as _reg_transport
from security.checks.snmp_checks    import CHECK_REGISTRY as _reg_snmp
from security.checks.logging_checks import CHECK_REGISTRY as _reg_logging
from security.checks.mgmt_plane_checks import CHECK_REGISTRY as _reg_mgmt
from security.checks.banner_checks  import CHECK_REGISTRY as _reg_banner

# Merged registry — later modules override on name collision
CHECK_REGISTRY: dict = {
    **_reg_ios,
    **_reg_auth,
    **_reg_transport,
    **_reg_snmp,
    **_reg_logging,
    **_reg_mgmt,
    **_reg_banner,
}

__all__ = ["CHECK_REGISTRY"]
