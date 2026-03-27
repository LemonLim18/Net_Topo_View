from __future__ import annotations

import re
from typing import Dict, List

from ..parser import ParsedConfig, ParsedKey


def _cmd_key(line: str) -> str:
    toks = [t for t in (line or "").strip().split(" ") if t]
    if not toks:
        return ""
    t0 = toks[0].lower()
    if t0 in ("ip", "ipv6") and len(toks) >= 2:
        return f"{t0} {toks[1].lower()}"
    if t0 == "acl" and len(toks) >= 2:
        return f"acl {toks[1].lower()}"
    if t0 == "ospf":
        return "ospf"
    if t0 == "interface":
        return "interface"
    return t0


def parse_vrp(lines: List[str]) -> ParsedConfig:
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

        m_ospf = re.match(r"^ospf\s+(\S+)$", line, re.I)
        if m_ospf:
            section_type = "ospf"
            section_id = m_ospf.group(1).strip()
            continue

        m_acl = re.match(r"^acl\s+number\s+(\S+)$", line, re.I)
        if m_acl:
            section_type = "acl"
            section_id = m_acl.group(1).strip()
            continue

        if re.match(r"^quit$", line, re.I):
            section_type = "global"
            section_id = ""
            continue

        cmd = _cmd_key(line)
        if not cmd:
            continue
        key = ParsedKey(section_type=section_type, section_id=section_id, command=cmd)
        objects.setdefault(key, line)

    return ParsedConfig(objects=objects)

