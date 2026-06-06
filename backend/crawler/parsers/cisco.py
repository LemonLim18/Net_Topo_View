"""
Cisco IOS / IOS-XE CLI output parsers.

Extracted verbatim from ``mini_nce.py`` so that the new crawler module
produces **byte-for-byte identical** results for Cisco devices.

These are *standalone* functions — they do NOT depend on the M1 driver
layer.  The BFS engine can call them when it needs to re-parse raw
output that was already collected, or they can be used in unit tests.
"""

import re
from typing import Dict, List, Optional

# -----------------------------------------------------------------------
# Compiled regexes (same as mini_nce.py)
# -----------------------------------------------------------------------
RE_IF_BRIEF_LINE = re.compile(
    r"^(?P<intf>\S+)\s+(?P<ip>\d+\.\d+\.\d+\.\d+|\S+)\s+"
)
RE_LOOPBACK = re.compile(r"^Loopback", re.IGNORECASE)
RE_HOSTNAME = re.compile(
    r"^\s*hostname\s+(?P<h>\S+)", re.IGNORECASE | re.MULTILINE
)

RE_LLDP_SUM_LINE = re.compile(
    r"^(?P<dev>\S+)\s+(?P<local_if>\S+)\s+\d+\s+\S+\s+(?P<port_id>\S+)",
    re.IGNORECASE,
)

RE_MANAGEMENT_ADDR = re.compile(
    r"IP:\s*(?P<ip>\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
)
RE_LOCAL_INTF = re.compile(r"Local Intf:\s*(?P<intf>\S+)", re.IGNORECASE)
RE_CHASSIS = re.compile(
    r"Chassis id:\s*(?P<mac>[\da-fA-F\.\:\-]+)", re.IGNORECASE
)
RE_PORT_ID = re.compile(r"Port id:\s*(?P<port>\S+)", re.IGNORECASE)
RE_SYSTEM_NAME = re.compile(r"System Name:\s*(?P<name>\S+)", re.IGNORECASE)

RE_ARP_LINE = re.compile(
    r"^\s*Internet\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+"
    r"([\da-fA-F\.]+)\s+\S+\s+(\S+)",
    re.IGNORECASE,
)


# -----------------------------------------------------------------------
# Parsers
# -----------------------------------------------------------------------

def clean_command_echoes(raw: str) -> str:
    """Remove CLI echoes and prompts from raw SSH output."""
    if not raw:
        return raw
    lines = []
    for line in raw.splitlines():
        s = line.rstrip()
        if not s:
            continue
        ls = s.lstrip()
        if ls.startswith("show ") or ls.startswith("terminal length"):
            continue
        if re.fullmatch(r"[A-Za-z0-9\-\._]+[>#]", ls):
            continue
        lines.append(s)
    return "\n".join(lines)


def extract_hostname_from_output(raw: str) -> Optional[str]:
    """Try multiple strategies to extract the hostname from raw CLI output."""
    if not raw:
        return None
    cleaned = clean_command_echoes(raw)
    m = RE_HOSTNAME.search(cleaned)
    if m:
        return m.group("h").strip()
    m2 = re.search(
        r"\bhostname\s+([A-Za-z0-9\-\._]+)\b", cleaned, re.IGNORECASE
    )
    if m2:
        return m2.group(1).strip()
    m3 = re.search(r"^([A-Za-z0-9\-\._]+)[>#]", raw, re.MULTILINE)
    if m3:
        return m3.group(1).strip()
    return None


def parse_ip_interface_brief(raw: str) -> Dict[str, str]:
    """Parse ``show ip interface brief`` → {interface_name: ip_address}."""
    out: Dict[str, str] = {}
    if not raw:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if (
            not line
            or line.lower().startswith("interface")
            or line.lower().startswith("address")
            or line.lower().startswith("internet")
        ):
            continue
        m = RE_IF_BRIEF_LINE.match(line)
        if not m:
            continue
        intf = m.group("intf")
        ip = m.group("ip")
        if ip.lower() != "unassigned" and re.match(
            r"\d+\.\d+\.\d+\.\d+", ip
        ):
            out[intf] = ip
    return out


def parse_lldp_summary(raw: str) -> List[Dict]:
    """Parse ``show lldp neighbor`` summary table."""
    res: List[Dict] = []
    if not raw:
        return res
    for line in raw.splitlines():
        line = line.rstrip()
        m = RE_LLDP_SUM_LINE.match(line)
        if m:
            dev = m.group("dev")
            local_if = m.group("local_if")
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", dev) or re.match(
                r"^\d+\.\d+\.\d+\.\d+$", local_if
            ):
                continue
            if str(dev).lower() == "internet":
                continue
            res.append(
                {
                    "dev_id": dev,
                    "local_if": local_if,
                    "remote_port": m.group("port_id"),
                }
            )
    return res


def parse_lldp_detail_for_mappings(raw: str) -> List[Dict]:
    """Parse ``show lldp neighbors detail`` into per-neighbor dicts."""
    neighbors: List[Dict] = []
    if not raw:
        return neighbors
    blocks = re.split(r"\n[-]{3,}\n|\r?\n\r?\n+", raw)
    for blk in blocks:
        b = blk.strip()
        if not b:
            continue
        local_if = chassis = port = sysname = mgmt = None
        m = RE_LOCAL_INTF.search(b)
        if m:
            local_if = m.group("intf").strip()
        m = RE_CHASSIS.search(b)
        if m:
            chassis = m.group("mac").strip().replace(".", ":").lower()
        m = RE_PORT_ID.search(b)
        if m:
            port = m.group("port").strip()
        m = RE_SYSTEM_NAME.search(b)
        if m:
            sysname = m.group("name").strip()
        m = RE_MANAGEMENT_ADDR.search(b)
        if m:
            mgmt = m.group("ip").strip()
        neighbors.append(
            {
                "local_if": local_if,
                "chassis": chassis,
                "remote_port": port,
                "system_name": sysname,
                "remote_mgmt": mgmt,
            }
        )
    return neighbors


def parse_arp(raw: str) -> List[Dict]:
    """Parse ``show ip arp`` → list of {ip, age, mac, intf}."""
    entries: List[Dict] = []
    if not raw:
        return entries
    for line in raw.splitlines():
        m = RE_ARP_LINE.match(line)
        if m:
            entries.append(
                {
                    "ip": m.group(1),
                    "age": m.group(2),
                    "mac": m.group(3).replace(".", ":").lower(),
                    "intf": m.group(4),
                }
            )
    return entries


def normalize_interface_name(short_if: str) -> str:
    """Expand abbreviated interface names: Gi0/1 → GigabitEthernet0/1."""
    if not short_if:
        return short_if
    if short_if.startswith("Et"):
        return "Ethernet" + short_if[2:]
    if short_if.startswith("Gi"):
        return "GigabitEthernet" + short_if[2:]
    if short_if.startswith("Fa"):
        return "FastEthernet" + short_if[2:]
    if short_if.startswith("Lo"):
        return "Loopback" + short_if[2:]
    return short_if
