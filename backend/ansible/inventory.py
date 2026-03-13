"""
Dynamic Ansible inventory generator.

Builds an ansible-runner compatible inventory dict from the live topology
stored in topology_store/current_topo.json and the device DB.

Credentials are NEVER embedded here — they always come from Ansible Vault
(vault/net_creds.yml) or environment variables resolved at playbook runtime.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from ansible.planning_context import normalize_role, normalize_site, normalize_vendor

logger = logging.getLogger("ansible.inventory")

TOPO_FILE = Path(__file__).parent.parent / "topology_store" / "current_topo.json"

# Mapping from our vendor strings → ansible_network_os values
_VENDOR_OS_MAP = {
    "cisco_ios":     "ios",
    "cisco_nxos":    "nxos",
    "cisco_xr":      "iosxr",
    "huawei_vrp":    "vrp",
    "juniper_junos": "junos",
    "aruba":         "aos",
}

# Vendors that support NETCONF (use netconf connection plugin)
_NETCONF_VENDORS = {"juniper_junos"}

# Vendors that have native Ansible resource modules
_NATIVE_MODULE_VENDORS = {"cisco_ios", "cisco_nxos", "cisco_xr", "juniper_junos"}


def build_inventory(
    target_hosts: Optional[list[str]] = None,
    topology_override: Optional[dict] = None,
) -> dict:
    """
    Generate an ansible-runner compatible inventory from the live topology.

    Args:
        target_hosts:       If provided, only include these hostnames.
        topology_override:  If provided, use this topology dict instead of
                            reading the file (useful for testing).

    Returns:
        Inventory dict suitable for ansible-runner (written as YAML to
        private_data_dir/inventory/hosts.yml by runner.py).
    """
    topo = topology_override
    if topo is None:
        if TOPO_FILE.exists():
            topo = json.loads(TOPO_FILE.read_text())
        else:
            logger.warning("Topology file not found at %s — empty inventory", TOPO_FILE)
            topo = {}

    nodes = topo.get("nodes", {})
    if isinstance(nodes, dict):
        node_list = list(nodes.values())
    else:
        node_list = nodes  # already a list

    hosts: dict = {}
    groups: dict = {}

    for node in node_list:
        if not isinstance(node, dict):
            continue

        hostname = node.get("id") or node.get("hostname") or node.get("name")
        if not hostname:
            continue
        if target_hosts and hostname not in target_hosts:
            continue

        vendor = normalize_vendor(node.get("vendor"))
        mgmt_ip = node.get("ip") or node.get("mgmt_ip") or hostname
        role = normalize_role(node.get("role"))
        site = normalize_site(node.get("site"))
        os_name = _VENDOR_OS_MAP.get(vendor, "default")
        connection = "netconf" if vendor in _NETCONF_VENDORS else "network_cli"
        neighbor_names = list(node.get("connectedNode") or [])
        interface_records = list(node.get("interfaces") or [])

        hosts[hostname] = {
            "ansible_host":         mgmt_ip,
            "ansible_network_os":   os_name,
            "ansible_connection":   connection,
            "ansible_user":         "{{ vault_net_user }}",
            "ansible_password":     "{{ vault_net_pass }}",
            "ansible_become":       True,
            "ansible_become_method": "enable",
            "ansible_become_pass":  "{{ vault_enable_pass }}",
            # Metadata for routing decisions
            "nettopo_vendor":       vendor,
            "nettopo_role":         role,
            "nettopo_site":         site,
            "nettopo_model":        node.get("model"),
            "nettopo_status":       node.get("status"),
            "nettopo_interfaces":   interface_records,
            "nettopo_neighbors":    neighbor_names,
            "nettopo_has_modules":  vendor in _NATIVE_MODULE_VENDORS,
            "nettopo_hop_chain":    node.get("hop_chain") or [],
        }

        # Group devices by vendor for group_vars
        group = f"vendor_{vendor}" if vendor else "ungrouped"
        groups.setdefault(group, {"hosts": {}})["hosts"][hostname] = {}
        if role:
            groups.setdefault(f"role_{role}", {"hosts": {}})["hosts"][hostname] = {}
            if role == "access":
                groups.setdefault("access_switches", {"hosts": {}})["hosts"][hostname] = {}
            if role == "distribution":
                groups.setdefault("distribution_switches", {"hosts": {}})["hosts"][hostname] = {}
            if role == "core":
                groups.setdefault("core_routers", {"hosts": {}})["hosts"][hostname] = {}
        if site:
            groups.setdefault(f"site_{site}", {"hosts": {}})["hosts"][hostname] = {}

    inventory: dict = {
        "all": {
            "hosts": hosts,
            "vars": {
                "ansible_command_timeout": 60,
                "ansible_connect_timeout": 30,
            },
            "children": groups,
        }
    }

    logger.info(
        "Built inventory: %d hosts across %d vendor groups",
        len(hosts), len(groups),
    )
    return inventory


def can_use_ansible(host_vars: dict) -> bool:
    """
    Return True if this host can be targeted via Ansible directly
    (no hop-chain relay required and vendor is supported).
    """
    hop_chain = host_vars.get("nettopo_hop_chain") or []
    return len(hop_chain) == 0


def has_native_modules(host_vars: dict) -> bool:
    """Return True if vendor has resource modules (not just network_cli)."""
    return bool(host_vars.get("nettopo_has_modules", False))
