from __future__ import annotations

import ipaddress
import logging
import os
import re
import shlex
import socket
from typing import Any, Dict, List, Optional, Tuple

from auth.credentials import CredentialVault
from common_services.service import DEFAULT_DB_PATH, get_effective_profile
from db.manager import DatabaseManager

try:
    from onboarding.ztp_publish import RemoteLinuxExecutor
except Exception:  # pragma: no cover - optional dependency in local runtime
    RemoteLinuxExecutor = None  # type: ignore[assignment]

logger = logging.getLogger("syslog.service")

_SYSLOG_PATTERN = re.compile(
    r"%(?P<facility>\w+)-(?P<severity>\d)-(?P<mnemonic>\w+):\s*(?P<message>.*)"
)

_HOST_FROM_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+\s+(?P<host>\S+)\s+")
_HOST_FROM_TRADITIONAL = re.compile(r"^<\d+>\w{3}\s+\d+\s+\d+:\d+:\d+\s+(?P<host>\S+)\s+")
_IDENT_BEFORE_COLON = re.compile(r"^\s*(?P<ident>[^:\s]+)\s*:\s*(?P<rest>.*)$")

_CISCO_SEVERITY_MAP = {
    "0": "critical",
    "1": "critical",
    "2": "critical",
    "3": "high",
    "4": "high",
    "5": "medium",
    "6": "low",
    "7": "info",
}

_HOSTS_BEGIN_MARKER = "# BEGIN NETTOPOVIEW SYSLOG HOSTS"
_HOSTS_END_MARKER = "# END NETTOPOVIEW SYSLOG HOSTS"


def parse_syslog_metadata(raw_message: str) -> Dict[str, Optional[str]]:
    raw = str(raw_message or "")
    facility: Optional[str] = None
    severity: Optional[str] = None
    mnemonic: Optional[str] = None
    message: Optional[str] = None

    match = _SYSLOG_PATTERN.search(raw)
    if match:
        facility = match.group("facility")
        severity_num = match.group("severity")
        mnemonic = match.group("mnemonic")
        message = (match.group("message") or "").strip() or None
        severity = _CISCO_SEVERITY_MAP.get(severity_num, "info")

    source_host: Optional[str] = None
    m_iso = _HOST_FROM_ISO_PREFIX.match(raw)
    if m_iso:
        source_host = (m_iso.group("host") or "").strip() or None
    else:
        m_std = _HOST_FROM_TRADITIONAL.match(raw)
        if m_std:
            source_host = (m_std.group("host") or "").strip() or None

    # Prefer explicit in-payload device identifier when present:
    # "<device_identifier> : <actual_message>"
    # This is more accurate than transport source when logs are relayed.
    m_ident = _IDENT_BEFORE_COLON.match(raw)
    if m_ident:
        explicit_ident = (m_ident.group("ident") or "").strip()
        if explicit_ident:
            source_host = explicit_ident

    return {
        "facility": facility,
        "severity": severity,
        "mnemonic": mnemonic,
        "message": message,
        "source_host": source_host,
    }


def _safe_label(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    # Standardize host/file labels for predictable syslog fan-out paths.
    normalized = (cleaned or fallback).lower()
    return normalized


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _inventory_entries(db: DatabaseManager) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    seen_ip: set[str] = set()
    for dev in db.get_all_devices():
        if (dev.source or "").strip().lower() == "crawler":
            continue
        ip = str(dev.management_ip or "").strip()
        if not ip or ip in seen_ip:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        seen_ip.add(ip)
        hostname = _safe_label(str(dev.hostname or ""), ip.replace(".", "_"))
        entries.append({"hostname": hostname, "ip": ip})
    entries.sort(key=lambda item: item["ip"])
    return entries


def _resolve_syslog_server_ip() -> str:
    env_host = str(os.getenv("SYSLOG_REMOTE_HOST", "")).strip()
    if env_host:
        return env_host
    try:
        effective = get_effective_profile(DEFAULT_DB_PATH, site=None, role=None, device_id=None)
        host = str(effective.get("syslog_host_ip") or "").strip()
        if host:
            return host
    except Exception:
        pass
    return "192.168.1.250"


def _infer_backend_ip_towards(peer_ip: str) -> Optional[str]:
    """
    Infer this backend host's reachable source IP toward a peer (e.g. syslog server).
    Useful when SYSLOG_FORWARD_APP_HOST is unset.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((peer_ip, 1))
        local_ip = sock.getsockname()[0]
        sock.close()
        if local_ip and local_ip not in {"0.0.0.0", "127.0.0.1"}:
            return local_ip
    except Exception:
        return None
    return None


def _resolve_forward_target(syslog_server_ip: str) -> Tuple[Optional[str], int, str]:
    """
    Resolve where rsyslog on the remote server should forward mirrored events.
    Priority:
    1) SYSLOG_FORWARD_APP_HOST / SYSLOG_FORWARD_APP_PORT
    2) infer backend source IP toward syslog server + BACKEND_SYSLOG_RECEIVER_PORT
    3) infer backend source IP + effective profile syslog_port
    """
    env_host = str(os.getenv("SYSLOG_FORWARD_APP_HOST", "")).strip()
    env_port = str(os.getenv("SYSLOG_FORWARD_APP_PORT", "")).strip()

    host = env_host or _infer_backend_ip_towards(syslog_server_ip)
    port = 6514
    source = "env" if env_host else "inferred"

    backend_receiver_port = str(os.getenv("BACKEND_SYSLOG_RECEIVER_PORT", "")).strip()

    if env_port.isdigit():
        port = int(env_port)
    elif backend_receiver_port.isdigit():
        port = int(backend_receiver_port)
    else:
        try:
            effective = get_effective_profile(DEFAULT_DB_PATH, site=None, role=None, device_id=None)
            port = int(effective.get("syslog_port") or 6514)
        except Exception:
            port = 6514

    if host and host in {"127.0.0.1", "0.0.0.0"}:
        host = None
    return host, port, source


def _resolve_server_credentials(
    db: DatabaseManager,
    *,
    server_ip: str,
    requested_label: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    vault = CredentialVault()
    requested = str(requested_label or "ZTP_SERVER_SSH").strip() or "ZTP_SERVER_SSH"
    candidate_pairs: List[Tuple[Optional[int], str]] = []
    candidates: List[Dict[str, Any]] = []

    # Highest priority: explicit environment credential for syslog server sync.
    env_user = str(os.getenv("SYSLOG_REMOTE_USER", "")).strip()
    env_pass = str(os.getenv("SYSLOG_REMOTE_PASSWORD", ""))
    if env_user and env_pass:
        candidates.append(
            {
                "username": env_user,
                "password": env_pass,
                "source": "env",
                "device_id": None,
                "label": "SYSLOG_REMOTE_USER",
            }
        )

    env_device_id_raw = str(os.getenv("SYSLOG_SERVER_CREDENTIAL_DEVICE_ID", "")).strip()
    if env_device_id_raw.isdigit():
        candidate_pairs.append((int(env_device_id_raw), requested))
        if requested != "SSH":
            candidate_pairs.append((int(env_device_id_raw), "SSH"))

    device = db.get_device_by_management_ip(server_ip)
    if device and device.id:
        candidate_pairs.append((int(device.id), requested))
        if requested != "SSH":
            candidate_pairs.append((int(device.id), "SSH"))

    candidate_pairs.append((None, requested))
    if requested != "SSH":
        candidate_pairs.append((None, "SSH"))

    attempted: List[str] = []
    seen: set[Tuple[Optional[int], str]] = set()
    for device_id, label in candidate_pairs:
        key = (int(device_id), str(label)) if device_id is not None else (None, str(label))
        if key in seen:
            continue
        seen.add(key)
        attempted.append(f"device_id={key[0] if key[0] is not None else 'NULL'} label={key[1]}")
        found = vault.get(key[0], key[1])
        if found:
            user = str(found.get("username") or "").strip()
            pwd = str(found.get("password") or "")
            if user and pwd:
                candidates.append(
                    {
                        "username": user,
                        "password": pwd,
                        "source": "vault",
                        "device_id": key[0],
                        "label": key[1],
                    }
                )

    # Also try device row SSH credential for the syslog server if present.
    server_device = db.get_device_by_management_ip(server_ip)
    if server_device:
        row_user = str(server_device.ssh_user or "").strip()
        row_pwd = str(server_device.ssh_password or "")
        if row_user and row_pwd:
            candidates.append(
                {
                    "username": row_user,
                    "password": row_pwd,
                    "source": "device_row",
                    "device_id": server_device.id,
                    "label": "device.ssh_user",
                }
            )

    # De-duplicate while preserving priority order.
    deduped: List[Dict[str, Any]] = []
    seen_creds: set[Tuple[str, str]] = set()
    for entry in candidates:
        cred_key = (str(entry.get("username") or ""), str(entry.get("password") or ""))
        if cred_key in seen_creds:
            continue
        seen_creds.add(cred_key)
        deduped.append(entry)

    return deduped, {"attempted": "; ".join(attempted)}


def _build_rsyslog_rule(
    *,
    mode: str,
    log_root: str,
    forward_host: Optional[str] = None,
    forward_port: int = 6514,
    file_owner: str = "syslog",
    file_group: str = "adm",
    dir_owner: str = "syslog",
    dir_group: str = "adm",
) -> str:
    token = "%hostname%" if mode == "hostname" else "%fromhost-ip%"
    forward_block = ""
    if str(forward_host or "").strip():
        fwd_host = str(forward_host).strip()
        fwd_port = int(forward_port or 6514)
        forward_block = (
            f'  action(type="omfwd" target="{fwd_host}" port="{fwd_port}" protocol="udp")\n'
        )
    return (
        "# Managed by NetTopoView. Device fan-out syslog rule.\n"
        f'template(name="DynamicDeviceLog" type="string" string="{log_root}/{token}/{token}.log")\n\n'
        'if ($inputname startswith "imudp") then {\n'
        f'  action(type="omfile" dirCreateMode="0755" fileCreateMode="0640" dirOwner="{dir_owner}" dirGroup="{dir_group}" fileOwner="{file_owner}" fileGroup="{file_group}" dynafile="DynamicDeviceLog")\n'
        f"{forward_block}"
        "  stop\n"
        "}\n"
    )


def _build_hosts_managed_block(entries: List[Dict[str, str]]) -> str:
    lines = [_HOSTS_BEGIN_MARKER]
    for row in entries:
        lines.append(f"{row['ip']} {row['hostname']}")
    lines.append(_HOSTS_END_MARKER)
    return "\n".join(lines) + "\n"


def _rewrite_hosts_content(existing: str, managed_block: str) -> str:
    raw = existing or ""
    pattern = re.compile(
        rf"{re.escape(_HOSTS_BEGIN_MARKER)}.*?{re.escape(_HOSTS_END_MARKER)}\n?",
        re.DOTALL,
    )
    if pattern.search(raw):
        updated = pattern.sub(managed_block, raw)
    else:
        updated = raw.rstrip() + "\n\n" + managed_block if raw.strip() else managed_block
    return updated


def _sync_with_executor(
    *,
    executor: Any,
    entries: List[Dict[str, str]],
    requested_mode: str,
    log_root: str,
    rules_path: str,
    hosts_path: str,
    validate_cmd: str,
    restart_cmd: str,
    forward_host: Optional[str],
    forward_port: int,
    file_owner: str,
    file_group: str,
    dir_owner: str,
    dir_group: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    current_hosts_result = executor.run_sudo(f"cat {shlex.quote(hosts_path)}", timeout=30)
    current_hosts = (
        str(current_hosts_result.get("stdout") or "")
        if current_hosts_result.get("ok")
        else ""
    )
    new_hosts = _rewrite_hosts_content(current_hosts, _build_hosts_managed_block(entries))
    executor.write_text_atomic(hosts_path, new_hosts, prefer_sudo=True)

    rsyslog_rule = _build_rsyslog_rule(
        mode=requested_mode,
        log_root=log_root,
        forward_host=forward_host,
        forward_port=forward_port,
        file_owner=file_owner,
        file_group=file_group,
        dir_owner=dir_owner,
        dir_group=dir_group,
    )
    executor.write_text_atomic(rules_path, rsyslog_rule, prefer_sudo=True)

    labels = [row["hostname"] if requested_mode == "hostname" else row["ip"] for row in entries]
    mkdir_targets = " ".join(shlex.quote(f"{log_root}/{label}") for label in labels)
    if mkdir_targets:
        touch_cmds = [
            f"touch {shlex.quote(f'{log_root}/{label}/{label}.log')}"
            for label in labels
        ]
        mk_cmd = f"mkdir -p {mkdir_targets}"
        if touch_cmds:
            mk_cmd += " && " + " && ".join(touch_cmds)
        mk_result = executor.run_sudo(mk_cmd, timeout=60)
        if not mk_result.get("ok"):
            result["file_prep_error"] = (mk_result.get("stderr") or mk_result.get("stdout") or "").strip()

    # Ensure rsyslog runtime user can write existing fan-out tree.
    owner_fix_cmd = (
        f"chown -R {shlex.quote(f'{file_owner}:{file_group}')} {shlex.quote(log_root)} && "
        f"find {shlex.quote(log_root)} -type d -exec chmod 0755 {{}} \\; && "
        f"find {shlex.quote(log_root)} -type f -exec chmod 0640 {{}} \\;"
    )
    owner_fix_result = executor.run_sudo(owner_fix_cmd, timeout=90)
    if not owner_fix_result.get("ok"):
        result["owner_fix_warning"] = (
            owner_fix_result.get("stderr") or owner_fix_result.get("stdout") or ""
        ).strip()

    validate = executor.run_sudo(validate_cmd, timeout=30)
    if not validate.get("ok"):
        result["validate_error"] = (validate.get("stderr") or validate.get("stdout") or "").strip()
        result["message"] = "Rsyslog validation failed; restart skipped."
        return result

    restart = executor.run_sudo(restart_cmd, timeout=30)
    if not restart.get("ok"):
        result["restart_error"] = (restart.get("stderr") or restart.get("stdout") or "").strip()
        result["message"] = "Rsyslog restart failed."
        return result

    result["synced"] = True
    return result


def sync_syslog_server_inventory(
    db: DatabaseManager,
    *,
    mode: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    enabled = _env_bool("SYSLOG_REMOTE_AUTO_SYNC", False)
    requested_mode = (mode or os.getenv("SYSLOG_DEVICE_PATH_MODE", "hostname")).strip().lower()
    if requested_mode not in {"hostname", "ip"}:
        requested_mode = "hostname"

    entries = _inventory_entries(db)
    result: Dict[str, Any] = {
        "enabled": enabled,
        "mode": requested_mode,
        "inventory_device_count": len(entries),
        "synced": False,
        "created_targets": [row["hostname"] if requested_mode == "hostname" else row["ip"] for row in entries],
    }
    if not enabled and not force:
        result["message"] = "Remote syslog sync is disabled (SYSLOG_REMOTE_AUTO_SYNC=false)."
        return result
    if RemoteLinuxExecutor is None:
        result["message"] = "Remote syslog sync skipped: paramiko/runtime dependency is unavailable."
        return result

    server_ip = _resolve_syslog_server_ip()
    server_port = int(os.getenv("SYSLOG_REMOTE_PORT", "22"))
    credential_label = str(os.getenv("SYSLOG_SERVER_CREDENTIAL_LABEL", "ZTP_SERVER_SSH")).strip() or "ZTP_SERVER_SSH"
    credentials, resolved = _resolve_server_credentials(db, server_ip=server_ip, requested_label=credential_label)
    result["server_ip"] = server_ip
    result["server_port"] = server_port
    result["credential_attempt_order"] = [
        f"{entry.get('source')}:{entry.get('label')}:{entry.get('device_id')}"
        for entry in credentials
    ]

    if not credentials:
        result["message"] = (
            "Remote syslog sync skipped: server credential not found. "
            f"Attempted {resolved.get('attempted', '')}. "
            "Set SYSLOG_REMOTE_USER/SYSLOG_REMOTE_PASSWORD or store vault credential label."
        )
        return result

    log_root = str(os.getenv("SYSLOG_REMOTE_LOG_ROOT", "/var/log/network-devices")).strip() or "/var/log/network-devices"
    rules_path = str(
        os.getenv("SYSLOG_REMOTE_RULE_PATH", "/etc/rsyslog.d/60-nettopoview-device-fanout.conf")
    ).strip() or "/etc/rsyslog.d/60-nettopoview-device-fanout.conf"
    hosts_path = str(os.getenv("SYSLOG_REMOTE_HOSTS_PATH", "/etc/hosts")).strip() or "/etc/hosts"
    restart_cmd = str(os.getenv("SYSLOG_REMOTE_RESTART_CMD", "systemctl restart rsyslog")).strip()
    validate_cmd = str(os.getenv("SYSLOG_REMOTE_VALIDATE_CMD", "rsyslogd -N1")).strip()
    forward_host, forward_port, forward_source = _resolve_forward_target(server_ip)
    file_owner = str(os.getenv("SYSLOG_REMOTE_FILE_OWNER", "syslog")).strip() or "syslog"
    file_group = str(os.getenv("SYSLOG_REMOTE_FILE_GROUP", "adm")).strip() or "adm"
    dir_owner = str(os.getenv("SYSLOG_REMOTE_DIR_OWNER", file_owner)).strip() or file_owner
    dir_group = str(os.getenv("SYSLOG_REMOTE_DIR_GROUP", file_group)).strip() or file_group
    result["forward_host"] = forward_host
    result["forward_port"] = forward_port
    result["forward_target_source"] = forward_source
    result["file_owner"] = file_owner
    result["file_group"] = file_group
    result["dir_owner"] = dir_owner
    result["dir_group"] = dir_group
    if not forward_host:
        result["forward_warning"] = (
            "No forward target resolved. Logs will still be written on syslog server "
            "but not mirrored to backend live stream."
        )

    auth_errors: List[str] = []
    last_error = ""
    for cred in credentials:
        username = str(cred.get("username") or "").strip()
        password = str(cred.get("password") or "")
        if not username or not password:
            continue

        result["credential_label"] = cred.get("label")
        result["credential_device_id"] = cred.get("device_id")
        result["credential_source"] = cred.get("source")
        executor = RemoteLinuxExecutor(host=server_ip, port=server_port, username=username, password=password)
        try:
            executor.connect()
            outcome = _sync_with_executor(
                executor=executor,
                entries=entries,
                requested_mode=requested_mode,
                log_root=log_root,
                rules_path=rules_path,
                hosts_path=hosts_path,
                validate_cmd=validate_cmd,
                restart_cmd=restart_cmd,
                forward_host=forward_host,
                forward_port=forward_port,
                file_owner=file_owner,
                file_group=file_group,
                dir_owner=dir_owner,
                dir_group=dir_group,
                result=result,
            )
            if outcome.get("synced"):
                outcome["message"] = f"Syslog server sync completed on {server_ip}."
                return outcome
            return outcome
        except Exception as exc:
            last_error = str(exc)
            if "authentication failed" in str(exc).lower():
                auth_errors.append(
                    f"{cred.get('source')}:{cred.get('label')}:{cred.get('device_id')}"
                )
                continue
            result["message"] = f"Syslog server sync failed on {server_ip}: {exc}"
            result["error"] = str(exc)
            return result
        finally:
            try:
                executor.close()
            except Exception:
                pass

    if auth_errors:
        result["message"] = (
            f"Syslog server sync failed on {server_ip}: Authentication failed. "
            f"Tried credentials: {', '.join(auth_errors)}"
        )
        result["error"] = last_error or "Authentication failed."
        return result

    result["message"] = f"Syslog server sync failed on {server_ip}: no usable credentials found."
    result["error"] = last_error or "No usable credentials."
    return result
