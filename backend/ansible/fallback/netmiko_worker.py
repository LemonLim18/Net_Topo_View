"""
SSH fallback worker using Netmiko.

Used when Ansible cannot reach a device directly:
  - Device is behind a hop-chain
  - Vendor has no Ansible support
  - Ansible run failed after retries

Mirrors the same result structure and log format as AnsibleRunResult so
the pipeline and UI treat both paths uniformly.
"""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("ansible.fallback.netmiko")

try:
    from netmiko import (  # type: ignore
        ConnectHandler,
        NetmikoTimeoutException,
        NetmikoAuthenticationException,
    )
    _NETMIKO_OK = True
except ImportError:
    _NETMIKO_OK = False
    logger.warning("netmiko not installed — SSH fallback unavailable. pip install netmiko")

# Map our internal vendor strings → Netmiko device_type
_VENDOR_DEVICE_TYPE: dict[str, str] = {
    "cisco_ios":     "cisco_ios",
    "cisco_nxos":    "cisco_nxos",
    "cisco_xr":      "cisco_xr",
    "huawei_vrp":    "huawei_vrp",
    "juniper_junos": "juniper_junos",
    "aruba":         "aruba_os",
    "default":       "cisco_ios",
}


@dataclass
class FallbackResult:
    success:    bool
    pre_config: str  = ""
    output:     str  = ""
    error:      str  = ""
    changed:    bool = False

    @property
    def summary(self) -> str:
        if self.success:
            return f"SSH fallback OK (changed={self.changed})"
        return f"SSH fallback FAILED: {self.error[:200]}"


def run_ssh_fallback(
    host:              str,
    username:          str,
    password:          str,
    commands:          list[str],
    vendor:            str = "cisco_ios",
    rollback_commands: list[str] | None = None,
    enable_secret:     str = "",
    port:              int = 22,
    job_id:            int | None = None,
    hop_chain:         list[dict] | None = None,
) -> FallbackResult:
    """
    Push configuration lines to a device via Netmiko SSH.

    Automatically captures a pre-change snapshot, pushes commands,
    saves the config, and rolls back on error.

    Args:
        host:               Target device hostname/IP.
        username:           SSH username.
        password:           SSH password.
        commands:           List of config-mode CLI commands to push.
        vendor:             Vendor string (e.g. 'cisco_ios').
        rollback_commands:  Commands to push if an error occurs.
        enable_secret:      Enable/privilege password (if different from password).
        port:               SSH port (default 22).
        job_id:             Job ID used for session log naming.
        hop_chain:          Reserved — future support for SSH hop-chain relay.

    Returns:
        FallbackResult with success flag, pre_config, output, changed.
    """
    if not _NETMIKO_OK:
        return FallbackResult(
            success=False,
            error="netmiko is not installed — cannot use SSH fallback",
        )

    if not commands:
        return FallbackResult(success=True, changed=False, output="(no commands)")

    device_type = _VENDOR_DEVICE_TYPE.get(vendor.lower(), "cisco_ios")
    session_log = f"/tmp/netmiko_fallback_{job_id or 'adhoc'}_{host}.log"

    params: dict = dict(
        device_type=device_type,
        host=host,
        port=port,
        username=username,
        password=password,
        secret=enable_secret or password,
        timeout=30,
        session_log=session_log,
        session_log_append=False,
    )

    # TODO: hop_chain support — establish jump server chain via Paramiko ProxyCommand
    if hop_chain:
        logger.warning(
            "hop_chain specified but not yet supported in Netmiko fallback — "
            "attempting direct connection (may fail)"
        )

    net_connect = None
    try:
        logger.info(
            "SSH fallback connecting: job=%s host=%s vendor=%s",
            job_id, host, vendor,
        )
        net_connect = ConnectHandler(**params)
        net_connect.enable()

        # PRE-SNAPSHOT
        pre_config = net_connect.send_command("show running-config")

        # APPLY
        output = net_connect.send_config_set(
            commands,
            enter_config_mode=True,
            exit_config_mode=True,
        )
        net_connect.save_config()

        logger.info("SSH fallback success: job=%s host=%s", job_id, host)
        return FallbackResult(
            success=True,
            pre_config=pre_config,
            output=output,
            changed=bool(output.strip()),
        )

    except NetmikoAuthenticationException as exc:
        err = f"Authentication failed on {host}: {exc}"
        logger.error(err)
        return FallbackResult(success=False, error=err)

    except NetmikoTimeoutException as exc:
        err = f"Connection timeout to {host}: {exc}"
        logger.error(err)
        return FallbackResult(success=False, error=err)

    except Exception as exc:
        err = f"Unexpected SSH error on {host}: {exc}"
        logger.error(err)
        # Attempt rollback if we still have a session
        if net_connect and rollback_commands:
            _safe_rollback(net_connect, rollback_commands, host, job_id)
        return FallbackResult(success=False, error=err)

    finally:
        if net_connect:
            try:
                net_connect.disconnect()
            except Exception:
                pass


def _safe_rollback(
    conn,
    commands: list[str],
    host:     str,
    job_id:   int | None,
) -> None:
    """Push rollback commands — best-effort, never raises."""
    try:
        logger.warning("SSH rollback: job=%s host=%s", job_id, host)
        conn.send_config_set(commands)
        conn.save_config()
        logger.info("SSH rollback complete: host=%s", host)
    except Exception as exc:
        logger.error("SSH rollback FAILED on %s: %s", host, exc)
