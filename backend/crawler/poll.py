"""
Poll a single network device using M1 drivers.

This replaces ``mini_nce.py:poll_device()`` with a version that uses
the Driver Factory (M1) instead of raw Paramiko SSH calls.

The return format is *deliberately* the same dict shape that the
legacy ``poll_device`` returns so the BFS engine builds an identical
topology JSON.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import paramiko

from drivers.factory import DriverFactory
from drivers.base import BaseDriver
from drivers.models import Neighbor, Interface, ARPEntry

logger = logging.getLogger("crawler.poll")

# Used by the BFS engine to detect loopback interfaces for mgmt_ip
RE_LOOPBACK = re.compile(r"^Loopback", re.IGNORECASE)

# SSH driver fallback order for auto-detection
_SSH_DRIVERS = ["cisco_ios", "huawei_vrp", "juniper_junos"]


# -----------------------------------------------------------------------
# Vendor auto-detection from SSH prompt
# -----------------------------------------------------------------------

# Legacy SSH algorithms for older devices (mirrors SSHDriver constants)
_LEGACY_CIPHERS = [
    "aes128-cbc", "aes256-cbc", "3des-cbc",
    "aes128-ctr", "aes256-ctr",
]
_LEGACY_KEXS = [
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group-exchange-sha1",
    "diffie-hellman-group1-sha1",
]
_LEGACY_HOST_KEYS = ["ssh-rsa", "ssh-dss"]


def _ssh_connect_with_fallback(
    client: paramiko.SSHClient,
    host_ip: str,
    port: int,
    username: str,
    password: Optional[str],
    timeout: int = 8,
) -> None:
    """Connect with automatic legacy algorithm fallback (like SSHDriver).

    Modern Paramiko (>= 2.9) disables ``ssh-rsa`` host keys by default in
    favour of ``rsa-sha2-256`` / ``rsa-sha2-512``.  Many Huawei VRP and older
    Cisco IOS devices **only** offer ``ssh-rsa``, ``diffie-hellman-group14-sha1``
    KEX, and AES-CBC ciphers — so the negotiation fails with an ``EOFError``.

    Strategy:
        1. Try with modern defaults.
        2. On failure, retry with ``disabled_algorithms`` that **forces**
           Paramiko to fall back to plain ``ssh-rsa`` host-key verification.
    """
    connect_kwargs = {
        "hostname": host_ip,
        "port": port,
        "username": username,
        "password": password,
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    try:
        client.connect(**connect_kwargs)
    except (paramiko.SSHException, EOFError, Exception) as first_err:
        logger.debug("Modern SSH failed for %s (%s), trying legacy...", host_ip, first_err)
        # Close and recreate so transport state is clean
        try:
            client.close()
        except Exception:
            pass
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Retry: disable SHA-2 RSA variants so Paramiko offers plain ssh-rsa
        legacy_kwargs = dict(connect_kwargs)
        legacy_kwargs["disabled_algorithms"] = {
            "pubkeys": ["rsa-sha2-256", "rsa-sha2-512"],
        }
        try:
            client.connect(**legacy_kwargs)
            logger.info("Legacy SSH fallback succeeded for %s", host_ip)
        except Exception:
            raise first_err  # raise the original if both fail


def detect_vendor_driver(
    host_ip: str,
    username: str,
    password: Optional[str],
    port: int = 22,
) -> Optional[str]:
    """
    Detect the device vendor by reading the initial SSH shell prompt.

    Opens a short-lived SSH session, reads the first prompt output, and
    matches against known vendor patterns:
        - Huawei VRP:  ``<hostname>`` angle-bracket prompt
        - Juniper Junos: ``user@hostname>`` format
        - Cisco IOS/XE: ``hostname#`` or ``hostname>`` format

    Returns one of: ``"cisco_ios"``, ``"huawei_vrp"``, ``"juniper_junos"``,
    or ``None`` if detection fails.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        _ssh_connect_with_fallback(client, host_ip, port, username, password)
        chan = client.invoke_shell()
        time.sleep(1.5)  # wait for banner + prompt

        output = ""
        while chan.recv_ready():
            output += chan.recv(4096).decode(errors="ignore")

        # Handle Huawei VRP initial password-change prompt
        if "Change now" in output:
            chan.send("N\n")
            time.sleep(1.0)
            while chan.recv_ready():
                output += chan.recv(4096).decode(errors="ignore")

        chan.close()
        client.close()

        # --- Pattern matching ---
        # Huawei VRP prompt: <Hostname> or Info: ... HUAWEI
        if re.search(r"<\S+>", output) or re.search(r"HUAWEI|VRP", output, re.IGNORECASE):
            logger.info("Auto-detected Huawei VRP on %s", host_ip)
            return "huawei_vrp"

        # Juniper Junos prompt: user@hostname> or {master:0}
        if re.search(r"\w+@\w+[>%]", output) or re.search(r"JUNOS|{master:", output, re.IGNORECASE):
            logger.info("Auto-detected Juniper Junos on %s", host_ip)
            return "juniper_junos"

        # Cisco IOS/XE prompt: Hostname# or Hostname>
        if re.search(r"\S+[#>]\s*$", output, re.MULTILINE):
            logger.info("Auto-detected Cisco IOS on %s", host_ip)
            return "cisco_ios"

        # Fallback: default to cisco_ios (most common)
        logger.warning("Could not detect vendor for %s, defaulting to cisco_ios. Prompt: %r",
                        host_ip, output[:200])
        return "cisco_ios"

    except Exception as e:
        logger.warning("Vendor detection failed for %s: %s", host_ip, e)
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


# -----------------------------------------------------------------------
# PollResult — typed wrapper around the legacy dict
# -----------------------------------------------------------------------

@dataclass
class PollResult:
    """Structured result from polling a single device."""
    hostname: Optional[str] = None
    down: bool = False
    interfaces: Dict[str, str] = field(default_factory=dict)
    lldp_summary: List[Dict] = field(default_factory=list)
    lldp_detail: List[Dict] = field(default_factory=list)
    arp_entries: List[Dict] = field(default_factory=list)
    # Enriched device info (from show version / get_device_info)
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    serial: Optional[str] = None
    uptime: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to the legacy dict format used by BFS engine."""
        d: Dict = {
            "hostname": self.hostname,
            "interfaces": self.interfaces,
            "lldp_summary": self.lldp_summary,
            "lldp_detail": self.lldp_detail,
            "arp_entries": self.arp_entries,
        }
        if self.down:
            d["down"] = True
        # Enriched fields
        if self.vendor:
            d["vendor"] = self.vendor
        if self.model:
            d["model"] = self.model
        if self.os_version:
            d["os_version"] = self.os_version
        if self.serial:
            d["serial"] = self.serial
        if self.uptime:
            d["uptime"] = self.uptime
        return d


def poll_device(
    host_ip: str,
    username: str,
    password: Optional[str],
    key_file: Optional[str] = None,
    driver_type: str = "cisco_ios",
    hop_chain: Optional[List[str]] = None,
    jump_host: Optional[Dict] = None,
    cred_overrides: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Dict:
    """
    Poll a single device using the M1 driver layer.

    When ``driver_type`` is ``"auto"``, the function first opens a
    short-lived SSH session to read the device prompt and auto-detect
    the vendor (Cisco IOS / Huawei VRP / Juniper Junos), then polls
    with the correct driver.

    Args:
        cred_overrides: Optional dict mapping device IPs to
            ``(username, password)`` tuples. When the target IP has an
            entry here, those credentials are used instead of the
            default ``username``/``password``.

    Returns the **same dict shape** as ``mini_nce.py:poll_device()`` so
    the BFS engine can consume it identically::

        {
            "hostname": "R1",
            "interfaces": {"Ethernet0/0": "192.168.1.1", ...},
            "lldp_summary": [{"dev_id": "R2", "local_if": "Et0/0", ...}],
            "lldp_detail": [{"local_if": "Et0/0", "chassis": "...", ...}],
            "arp_entries": [{"ip": "...", "age": "...", "mac": "...", "intf": "..."}],
        }

    On failure, returns a dict with ``"down": True``.
    """
    # --- Build credential attempts: per-device override first, then global default ---
    effective_user = username
    effective_pass = password
    credential_attempts: List[Tuple[str, str, Optional[str]]] = []

    if cred_overrides and host_ip in cred_overrides:
        effective_user, effective_pass = cred_overrides[host_ip]
        logger.info("Using credential override for %s (user=%s)", host_ip, effective_user)
        credential_attempts.append(("override", effective_user, effective_pass))
    else:
        credential_attempts.append(("default", effective_user, effective_pass))

    if (effective_user, effective_pass) != (username, password):
        credential_attempts.append(("default", username, password))

    last_err: Optional[Exception] = None
    total_attempts = len(credential_attempts)

    for attempt_index, (attempt_source, attempt_user, attempt_pass) in enumerate(credential_attempts, start=1):
        driver: Optional[BaseDriver] = None
        try:
            # --- Auto-detect vendor when driver_type is "auto" ---
            actual_driver = driver_type
            if driver_type == "auto":
                detected = detect_vendor_driver(host_ip, attempt_user, attempt_pass)
                if detected is None:
                    raise RuntimeError("vendor detection failed")
                actual_driver = detected
                logger.info("Auto-detected driver for %s: %s", host_ip, actual_driver)

            driver = DriverFactory.create(
                actual_driver,
                host=host_ip,
                username=attempt_user,
                password=attempt_pass,
                key_file=key_file,
                hop_chain=hop_chain,
                jump_host=jump_host,
            )
            driver.connect()

            # --- Collect data via M1 driver ---
            hostname = driver.get_hostname()

            # --- Collect enriched device info (model, version, serial, uptime) ---
            vendor_str: Optional[str] = None
            model_str: Optional[str] = None
            os_version_str: Optional[str] = None
            serial_str: Optional[str] = None
            uptime_str: Optional[str] = None
            try:
                dev_info = driver.get_device_info()
                if dev_info:
                    vendor_str = dev_info.vendor.value if hasattr(dev_info.vendor, "value") else str(dev_info.vendor)
                    model_str = dev_info.model or None
                    os_version_str = dev_info.os_version or None
                    serial_str = dev_info.serial or None
                    uptime_str = dev_info.uptime or None
                    if dev_info.hostname and not hostname:
                        hostname = dev_info.hostname
            except Exception as e:
                logger.debug("get_device_info() failed for %s: %s", host_ip, e)

            # Convert M1 Interface objects -> legacy {name: ip} dict
            raw_interfaces: List[Interface] = driver.get_interfaces()
            ifaces: Dict[str, str] = {}
            for iface in raw_interfaces:
                if iface.ip_address:
                    ifaces[iface.name] = iface.ip_address

            # Convert M1 Neighbor objects -> legacy lldp_summary + lldp_detail
            raw_neighbors: List[Neighbor] = driver.get_neighbors()
            lldp_summary: List[Dict] = []
            lldp_detail: List[Dict] = []
            for n in raw_neighbors:
                lldp_summary.append({
                    "dev_id": n.remote_device,
                    "local_if": n.local_interface,
                    "remote_port": n.remote_interface,
                })
                lldp_detail.append({
                    "local_if": n.local_interface,
                    "chassis": None,
                    "remote_port": n.remote_interface,
                    "system_name": n.remote_device,
                    "remote_mgmt": n.remote_ip if n.remote_ip else None,
                })

            # Convert M1 ARPEntry objects -> legacy arp dicts
            raw_arp: List[ARPEntry] = driver.get_arp_table()
            arp_entries: List[Dict] = []
            for a in raw_arp:
                arp_entries.append({
                    "ip": a.ip,
                    "age": a.age,
                    "mac": a.mac,
                    "intf": a.interface,
                })

            result = PollResult(
                hostname=hostname or host_ip,
                interfaces=ifaces,
                lldp_summary=lldp_summary,
                lldp_detail=lldp_detail,
                arp_entries=arp_entries,
                vendor=vendor_str,
                model=model_str,
                os_version=os_version_str,
                serial=serial_str,
                uptime=uptime_str,
            )
            logger.info(
                "Polled %s (%s) — %d ifaces, %d neighbors, %d ARP, vendor=%s model=%s",
                hostname,
                host_ip,
                len(ifaces),
                len(lldp_summary),
                len(arp_entries),
                vendor_str or "?",
                model_str or "?",
            )
            return result.to_dict()

        except Exception as e:
            last_err = e
            if attempt_index < total_attempts:
                logger.warning(
                    "poll_device %s attempt %d/%d failed (source=%s user=%s): %s; trying fallback credential",
                    host_ip,
                    attempt_index,
                    total_attempts,
                    attempt_source,
                    attempt_user,
                    e,
                )
            else:
                logger.warning(
                    "poll_device %s failed after %d credential attempt(s): %s",
                    host_ip,
                    total_attempts,
                    e,
                )
        finally:
            if driver is not None:
                try:
                    driver.disconnect()
                except Exception:
                    pass

    if driver_type == "auto":
        logger.warning("Cannot reach %s for vendor detection — marking down", host_ip)
    elif last_err is not None:
        logger.warning("poll_device %s failed: %s", host_ip, last_err)
    return PollResult(hostname=host_ip, down=True).to_dict()
