from __future__ import annotations

import ipaddress
import logging
import os
import posixpath
import re
import shlex
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

try:
    import paramiko  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency in some dev envs
    paramiko = None

from db.manager import DatabaseManager
from monitor.telegraf_gen import TelegrafConfigGenerator

logger = logging.getLogger("monitor.telegraf_sync")


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _normalize_host(raw: str) -> Optional[str]:
    value = str(raw or "").strip().strip('"').strip("'")
    if not value:
        return None

    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0].strip()
    if not value:
        return None

    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif ":" in value:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            value = host

    value = value.strip()
    if not value:
        return None
    return value


def extract_snmp_targets(config_text: str) -> List[str]:
    targets: Set[str] = set()
    pattern = re.compile(r"(?m)^\s*agents\s*=\s*\[([^\]]*)\]")
    for match in pattern.finditer(config_text or ""):
        blob = match.group(1)
        for token in re.findall(r'"([^"]+)"', blob):
            host = _normalize_host(token)
            if not host:
                continue
            try:
                ipaddress.ip_address(host)
                targets.add(host)
            except ValueError:
                # Keep hostnames too, if used in future.
                targets.add(host)
    return sorted(targets)


def _inventory_devices_for_telegraf(db: DatabaseManager) -> List[Dict[str, str]]:
    devices = db.get_all_devices()
    seen_ips: Set[str] = set()
    payload: List[Dict[str, str]] = []

    for dev in devices:
        if (dev.source or "").strip().lower() == "crawler":
            continue
        ip = (dev.management_ip or "").strip()
        if not ip:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            logger.debug("Skipping invalid management IP in inventory: %s", ip)
            continue
        if ip in seen_ips:
            continue
        seen_ips.add(ip)

        sys_descr_parts = [str(dev.vendor or "").strip(), str(dev.model or "").strip(), str(dev.os_version or "").strip()]
        sys_descr = " ".join(part for part in sys_descr_parts if part).strip()
        payload.append(
            {
                "management_ip": ip,
                "sys_descr": sys_descr,
            }
        )

    payload.sort(key=lambda row: row["management_ip"])
    return payload


def _run_ssh_command(
    client: paramiko.SSHClient,
    command: str,
    *,
    timeout: int = 60,
    use_sudo: bool = False,
    sudo_password: str = "",
) -> Dict[str, Any]:
    def _exec(cmd: str, *, with_pty: bool = False, feed_password: Optional[str] = None) -> Dict[str, Any]:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=with_pty)
        if feed_password is not None:
            try:
                stdin.write(feed_password + "\n")
                stdin.flush()
            except Exception:
                pass
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        return {
            "command": cmd,
            "exit_code": int(exit_code),
            "stdout": out,
            "stderr": err,
            "ok": int(exit_code) == 0,
        }

    if not use_sudo:
        return _exec(command)

    wrapped = f"sudo -n bash -lc {shlex.quote(command)}"
    first = _exec(wrapped, with_pty=True)
    if first.get("ok"):
        first["sudo_used"] = True
        return first

    combined = f"{first.get('stdout', '')} {first.get('stderr', '')}".lower()
    needs_password = "password" in combined or "authentication" in combined
    if needs_password and sudo_password:
        wrapped_pw = f"sudo -S -p '' bash -lc {shlex.quote(command)}"
        second = _exec(wrapped_pw, with_pty=True, feed_password=sudo_password)
        second["sudo_used"] = True
        return second
    return first


def _read_remote_file(
    client: paramiko.SSHClient,
    remote_path: str,
    *,
    allow_sudo: bool,
    sudo_password: str,
) -> Optional[str]:
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_path, "r") as handle:
            raw = handle.read()
            return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception:
        if not allow_sudo:
            return None
        read_res = _run_ssh_command(
            client,
            f"cat {shlex.quote(remote_path)}",
            use_sudo=True,
            sudo_password=sudo_password,
        )
        if read_res.get("ok"):
            return str(read_res.get("stdout", ""))
        return None
    finally:
        sftp.close()


def _write_remote_file(
    client: paramiko.SSHClient,
    remote_path: str,
    content: str,
    *,
    allow_sudo: bool,
    sudo_password: str,
) -> Dict[str, Any]:
    remote_dir = posixpath.dirname(remote_path) or "/"
    tmp_path = f"/tmp/nettopoview_telegraf_{int(datetime.utcnow().timestamp())}.conf"

    sftp = client.open_sftp()
    try:
        with sftp.file(tmp_path, "w") as handle:
            handle.write(content)
        try:
            sftp.chmod(tmp_path, 0o644)
        except Exception:
            pass
    finally:
        sftp.close()

    if allow_sudo:
        mkdir_res = _run_ssh_command(
            client,
            f"mkdir -p {shlex.quote(remote_dir)}",
            use_sudo=True,
            sudo_password=sudo_password,
        )
        if not mkdir_res.get("ok"):
            return {
                "ok": False,
                "exit_code": mkdir_res.get("exit_code", 1),
                "stdout": mkdir_res.get("stdout", ""),
                "stderr": mkdir_res.get("stderr", ""),
                "message": "Failed to create remote directory",
            }

        install_res = _run_ssh_command(
            client,
            f"install -m 0644 {shlex.quote(tmp_path)} {shlex.quote(remote_path)} && rm -f {shlex.quote(tmp_path)}",
            use_sudo=True,
            sudo_password=sudo_password,
        )
        if install_res.get("ok"):
            return {"ok": True, "message": "installed_with_sudo"}
        return {
            "ok": False,
            "exit_code": install_res.get("exit_code", 1),
            "stdout": install_res.get("stdout", ""),
            "stderr": install_res.get("stderr", ""),
            "message": "Failed to install remote config with sudo",
        }

    sftp_plain = client.open_sftp()
    try:
        with sftp_plain.file(remote_path, "w") as handle:
            handle.write(content)
        try:
            sftp_plain.chmod(remote_path, 0o644)
        except Exception:
            pass
        rm_res = _run_ssh_command(client, f"rm -f {shlex.quote(tmp_path)}", timeout=10)
        if not rm_res.get("ok"):
            logger.debug("Best-effort tmp cleanup failed: %s", rm_res.get("stderr", ""))
        return {"ok": True, "message": "installed_direct"}
    except Exception as exc:
        return {"ok": False, "exit_code": 1, "stderr": str(exc), "message": "Failed to write remote config"}
    finally:
        sftp_plain.close()


def sync_telegraf_config(
    db: DatabaseManager,
    *,
    local_path: str = "telegraf.conf",
    deploy_remote: Optional[bool] = None,
) -> Dict[str, Any]:
    deploy_default = _env_bool("TELEGRAF_REMOTE_AUTO_DEPLOY", False)
    deploy = deploy_default if deploy_remote is None else bool(deploy_remote)

    inventory_devices = _inventory_devices_for_telegraf(db)
    generator = TelegrafConfigGenerator()
    rendered = generator.generate(inventory_devices)
    generator.write(rendered, local_path)

    desired_targets = extract_snmp_targets(rendered)
    result: Dict[str, Any] = {
        "local_config_path": local_path,
        "inventory_device_count": len(inventory_devices),
        "desired_targets": desired_targets,
        "desired_target_count": len(desired_targets),
        "remote_sync_enabled": deploy,
        "added_targets": [],
        "removed_targets": [],
        "deployed": False,
        "validated": False,
        "restarted": False,
    }

    if not deploy:
        result["message"] = "Local telegraf.conf regenerated. Remote deploy is disabled."
        return result
    if paramiko is None:
        result["message"] = "Remote deploy skipped: paramiko is not installed in backend runtime."
        return result

    remote_host = os.getenv("TELEGRAF_REMOTE_HOST", "192.168.1.250").strip() or "192.168.1.250"
    remote_port = int(os.getenv("TELEGRAF_REMOTE_PORT", "22"))
    remote_user = os.getenv("TELEGRAF_REMOTE_USER", "").strip()
    remote_pass = os.getenv("TELEGRAF_REMOTE_PASSWORD", "")
    remote_key = os.getenv("TELEGRAF_REMOTE_KEY_PATH", "").strip()
    remote_config_path = os.getenv("TELEGRAF_REMOTE_CONFIG_PATH", "/etc/telegraf/telegraf.conf").strip()
    test_cmd = os.getenv(
        "TELEGRAF_REMOTE_TEST_CMD",
        f"telegraf --config {shlex.quote(remote_config_path)} --test",
    ).strip()
    restart_cmd = os.getenv("TELEGRAF_REMOTE_RESTART_CMD", "systemctl restart telegraf").strip()
    status_cmd = os.getenv("TELEGRAF_REMOTE_STATUS_CMD", "systemctl is-active telegraf").strip()
    use_sudo = _env_bool("TELEGRAF_REMOTE_USE_SUDO", True)

    if not remote_user:
        result["message"] = "Remote deploy skipped: TELEGRAF_REMOTE_USER is empty."
        return result
    if not remote_pass and not remote_key:
        result["message"] = "Remote deploy skipped: set TELEGRAF_REMOTE_PASSWORD or TELEGRAF_REMOTE_KEY_PATH."
        return result

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    backup_path: Optional[str] = None
    remote_existing_text: Optional[str] = None
    remote_existing_targets: List[str] = []
    try:
        client.connect(
            hostname=remote_host,
            port=remote_port,
            username=remote_user,
            password=remote_pass or None,
            key_filename=remote_key or None,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        remote_existing_text = _read_remote_file(
            client,
            remote_config_path,
            allow_sudo=use_sudo,
            sudo_password=remote_pass,
        )
        remote_existing_targets = extract_snmp_targets(remote_existing_text or "")
        desired_set = set(desired_targets)
        current_set = set(remote_existing_targets)
        result["existing_targets"] = remote_existing_targets
        result["existing_target_count"] = len(remote_existing_targets)
        result["added_targets"] = sorted(desired_set - current_set)
        result["removed_targets"] = sorted(current_set - desired_set)

        backup_path = f"{remote_config_path}.bak-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        backup_cmd = f"if [ -f {shlex.quote(remote_config_path)} ]; then cp {shlex.quote(remote_config_path)} {shlex.quote(backup_path)}; fi"
        backup_res = _run_ssh_command(
            client,
            backup_cmd,
            use_sudo=use_sudo,
            sudo_password=remote_pass,
        )
        if not backup_res.get("ok"):
            result["backup_error"] = backup_res.get("stderr") or backup_res.get("stdout")

        write_res = _write_remote_file(
            client,
            remote_config_path,
            rendered,
            allow_sudo=use_sudo,
            sudo_password=remote_pass,
        )
        if not write_res.get("ok"):
            raise RuntimeError(write_res.get("stderr") or write_res.get("message") or "Remote config write failed")
        result["deployed"] = True

        validate_res = _run_ssh_command(
            client,
            test_cmd,
            use_sudo=use_sudo,
            sudo_password=remote_pass,
            timeout=120,
        )
        result["validation_exit_code"] = validate_res.get("exit_code")
        if not validate_res.get("ok"):
            result["validation_error"] = (validate_res.get("stderr") or validate_res.get("stdout") or "").strip()
            if backup_path:
                rollback_cmd = (
                    f"if [ -f {shlex.quote(backup_path)} ]; then cp {shlex.quote(backup_path)} "
                    f"{shlex.quote(remote_config_path)}; fi"
                )
                rollback_res = _run_ssh_command(
                    client,
                    rollback_cmd,
                    use_sudo=use_sudo,
                    sudo_password=remote_pass,
                )
                result["rollback_applied"] = bool(rollback_res.get("ok"))
            raise RuntimeError("Remote telegraf config validation failed")
        result["validated"] = True

        restart_res = _run_ssh_command(
            client,
            restart_cmd,
            use_sudo=use_sudo,
            sudo_password=remote_pass,
            timeout=90,
        )
        result["restart_exit_code"] = restart_res.get("exit_code")
        if not restart_res.get("ok"):
            raise RuntimeError(restart_res.get("stderr") or restart_res.get("stdout") or "Failed to restart telegraf")
        result["restarted"] = True

        status_res = _run_ssh_command(
            client,
            status_cmd,
            use_sudo=use_sudo,
            sudo_password=remote_pass,
            timeout=30,
        )
        result["service_status"] = (status_res.get("stdout") or "").strip()
        if not status_res.get("ok"):
            result["service_status_error"] = (status_res.get("stderr") or "").strip()

        result["message"] = f"Telegraf remote sync completed on {remote_host}."
        return result
    except Exception as exc:
        result["error"] = str(exc)
        if not result.get("message"):
            result["message"] = f"Telegraf remote sync failed on {remote_host}: {exc}"
        return result
    finally:
        try:
            client.close()
        except Exception:
            pass
