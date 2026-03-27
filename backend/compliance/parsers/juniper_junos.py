from __future__ import annotations

from typing import Dict, List

from ..parser import ParsedConfig, ParsedKey


def parse_junos(lines: List[str]) -> ParsedConfig:
    objects: Dict[ParsedKey, str] = {}
    # Junos config is often "set ..." lines; treat the path as section_id and leaf as command.
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if not line.lower().startswith("set "):
            # still store as global-ish
            key = ParsedKey(section_type="global", section_id="", command=line.split(" ", 1)[0].lower())
            objects.setdefault(key, line)
            continue
        toks = [t for t in line.split(" ") if t]
        # toks[0] = set
        if len(toks) < 3:
            key = ParsedKey(section_type="set", section_id="", command="set")
            objects.setdefault(key, line)
            continue
        # Use everything but last token as path section_id, last token as value-bearing leaf key.
        path = " ".join(toks[1:-1]).strip().lower()
        leaf = toks[-1].strip().lower()
        # For stable pairing, treat the last non-value command word as command; leaf tends to be value.
        # Example: set system services ssh -> command=ssh, section_id=system services
        if len(toks) >= 4:
            section_id = " ".join(toks[1:-1]).strip().lower()
            command = toks[-2].strip().lower()
            key = ParsedKey(section_type="set", section_id=section_id, command=command)
        else:
            key = ParsedKey(section_type="set", section_id=path, command="set")
        objects.setdefault(key, line)
    return ParsedConfig(objects=objects)

