"""
BFS Topology Discovery Engine.

Reimplements ``mini_nce.py:crawl_topology()`` on top of:
*   **M1 Drivers** — for SSH / SDN connections
*   **M14 Database** — for persistent storage (optional)

The output is the **exact same JSON shape** the frontend already
consumes::

    {
      "timestamp": "2026-02-16T23:00:00+08:00",
      "seeds": ["192.168.1.123"],
      "nodes": {
        "R1": {
          "interfaceList": [{"Ethernet0/0": "192.168.1.1", ...}],
          "connectedNode": ["R2", "R3"],
          "edgeLink": { "Et0/0": {...}, ... },
          "mgmt_ip": "1.1.1.1"
        },
        ...
      }
    }
"""

import re
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from crawler.poll import poll_device
from crawler.parsers.cisco import normalize_interface_name

logger = logging.getLogger("crawler.bfs")

RE_LOOPBACK = re.compile(r"^Loopback", re.IGNORECASE)
TZ_UTC8 = timezone(timedelta(hours=8))


# -----------------------------------------------------------------------
# Node key management (same logic as mini_nce.py)
# -----------------------------------------------------------------------

def replace_node_key(nodes: Dict, old_key: str, new_key: str) -> None:
    """
    Rename a node key and update all edgeLink / connectedNode references.

    Copied verbatim from ``mini_nce.py`` to guarantee identical merging
    behaviour for placeholder → real-hostname resolution.
    """
    if old_key == new_key or old_key not in nodes:
        return

    dest = nodes.get(
        new_key,
        {
            "interfaceList": [{}],
            "connectedNode": [],
            "edgeLink": {},
            "mgmt_ip": None,
        },
    )
    src = nodes[old_key]

    # merge interfaceList (list with single dict)
    try:
        dest_if = dest.get("interfaceList", [{}])[0]
        src_if = src.get("interfaceList", [{}])[0]
        merged_if = dict(dest_if)
        merged_if.update({k: v for k, v in src_if.items() if k not in merged_if})
        dest["interfaceList"] = [merged_if]
    except Exception:
        pass

    # merge connectedNode
    for n in src.get("connectedNode", []):
        if n not in dest["connectedNode"]:
            dest["connectedNode"].append(n)

    # merge edgeLink
    for k, v in src.get("edgeLink", {}).items():
        if k not in dest["edgeLink"]:
            dest["edgeLink"][k] = v

    # prefer dest mgmt_ip else src
    if not dest.get("mgmt_ip"):
        dest["mgmt_ip"] = src.get("mgmt_ip")

    nodes[new_key] = dest
    del nodes[old_key]

    # update all references
    for _nk, node in nodes.items():
        node["connectedNode"] = [
            new_key if x == old_key else x
            for x in node.get("connectedNode", [])
        ]
        for _inf, ed in list(node.get("edgeLink", {}).items()):
            if ed.get("remote_node") == old_key:
                node["edgeLink"][_inf]["remote_node"] = new_key


# -----------------------------------------------------------------------
# Crawl topology — BFS
# -----------------------------------------------------------------------

def crawl_topology(
    seeds: List[str],
    username: str,
    password: Optional[str],
    key_file: Optional[str] = None,
    driver_type: str = "cisco_ios",
    jump_host: Optional[dict] = None,
    workers: int = 10,
    cred_overrides: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Dict:
    """
    BFS-discover the full network topology from *seeds*.

    Args:
        seeds:        List of seed device IPs to start discovery from.
        username:     SSH username (used for all devices).
        password:     SSH password (used for all devices).
        key_file:     Optional SSH key file path.
        driver_type:  M1 driver type (e.g. ``"cisco_ios"``).
        jump_host:    Optional jump/bastion host dict.
        workers:      Number of parallel polling threads.

    Returns:
        A topology dict with the same shape as ``mini_nce.py`` output::

            {"timestamp": "...", "seeds": [...], "nodes": {…}}
    """
    q = deque([(ip, []) for ip in seeds])  # (target_ip, hop_chain)
    queued_ips = set(seeds)
    visited_ips: set = set()
    completed_nodes: set = set()
    nodes: Dict[str, Dict] = {}
    ip_to_key: Dict[str, str] = {}
    ip_hop_chain: Dict[str, List[str]] = {ip: [] for ip in seeds}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        while q:
            # Build a batch of up to ``workers`` items
            batch = []
            while q and len(batch) < workers:
                batch.append(q.popleft())

            future_map = {
                ex.submit(
                    poll_device,
                    ip,
                    username,
                    password,
                    key_file,
                    driver_type,
                    hop_chain=chain,
                    jump_host=jump_host,
                    cred_overrides=cred_overrides,
                ): (ip, chain)
                for ip, chain in batch
            }

            for fut in as_completed(future_map):
                ip, hop_chain = future_map[fut]
                if ip in visited_ips:
                    continue
                try:
                    dev = fut.result()
                except Exception as e:
                    logger.warning("Failed to poll %s: %s", ip, e)
                    visited_ips.add(ip)
                    continue
                visited_ips.add(ip)

                # --- Hostname resolution ---
                hostname = dev.get("hostname") or ip
                prev_key = ip_to_key.get(ip)
                if prev_key and prev_key != hostname:
                    replace_node_key(nodes, prev_key, hostname)
                    for k, v in list(ip_to_key.items()):
                        if v == prev_key:
                            ip_to_key[k] = hostname
                ip_to_key[ip] = hostname

                if hostname not in nodes:
                    nodes[hostname] = {
                        "interfaceList": [{}],
                        "connectedNode": [],
                        "edgeLink": {},
                        "mgmt_ip": None,
                    }

                # --- Interfaces ---
                iface_dict = {
                    ifname: ipaddr
                    for ifname, ipaddr in dev.get("interfaces", {}).items()
                }
                nodes[hostname]["interfaceList"] = [iface_dict]

                loopbacks = {
                    i: p
                    for i, p in dev.get("interfaces", {}).items()
                    if RE_LOOPBACK.match(i)
                }
                if loopbacks:
                    nodes[hostname]["mgmt_ip"] = next(iter(loopbacks.values()))
                else:
                    nodes[hostname]["mgmt_ip"] = ip

                # --- ARP index ---
                arp_by_intf: Dict[str, list] = {}
                arp_by_mac: Dict[str, str] = {}
                for a in dev.get("arp_entries", []):
                    intf = a.get("intf")
                    mac = a.get("mac")
                    if intf:
                        arp_by_intf.setdefault(intf.lower(), []).append(a)
                    if mac:
                        arp_by_mac[mac.lower()] = a.get("ip")

                # --- LLDP summary edges ---
                for ent in dev.get("lldp_summary", []):
                    remote_label = ent.get("dev_id")
                    local_if = ent.get("local_if")
                    remote_port = ent.get("remote_port")
                    if (
                        remote_label
                        and remote_label
                        not in nodes[hostname]["connectedNode"]
                    ):
                        nodes[hostname]["connectedNode"].append(remote_label)
                    if (
                        local_if
                        and local_if not in nodes[hostname]["edgeLink"]
                    ):
                        nodes[hostname]["edgeLink"][local_if] = {
                            "remote_node": remote_label,
                            "remote_interface": remote_port,
                            "remote_ip": None,
                        }

                # --- LLDP detail enrichment ---
                lldp_details = dev.get("lldp_detail", [])
                for detail in lldp_details:
                    local_if_short = detail.get("local_if")
                    chassis = detail.get("chassis")
                    mgmt = detail.get("remote_mgmt")
                    if not local_if_short:
                        continue
                    full_if = normalize_interface_name(local_if_short)

                    if local_if_short not in nodes[hostname]["edgeLink"]:
                        nodes[hostname]["edgeLink"][local_if_short] = {
                            "remote_node": detail.get("system_name")
                            or chassis
                            or "unknown",
                            "remote_interface": detail.get("remote_port"),
                            "remote_ip": None,
                        }

                    # Resolve neighbor IP
                    found_ip = mgmt
                    if not found_ip and chassis:
                        found_ip = arp_by_mac.get(chassis.lower())
                    if not found_ip:
                        entries = arp_by_intf.get(full_if.lower(), [])
                        for a in entries:
                            age = a.get("age")
                            if age is None or str(age).strip() == "-":
                                continue
                            found_ip = a.get("ip")
                            if found_ip:
                                break

                    if found_ip:
                        nodes[hostname]["edgeLink"][local_if_short][
                            "remote_ip"
                        ] = found_ip

                        # Placeholder node creation
                        remote_node_label = nodes[hostname]["edgeLink"][
                            local_if_short
                        ].get("remote_node")
                        is_ip_label = False
                        if remote_node_label and re.match(
                            r"^\d+\.\d+\.\d+\.\d+$", str(remote_node_label)
                        ):
                            is_ip_label = True

                        if remote_node_label and not is_ip_label:
                            remote_label = remote_node_label
                            if remote_label not in nodes:
                                nodes[remote_label] = {
                                    "interfaceList": [{}],
                                    "connectedNode": [hostname],
                                    "edgeLink": {},
                                    "mgmt_ip": found_ip,
                                }
                            else:
                                if hostname not in nodes[remote_label].get(
                                    "connectedNode", []
                                ):
                                    nodes[remote_label][
                                        "connectedNode"
                                    ].append(hostname)
                        else:
                            remote_label = None

                        # Enqueue neighbor for BFS
                        if (
                            found_ip != ip
                            and found_ip not in visited_ips
                            and found_ip not in queued_ips
                        ):
                            neighbor_chain = list(hop_chain) + [ip]
                            queued_ips.add(found_ip)
                            q.append((found_ip, neighbor_chain))
                            ip_hop_chain[found_ip] = neighbor_chain
                            logger.debug(
                                "Enqueued neighbor %s → %s via chain %s",
                                remote_label,
                                found_ip,
                                neighbor_chain,
                            )

                completed_nodes.add(hostname)

    # ------------------------------------------------------------------
    # Post-crawl cleanup (same as mini_nce.py)
    # ------------------------------------------------------------------
    logger.info(
        "Post-crawl cleanup: %d nodes, %d completed",
        len(nodes),
        len(completed_nodes),
    )

    reachable_nodes = set(completed_nodes)

    # Remove unreachable nodes
    nodes_to_remove = [
        nid for nid in list(nodes.keys()) if nid not in reachable_nodes
    ]
    for nid in nodes_to_remove:
        try:
            del nodes[nid]
            logger.info("Removed unreachable node: %s", nid)
        except KeyError:
            pass

    # Clean stale references
    for _node_id, node_data in nodes.items():
        if "connectedNode" in node_data and isinstance(
            node_data["connectedNode"], list
        ):
            node_data["connectedNode"] = [
                n for n in node_data["connectedNode"] if n in nodes
            ]

        if "edgeLink" in node_data and isinstance(
            node_data["edgeLink"], dict
        ):
            links_to_remove = [
                li
                for li, info in node_data["edgeLink"].items()
                if isinstance(info, dict) and info.get("remote_node") not in nodes
            ]
            for li in links_to_remove:
                del node_data["edgeLink"][li]

    # Remove IP-keyed placeholder nodes
    ip_node_keys = [
        nid
        for nid in list(nodes.keys())
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", nid)
    ]
    for ipk in ip_node_keys:
        for _n_id, n_data in nodes.items():
            if "connectedNode" in n_data and isinstance(
                n_data["connectedNode"], list
            ):
                n_data["connectedNode"] = [
                    x for x in n_data["connectedNode"] if x != ipk
                ]
            if "edgeLink" in n_data and isinstance(n_data["edgeLink"], dict):
                rm = [
                    li
                    for li, info in n_data["edgeLink"].items()
                    if isinstance(info, dict) and info.get("remote_node") == ipk
                ]
                for li in rm:
                    del n_data["edgeLink"][li]
        try:
            del nodes[ipk]
            logger.info("Removed IP-keyed placeholder node: %s", ipk)
        except KeyError:
            pass

    logger.info(
        "Post-crawl cleanup complete: %d nodes remaining, %d removed",
        len(nodes),
        len(nodes_to_remove),
    )

    ts = datetime.now(tz=TZ_UTC8).isoformat(timespec="seconds")
    return {"timestamp": ts, "seeds": seeds, "nodes": nodes}
