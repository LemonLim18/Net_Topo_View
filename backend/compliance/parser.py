from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ParsedKey:
    section_type: str   # e.g. interface|router_ospf|line|global|set
    section_id: str     # e.g. Ethernet0/1|1|vty 0 4|""|system services ssh
    command: str        # normalized command key


@dataclass
class ParsedConfig:
    """Lightweight vendor-aware parsed view.

    `objects` maps a ParsedKey -> full normalized line text.
    """

    objects: Dict[ParsedKey, str]


def parse(vendor: Optional[str], normalized_lines: List[str]) -> ParsedConfig:
    v = (vendor or "").strip().lower()
    if v.startswith("juniper") or v == "junos" or "junos" in v:
        from .parsers.juniper_junos import parse_junos

        return parse_junos(normalized_lines)
    if v.startswith("huawei") or v == "vrp" or "vrp" in v:
        from .parsers.huawei_vrp import parse_vrp

        return parse_vrp(normalized_lines)
    # default IOS-like
    from .parsers.cisco_ios import parse_ios

    return parse_ios(normalized_lines)

