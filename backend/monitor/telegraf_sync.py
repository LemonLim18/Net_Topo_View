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
from monitor.telegraf_gen import TelegrafConfigGenerator, classify_vendor

logger = logging.getLogger("monitor.telegraf_sync")


def _normalize_local_vendor(vendor_raw: str) -> Optional[str]:
    v = str(vendor_raw or "").strip().lower()
    if not v:
        return None
    if "cisco" in v:
        return "cisco"
    if "huawei" in v:
        return "huawei"
    if "juniper" in v or "junos" in v:
        return "juniper"
    return None


def _group_vendor_agents(devices: List[Dict[str, str]]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {"cisco": [], "huawei": [], "juniper": []}
    seen: Set[str] = set()
    for dev in devices:
        ip = str(dev.get("management_ip") or dev.get("ip") or "").strip()
        if not ip or ip in seen:
            continue
        seen.add(ip)
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        # Trust local inventory vendor first; fallback to classifier only if absent/unknown.
        vendor = _normalize_local_vendor(str(dev.get("vendor") or ""))
        if vendor is None:
            vendor = classify_vendor(ip, str(dev.get("sys_descr") or ""))
        if vendor in groups:
            groups[vendor].append(ip)
    for k in groups:
        groups[k] = sorted(groups[k])
    return groups


def _format_agents_list(agents: List[str]) -> str:
    return "[" + ", ".join(f'"{ip}"' for ip in agents) + "]"


def _patch_named_snmp_block_agents(config_text: str, block_name: str, agents: List[str]) -> tuple[str, bool]:
    # Normalize CRLF so multiline regex and line anchors remain deterministic.
    normalized = (config_text or "").replace("\r\n", "\n")

    block_start_re = re.compile(r"(?m)^\s*\[\[inputs\.snmp\]\]\s*$")
    starts = [m.start() for m in block_start_re.finditer(normalized)]
    if not starts:
        return normalized, False

    updated = False
    out_parts: List[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(normalized)
        block = normalized[start:end]

        if re.search(rf'(?m)^\s*name\s*=\s*"{re.escape(block_name)}"\s*$', block):
            agents_line = f"  agents = {_format_agents_list(agents)}"
            if re.search(r"(?m)^\s*agents\s*=\s*\[[^\]]*\]\s*$", block):
                block = re.sub(r"(?m)^\s*agents\s*=\s*\[[^\]]*\]\s*$", agents_line, block, count=1)
            else:
                # Prefer inserting after the `name =` line in the matched block.
                name_line_re = re.compile(rf'(?m)^(\s*name\s*=\s*"{re.escape(block_name)}"\s*)$')
                if name_line_re.search(block):
                    block = name_line_re.sub(rf"\1\n{agents_line}", block, count=1)
                else:
                    block = re.sub(r"(?m)^\s*\[\[inputs\.snmp\]\]\s*$", f"[[inputs.snmp]]\n{agents_line}", block, count=1)
            updated = True

        out_parts.append(block)

    # Preserve any leading preamble before first block.
    preamble = normalized[: starts[0]]
    return preamble + "".join(out_parts), updated


def patch_telegraf_main_agents_only(config_text: str, vendor_agents: Dict[str, List[str]]) -> tuple[str, Dict[str, bool]]:
    updated = {"snmp_cisco": False, "snmp_huawei": False, "snmp_juniper": False}
    out = config_text
    out, updated["snmp_cisco"] = _patch_named_snmp_block_agents(out, "snmp_cisco", vendor_agents.get("cisco", []))
    out, updated["snmp_huawei"] = _patch_named_snmp_block_agents(out, "snmp_huawei", vendor_agents.get("huawei", []))
    out, updated["snmp_juniper"] = _patch_named_snmp_block_agents(out, "snmp_juniper", vendor_agents.get("juniper", []))
    return out, updated


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
                "vendor": str(dev.vendor or "").strip(),
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
    vendor_agents = _group_vendor_agents(inventory_devices)
    desired_targets = sorted(set(vendor_agents.get("cisco", []) + vendor_agents.get("huawei", []) + vendor_agents.get("juniper", [])))
    generator = TelegrafConfigGenerator()
    rendered = generator.generate_device_include(inventory_devices)
    TelegrafConfigGenerator().write(rendered, local_path)
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
        result["message"] = "Local managed Telegraf vendor-agent snapshot regenerated. Remote deploy is disabled."
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
    remote_include_path = os.getenv(
        "TELEGRAF_REMOTE_MANAGED_INCLUDE_PATH",
        "/etc/telegraf/telegraf.d/20-nettopoview-devices-snmp.conf",
    ).strip()
    remote_mode = os.getenv("TELEGRAF_REMOTE_MODE", "include").strip().lower()
    if remote_mode in {"main", "main_config", "agents_only"}:
        remote_mode = "main_agents_only"
    if remote_mode not in {"include", "main_agents_only"}:
        remote_mode = "include"
    remote_config_dir = os.getenv("TELEGRAF_REMOTE_CONFIG_DIR", "/etc/telegraf/telegraf.d").strip()
    test_cmd = os.getenv(
        "TELEGRAF_REMOTE_TEST_CMD",
        f"telegraf --config {shlex.quote(remote_config_path)} --config-directory {shlex.quote(remote_config_dir)} --test",
    ).strip()
    restart_cmd = os.getenv("TELEGRAF_REMOTE_RESTART_CMD", "systemctl reload telegraf").strip()
    status_cmd = os.getenv("TELEGRAF_REMOTE_STATUS_CMD", "systemctl is-active telegraf").strip()
    use_sudo = _env_bool("TELEGRAF_REMOTE_USE_SUDO", True)
    validate_on_sync = _env_bool("TELEGRAF_REMOTE_VALIDATE_ON_SYNC", False)
    restart_on_sync = _env_bool("TELEGRAF_REMOTE_RESTART_ON_SYNC", True)

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
        target_path = remote_config_path if remote_mode == "main_agents_only" else remote_include_path
        remote_existing_text = _read_remote_file(
            client,
            target_path,
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

        backup_path = f"{target_path}.bak-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        backup_cmd = f"if [ -f {shlex.quote(target_path)} ]; then cp {shlex.quote(target_path)} {shlex.quote(backup_path)}; fi"
        backup_res = _run_ssh_command(
            client,
            backup_cmd,
            use_sudo=use_sudo,
            sudo_password=remote_pass,
        )
        if not backup_res.get("ok"):
            result["backup_error"] = backup_res.get("stderr") or backup_res.get("stdout")

        if remote_mode == "main_agents_only":
            patched_main, updated_blocks = patch_telegraf_main_agents_only(remote_existing_text or "", vendor_agents)
            result["patched_blocks"] = updated_blocks
            result["mode"] = "main_agents_only"
            if not any(updated_blocks.values()):
                result["message"] = (
                    "No vendor SNMP blocks were matched in telegraf.conf. "
                    "Expected block names: snmp_cisco, snmp_huawei, snmp_juniper. "
                    "Set TELEGRAF_REMOTE_MODE=include to manage only the telegraf.d drop-in."
                )
                result["deployed"] = False
                return result
            remote_content = patched_main
        else:
            result["mode"] = "include"
            remote_content = rendered
            if posixpath.normpath(remote_include_path) == posixpath.normpath(remote_config_path):
                result["message"] = (
                    "Remote deploy refused: TELEGRAF_REMOTE_MANAGED_INCLUDE_PATH must not equal "
                    "TELEGRAF_REMOTE_CONFIG_PATH in include mode."
                )
                result["deployed"] = False
                return result
            if posixpath.basename(remote_include_path) == "telegraf.conf":
                result["message"] = (
                    "Remote deploy refused: managed include path points at a main telegraf.conf file. "
                    "Use /etc/telegraf/telegraf.d/20-nettopoview-devices-snmp.conf."
                )
                result["deployed"] = False
                return result
            if "[agent]" in remote_content or "[[outputs." in remote_content:
                result["message"] = (
                    "Remote deploy refused: generated include unexpectedly contains agent/output sections."
                )
                result["deployed"] = False
                return result

        write_res = _write_remote_file(
            client,
            target_path,
            remote_content,
            allow_sudo=use_sudo,
            sudo_password=remote_pass,
        )
        if not write_res.get("ok"):
            raise RuntimeError(write_res.get("stderr") or write_res.get("message") or "Remote config write failed")

        if remote_mode == "include":
            result["remote_main_config_untouched"] = True
        else:
            include_note = (
                "# NetTopoView managed mode: agents-only patching in /etc/telegraf/telegraf.conf\n"
                "# This file is intentionally not used for SNMP blocks.\n"
                "# Vendor agents are updated in named blocks: snmp_cisco, snmp_huawei, snmp_juniper.\n"
            )
            include_write_res = _write_remote_file(
                client,
                remote_include_path,
                include_note,
                allow_sudo=use_sudo,
                sudo_password=remote_pass,
            )
            result["include_note_written"] = bool(include_write_res.get("ok"))
            if not include_write_res.get("ok"):
                result["include_note_error"] = include_write_res.get("stderr") or include_write_res.get("message")
        result["deployed"] = True

        if validate_on_sync:
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
                        f"{shlex.quote(target_path)}; fi"
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
        else:
            result["validated"] = True
            result["validation_skipped"] = True

        if restart_on_sync:
            restart_res = _run_ssh_command(
                client,
                restart_cmd,
                use_sudo=use_sudo,
                sudo_password=remote_pass,
                timeout=30,
            )
            result["restart_exit_code"] = restart_res.get("exit_code")
            if not restart_res.get("ok"):
                raise RuntimeError(restart_res.get("stderr") or restart_res.get("stdout") or "Failed to restart telegraf")
            result["restarted"] = True
        else:
            result["restarted"] = True
            result["restart_skipped"] = True

        if restart_on_sync:
            status_res = _run_ssh_command(
                client,
                status_cmd,
                use_sudo=use_sudo,
                sudo_password=remote_pass,
                timeout=10,
            )
            result["service_status"] = (status_res.get("stdout") or "").strip()
            if not status_res.get("ok"):
                result["service_status_error"] = (status_res.get("stderr") or "").strip()

        result["remote_main_config_path"] = remote_config_path
        result["remote_managed_include_path"] = remote_include_path
        result["message"] = (
            f"Telegraf remote include sync completed on {remote_host}."
            if remote_mode == "include"
            else f"Telegraf remote main-config agents patch completed on {remote_host}."
        )
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
