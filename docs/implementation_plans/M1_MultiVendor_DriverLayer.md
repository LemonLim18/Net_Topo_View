# M1: Multi-Vendor Driver Layer — Implementation Plan

Build a unified driver abstraction that normalizes device data from **both SSH/CLI (legacy)** and **SDN controller REST APIs (modern)** into a single Python interface.

## Architecture

```text
BaseDriver (ABC)
├── SSHDriver (ABC) ─── shared SSH/Netmiko logic
│   ├── CiscoIOSDriver     → Paramiko SSH (existing mini_nce.py logic)
│   ├── HuaweiVRPDriver    → Netmiko SSH
│   └── JuniperJunosDriver → Netmiko SSH (stub for Q3)
│
└── SDNControllerDriver (ABC) ─── shared REST/HTTP logic
    ├── OpenDaylightDriver  → RESTCONF API (MD-SAL)
    └── ONOSDriver          → REST API (/onos/v1/*)
```

## Output Contract

Every driver returns the same dataclasses:

| Dataclass | Fields | Usage |
|-----------|--------|-------|
| `Interface` | name, ip_address, status, speed | Interface inventory |
| `Neighbor` | local_interface, remote_device, remote_interface, remote_ip | Topology links |
| `Route` | prefix, next_hop, protocol, metric | Routing table |
| `ARPEntry` | ip, mac, interface | ARP/MAC table |
| `DeviceInfo` | hostname, vendor, model, os_version, serial, uptime | Device identity |

## Files

| File | Description |
|------|-------------|
| `backend/drivers/models.py` | Shared dataclasses |
| `backend/drivers/base.py` | Abstract `BaseDriver` |
| `backend/drivers/ssh_driver.py` | Abstract `SSHDriver(BaseDriver)` — shared SSH logic |
| `backend/drivers/sdn_driver.py` | Abstract `SDNControllerDriver(BaseDriver)` — shared HTTP logic |
| `backend/drivers/cisco_ios.py` | `CiscoIOSDriver` — Paramiko SSH, Cisco IOS commands |
| `backend/drivers/huawei_vrp.py` | `HuaweiVRPDriver` — Netmiko SSH, VRP commands |
| `backend/drivers/juniper_junos.py` | `JuniperJunosDriver` — stub for Q3 |
| `backend/drivers/opendaylight.py` | `OpenDaylightDriver` — RESTCONF API |
| `backend/drivers/onos.py` | `ONOSDriver` — REST API |
| `backend/drivers/factory.py` | `DriverFactory` — auto-select driver |
| `backend/drivers/__init__.py` | Package exports |

## Status
- **Created:** 2026-02-16
- **Branch:** `MultiVendor_DriverLayer`
- **Depends on:** Nothing (leaf module)
- **Used by:** M2 (Crawler), M3 (Config Engine), M4 (Deploy), M8 (Drift), M13 (Security)
