#!/usr/bin/env python3
"""
topology_mini_nce_v7.py

- Robust LLDP crawler (SSH):
  * collects hostname, show ip interface brief, LLDP summary/detail, ARP
  * BFS crawl across reachable neighbors (resolve via LLDP detail IP or ARP)
  * merges placeholder nodes into actual hostname nodes when discovered
  * timestamp is human-readable ISO 8601 string (UTC+8)
  * --verbose flag for raw outputs and debug logs
"""
import re
import json
import time
import argparse
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from getpass import getpass
from typing import Dict, List, Optional
import paramiko
from rich import print
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("topo-backend")

# -----------------------
# Commands & regexes
# -----------------------
IP_BRIEF_CMD = "show ip interface brief\n"
HOSTNAME_CMD = "show running-config | include ^hostname\n"
LLDP_SUM_CMD = "terminal length 0\nshow lldp neighbor\n"
LLDP_DETAIL_CMD = "terminal length 0\nshow lldp neighbors detail\n"
ARP_CMD = "show ip arp\n"

RE_IF_BRIEF_LINE = re.compile(r"^(?P<intf>\S+)\s+(?P<ip>\d+\.\d+\.\d+\.\d+|\S+)\s+")
RE_LOOPBACK = re.compile(r"^Loopback", re.IGNORECASE)
RE_HOSTNAME = re.compile(r"^\s*hostname\s+(?P<h>\S+)", re.IGNORECASE | re.MULTILINE)

RE_LLDP_SUM_LINE = re.compile(
    r"^(?P<dev>\S+)\s+(?P<local_if>\S+)\s+\d+\s+\S+\s+(?P<port_id>\S+)", re.IGNORECASE
)

RE_MANAGEMENT_ADDR = re.compile(r"IP:\s*(?P<ip>\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
RE_LOCAL_INTF = re.compile(r"Local Intf:\s*(?P<intf>\S+)", re.IGNORECASE)
RE_CHASSIS = re.compile(r"Chassis id:\s*(?P<mac>[\da-fA-F\.\:\-]+)", re.IGNORECASE)
RE_PORT_ID = re.compile(r"Port id:\s*(?P<port>\S+)", re.IGNORECASE)
RE_SYSTEM_NAME = re.compile(r"System Name:\s*(?P<name>\S+)", re.IGNORECASE)

RE_ARP_LINE = re.compile(r"^\s*Internet\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+([\da-fA-F\.]+)\s+\S+\s+(\S+)", re.IGNORECASE)

SSH_CONNECT_TIMEOUT = 5
SSH_COMMAND_TIMEOUT = 10

VERBOSE = False

# -----------------------
# Helpers
# -----------------------
def verbose_print(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)

def _read_until(chan, timeout: float = 3.0) -> str:
    """Read all available data from a paramiko channel with a deadline."""
    out = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chan.recv_ready():
            out += chan.recv(65536)
        else:
            time.sleep(0.1)
    # Final drain
    while chan.recv_ready():
        out += chan.recv(65536)
    return out.decode(errors="ignore")


def _do_cli_ssh_hop(chan, target_host: str, user: str, password: Optional[str]) -> bool:
    """
    From an already-established interactive shell on a Cisco device,
    initiate an SSH connection to target_host using the Cisco CLI.
    Returns True if the hop was successful, False otherwise.
    Uses a polling loop to wait for the password prompt, which handles
    slower SSH negotiation in deeply nested sessions.
    """
    ssh_cmd = f"ssh -l {user} {target_host}\n"
    chan.send(ssh_cmd)
    logger.info(f"Hop: sent '{ssh_cmd.strip()}'")

    # Poll for password prompt / host-key confirmation / error with increasing patience
    accumulated = ""
    deadline = time.time() + 15.0  # max 15 seconds for SSH negotiation
    while time.time() < deadline:
        time.sleep(0.5)
        chunk = ""
        while chan.recv_ready():
            chunk += chan.recv(65536).decode(errors="ignore")
        if chunk:
            accumulated += chunk
            lower_acc = accumulated.lower()

            # Check for error conditions first
            if "%" in accumulated and ("refused" in lower_acc or "error" in lower_acc or "invalid" in lower_acc):
                logger.warning(f"Hop: SSH connection error for {target_host}: {accumulated.strip()}")
                return False

            # Handle host key confirmation
            if "yes/no" in lower_acc or "fingerprint" in lower_acc:
                chan.send("yes\n")
                time.sleep(1.0)
                accumulated = ""  # reset after handling
                continue

            # Password prompt found
            if "password" in lower_acc:
                break

    if "password" not in accumulated.lower():
        logger.warning(f"Hop: no password prompt for {target_host} after wait (got: {repr(accumulated[:200])})")
        return False

    verbose_print(f"[debug]Hop pre-auth output: {repr(accumulated[:300])}")

    # Send password
    if not password:
        logger.warning(f"Hop: password required for {target_host} but none provided")
        return False

    chan.send(password + "\n")

    # Wait for post-auth response and check for success
    time.sleep(1.0)
    post_auth = _read_until(chan, timeout=2.0)
    verbose_print(f"[debug]Hop post-auth output: {repr(post_auth[:300])}")

    # Check for authentication failure
    if "% Login invalid" in post_auth or "% Authentication failed" in post_auth or "denied" in post_auth.lower():
        logger.warning(f"Hop: auth failed for {target_host}")
        return False

    return True


def ssh_invoke_via_chain(hop_chain: List[str], target_host: str, user: str,
                         password: Optional[str], key_file: Optional[str],
                         commands: List[str], timeout: int = SSH_COMMAND_TIMEOUT) -> Optional[str]:
    """
    SSH to the first host in hop_chain (must be directly reachable), then chain through
    each subsequent hop using Cisco CLI 'ssh -l user host' commands, and finally
    SSH to target_host and run the given commands.

    hop_chain: List of IPs to traverse, e.g. ['192.168.1.123'] for single hop,
               or ['192.168.1.123', '192.168.2.2'] for double hop.
               The first IP must be directly reachable from the controller.
    target_host: The final target device IP to poll.
    """
    if not hop_chain:
        return None

    first_hop = hop_chain[0]
    remaining_hops = hop_chain[1:]  # intermediate hops before the target

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        # Connect to the first hop directly (must be reachable)
        client.connect(
            hostname=first_hop, username=user, password=password, key_filename=key_file,
            timeout=SSH_CONNECT_TIMEOUT, banner_timeout=SSH_CONNECT_TIMEOUT,
            allow_agent=False, look_for_keys=False
        )
        chan = client.invoke_shell()
        chan.settimeout(timeout)

        # Wait for the first hop's prompt
        time.sleep(0.5)
        _read_until(chan, timeout=1.0)

        # Chain through remaining intermediate hops
        for hop_ip in remaining_hops:
            if not _do_cli_ssh_hop(chan, hop_ip, user, password):
                logger.warning(f"Chain: failed at intermediate hop {hop_ip}")
                return None
            logger.info(f"Chain: successfully hopped to {hop_ip}")

        # Final hop to the target
        if not _do_cli_ssh_hop(chan, target_host, user, password):
            logger.warning(f"Chain: failed at final hop to {target_host}")
            return None
        logger.info(f"Chain: successfully reached target {target_host}")

        # Now we are on the target device — send commands
        for cmd in commands:
            chan.send(cmd if cmd.endswith("\n") else cmd + "\n")
            time.sleep(0.05)

        # Wait for all command output to arrive
        time.sleep(1.5)
        text = _read_until(chan, timeout=4.0)

        if VERBOSE:
            sep = "=" * 70
            chain_str = " -> ".join(hop_chain + [target_host])
            print(f"[magenta]{sep}\nRAW output from {target_host} via chain {chain_str}\n{sep}\n{text}\n{sep}[/magenta]")

        logger.info(f"Chain: collected output from {target_host} ({len(text)} bytes) via {' -> '.join(hop_chain)}")
        return text

    except (paramiko.ssh_exception.SSHException, TimeoutError, OSError) as e:
        logger.warning(f"Chain: {target_host} via {hop_chain} unreachable: {e}")
        return None
    except Exception as e:
        logger.warning(f"Chain: {target_host} via {hop_chain} failed: {e}")
        return None
    finally:
        try:
            client.close()
        except:
            pass


def ssh_invoke(host: str, user: str, password: Optional[str], key_file: Optional[str],
               commands: List[str], jump: Optional[dict]=None, timeout:int=SSH_COMMAND_TIMEOUT) -> Optional[str]:
    """
    SSH connection with automatic fallback to legacy algorithms for older devices.
    Tries modern algorithms first, then falls back to legacy-compatible ones.
    """
    legacy_algorithms = {
        "kex": [],      # Don't disable any KEX algorithms (allows diffie-hellman-group1-sha1, etc.)
        "ciphers": [],  # Don't disable any ciphers (allows 3des-cbc, aes128-cbc, etc.)
        "macs": [],     # Don't disable any MACs (allows hmac-md5, hmac-sha1, etc.)
        "keys": []      # Don't disable any key types (allows ssh-rsa, ssh-dss, etc.)
    }

    connection_attempts = [
        (False, None),          # modern algorithms
        (True, legacy_algorithms)  # legacy fallback
    ]

    last_error = None

    for use_legacy, algo_config in connection_attempts:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        sock = None
        transport = None
        try:
            if use_legacy and algo_config:
                # create Transport manually for legacy algorithms
                transport = paramiko.Transport((host, 22), disabled_algorithms=algo_config)
                transport.start_client(timeout=SSH_CONNECT_TIMEOUT)

                if password:
                    transport.auth_password(user, password)
                elif key_file:
                    pkey = None
                    try:
                        pkey = paramiko.RSAKey.from_private_key_file(key_file)
                    except:
                        try:
                            pkey = paramiko.DSSKey.from_private_key_file(key_file)
                        except:
                            raise Exception("Failed to load key file")
                    if pkey:
                        transport.auth_publickey(user, pkey)
                else:
                    raise Exception("No password or key file provided")

                client._transport = transport
            else:
                # modern connection
                client.connect(
                    hostname=host, username=user, password=password, key_filename=key_file,
                    timeout=SSH_CONNECT_TIMEOUT, banner_timeout=SSH_CONNECT_TIMEOUT,
                    allow_agent=False, look_for_keys=False
                )

            # invoke shell & run commands
            chan = client.invoke_shell()
            chan.settimeout(min(timeout, 5))
            for cmd in commands:
                chan.send(cmd if cmd.endswith("\n") else cmd + "\n")
                time.sleep(0.05)
            # Wait for all command output to arrive (especially LLDP detail)
            time.sleep(1.0)
            text = _read_until(chan, timeout=3.0)

            if VERBOSE:
                sep = "="*70
                algo_used = "legacy" if use_legacy else "modern"
                print(f"[magenta]{sep}\nRAW output from {host} (algorithms: {algo_used}) for commands: {commands}\n{sep}\n{text}\n{sep}[/magenta]")

            return text
        except (paramiko.ssh_exception.SSHException, TimeoutError, OSError) as e:
            last_error = e
            logger.warning(f"Host {host} unreachable: {e}")
            # Immediately return a "down" placeholder
            return {"hostname": host, "down": True, "interfaces": {}, "lldp_summary": [], "lldp_detail": [], "arp_entries": []}
        except Exception as e:
            last_error = e
            verbose_print(f"[debug]Connection attempt with {'legacy' if use_legacy else 'modern'} algorithms failed: {e}")
        finally:
            try: transport and transport.close()
            except: pass
            try: client.close()
            except: pass

    print(f"[yellow]ssh_invoke {host} failed after all attempts: {last_error}[/yellow]")
    return None

def clean_command_echoes(raw: str) -> str:
    if not raw: return raw
    lines = []
    for line in raw.splitlines():
        s = line.rstrip()
        if not s: continue
        ls = s.lstrip()
        if ls.startswith("show ") or ls.startswith("terminal length"): continue
        if re.fullmatch(r"[A-Za-z0-9\-\._]+[>#]", ls): continue
        lines.append(s)
    return "\n".join(lines)

def extract_hostname_from_output(raw: str) -> Optional[str]:
    if not raw: return None
    cleaned = clean_command_echoes(raw)
    m = RE_HOSTNAME.search(cleaned)
    if m: return m.group("h").strip()
    m2 = re.search(r"\bhostname\s+([A-Za-z0-9\-\._]+)\b", cleaned, re.IGNORECASE)
    if m2: return m2.group(1).strip()
    m3 = re.search(r"^([A-Za-z0-9\-\._]+)[>#]", raw, re.MULTILINE)
    if m3: return m3.group(1).strip()
    return None

# -----------------------
# Parsers
# -----------------------
def parse_ip_interface_brief(raw: str) -> Dict[str,str]:
    out = {}
    if not raw: return out
    for line in raw.splitlines():
        line = line.strip()
        # Skip lines that are not part of the interface brief table.
        # Some command outputs (like 'show ip arp') include lines that start with
        # the word 'Internet' followed by an IP address; those should not be
        # interpreted as interface names.
        if not line or line.lower().startswith("interface") or line.lower().startswith("address") or line.lower().startswith("internet"):
            continue
        m = RE_IF_BRIEF_LINE.match(line)
        if not m: continue
        intf = m.group("intf"); ip = m.group("ip")
        if ip.lower() != "unassigned" and re.match(r"\d+\.\d+\.\d+\.\d+", ip):
            out[intf] = ip
    return out

def parse_lldp_summary(raw: str) -> List[Dict]:
    res = []
    if not raw: return res
    for line in raw.splitlines():
        line = line.rstrip()
        m = RE_LLDP_SUM_LINE.match(line)
        if m:
            dev = m.group("dev")
            local_if = m.group("local_if")
            # Skip lines that are actually ARP or other tables where the first
            # column is the literal word 'Internet' or the local_if looks like
            # an IPv4 address. Those were being mis-parsed as LLDP summary.
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", dev) or re.match(r"^\d+\.\d+\.\d+\.\d+$", local_if):
                continue
            if str(dev).lower() == "internet":
                continue
            res.append({
                "dev_id": dev,
                "local_if": local_if,
                "remote_port": m.group("port_id")
            })
    return res

def parse_lldp_detail_for_mappings(raw: str) -> List[Dict]:
    neighbors = []
    if not raw:
        return neighbors
    blocks = re.split(r"\n[-]{3,}\n|\r?\n\r?\n+", raw)
    for blk in blocks:
        b = blk.strip()
        if not b:
            continue
        local_if=None; chassis=None; port=None; sysname=None; mgmt=None
        m = RE_LOCAL_INTF.search(b)
        if m: local_if=m.group("intf").strip()
        m = RE_CHASSIS.search(b)
        if m: chassis = m.group("mac").strip().replace('.',':').lower()
        m = RE_PORT_ID.search(b)
        if m: port = m.group("port").strip()
        m = RE_SYSTEM_NAME.search(b)
        if m: sysname = m.group("name").strip()
        m = RE_MANAGEMENT_ADDR.search(b)
        if m: mgmt = m.group("ip").strip()
        neighbors.append({
            "local_if": local_if,
            "chassis": chassis,
            "remote_port": port,
            "system_name": sysname,
            "remote_mgmt": mgmt
        })
    return neighbors

def parse_arp(raw: str) -> List[Dict]:
    """Return list of dicts: ip, age, mac, intf"""
    entries = []
    if not raw:
        return entries
    for line in raw.splitlines():
        m = RE_ARP_LINE.match(line)
        if m:
            entries.append({
                "ip": m.group(1),
                "age": m.group(2),
                "mac": m.group(3).replace('.',':').lower(),
                "intf": m.group(4)
            })
    return entries

def normalize_interface_name(short_if: str) -> str:
    if not short_if:
        return short_if
    if short_if.startswith("Et"): return "Ethernet" + short_if[2:]
    if short_if.startswith("Gi"): return "GigabitEthernet" + short_if[2:]
    if short_if.startswith("Fa"): return "FastEthernet" + short_if[2:]
    if short_if.startswith("Lo"): return "Loopback" + short_if[2:]
    return short_if

# -----------------------
# Replace/Merge placeholder node keys
# -----------------------
def replace_node_key(nodes: Dict, old_key: str, new_key: str):
    """Rename node key old_key -> new_key and update all edgeLink & connectedNode references."""
    if old_key == new_key or old_key not in nodes:
        return
    dest = nodes.get(new_key, {"interfaceList":[{}], "connectedNode":[], "edgeLink":{}, "mgmt_ip": None})
    src = nodes[old_key]
    # merge interfaceList (list with single dict)
    try:
        dest_if = dest.get("interfaceList", [{}])[0]
        src_if = src.get("interfaceList", [{}])[0]
        merged_if = dict(dest_if)
        merged_if.update({k:v for k,v in src_if.items() if k not in merged_if})
        dest["interfaceList"] = [merged_if]
    except Exception:
        pass
    # merge connectedNode
    for n in src.get("connectedNode", []):
        if n not in dest["connectedNode"]:
            dest["connectedNode"].append(n)
    # merge edgeLink
    for k,v in src.get("edgeLink", {}).items():
        if k not in dest["edgeLink"]:
            dest["edgeLink"][k] = v
    # prefer dest mgmt_ip else src
    if not dest.get("mgmt_ip"):
        dest["mgmt_ip"] = src.get("mgmt_ip")
    nodes[new_key] = dest
    del nodes[old_key]
    # update all references
    for nk, node in nodes.items():
        node["connectedNode"] = [new_key if x == old_key else x for x in node.get("connectedNode", [])]
        for inf, ed in list(node.get("edgeLink", {}).items()):
            if ed.get("remote_node") == old_key:
                node["edgeLink"][inf]["remote_node"] = new_key

# -----------------------
# Poll single device
# -----------------------
def poll_device(host_ip: str, user: str, password: Optional[str], key_file: Optional[str],
               jump: Optional[dict] = None, hop_chain: Optional[List[str]] = None) -> Dict:
    ret = {"hostname": None, "interfaces": {}, "lldp_summary": [], "lldp_detail": [], "arp_entries": []}
    # Combine commands into a single SSH session to reduce connection/setup overhead
    commands = [HOSTNAME_CMD, IP_BRIEF_CMD, LLDP_SUM_CMD, LLDP_DETAIL_CMD, ARP_CMD]

    out = None
    # Strategy 1: Try direct connection first (works for seed devices on the same subnet)
    try:
        out = ssh_invoke(host_ip, user, password, key_file, commands, jump)
    except Exception:
        pass

    # If direct connection returned a "down" placeholder or None, try via hop chain
    if (out is None or isinstance(out, dict)) and hop_chain:
        logger.info(f"Direct SSH to {host_ip} failed; retrying via chain {' -> '.join(hop_chain)}")
        try:
            out = ssh_invoke_via_chain(hop_chain, host_ip, user, password, key_file, commands)
        except Exception:
            pass

    if out is None or isinstance(out, dict):
        logger.warning(f"{host_ip} unreachable (direct and via chain)")
        return {"hostname": host_ip, "down": True, "interfaces": {}, "lldp_summary": [], "lldp_detail": [], "arp_entries": []}

    hostname = extract_hostname_from_output(out) if out else host_ip
    ret["hostname"] = hostname
    verbose_print(f"[debug]Resolved hostname for {host_ip} => {hostname}")

    # Parsers operate on the full combined output; each extracts its relevant parts
    if out:
        ret["interfaces"] = parse_ip_interface_brief(out)
        ret["lldp_summary"] = parse_lldp_summary(out)
        ret["lldp_detail"] = parse_lldp_detail_for_mappings(out)
        ret["arp_entries"] = parse_arp(out)

    return ret

# -----------------------
# Crawl topology BFS
# -----------------------
def crawl_topology(seeds: List[str], user: str, password: Optional[str], key_file: Optional[str],
                   jump: Optional[dict], workers:int=10) -> Dict:
    q = deque([(ip, []) for ip in seeds])  # (target_ip, hop_chain_to_reach_it)
    queued_ips = set(seeds)
    visited_ips = set()
    completed_nodes = set()
    nodes: Dict[str, Dict] = {}
    ip_to_key: Dict[str, str] = {}
    ip_hop_chain: Dict[str, List[str]] = {ip: [] for ip in seeds}  # track hop chain for each IP

    with ThreadPoolExecutor(max_workers=workers) as ex:
        while q:
            batch = []
            while q and len(batch) < workers:
                batch.append(q.popleft())
            future_map = {ex.submit(poll_device, ip, user, password, key_file, jump, hop_chain=chain): (ip, chain) for ip, chain in batch}

            for fut in as_completed(future_map):
                ip, hop_chain = future_map[fut]
                if ip in visited_ips:
                    verbose_print(f"[debug]Skipping already-visited {ip}")
                    continue
                try:
                    dev = fut.result()
                except Exception as e:
                    verbose_print(f"[yellow]Failed to poll {ip}: {e}[/yellow]")
                    visited_ips.add(ip)
                    continue
                visited_ips.add(ip)
                verbose_print(f"[debug]Completed poll {ip}; hop_chain={hop_chain}")

                hostname = dev.get("hostname") or ip
                prev_key = ip_to_key.get(ip)
                if prev_key and prev_key != hostname:
                    verbose_print(f"[debug]Renaming placeholder {prev_key} -> actual hostname {hostname}")
                    replace_node_key(nodes, prev_key, hostname)
                    for k, v in list(ip_to_key.items()):
                        if v == prev_key:
                            ip_to_key[k] = hostname
                ip_to_key[ip] = hostname

                if hostname not in nodes:
                    nodes[hostname] = {"interfaceList":[{}], "connectedNode":[], "edgeLink":{}, "mgmt_ip": None}

                iface_dict = {ifname: ipaddr for ifname, ipaddr in dev.get("interfaces", {}).items()}
                nodes[hostname]["interfaceList"] = [iface_dict]

                loopbacks = {i:p for i,p in dev.get("interfaces", {}).items() if RE_LOOPBACK.match(i)}
                if loopbacks:
                    nodes[hostname]["mgmt_ip"] = next(iter(loopbacks.values()))
                else:
                    nodes[hostname]["mgmt_ip"] = ip

                arp_by_intf = {}
                arp_by_mac = {}
                for a in dev.get("arp_entries", []):
                    intf = a.get("intf")
                    mac = a.get("mac")
                    if intf:
                        arp_by_intf.setdefault(intf.lower(), []).append(a)
                    if mac:
                        arp_by_mac[mac.lower()] = a.get("ip")

                for ent in dev.get("lldp_summary", []):
                    remote_label = ent.get("dev_id")
                    local_if = ent.get("local_if")
                    remote_port = ent.get("remote_port")
                    if remote_label and remote_label not in nodes[hostname]["connectedNode"]:
                        nodes[hostname]["connectedNode"].append(remote_label)
                    if local_if and local_if not in nodes[hostname]["edgeLink"]:
                        nodes[hostname]["edgeLink"][local_if] = {"remote_node": remote_label, "remote_interface": remote_port, "remote_ip": None}

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
                            "remote_node": detail.get("system_name") or chassis or "unknown",
                            "remote_interface": detail.get("remote_port"),
                            "remote_ip": None
                        }

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
                            if found_ip: break

                    if found_ip:
                        nodes[hostname]["edgeLink"][local_if_short]["remote_ip"] = found_ip
                        # Prefer using a textual remote_node label (system name or chassis) when available.
                        # If the remote_node is just an IP address, do NOT create a node keyed by that IP.
                        remote_node_label = nodes[hostname]["edgeLink"][local_if_short].get("remote_node")
                        is_ip_label = False
                        if remote_node_label and re.match(r"^\d+\.\d+\.\d+\.\d+$", str(remote_node_label)):
                            is_ip_label = True

                        # Only create a placeholder node when we have a non-IP label (hostname/chassis).
                        if remote_node_label and not is_ip_label:
                            remote_label = remote_node_label
                            if remote_label not in nodes:
                                nodes[remote_label] = {"interfaceList":[{}], "connectedNode":[hostname], "edgeLink":{}, "mgmt_ip": found_ip}
                            else:
                                if hostname not in nodes[remote_label].get("connectedNode", []):
                                    nodes[remote_label]["connectedNode"].append(hostname)
                        else:
                            # remote node label is absent or an IP-only label: do not create a node keyed by that IP.
                            # We still keep the remote_ip field set on the edgeLink and will queue the IP for polling
                            # so it can be discovered as a proper hostname later.
                            remote_label = None

                        if found_ip != ip and found_ip not in visited_ips and found_ip not in queued_ips:
                            # Build hop chain for the neighbor: current device's chain + current device IP
                            neighbor_chain = list(hop_chain) + [ip]
                            queued_ips.add(found_ip)
                            q.append((found_ip, neighbor_chain))
                            ip_hop_chain[found_ip] = neighbor_chain
                            verbose_print(f"[debug]Enqueued neighbor {remote_label} -> {found_ip} via chain {neighbor_chain}")

                completed_nodes.add(hostname)
                verbose_print(f"[debug]Marked completed: {hostname}")

    # Post-crawl cleanup: Remove nodes that are completely unreachable
    # and clean up stale references to unreachable nodes
    logger.info(f"[CRAWL] Post-crawl cleanup: Starting with {len(nodes)} nodes, {len(completed_nodes)} completed")
    
    # Get all discovered node names (nodes that were actually polled successfully)
    discovered_node_names = set(completed_nodes)
    
    # Note: only nodes that were actually polled successfully are considered "discovered".
    # To ensure unreachable nodes (even if referenced by other nodes via LLDP) are
    # removed entirely, we do NOT preserve referenced-only nodes. Reachable nodes
    # are therefore only those that were directly polled.
    reachable_nodes = discovered_node_names
    
    # Find nodes to remove: nodes that are not reachable from any discovered node
    nodes_to_remove = []
    for node_id, node_data in list(nodes.items()):
        if node_id not in reachable_nodes:
            nodes_to_remove.append(node_id)
            logger.info(f"[CRAWL] Marking unreachable node for removal: {node_id} (not reachable from any route)")

    # Remove unreachable nodes immediately
    for node_id in nodes_to_remove:
        try:
            del nodes[node_id]
            logger.info(f"[CRAWL] Removed unreachable node: {node_id}")
        except KeyError:
            pass
    
    # Clean up stale references in remaining nodes
    for node_id, node_data in nodes.items():
            # We intentionally do NOT remove references to nodes that we marked as 'down'.
            # This preserves visible links to nodes that are currently unreachable.
            # However, if there are truly unknown remote_node references (not in nodes at all),
            # we can still prune those.
            if "connectedNode" in node_data and isinstance(node_data["connectedNode"], list):
                original_connected = node_data["connectedNode"].copy()
                node_data["connectedNode"] = [n for n in node_data["connectedNode"] if n in nodes]
                removed = set(original_connected) - set(node_data["connectedNode"])
                if removed:
                    logger.info(f"[CRAWL] Removed stale connectedNode references from {node_id}: {removed}")

            # Remove edgeLinks that point to completely unknown nodes (not present in nodes dict)
            if "edgeLink" in node_data and isinstance(node_data["edgeLink"], dict):
                edge_links = node_data["edgeLink"]
                links_to_remove = []
                for local_if, link_info in edge_links.items():
                    if isinstance(link_info, dict):
                        remote_node = link_info.get("remote_node")
                        if remote_node and remote_node not in nodes:
                            links_to_remove.append(local_if)
                            logger.info(f"[CRAWL] Removed stale edgeLink from {node_id}: {local_if} -> {remote_node}")

                for local_if in links_to_remove:
                    del edge_links[local_if]
    
    logger.info(f"[CRAWL] Post-crawl cleanup complete: {len(nodes)} nodes remaining, {len(nodes_to_remove)} nodes removed")

    tz_utc8 = timezone(timedelta(hours=8))
    ts = datetime.now(tz=tz_utc8).isoformat(timespec="seconds")
    # Final pass: remove nodes whose keys are pure IPv4 addresses — we don't treat interface IPs as node IDs
    ip_node_keys = [nid for nid in list(nodes.keys()) if re.match(r"^\d+\.\d+\.\d+\.\d+$", nid)]
    if ip_node_keys:
        for ipk in ip_node_keys:
            # remove references to this IP-node from other nodes
            for n_id, n_data in nodes.items():
                if "connectedNode" in n_data and isinstance(n_data["connectedNode"], list):
                    if ipk in n_data["connectedNode"]:
                        n_data["connectedNode"] = [x for x in n_data["connectedNode"] if x != ipk]
                if "edgeLink" in n_data and isinstance(n_data["edgeLink"], dict):
                    links_to_remove = [li for li, info in n_data["edgeLink"].items() if isinstance(info, dict) and info.get("remote_node") == ipk]
                    for li in links_to_remove:
                        del n_data["edgeLink"][li]
            # finally delete the IP-keyed node
            try:
                del nodes[ipk]
                logger.info(f"[CRAWL] Removed IP-keyed placeholder node: {ipk}")
            except KeyError:
                pass

    topo = {"timestamp": ts, "seeds": seeds, "nodes": nodes}
    return topo

# -----------------------
# CLI & main
# -----------------------
def main():
    global VERBOSE
    p = argparse.ArgumentParser(description="topology_mini_nce_v7 - LLDP crawler (final patched)")
    p.add_argument("--seeds", "-s", required=True, help="Comma-separated seed mgmt IPs")
    p.add_argument("--user", "-u", required=True)
    p.add_argument("--password", "-p", help="Password (prompt if omitted)")
    p.add_argument("--key", "-k", help="SSH private key file")
    p.add_argument("--jump-host", help="Optional jump host IP")
    p.add_argument("--jump-user", help="Jump host user")
    p.add_argument("--out", "-o", default="topology_mini_nce_v7.json")
    p.add_argument("--verbose", action="store_true", help="Verbose debug output")
    args = p.parse_args()

    VERBOSE = args.verbose
    seeds = [x.strip() for x in args.seeds.split(",") if x.strip()]
    if not seeds:
        print("[red]No seeds provided[/red]")
        return
    pw = args.password
    if not pw and not args.key:
        pw = getpass(f"Password for {args.user}@{seeds[0]}: ")

    jump = {"host": args.jump_host, "user": args.jump_user or args.user} if args.jump_host else None
    topo = crawl_topology(seeds, args.user, pw, args.key, jump, workers=6)

    with open(args.out, "w") as fh:
        json.dump(topo, fh, indent=2)
    print(f"[green]Wrote {args.out}[/green]")

if __name__ == "__main__":
    main()