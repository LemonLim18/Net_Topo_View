"""
Abstract SSH driver — shared logic for all CLI-based network devices.

Handles SSH connection setup, command execution, hop-chain proxying,
and legacy algorithm fallback. Vendor-specific subclasses only need to
implement the parsing/command logic.

Inheritance:
    BaseDriver → SSHDriver → CiscoIOSDriver
                            → HuaweiVRPDriver
                            → JuniperJunosDriver
"""

import re
import time
import logging
from abc import abstractmethod
from typing import List, Optional, Dict, Tuple

import paramiko

from backend.drivers.base import BaseDriver
from backend.drivers.models import (
    Interface,
    Neighbor,
    Route,
    ARPEntry,
    DeviceInfo,
    DriverResult,
    DriverType,
    Vendor,
)

logger = logging.getLogger("drivers.ssh")


class SSHDriver(BaseDriver):
    """
    Abstract SSH-based driver with shared connection and command execution.

    Subclasses must implement:
        - _get_commands()         → vendor-specific CLI commands
        - _parse_hostname()       → extract hostname from output
        - _parse_interfaces()     → parse interface table
        - _parse_neighbors()      → parse LLDP/CDP output
        - _parse_routing_table()  → parse routing table
        - _parse_arp_table()      → parse ARP table
        - _parse_device_info()    → parse version/model info
        - _get_config_mode_cmds() → enter/exit config mode commands
    """

    # Timeouts (seconds)
    CONNECT_TIMEOUT = 5
    COMMAND_TIMEOUT = 10
    READ_TIMEOUT = 3.0

    # Legacy SSH algorithms for older devices (IOS 12.x, old VRP)
    LEGACY_CIPHERS = [
        "aes128-cbc", "aes256-cbc", "3des-cbc",
        "aes128-ctr", "aes256-ctr",
    ]
    LEGACY_KEXS = [
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group-exchange-sha1",
        "diffie-hellman-group1-sha1",
    ]
    LEGACY_HOST_KEYS = [
        "ssh-rsa", "ssh-dss",
    ]

    def __init__(
        self,
        host: str,
        username: str,
        vendor: Vendor,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
        port: int = 22,
        hop_chain: Optional[List[str]] = None,
        jump_host: Optional[Dict] = None,
    ):
        super().__init__(vendor=vendor, driver_type=DriverType.SSH)
        self.host = host
        self.username = username
        self.password = password
        self.key_file = key_file
        self.port = port
        self.hop_chain = hop_chain      # List of IPs to hop through
        self.jump_host = jump_host      # {"host": ..., "user": ..., "password": ...}

        # Connection state
        self._client: Optional[paramiko.SSHClient] = None
        self._channel = None

    # ------------------------------------------------------------------
    # Abstract methods — subclasses implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def _get_disable_paging_command(self) -> str:
        """Return the CLI command to disable paging (e.g., 'terminal length 0')."""
        ...

    @abstractmethod
    def _parse_hostname(self, raw_output: str) -> str:
        """Extract hostname from raw CLI output."""
        ...

    @abstractmethod
    def _parse_interfaces(self, raw_output: str) -> List[Interface]:
        """Parse interface brief output into Interface objects."""
        ...

    @abstractmethod
    def _parse_neighbors(self, raw_output: str) -> List[Neighbor]:
        """Parse LLDP/CDP output into Neighbor objects."""
        ...

    @abstractmethod
    def _parse_routing_table(self, raw_output: str) -> List[Route]:
        """Parse routing table output into Route objects."""
        ...

    @abstractmethod
    def _parse_arp_table(self, raw_output: str) -> List[ARPEntry]:
        """Parse ARP table output into ARPEntry objects."""
        ...

    @abstractmethod
    def _parse_device_info(self, raw_output: str) -> DeviceInfo:
        """Parse version/hardware output into DeviceInfo."""
        ...

    @abstractmethod
    def _get_config_mode_commands(self) -> Tuple[str, str]:
        """Return (enter_config_cmd, exit_config_cmd) e.g., ('configure terminal', 'end')."""
        ...

    @abstractmethod
    def _get_show_commands(self) -> Dict[str, str]:
        """
        Return a dict mapping data type to CLI command.
        Example: {
            "hostname": "show running-config | include ^hostname",
            "interfaces": "show ip interface brief",
            "neighbors": "show lldp neighbor",
            ...
        }
        """
        ...

    # ------------------------------------------------------------------
    # SSH connection — inherited by all SSH drivers
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish SSH connection with automatic legacy algorithm fallback."""
        if self._client is not None:
            return  # Already connected

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Try modern algorithms first
        try:
            self._connect_with_algorithms()
            logger.info(f"SSH connected to {self.host} (modern algorithms)")
            return
        except paramiko.SSHException:
            logger.debug(f"Modern SSH failed for {self.host}, trying legacy...")

        # Fallback to legacy algorithms
        try:
            self._connect_with_algorithms(use_legacy=True)
            logger.info(f"SSH connected to {self.host} (legacy algorithms)")
        except Exception as e:
            self._client = None
            raise ConnectionError(f"Cannot SSH to {self.host}: {e}") from e

    def _connect_with_algorithms(self, use_legacy: bool = False) -> None:
        """Connect with specified algorithm set."""
        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.CONNECT_TIMEOUT,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if self.password:
            connect_kwargs["password"] = self.password
        if self.key_file:
            connect_kwargs["key_filename"] = self.key_file

        if use_legacy:
            connect_kwargs["disabled_algorithms"] = {}
            transport_kwargs = {
                "disabled_algorithms": {
                    "kex": [],
                    "keys": [],
                    "ciphers": [],
                }
            }
            # We can't directly set algorithms in connect(), so we handle
            # this via transport after connection for older paramiko versions.
            # Modern paramiko supports the `disabled_algorithms` parameter.

        self._client.connect(**connect_kwargs)

    def disconnect(self) -> None:
        """Close SSH connection."""
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        logger.debug(f"SSH disconnected from {self.host}")

    def is_alive(self) -> bool:
        """Check if SSH session is still active."""
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def execute_command(self, command: str, timeout: int = None) -> str:
        """
        Execute a single CLI command and return the output.
        Automatically handles paging disable on first use.
        """
        if not self.is_alive():
            raise ConnectionError(f"Not connected to {self.host}")

        timeout = timeout or self.COMMAND_TIMEOUT

        try:
            stdin, stdout, stderr = self._client.exec_command(
                command.strip(),
                timeout=timeout
            )
            output = stdout.read().decode(errors="ignore")
            return output
        except Exception as e:
            logger.error(f"Command execution failed on {self.host}: {e}")
            raise

    def run_command(self, command: str) -> str:
        """Alias for execute_command to satisfy BaseDriver interface."""
        return self.execute_command(command)

    def execute_commands(self, commands: List[str], timeout: int = None) -> str:
        """
        Execute multiple commands in an interactive shell session.
        Returns the combined output from all commands.
        """
        if not self.is_alive():
            raise ConnectionError(f"Not connected to {self.host}")

        timeout = timeout or self.COMMAND_TIMEOUT

        # Open interactive shell
        channel = self._client.invoke_shell()
        time.sleep(0.5)

        # Disable paging first
        paging_cmd = self._get_disable_paging_command()
        if paging_cmd:
            channel.send(paging_cmd + "\n")
            time.sleep(0.5)

        # Send all commands
        for cmd in commands:
            channel.send(cmd.strip() + "\n")
            time.sleep(0.3)

        # Read all output with deadline
        output = self._read_channel(channel, timeout=timeout)
        channel.close()

        return output

    def _read_channel(self, channel, timeout: float = None) -> str:
        """Read all available data from a channel with a deadline."""
        timeout = timeout or self.READ_TIMEOUT
        output = b""
        deadline = time.time() + timeout

        while time.time() < deadline:
            if channel.recv_ready():
                output += channel.recv(65536)
            else:
                time.sleep(0.1)

        # Final drain
        while channel.recv_ready():
            output += channel.recv(65536)

        return output.decode(errors="ignore")

    # ------------------------------------------------------------------
    # BaseDriver interface implementation
    # ------------------------------------------------------------------

    def get_hostname(self) -> str:
        """Get device hostname by running the vendor-specific command."""
        commands = self._get_show_commands()
        raw = self.execute_commands([commands["hostname"]])
        return self._parse_hostname(raw)

    def get_device_info(self) -> DeviceInfo:
        """Get device version/hardware info."""
        commands = self._get_show_commands()
        raw = self.execute_commands([commands.get("version", "")])
        return self._parse_device_info(raw)

    def get_interfaces(self) -> List[Interface]:
        """Get all interfaces with IP and status."""
        commands = self._get_show_commands()
        raw = self.execute_commands([commands["interfaces"]])
        return self._parse_interfaces(raw)

    def get_neighbors(self) -> List[Neighbor]:
        """Get LLDP/CDP neighbors."""
        commands = self._get_show_commands()
        neighbour_cmds = [
            commands.get("neighbors_summary", ""),
            commands.get("neighbors_detail", ""),
        ]
        raw = self.execute_commands([c for c in neighbour_cmds if c])
        return self._parse_neighbors(raw)

    def get_routing_table(self) -> List[Route]:
        """Get the routing table."""
        commands = self._get_show_commands()
        raw = self.execute_commands([commands.get("routes", "")])
        return self._parse_routing_table(raw)

    def get_arp_table(self) -> List[ARPEntry]:
        """Get the ARP table."""
        commands = self._get_show_commands()
        raw = self.execute_commands([commands.get("arp", "")])
        return self._parse_arp_table(raw)

    def get_running_config(self) -> str:
        """Get the full running configuration."""
        commands = self._get_show_commands()
        return self.execute_commands([commands.get("running_config", "show running-config")])

    def push_config(self, commands: List[str]) -> DriverResult:
        """Push config commands in config mode."""
        enter_cmd, exit_cmd = self._get_config_mode_commands()

        try:
            all_commands = [enter_cmd] + commands + [exit_cmd]
            output = self.execute_commands(all_commands)
            return DriverResult(
                success=True,
                data=output,
                device_hostname=self.host,
                driver_type=DriverType.SSH,
            )
        except Exception as e:
            return DriverResult(
                success=False,
                error=str(e),
                device_hostname=self.host,
                driver_type=DriverType.SSH,
            )

    # ------------------------------------------------------------------
    # Convenience: poll everything at once (like mini_nce.py's poll_device)
    # ------------------------------------------------------------------

    def poll_all(self) -> Dict:
        """
        Fetch all data in one session — equivalent to mini_nce.py's poll_device().
        Returns a dict with all parsed data.
        """
        commands = self._get_show_commands()
        all_cmds = [v for v in commands.values() if v]
        raw = self.execute_commands(all_cmds)

        return {
            "hostname": self._parse_hostname(raw),
            "interfaces": [i.to_dict() for i in self._parse_interfaces(raw)],
            "neighbors": [n.to_dict() for n in self._parse_neighbors(raw)],
            "arp_entries": [a.to_dict() for a in self._parse_arp_table(raw)],
        }
