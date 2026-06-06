from __future__ import annotations

import re
from typing import Dict, List

from ..parser import ParsedConfig, ParsedKey


def _cmd_key(line: str) -> str:
    # Reduce value changes into same command key where possible.
    toks = [t for t in (line or "").strip().split(" ") if t]
    if not toks:
        return ""
    # keys for common patterns
    if toks[0].lower() in ("ip", "ipv6") and len(toks) >= 2:
        return f"{toks[0].lower()} {toks[1].lower()}"
    if toks[0].lower() in ("logging", "ntp", "aaa", "snmp-server") and len(toks) >= 1:
        return toks[0].lower()
    if toks[0].lower() == "hostname":
        return "hostname"
    return toks[0].lower()


def parse_ios(lines: List[str]) -> ParsedConfig:
    objects: Dict[ParsedKey, str] = {}
    section_type = "global"
    section_id = ""

    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue

        m_if = re.match(r"^interface\s+(.+)$", line, re.I)
        if m_if:
            section_type = "interface"
            section_id = m_if.group(1).strip()
            continue

        m_ospf = re.match(r"^router\s+ospf\s+(\S+)$", line, re.I)
        if m_ospf:
            section_type = "router_ospf"
            section_id = m_ospf.group(1).strip()
            continue

        m_line = re.match(r"^line\s+(\S+)\s+(.+)$", line, re.I)
        if m_line:
            section_type = "line"
            section_id = f"{m_line.group(1).strip()} {m_line.group(2).strip()}"
            continue

        if re.match(r"^exit$", line, re.I):
            section_type = "global"
            section_id = ""
            continue

        cmd = _cmd_key(line)
        if not cmd:
            continue
        key = ParsedKey(section_type=section_type, section_id=section_id, command=cmd)
        # keep first occurrence; subsequent duplicates are ignored in parsed view
        objects.setdefault(key, line)

    return ParsedConfig(objects=objects)

