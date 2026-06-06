"""
Vendor-specific CLI output parsers.

Currently supports:
    - Cisco IOS / IOS-XE  (cisco.py)
"""

from crawler.parsers.cisco import (  # noqa: F401
    parse_ip_interface_brief,
    parse_lldp_summary,
    parse_lldp_detail_for_mappings,
    parse_arp,
    normalize_interface_name,
    extract_hostname_from_output,
    clean_command_echoes,
)
