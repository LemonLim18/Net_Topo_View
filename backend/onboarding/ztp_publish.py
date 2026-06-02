from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import shlex
import threading
from typing import Any, Dict, List, Optional

import paramiko  # type: ignore[import-not-found]

from auth.credentials import CredentialVault
from common_services.service import get_effective_profile
from config_engine.renderer import TemplateRenderer
from db.manager import DatabaseManager

from .config_generator import OnboardingConfigGenerator

logger = logging.getLogger("onboarding.ztp_publish")

_DEFAULT_TARGET_SERVER_IP = os.getenv("ZTP_TARGET_SERVER_IP", "192.168.1.240").strip()
_DEFAULT_TARGET_SERVER_PORT = int(os.getenv("ZTP_TARGET_SERVER_PORT", "22"))
_DEFAULT_TFTP_ROOT = os.getenv("ZTP_TFTP_ROOT", "/var/lib/tftpboot").strip()
_DEFAULT_DHCP_CONFIG_PATH = os.getenv("ZTP_DHCP_CONFIG_PATH", "/etc/dhcp/dhcpd.conf").strip()
_DEFAULT_DHCP_INCLUDE_PATH = os.getenv("ZTP_DHCP_INCLUDE_PATH", "/etc/dhcp/ztp-host-map.conf").strip()
_DEFAULT_DHCP_LEASE_FILE = os.getenv("ZTP_DHCP_LEASE_FILE", "/var/lib/dhcp/dhcpd.leases").strip()

_renderer = TemplateRenderer()
_publish_lock = threading.Lock()


class RemoteExecutionError(RuntimeError):
    pass


class RemoteLinuxExecutor:
    def __init__(self, host: str, port: int, username: str, password: str):
        self._host = host
        self._port = int(port)
        self._username = username
        self._password = password
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def run(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        if self._client is None:
            raise RemoteExecutionError("SSH session is not connected")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        return {
            "command": command,
            "exit_code": int(code),
            "stdout": out,
            "stderr": err,
            "ok": int(code) == 0,
        }

    def run_sudo(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        if self._client is None:
            raise RemoteExecutionError("SSH session is not connected")

        wrapped_non_interactive = f"sudo -n bash -lc {shlex.quote(command)}"
        stdin, stdout, stderr = self._client.exec_command(wrapped_non_interactive, timeout=timeout, get_pty=True)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")

        if int(code) == 0:
            return {
                "command": wrapped_non_interactive,
                "exit_code": int(code),
                "stdout": out,
                "stderr": err,
                "ok": True,
            }

        # If sudo policy requires a password, retry with -S and feed SSH credential password.
        combined = f"{out} {err}".lower()
        needs_password = ("password is required" in combined) or ("a password is required" in combined)
        if not needs_password or not self._password:
            return {
                "command": wrapped_non_interactive,
                "exit_code": int(code),
                "stdout": out,
                "stderr": err,
                "ok": False,
            }

        wrapped_password = f"sudo -S -p '' bash -lc {shlex.quote(command)}"
        stdin2, stdout2, stderr2 = self._client.exec_command(wrapped_password, timeout=timeout, get_pty=True)
        try:
            stdin2.write(f"{self._password}\n")
            stdin2.flush()
        except Exception:
            pass

        code2 = stdout2.channel.recv_exit_status()
        out2 = stdout2.read().decode(errors="ignore")
        err2 = stderr2.read().decode(errors="ignore")
        return {
            "command": wrapped_password,
            "exit_code": int(code2),
            "stdout": out2,
            "stderr": err2,
            "ok": int(code2) == 0,
        }

    def write_text_atomic(self, remote_path: str, content: str, prefer_sudo: bool = False) -> None:
        if self._client is None:
            raise RemoteExecutionError("SSH session is not connected")

        sftp = self._client.open_sftp()
        try:
            self._ensure_remote_dir(sftp, posixpath.dirname(remote_path))
            tmp_path = f"{remote_path}.tmp"
            with sftp.file(tmp_path, "w") as handle:
                handle.write(content)
            try:
                sftp.chmod(tmp_path, 0o644)
            except Exception:
                pass
            sftp.rename(tmp_path, remote_path)
        except Exception as exc:
            if not prefer_sudo:
                raise

            tmp_path = f"/tmp/nettopoview_{threading.get_ident()}_{os.path.basename(remote_path)}.tmp"
            try:
                with sftp.file(tmp_path, "w") as handle:
                    handle.write(content)
                try:
                    sftp.chmod(tmp_path, 0o644)
                except Exception:
                    pass
            except Exception:
                raise exc

            remote_dir = posixpath.dirname(remote_path) or "/"
            ensure_dir = self.run_sudo(f"mkdir -p {shlex.quote(remote_dir)}")
            if not ensure_dir.get("ok"):
                raise RemoteExecutionError(
                    "Failed to create remote directory with sudo: "
                    f"{ensure_dir.get('stderr') or ensure_dir.get('stdout')}"
                )

            install_cmd = (
                f"install -m 0644 {shlex.quote(tmp_path)} {shlex.quote(remote_path)} "
                f"&& rm -f {shlex.quote(tmp_path)}"
            )
            installed = self.run_sudo(install_cmd)
            if not installed.get("ok"):
                raise RemoteExecutionError(
                    "Failed to write privileged file with sudo. "
                    "Ensure SSH user has passwordless sudo for mkdir/install/systemctl/dhcpd. "
                    f"Details: {installed.get('stderr') or installed.get('stdout')}"
                )
        finally:
            sftp.close()

    @staticmethod
    def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
        if not remote_dir or remote_dir == "/":
            return
        segments = [segment for segment in remote_dir.split("/") if segment]
        current = "/"
        for segment in segments:
            current = posixpath.join(current, segment)
            try:
                sftp.stat(current)
            except IOError:
                sftp.mkdir(current)


def _is_permission_denied(result: Dict[str, Any]) -> bool:
    text = f"{result.get('stderr') or ''} {result.get('stdout') or ''}".lower()
    return (
        "permission denied" in text
        or "operation not permitted" in text
        or "interactive authentication required" in text
        or "authentication is required" in text
        or "not authorized" in text
        or "access denied" in text
    )


def _run_remote_command(
    executor: RemoteLinuxExecutor,
    command: str,
    *,
    timeout: int = 30,
    privileged: bool = False,
) -> Dict[str, Any]:
    result = executor.run(command, timeout=timeout)
    if result.get("ok"):
        return result
    if privileged and _is_permission_denied(result):
        sudo_result = executor.run_sudo(command, timeout=timeout)
        if sudo_result.get("ok"):
            sudo_result["sudo_used"] = True
        return sudo_result
    return result


def create_publish_job(
    db: DatabaseManager,
    *,
    device_ids: List[int],
    payload: Dict[str, Any],
    requested_by: str,
    mode: str,
    target_server_ip: str,
    credential_label: str,
) -> int:
    sanitized_ids = [int(device_id) for device_id in device_ids]
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    with db._connect() as conn:
        cur = conn.execute(
            """INSERT INTO ztp_publish_jobs
               (status, mode, target_server_ip, credential_label, requested_by, payload_json,
                total_items, success_items, failed_items, created_at, updated_at)
               VALUES ('queued', ?, ?, ?, ?, ?, ?, 0, 0, datetime('now'), datetime('now'))""",
            (
                mode,
                target_server_ip,
                credential_label,
                requested_by,
                payload_json,
                len(sanitized_ids),
            ),
        )
        job_id = int(cur.lastrowid)

        if sanitized_ids:
            placeholders = ",".join("?" for _ in sanitized_ids)
            rows = conn.execute(
                f"SELECT id, hostname, vendor, management_ip FROM devices WHERE id IN ({placeholders})",
                tuple(sanitized_ids),
            ).fetchall()
            by_id = {int(row["id"]): row for row in rows}
        else:
            by_id = {}

        for device_id in sanitized_ids:
            row = by_id.get(device_id)
            hostname = str(row["hostname"]) if row else f"device-{device_id}"
            vendor = str(row["vendor"] or "") if row else ""
            management_ip = str(row["management_ip"] or "") if row else ""
            conn.execute(
                """INSERT INTO ztp_publish_items
                   (job_id, device_id, hostname, vendor, management_ip, status, stage, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', 'queued', datetime('now'), datetime('now'))""",
                (job_id, device_id, hostname, vendor, management_ip),
            )

    return job_id


def list_publish_jobs(db: DatabaseManager, limit: int = 20) -> List[Dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT * FROM ztp_publish_jobs
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [_hydrate_job_row(row) for row in rows]


def get_publish_job(db: DatabaseManager, job_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM ztp_publish_jobs WHERE id = ?", (int(job_id),)).fetchone()
        if not row:
            return None
        items = conn.execute(
            """SELECT * FROM ztp_publish_items
               WHERE job_id = ?
               ORDER BY id ASC""",
            (int(job_id),),
        ).fetchall()

    payload = _hydrate_job_row(row)
    payload["items"] = [_hydrate_item_row(item) for item in items]
    return payload


def get_publish_manifest(db: DatabaseManager, job_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute("SELECT manifest_json FROM ztp_publish_jobs WHERE id = ?", (int(job_id),)).fetchone()
    if not row:
        return None
    try:
        manifest = json.loads(row["manifest_json"] or "{}")
        return manifest if isinstance(manifest, dict) else {}
    except Exception:
        return {}


def run_publish_job(db_path: str, job_id: int) -> None:
    db = DatabaseManager(db_path)
    vault = CredentialVault()

    with _publish_lock:
        _run_publish_job_locked(db, vault, int(job_id))


def _run_publish_job_locked(db: DatabaseManager, vault: CredentialVault, job_id: int) -> None:
    job = get_publish_job(db, job_id)
    if not job:
        logger.error("ZTP publish job %s not found", job_id)
        return

    payload = dict(job.get("payload") or {})
    target_server_ip = str(job.get("target_server_ip") or _DEFAULT_TARGET_SERVER_IP)
    target_server_port = int(payload.get("target_server_port") or _DEFAULT_TARGET_SERVER_PORT)
    tftp_root = str(payload.get("tftp_root") or _DEFAULT_TFTP_ROOT)
    dhcp_config_path = str(payload.get("dhcp_config_path") or _DEFAULT_DHCP_CONFIG_PATH)
    dhcp_include_path = str(payload.get("dhcp_include_path") or _DEFAULT_DHCP_INCLUDE_PATH)
    dhcp_lease_file = str(payload.get("dhcp_lease_file") or _DEFAULT_DHCP_LEASE_FILE)
    restart_services = bool(payload.get("restart_services", True))
    reset_leases = bool(payload.get("reset_leases", True))
    dry_run = bool(payload.get("dry_run", False))

    _update_job(
        db,
        job_id,
        status="running",
        started_at="datetime('now')",
        updated_at="datetime('now')",
        error_text="",
    )

    try:
        cred_device_id = _normalize_credential_device_id(payload.get("credential_device_id"))
        if cred_device_id is None:
            target_device = db.get_device_by_management_ip(target_server_ip)
            if target_device:
                cred_device_id = target_device.id

        requested_label = str(job.get("credential_label") or "ZTP_SERVER_SSH").strip() or "ZTP_SERVER_SSH"

        credential: Optional[Dict[str, str]] = None
        attempted: List[str] = []

        candidate_pairs: List[tuple[Optional[int], str]] = []
        if cred_device_id is not None:
            candidate_pairs.append((int(cred_device_id), requested_label))
            if requested_label != "SSH":
                candidate_pairs.append((int(cred_device_id), "SSH"))

        # Global credential slot uses NULL device_id in credential vault.
        candidate_pairs.append((None, requested_label))
        if requested_label != "SSH":
            candidate_pairs.append((None, "SSH"))

        seen_candidates: set[tuple[Optional[int], str]] = set()
        for device_key, label in candidate_pairs:
            key = (int(device_key), str(label)) if device_key is not None else (None, str(label))
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            attempted.append(f"device_id={key[0] if key[0] is not None else 'NULL'} label={key[1]}")
            credential = vault.get(key[0], key[1])
            if credential:
                cred_device_id = key[0]
                requested_label = key[1]
                break

        if not credential:
            attempted_text = "; ".join(attempted)
            raise RuntimeError(
                "ZTP server credential not found in vault. "
                f"Tried: {attempted_text}. "
                "Store credential via onboarding ZTP server credential API or map the server in inventory."
            )

        executor = RemoteLinuxExecutor(
            host=target_server_ip,
            port=target_server_port,
            username=str(credential.get("username") or ""),
            password=str(credential.get("password") or ""),
        )

        if not executor._username or not executor._password:
            raise RuntimeError("Resolved credential is empty")

        step_logs: List[Dict[str, Any]] = []

        try:
            if not dry_run:
                executor.connect()

                # Server preflight: ensure TFTP/DHCP paths exist before per-device writes.
                tftp_root_norm = tftp_root.rstrip("/") or "/"
                ztp_root = posixpath.join(tftp_root_norm, "ztp")
                ensure_tftp = _run_remote_command(
                    executor,
                    f"mkdir -p {shlex.quote(tftp_root_norm)} {shlex.quote(ztp_root)}"
                    , privileged=True
                )
                step_logs.append({"step": "ensure_tftp_paths", **ensure_tftp})
                if not ensure_tftp.get("ok"):
                    raise RuntimeError(
                        f"Failed to ensure TFTP directories: {ensure_tftp.get('stderr') or ensure_tftp.get('stdout')}"
                    )

                dhcp_dirs: List[str] = []
                for candidate in (posixpath.dirname(dhcp_config_path), posixpath.dirname(dhcp_include_path)):
                    c = (candidate or "").strip()
                    if c and c not in dhcp_dirs:
                        dhcp_dirs.append(c)

                if dhcp_dirs:
                    ensure_dhcp_dirs = _run_remote_command(
                        executor,
                        "mkdir -p " + " ".join(shlex.quote(path) for path in dhcp_dirs)
                        , privileged=True
                    )
                    step_logs.append({"step": "ensure_dhcp_paths", **ensure_dhcp_dirs})
                    if not ensure_dhcp_dirs.get("ok"):
                        raise RuntimeError(
                            f"Failed to ensure DHCP paths: {ensure_dhcp_dirs.get('stderr') or ensure_dhcp_dirs.get('stdout')}"
                        )

                ensure_dhcp_files = _run_remote_command(
                    executor,
                    f"touch {shlex.quote(dhcp_config_path)} {shlex.quote(dhcp_include_path)}"
                    , privileged=True
                )
                step_logs.append({"step": "ensure_dhcp_files", **ensure_dhcp_files})
                if not ensure_dhcp_files.get("ok"):
                    raise RuntimeError(
                        f"Failed to ensure DHCP files: {ensure_dhcp_files.get('stderr') or ensure_dhcp_files.get('stdout')}"
                    )

                pre_include_line = f'include "{dhcp_include_path}";'
                pre_include_cmd = (
                    f"grep -Fq {shlex.quote(pre_include_line)} {shlex.quote(dhcp_config_path)} "
                    f"|| echo {shlex.quote(pre_include_line)} >> {shlex.quote(dhcp_config_path)}"
                )
                pre_include_result = _run_remote_command(executor, pre_include_cmd, privileged=True)
                step_logs.append({"step": "ensure_dhcp_include_preflight", **pre_include_result})
                if not pre_include_result.get("ok"):
                    raise RuntimeError(
                        f"Failed to preflight DHCP include line: {pre_include_result.get('stderr') or pre_include_result.get('stdout')}"
                    )

            successful_mappings: List[Dict[str, Any]] = []
            item_rows = list(job.get("items") or [])

            for item in item_rows:
                item_id = int(item["id"])
                device_id = int(item["device_id"])
                _update_item(
                    db,
                    item_id,
                    status="running",
                    stage="rendering_config",
                    updated_at="datetime('now')",
                    error_text="",
                )

                try:
                    device = db.get_device_by_id(device_id)
                    if not device:
                        raise RuntimeError(f"Device {device_id} not found")

                    config_override = _resolve_config_override(payload, device_id)
                    config_text = _generate_day0_config_for_device(db, device, config_override)
                    vendor = _safe_segment(device.vendor or "unknown")
                    hostname = _safe_segment(device.hostname or f"device-{device_id}")
                    rel_filename = f"ztp/{vendor}/{hostname}.cfg"
                    remote_path = posixpath.join(tftp_root.rstrip("/"), rel_filename)

                    if not dry_run:
                        executor.write_text_atomic(remote_path, config_text, prefer_sudo=True)

                    management_mac = _resolve_management_mac(db, device_id, device.management_interface)
                    if not management_mac:
                        raise RuntimeError("Management MAC address is missing in interfaces table")

                    fixed_ip = str(device.management_ip or "").strip()
                    if not fixed_ip:
                        raise RuntimeError("Management IP is missing on device")

                    _upsert_mapping(
                        db,
                        device_id=device_id,
                        hostname=device.hostname,
                        vendor=device.vendor or "unknown",
                        management_ip=fixed_ip,
                        management_mac=management_mac,
                        filename=rel_filename,
                    )

                    _update_item(
                        db,
                        item_id,
                        status="success",
                        stage="uploaded",
                        management_mac=management_mac,
                        filename=rel_filename,
                        remote_path=remote_path,
                        updated_at="datetime('now')",
                        details_json=json.dumps(
                            {
                                "message": "config uploaded and mapping updated",
                                "dry_run": dry_run,
                            },
                            ensure_ascii=False,
                        ),
                    )

                    successful_mappings.append(
                        {
                            "device_id": device_id,
                            "hostname": device.hostname,
                            "vendor": device.vendor or "unknown",
                            "management_ip": fixed_ip,
                            "management_mac": management_mac,
                            "filename": rel_filename,
                            "remote_path": remote_path,
                        }
                    )
                except Exception as exc:
                    _update_item(
                        db,
                        item_id,
                        status="failed",
                        stage="failed",
                        updated_at="datetime('now')",
                        error_text=str(exc),
                    )

            if successful_mappings and not dry_run:
                all_mappings = _list_active_mappings(db)
                include_content = _render_dhcp_include_file(all_mappings)
                executor.write_text_atomic(dhcp_include_path, include_content, prefer_sudo=True)

                include_line = f'include "{dhcp_include_path}";'
                include_cmd = (
                    f"grep -Fq {shlex.quote(include_line)} {shlex.quote(dhcp_config_path)} "
                    f"|| echo {shlex.quote(include_line)} >> {shlex.quote(dhcp_config_path)}"
                )
                include_result = _run_remote_command(executor, include_cmd, privileged=True)
                step_logs.append({"step": "ensure_dhcp_include", **include_result})
                if not include_result.get("ok"):
                    raise RuntimeError(
                        f"Failed to ensure DHCP include line: {include_result.get('stderr') or include_result.get('stdout')}"
                    )

                validate_result = _run_remote_command(
                    executor,
                    f"dhcpd -t -cf {shlex.quote(dhcp_config_path)}",
                    privileged=True,
                )
                step_logs.append({"step": "validate_dhcp", **validate_result})
                if not validate_result.get("ok"):
                    raise RuntimeError(
                        f"dhcpd validation failed: {validate_result.get('stderr') or validate_result.get('stdout')}"
                    )

                if reset_leases:
                    lease_result = _run_remote_command(
                        executor,
                        f"rm -f {shlex.quote(dhcp_lease_file)}; touch {shlex.quote(dhcp_lease_file)}",
                        privileged=True,
                    )
                    step_logs.append({"step": "reset_leases", **lease_result})
                    if not lease_result.get("ok"):
                        raise RuntimeError(
                            f"Lease reset failed: {lease_result.get('stderr') or lease_result.get('stdout')}"
                        )

                if restart_services:
                    dhcp_restart = _run_remote_command(
                        executor,
                        "systemctl restart isc-dhcp-server.service",
                        privileged=True,
                    )
                    step_logs.append({"step": "restart_dhcp", **dhcp_restart})
                    if not dhcp_restart.get("ok"):
                        raise RuntimeError(
                            f"DHCP restart failed: {dhcp_restart.get('stderr') or dhcp_restart.get('stdout')}"
                        )

                    tftp_restart = _run_remote_command(
                        executor,
                        "systemctl restart tftpd-hpa.service",
                        privileged=True,
                    )
                    step_logs.append({"step": "restart_tftp", **tftp_restart})
                    if not tftp_restart.get("ok"):
                        raise RuntimeError(
                            f"TFTP restart failed: {tftp_restart.get('stderr') or tftp_restart.get('stdout')}"
                        )

            success_items, failed_items, total_items = _compute_item_counts(db, job_id)
            terminal_status = _derive_terminal_status(total_items, success_items, failed_items)

            manifest = {
                "job_id": job_id,
                "server_ip": target_server_ip,
                "server_port": target_server_port,
                "tftp_root": tftp_root,
                "credential_device_id": cred_device_id,
                "credential_label": requested_label,
                "items": [
                    {
                        **mapping,
                        "tftp_url": f"tftp://{target_server_ip}/{mapping['filename']}",
                        "copy_command": f"copy tftp://{target_server_ip}/{mapping['filename']} running-config",
                    }
                    for mapping in successful_mappings
                ],
            }

            _update_job(
                db,
                job_id,
                status=terminal_status,
                success_items=success_items,
                failed_items=failed_items,
                total_items=total_items,
                summary_json=json.dumps(
                    {
                        "server_steps": step_logs,
                        "dry_run": dry_run,
                        "restart_services": restart_services,
                        "reset_leases": reset_leases,
                    },
                    ensure_ascii=False,
                ),
                manifest_json=json.dumps(manifest, ensure_ascii=False),
                finished_at="datetime('now')",
                updated_at="datetime('now')",
            )
        finally:
            try:
                executor.close()  # type: ignore[name-defined]
            except Exception:
                pass

    except Exception as exc:
        _mark_pending_items_failed(db, job_id, str(exc))
        success_items, failed_items, total_items = _compute_item_counts(db, job_id)
        if total_items > 0 and success_items > 0:
            status = "partial"
        else:
            status = "failed"

        _update_job(
            db,
            job_id,
            status=status,
            success_items=success_items,
            failed_items=max(failed_items, total_items - success_items),
            total_items=total_items,
            error_text=str(exc),
            finished_at="datetime('now')",
            updated_at="datetime('now')",
        )


def _derive_terminal_status(total_items: int, success_items: int, failed_items: int) -> str:
    if total_items <= 0:
        return "failed"
    if failed_items <= 0 and success_items == total_items:
        return "success"
    if success_items > 0:
        return "partial"
    return "failed"


def _compute_item_counts(db: DatabaseManager, job_id: int) -> tuple[int, int, int]:
    with db._connect() as conn:
        row = conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                 SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
               FROM ztp_publish_items
               WHERE job_id = ?""",
            (int(job_id),),
        ).fetchone()
    if not row:
        return (0, 0, 0)
    total = int(row["total"] or 0)
    success_count = int(row["success_count"] or 0)
    failed_count = int(row["failed_count"] or 0)
    return success_count, failed_count, total


def _mark_pending_items_failed(db: DatabaseManager, job_id: int, error_text: str) -> None:
    with db._connect() as conn:
        conn.execute(
            """UPDATE ztp_publish_items
               SET status = 'failed',
                   stage = 'failed',
                   error_text = ?,
                   updated_at = datetime('now')
               WHERE job_id = ? AND status IN ('pending', 'running')""",
            (str(error_text or "job failed"), int(job_id)),
        )


def _resolve_config_override(payload: Dict[str, Any], device_id: int) -> Optional[str]:
    overrides = payload.get("config_overrides")
    if not isinstance(overrides, dict):
        return None

    direct = overrides.get(str(device_id))
    if isinstance(direct, str) and direct.strip():
        return direct

    alt = overrides.get(device_id)
    if isinstance(alt, str) and alt.strip():
        return alt

    return None


def _render_dhcp_include_file(mappings: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# Managed by NetTopoView (onboarding ztp publish)")
    lines.append("# Manual edits in this file are overwritten by publish jobs.")
    lines.append("")

    for mapping in mappings:
        host = _safe_segment(str(mapping.get("hostname") or f"device-{mapping.get('device_id')}").lower())
        mac = str(mapping.get("management_mac") or "").strip().lower()
        ip_addr = str(mapping.get("management_ip") or "").strip()
        filename = str(mapping.get("filename") or "").strip()
        if not mac or not filename:
            continue

        lines.append(f"host {host} {{")
        lines.append(f"  hardware ethernet {mac};")
        if ip_addr:
            lines.append(f"  fixed-address {ip_addr};")
        lines.append(f"  filename \"{filename}\";")
        lines.append("}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _upsert_mapping(
    db: DatabaseManager,
    *,
    device_id: int,
    hostname: str,
    vendor: str,
    management_ip: str,
    management_mac: str,
    filename: str,
) -> None:
    with db._connect() as conn:
        conn.execute(
            """INSERT INTO ztp_dhcp_mappings
               (device_id, hostname, vendor, management_ip, management_mac, filename, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'))
               ON CONFLICT(device_id) DO UPDATE SET
                   hostname = excluded.hostname,
                   vendor = excluded.vendor,
                   management_ip = excluded.management_ip,
                   management_mac = excluded.management_mac,
                   filename = excluded.filename,
                   enabled = 1,
                   updated_at = datetime('now')""",
            (
                int(device_id),
                str(hostname or ""),
                str(vendor or ""),
                str(management_ip or ""),
                str(management_mac or ""),
                str(filename or ""),
            ),
        )


def _list_active_mappings(db: DatabaseManager) -> List[Dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT * FROM ztp_dhcp_mappings
               WHERE enabled = 1
               ORDER BY hostname ASC, id ASC"""
        ).fetchall()
    return [dict(row) for row in rows]


def _resolve_management_mac(db: DatabaseManager, device_id: int, management_interface: Optional[str]) -> Optional[str]:
    interfaces = db.get_interfaces_for_device(device_id)

    target_name = str(management_interface or "").strip().lower()
    if target_name:
        for iface in interfaces:
            if str(iface.name or "").strip().lower() == target_name:
                normalized = _normalize_mac(getattr(iface, "mac_address", None))
                if normalized:
                    return normalized

    for iface in interfaces:
        normalized = _normalize_mac(getattr(iface, "mac_address", None))
        if normalized:
            return normalized

    return None


def _normalize_mac(raw: Optional[str]) -> Optional[str]:
    value = str(raw or "").strip().lower()
    if not value:
        return None

    hex_only = re.sub(r"[^0-9a-f]", "", value)
    if len(hex_only) != 12:
        return None
    return ":".join(hex_only[idx:idx + 2] for idx in range(0, 12, 2))


def _safe_segment(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(raw or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def _guess_gateway(mgmt_ip: Optional[str]) -> str:
    if not mgmt_ip:
        return "0.0.0.0"
    try:
        parts = mgmt_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.1"
    except Exception:
        return "0.0.0.0"


def _resolve_gateway(device: Any) -> str:
    role = str(getattr(device, "role", "") or "").strip().lower()
    if role != "access":
        return ""
    configured = str(getattr(device, "oob_gateway_ip", None) or "").strip()
    return configured or _guess_gateway(getattr(device, "management_ip", None))


def _is_virtual_or_management_interface(name: str, mgmt_interface: Optional[str]) -> bool:
    normalized = str(name or "").strip().lower()
    mgmt_normalized = str(mgmt_interface or "").strip().lower()
    if not normalized:
        return True
    if mgmt_normalized and normalized == mgmt_normalized:
        return True
    return normalized.startswith(("vlan", "vlanif", "loopback", "lo", "null", "fxp", "meth"))


def _discovery_interfaces_for_device(db: DatabaseManager, device: Any) -> List[str]:
    if not getattr(device, "id", None):
        return []
    result: List[str] = []
    for iface in db.get_interfaces_for_device(int(device.id)):
        name = str(getattr(iface, "name", "") or "").strip()
        status = str(getattr(iface, "status", "") or "").strip().lower()
        if status in {"down", "disabled", "shutdown", "admin-down"}:
            continue
        if _is_virtual_or_management_interface(name, getattr(device, "management_interface", None)):
            continue
        if name not in result:
            result.append(name)
    return result


def _default_mgmt_iface(vendor: Optional[str], role: Optional[str] = None) -> str:
    vendor_norm = (vendor or "cisco_ios").lower()
    role_norm = (role or "").strip().lower()

    if "cisco" in vendor_norm and role_norm in ("distribution", "access"):
        return "Ethernet0/0"
    if "huawei" in vendor_norm:
        return "MEth0/0/0"
    if "juniper" in vendor_norm:
        return "fxp0"
    return "GigabitEthernet0/0"


def _normalize_day0_params(params: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(params)
    normalized["vendor"] = (normalized.get("vendor") or "cisco_ios").lower()
    normalized["role"] = str(normalized.get("role") or "network-device").strip().lower()
    normalized.setdefault("subnet_mask", "255.255.255.0")
    normalized["mgmt_l3_interface"] = bool(
        "cisco" in normalized["vendor"] and normalized["role"] in ("distribution", "access")
    )
    normalized["mgmt_prefix_len"] = _prefix_from_mask(
        str(normalized.get("subnet_mask") or "255.255.255.0")
    )
    return normalized


def _config_uses_management_interface(config_text: str, management_interface: Optional[str]) -> bool:
    expected = str(management_interface or "").strip()
    if not expected:
        return True
    # Look for interface configuration statements to avoid matching disabled discovery interfaces
    # Juniper: 'set interfaces <name> ' or 'interfaces <name>.'
    # Cisco/Huawei: 'interface <name>'
    pattern = rf"(?:set interfaces\s+|interface\s+){re.escape(expected)}(?:\s|\.|$)"
    return re.search(pattern, str(config_text or ""), flags=re.IGNORECASE) is not None

def _is_override_stale(config_text: str, device: Any) -> bool:
    # 1. Check management interface configuration
    if not _config_uses_management_interface(config_text, getattr(device, "management_interface", None)):
        return True
    
    # 2. Check hostname presence (case-insensitive)
    hostname = str(getattr(device, "hostname", "") or "").strip()
    if hostname and hostname.lower() not in config_text.lower():
        return True
        
    # 3. Check management IP presence (ignore if empty or 0.0.0.0)
    mgmt_ip = str(getattr(device, "management_ip", "") or "").strip()
    if mgmt_ip and mgmt_ip != "0.0.0.0" and mgmt_ip not in config_text:
        return True
        
    return False


def _common_services_template_params(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "snmp_community": profile.get("snmp_community"),
        "snmp_huawei_community": profile.get("snmp_huawei_community"),
        "snmp_location": profile.get("snmp_location"),
        "snmp_contact": profile.get("snmp_contact"),
        "snmp_host_ip": profile.get("snmp_host_ip"),
        "syslog_host_ip": profile.get("syslog_host_ip"),
        "ntp_server_ip": profile.get("ntp_server_ip"),
    }


def _render_day0_config(params: Dict[str, Any]) -> str:
    vendor = str(params.get("vendor") or "cisco_ios").lower()
    try:
        return _renderer.render(vendor, "day-0", params)
    except Exception as exc:
        logger.warning(
            "Template day-0 render failed for vendor '%s'; using legacy fallback. reason=%s",
            vendor,
            exc,
        )
        return OnboardingConfigGenerator().generate(params)


def _prefix_from_mask(mask: str) -> int:
    try:
        return sum(bin(int(octet)).count("1") for octet in mask.split("."))
    except Exception:
        return 24


def _generate_day0_config_for_device(db: DatabaseManager, device: Any, config_override: Optional[str]) -> str:
    if isinstance(config_override, str) and config_override.strip():
        return config_override

    saved_override = None
    try:
        saved_override = db.get_onboarding_day0_override(int(device.id))
    except Exception:
        saved_override = None

    if saved_override and str(saved_override.get("config_text") or "").strip():
        override_config = str(saved_override["config_text"])
        if not _is_override_stale(override_config, device):
            return override_config
        logger.info(
            "Ignoring stale Day-0 override for ZTP publish device '%s': inventory info has changed.",
            getattr(device, "hostname", device.id),
        )

    effective_common = get_effective_profile(
        db._db_path,
        role=device.role,
        device_id=device.id,
    )

    raw_params = {
        "hostname": device.hostname,
        "mgmt_ip": device.management_ip or "0.0.0.0",
        "subnet_mask": device.management_subnet_mask or "255.255.255.0",
        "gateway": _resolve_gateway(device),
        "mgmt_interface": device.management_interface or _default_mgmt_iface(device.vendor, device.role),
        "discovery_interfaces": _discovery_interfaces_for_device(db, device),
        "vendor": device.vendor or "cisco_ios",
        "role": device.role or "network-device",
        "ssh_user": device.ssh_user or "admin",
        "ssh_password": device.ssh_password or "CHANGE_ME",
        "port": device.ssh_port or 22,
        **_common_services_template_params(effective_common),
    }
    params = _normalize_day0_params(raw_params)
    return _render_day0_config(params)


def _update_job(db: DatabaseManager, job_id: int, **fields: Any) -> None:
    if not fields:
        return
    assignments: List[str] = []
    values: List[Any] = []
    for key, value in fields.items():
        if isinstance(value, str) and value.startswith("datetime('"):
            assignments.append(f"{key} = {value}")
        else:
            assignments.append(f"{key} = ?")
            values.append(value)
    values.append(int(job_id))

    with db._connect() as conn:
        conn.execute(
            f"UPDATE ztp_publish_jobs SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )


def _update_item(db: DatabaseManager, item_id: int, **fields: Any) -> None:
    if not fields:
        return
    assignments: List[str] = []
    values: List[Any] = []
    for key, value in fields.items():
        if isinstance(value, str) and value.startswith("datetime('"):
            assignments.append(f"{key} = {value}")
        else:
            assignments.append(f"{key} = ?")
            values.append(value)
    values.append(int(item_id))

    with db._connect() as conn:
        conn.execute(
            f"UPDATE ztp_publish_items SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )


def _hydrate_job_row(row: Any) -> Dict[str, Any]:
    payload = dict(row)
    payload["payload"] = _safe_json_load(payload.pop("payload_json", None), default={})
    payload["summary"] = _safe_json_load(payload.pop("summary_json", None), default={})
    payload["manifest"] = _safe_json_load(payload.pop("manifest_json", None), default={})
    return payload


def _hydrate_item_row(row: Any) -> Dict[str, Any]:
    payload = dict(row)
    payload["details"] = _safe_json_load(payload.pop("details_json", None), default={})
    return payload


def _safe_json_load(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _normalize_credential_device_id(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed
