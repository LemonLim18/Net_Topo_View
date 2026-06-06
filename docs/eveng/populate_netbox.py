#!/usr/bin/env python3
"""
Populate NetBox with CogniShield lab topology.

Usage:
    export NETBOX_URL=http://192.168.1.252:8000
    export NETBOX_TOKEN=<your-api-token>
    python3 populate_netbox.py

Prerequisites:
    pip install pynetbox
"""

import os
import sys

try:
    import pynetbox
except ImportError:
    print("ERROR: pynetbox not installed. Run: pip install pynetbox")
    sys.exit(1)

NETBOX_URL = os.getenv("NETBOX_URL", "http://192.168.1.252:8000")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", "")

if not NETBOX_TOKEN:
    print("ERROR: Set NETBOX_TOKEN environment variable")
    print("  Create token at: {}/user/api-tokens/".format(NETBOX_URL))
    sys.exit(1)

nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
nb.http_session.verify = False

# -----------------------------------------------------------
# 1. Manufacturers
# -----------------------------------------------------------
MANUFACTURERS = [
    {"name": "Cisco", "slug": "cisco"},
    {"name": "Huawei", "slug": "huawei"},
    {"name": "Juniper", "slug": "juniper"},
]

# -----------------------------------------------------------
# 2. Device Types
# -----------------------------------------------------------
DEVICE_TYPES = [
    {"manufacturer": "cisco",   "model": "CSR 1000v",   "slug": "cisco-csr1000v",   "u_height": 1},
    {"manufacturer": "cisco",   "model": "IOL L3",      "slug": "cisco-iol-l3",     "u_height": 1},
    {"manufacturer": "cisco",   "model": "IOL L2",      "slug": "cisco-iol-l2",     "u_height": 1},
    {"manufacturer": "huawei",  "model": "CE 12800",    "slug": "huawei-ce12800",   "u_height": 2},
    {"manufacturer": "huawei",  "model": "CE 6800",     "slug": "huawei-ce6800",    "u_height": 1},
    {"manufacturer": "juniper", "model": "vEX",         "slug": "juniper-vex",      "u_height": 1},
]

# -----------------------------------------------------------
# 3. Sites & Device Roles
# -----------------------------------------------------------
SITES = [
    {"name": "CogniShield-Lab", "slug": "cognishield-lab", "status": "active"},
]

DEVICE_ROLES = [
    {"name": "Core Router",          "slug": "core-router",          "color": "f44336"},
    {"name": "Distribution Switch",  "slug": "distribution-switch",  "color": "ff9800"},
    {"name": "Access Switch",        "slug": "access-switch",        "color": "4caf50"},
]

# -----------------------------------------------------------
# 4. Devices
# -----------------------------------------------------------
DEVICES = [
    # Core (Cisco CSR 1000v)
    {"name": "Cisco_Core_1",    "device_type": "cisco-csr1000v",  "role": "core-router",         "mgmt_ip": "192.168.1.40/24"},
    {"name": "Cisco_Core_2",    "device_type": "cisco-csr1000v",  "role": "core-router",         "mgmt_ip": "192.168.1.41/24"},
    # Distribution — Cisco IOL L3
    {"name": "Dist_Cisco_SW1",  "device_type": "cisco-iol-l3",    "role": "distribution-switch", "mgmt_ip": "192.168.1.42/24"},
    {"name": "Dist_Cisco_SW3",  "device_type": "cisco-iol-l3",    "role": "distribution-switch", "mgmt_ip": "192.168.1.44/24"},
    # Distribution — Huawei CE 12800
    {"name": "Dist_Huawei_SW2", "device_type": "huawei-ce12800",  "role": "distribution-switch", "mgmt_ip": "192.168.1.43/24"},
    {"name": "Dist_Huawei_SW4", "device_type": "huawei-ce12800",  "role": "distribution-switch", "mgmt_ip": "192.168.1.45/24"},
    # Access — Cisco IOL L2 (Block A)
    {"name": "Acc_Cisco_1",     "device_type": "cisco-iol-l2",    "role": "access-switch",       "mgmt_ip": "192.168.1.46/24"},
    {"name": "Acc_Cisco_2",     "device_type": "cisco-iol-l2",    "role": "access-switch",       "mgmt_ip": "192.168.1.47/24"},
    # Access — Huawei CE 6800 (Block A)
    {"name": "Acc_Huawei_1",    "device_type": "huawei-ce6800",   "role": "access-switch",       "mgmt_ip": "192.168.1.48/24"},
    # Access — Cisco IOL L2 (Block B)
    {"name": "Acc_Cisco_3",     "device_type": "cisco-iol-l2",    "role": "access-switch",       "mgmt_ip": "192.168.1.49/24"},
    {"name": "Acc_Cisco_4",     "device_type": "cisco-iol-l2",    "role": "access-switch",       "mgmt_ip": "192.168.1.50/24"},
    # Access — Juniper vEX (Block B)
    {"name": "Acc_Juniper_1",   "device_type": "juniper-vex",     "role": "access-switch",       "mgmt_ip": "192.168.1.51/24"},
]

# -----------------------------------------------------------
# 5. Cables (inter-device links)
# -----------------------------------------------------------
CABLES = [
    # Core ↔ Core
    ("Cisco_Core_1", "GigabitEthernet1", "Cisco_Core_2", "GigabitEthernet1"),
    # Core1 ↔ Distribution
    ("Cisco_Core_1", "GigabitEthernet2", "Dist_Cisco_SW1",  "Ethernet0/1"),
    ("Cisco_Core_1", "GigabitEthernet3", "Dist_Huawei_SW2", "GE1/0/1"),
    ("Cisco_Core_1", "GigabitEthernet4", "Dist_Cisco_SW3",  "Ethernet0/1"),
    ("Cisco_Core_1", "GigabitEthernet5", "Dist_Huawei_SW4", "GE1/0/0"),
    # Core2 ↔ Distribution
    ("Cisco_Core_2", "GigabitEthernet2", "Dist_Cisco_SW1",  "Ethernet0/2"),
    ("Cisco_Core_2", "GigabitEthernet3", "Dist_Huawei_SW2", "GE1/0/2"),
    ("Cisco_Core_2", "GigabitEthernet4", "Dist_Cisco_SW3",  "Ethernet0/2"),
    ("Cisco_Core_2", "GigabitEthernet5", "Dist_Huawei_SW4", "GE1/0/1"),
    # Distribution Pair Inter-Switch Links (peer links)
    ("Dist_Cisco_SW1", "Ethernet1/2",    "Dist_Huawei_SW2", "GE1/0/5"),
    ("Dist_Cisco_SW3", "Ethernet1/2",    "Dist_Huawei_SW4", "GE1/0/5"),
    # Distribution ↔ Access (Block A — dual-homed: each access to BOTH dist)
    ("Dist_Cisco_SW1",  "Ethernet0/3",  "Acc_Cisco_1",   "Ethernet0/1"),
    ("Dist_Huawei_SW2", "GE1/0/3",      "Acc_Cisco_1",   "Ethernet1/0"),
    ("Dist_Cisco_SW1",  "Ethernet1/0",  "Acc_Cisco_2",   "Ethernet0/1"),
    ("Dist_Huawei_SW2", "GE1/0/4",      "Acc_Cisco_2",   "Ethernet1/0"),
    ("Dist_Cisco_SW1",  "Ethernet1/1",  "Acc_Huawei_1",  "GE1/0/1"),
    ("Dist_Huawei_SW2", "GE1/0/0",      "Acc_Huawei_1",  "GE1/0/5"),
    # Distribution ↔ Access (Block B — dual-homed: each access to BOTH dist)
    ("Dist_Cisco_SW3",  "Ethernet0/3",  "Acc_Cisco_3",   "Ethernet0/1"),
    ("Dist_Huawei_SW4", "GE1/0/3",      "Acc_Cisco_3",   "Ethernet1/0"),
    ("Dist_Cisco_SW3",  "Ethernet1/0",  "Acc_Cisco_4",   "Ethernet0/1"),
    ("Dist_Huawei_SW4", "GE1/0/4",      "Acc_Cisco_4",   "Ethernet1/0"),
    ("Dist_Cisco_SW3",  "Ethernet1/1",  "Acc_Juniper_1", "ge-0/0/0"),
    ("Dist_Huawei_SW4", "GE1/0/2",      "Acc_Juniper_1", "ge-0/0/1"),
]


def get_or_create(endpoint, **kwargs):
    """Get existing object or create new one."""
    slug = kwargs.get("slug") or kwargs.get("name", "").lower().replace(" ", "-")
    existing = endpoint.filter(slug=slug) if hasattr(kwargs, "get") and "slug" in kwargs else None

    try:
        existing = list(endpoint.filter(slug=slug)) if "slug" in kwargs else list(endpoint.filter(name=kwargs.get("name", "")))
    except Exception:
        existing = []

    if existing:
        print(f"  EXISTS: {kwargs.get('name', slug)}")
        return existing[0]

    try:
        obj = endpoint.create(kwargs)
        print(f"  CREATED: {kwargs.get('name', slug)}")
        return obj
    except Exception as e:
        print(f"  ERROR creating {kwargs.get('name', slug)}: {e}")
        return None


def main():
    print("=== Populating NetBox for CogniShield Lab ===\n")

    # Manufacturers
    print("Manufacturers:")
    for m in MANUFACTURERS:
        get_or_create(nb.dcim.manufacturers, **m)

    # Device Types
    print("\nDevice Types:")
    for dt in DEVICE_TYPES:
        mfg = nb.dcim.manufacturers.get(slug=dt.pop("manufacturer"))
        dt["manufacturer"] = mfg.id if mfg else 1
        get_or_create(nb.dcim.device_types, **dt)

    # Sites
    print("\nSites:")
    for s in SITES:
        get_or_create(nb.dcim.sites, **s)

    # Device Roles
    print("\nDevice Roles:")
    for r in DEVICE_ROLES:
        get_or_create(nb.dcim.device_roles, **r)

    # Devices
    print("\nDevices:")
    site = nb.dcim.sites.get(slug="cognishield-lab")
    for dev in DEVICES:
        mgmt_ip = dev.pop("mgmt_ip")
        dt = nb.dcim.device_types.get(slug=dev["device_type"])
        role = nb.dcim.device_roles.get(slug=dev["role"])

        device = get_or_create(
            nb.dcim.devices,
            name=dev["name"],
            device_type=dt.id if dt else 1,
            role=role.id if role else 1,
            site=site.id if site else 1,
            status="active",
        )

        if device:
            # Create management interface + IP
            try:
                iface = nb.dcim.interfaces.create(
                    device=device.id,
                    name="Management",
                    type="virtual",
                )
                ip = nb.ipam.ip_addresses.create(
                    address=mgmt_ip,
                    assigned_object_type="dcim.interface",
                    assigned_object_id=iface.id,
                    status="active",
                )
                device.primary_ip4 = ip.id
                device.save()
                print(f"    IP: {mgmt_ip}")
            except Exception as e:
                print(f"    IP assignment skipped: {e}")

    print("\n=== NetBox Population Complete ===")
    print(f"Dashboard: {NETBOX_URL}")


if __name__ == "__main__":
    main()
