"""
Syntax Validator — Per-vendor configuration syntax checks.

Performs basic structural validation of rendered configs before applying
them to devices. Catches common mistakes like unmatched blocks, missing
keywords, and invalid patterns.

Usage:
    validator = SyntaxValidator()
    warnings = validator.validate("cisco_ios", rendered_config)
    if warnings:
        print("Issues found:", warnings)
"""

import re
import logging
from typing import List, Dict, Callable, Optional

logger = logging.getLogger("config_engine.validator")


class SyntaxValidator:
    """
    Basic syntax validation for rendered network configurations.

    This is NOT a full parser — it catches common structural issues
    that could cause device errors during deployment.
    """

    def validate(self, vendor: str, config: str) -> List[str]:
        """
        Validate a rendered config for a given vendor.

        Args:
            vendor: One of 'cisco_ios', 'huawei_vrp', 'juniper_junos'
            config: The rendered configuration text.

        Returns:
            List of warning messages. Empty list = no issues found.
        """
        if not config or not config.strip():
            return ["Configuration is empty."]

        # Get vendor-specific checks
        checks = self._get_vendor_checks(vendor)
        warnings = []

        for check_fn in checks:
            result = check_fn(config)
            warnings.extend(result)

        return warnings

    def _get_vendor_checks(self, vendor: str) -> List[Callable[[str], List[str]]]:
        """Return the list of validation functions for a vendor."""
        vendor_checks = {
            "cisco_ios": [
                self._check_cisco_block_balance,
                self._check_cisco_no_empty_commands,
                self._check_cisco_ip_format,
                self._check_cisco_interface_names,
                self._check_common_trailing_whitespace,
            ],
            "huawei_vrp": [
                self._check_huawei_commit_present,
                self._check_huawei_no_empty_commands,
                self._check_cisco_ip_format,  # IP format is universal
                self._check_common_trailing_whitespace,
            ],
            "juniper_junos": [
                self._check_junos_set_format,
                self._check_junos_no_mixed_modes,
                self._check_cisco_ip_format,
                self._check_common_trailing_whitespace,
            ],
        }

        checks = vendor_checks.get(vendor, [])
        if not checks:
            logger.warning(f"No validator rules for vendor '{vendor}'")
            return [self._check_common_trailing_whitespace]

        return checks

    # ------------------------------------------------------------------
    # Cisco IOS checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_cisco_block_balance(config: str) -> List[str]:
        """Check that Cisco config blocks are properly structured."""
        warnings = []
        lines = config.splitlines()

        # Track router/interface blocks: they should end with 'exit' or '!'
        in_block = False
        block_name = ""

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Entering a block
            if re.match(r"^(router|interface|ip access-list|vlan|line)\s", stripped, re.IGNORECASE):
                if in_block:
                    warnings.append(
                        f"Line {i}: Nested block '{stripped}' inside '{block_name}' — "
                        f"may need 'exit' before this."
                    )
                in_block = True
                block_name = stripped

            # Exiting a block
            if stripped in ("exit", "!", "end"):
                in_block = False
                block_name = ""

        return warnings

    @staticmethod
    def _check_cisco_no_empty_commands(config: str) -> List[str]:
        """Warn about lines that are just whitespace or only keywords with no args."""
        warnings = []
        single_keywords = {"router", "interface", "ip", "no"}

        for i, line in enumerate(config.splitlines(), 1):
            stripped = line.strip()
            if stripped.lower() in single_keywords:
                warnings.append(
                    f"Line {i}: Incomplete command '{stripped}' — missing arguments."
                )

        return warnings

    @staticmethod
    def _check_cisco_interface_names(config: str) -> List[str]:
        """Check that interface names follow valid patterns."""
        warnings = []
        valid_prefixes = [
            "GigabitEthernet", "FastEthernet", "TenGigabitEthernet",
            "Loopback", "Vlan", "Port-channel", "Tunnel",
            "Serial", "Ethernet", "ge-", "xe-", "et-",
        ]

        for i, line in enumerate(config.splitlines(), 1):
            m = re.match(r"^interface\s+(\S+)", line.strip(), re.IGNORECASE)
            if m:
                intf = m.group(1)
                if not any(intf.startswith(p) for p in valid_prefixes):
                    warnings.append(
                        f"Line {i}: Unusual interface name '{intf}' — verify spelling."
                    )

        return warnings

    # ------------------------------------------------------------------
    # Huawei VRP checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_huawei_commit_present(config: str) -> List[str]:
        """
        Huawei VRP doesn't normally use 'commit' (unlike Junos),
        but we check for 'return' at the end as a best practice.
        """
        warnings = []
        stripped = config.strip()

        if stripped and not stripped.endswith("return"):
            warnings.append(
                "Huawei VRP config should end with 'return' to exit system-view."
            )

        return warnings

    @staticmethod
    def _check_huawei_no_empty_commands(config: str) -> List[str]:
        """Warn about incomplete Huawei commands."""
        warnings = []
        single_keywords = {"ospf", "bgp", "interface", "acl", "vlan"}

        for i, line in enumerate(config.splitlines(), 1):
            stripped = line.strip()
            if stripped.lower() in single_keywords:
                warnings.append(
                    f"Line {i}: Incomplete command '{stripped}' — missing arguments."
                )

        return warnings

    # ------------------------------------------------------------------
    # Juniper Junos checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_junos_set_format(config: str) -> List[str]:
        """Junos configs should use 'set', 'delete', or 'deactivate' commands."""
        warnings = []

        for i, line in enumerate(config.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if not stripped.startswith(("set ", "delete ", "deactivate ", "activate ")):
                warnings.append(
                    f"Line {i}: '{stripped[:40]}...' doesn't start with 'set'/'delete'. "
                    f"Junos expects flat 'set' commands."
                )

        return warnings

    @staticmethod
    def _check_junos_no_mixed_modes(config: str) -> List[str]:
        """Warn if config mixes 'set' format with hierarchical format."""
        warnings = []
        has_set = any(l.strip().startswith("set ") for l in config.splitlines())
        has_hierarchy = any(
            re.match(r"^\s+(protocols|system|interfaces|routing-options)", l)
            for l in config.splitlines()
        )

        if has_set and has_hierarchy:
            warnings.append(
                "Mixed 'set' commands and hierarchical format detected. "
                "Use one or the other consistently."
            )

        return warnings

    # ------------------------------------------------------------------
    # Common checks (all vendors)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_cisco_ip_format(config: str) -> List[str]:
        """Check that IP addresses in config look valid."""
        warnings = []
        ip_pattern = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

        for i, line in enumerate(config.splitlines(), 1):
            for m in ip_pattern.finditer(line):
                ip = m.group(1)
                octets = ip.split(".")
                for octet in octets:
                    if int(octet) > 255:
                        warnings.append(
                            f"Line {i}: Invalid IP address '{ip}' — "
                            f"octet {octet} exceeds 255."
                        )
                        break

        return warnings

    @staticmethod
    def _check_common_trailing_whitespace(config: str) -> List[str]:
        """Warn about excessive trailing whitespace (cosmetic but can cause issues)."""
        warnings = []

        for i, line in enumerate(config.splitlines(), 1):
            if line != line.rstrip() and len(line) - len(line.rstrip()) > 2:
                warnings.append(
                    f"Line {i}: Excessive trailing whitespace."
                )

        return warnings
