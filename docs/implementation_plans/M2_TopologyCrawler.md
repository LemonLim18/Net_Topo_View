# M2: Topology Crawler Refactor — Implementation Plan

## Goal
Refactor `mini_nce.py` (765 lines) into a modular `backend/crawler/` package
that uses M1 Drivers instead of raw Paramiko and M14 Database instead of JSON files.

> **Legacy files `backend.py` and `mini_nce.py` are NOT deleted.**

## File Tree

```text
backend/crawler/
├── __init__.py           # exports crawl_topology()
├── bfs_engine.py         # BFS loop + ThreadPoolExecutor
├── poll.py               # poll_device() using M1 drivers
├── topology.py           # canonical_topology() + diff
├── store.py              # bridge to M14 DatabaseManager
└── parsers/
    ├── __init__.py
    └── cisco.py          # parsers extracted from mini_nce.py
```

## Migration Map

| Old Function | New Location | Change |
|---|---|---|
| `ssh_invoke()` | Replaced by M1 Drivers | No longer needed |
| `parse_ip_interface_brief()` | `parsers/cisco.py` | Copied verbatim |
| `parse_lldp_summary()` | `parsers/cisco.py` | Copied verbatim |
| `parse_lldp_detail_for_mappings()` | `parsers/cisco.py` | Copied verbatim |
| `parse_arp()` | `parsers/cisco.py` | Copied verbatim |
| `poll_device()` | `poll.py` | Uses M1 drivers |
| `crawl_topology()` | `bfs_engine.py` | Uses new poll + M14 |
| `canonical_topology()` | `topology.py` | Moved from backend.py |

## Verification
- All imports verified in venv
- Parser output matches mini_nce.py identically
- Topology diff logic tested (identical/changed cases)
- replace_node_key merging verified
