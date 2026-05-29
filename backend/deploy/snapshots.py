"""
M4 Deployment Pipeline — Pre / Post Config Snapshot Manager.

Captures running config from a device via the M1 driver layer and stores
it in the `config_snapshots` table (M14 schema).  Called by the pipeline
at the SNAPSHOT_PRE and COMMIT stages.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from db.manager import DatabaseManager
from db.models import ConfigSnapshot, AuditLogEntry
from drivers.factory import DriverFactory

logger = logging.getLogger("deploy.snapshots")

_RESTORE_SUPPORTED_VENDORS = {"cisco_ios", "cisco_xe", "huawei_vrp"}


def _normalize_vendor(vendor: Optional[str]) -> str:
    return (vendor or "").strip().lower()


class SnapshotManager:
    """
    Handles pre-change and post-change config capture.

    Pre-snapshot  → taken before any config is pushed (SNAPSHOT_PRE stage).
    Post-snapshot → taken after config is applied and verified (COMMIT stage).
    Both snapshot IDs are written back to the `jobs` table so rollback can
    diff them or restore the pre-snapshot at any time.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        device_id: int,
        label: str = "pre",
        fetched_config: Optional[str] = None,
        description: Optional[str] = None,
        owner_username: Optional[str] = None,
    ) -> Optional[int]:
        """
        Fetch the running configuration and store it in DB.

        Parameters
        ----------
        device_id:       DB id of the target device.
        label:           'pre' | 'post' — stored as part of the snapshot record.
        fetched_config:  If the caller already has the config text (e.g. from a
                         previous driver call) pass it in to avoid a second
                         connection.  If None, a fresh SSH fetch is performed.
        owner_username:  Username of the user who initiated the capture (M15 ownership).

        Returns
        -------
        snapshot_id (int) or None on failure.
        """
        device = self.db.get_device_by_id(device_id)
        if device is None:
            logger.error("Device #%d not found", device_id)
            return None

        if fetched_config is None:
            driver = None
            try:
                driver = DriverFactory.get_driver(device)
                driver.connect()
                fetched_config = driver.get_running_config()
            except Exception as exc:
                logger.error(
                    "Failed to fetch config from %s for %s snapshot: %s",
                    device.hostname, label, exc,
                )
                return None
            finally:
                if driver is not None:
                    try:
                        driver.disconnect()
                    except Exception as disc_exc:
                        logger.warning("Disconnect after capture failed: %s", disc_exc)

        snapshot = ConfigSnapshot(
            device_id=device_id,
            config_text=fetched_config,
            captured_at=datetime.utcnow().isoformat() + "Z",
            description=(str(description).strip() if description is not None else None) or None,
            source_type=("manual" if label == "backup" else "deploy_pipeline"),
            owner_username=owner_username,
        )
        snapshot_id = self.db.save_config_snapshot(snapshot)
        logger.info(
            "Saved %s-snapshot #%d for device %s (%d bytes)",
            label, snapshot_id, device.hostname, len(fetched_config),
        )
        return snapshot_id

    # ------------------------------------------------------------------
    # Retrieve / Compare
    # ------------------------------------------------------------------

    def get(self, snapshot_id: int) -> Optional[ConfigSnapshot]:
        """Retrieve a single snapshot by its DB id."""
        return self.db.get_config_snapshot(snapshot_id)

    def restore_snapshot(self, snapshot_id: int, owner_username: Optional[str] = None) -> Dict[str, Any]:
        """
        Apply a stored snapshot back onto the live device.

        Current safety policy:
        - Supported: Cisco IOS / IOS-XE, Huawei VRP
        - Not yet supported: Juniper Junos (saved snapshot format is hierarchical and
          cannot be safely replayed with the current line-oriented push_config flow)

        The method always captures a pre-restore backup first so operators have an
        immediate rollback reference if the applied restore is not what they intended.
        """
        snap = self.db.get_config_snapshot(snapshot_id)
        if snap is None:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        device = self.db.get_device_by_id(snap.device_id)
        if device is None:
            raise ValueError(f"Device #{snap.device_id} for snapshot {snapshot_id} no longer exists")

        vendor = _normalize_vendor(getattr(device, "vendor", None))
        if vendor not in _RESTORE_SUPPORTED_VENDORS:
            raise ValueError(
                "Snapshot restore is currently supported only for Cisco IOS/IOS-XE and Huawei VRP. "
                "Juniper snapshots remain view-only until a vendor-safe restore workflow is added."
            )

        config_text = (snap.config_text or "").strip()
        if not config_text:
            raise ValueError("This snapshot has no configuration content to restore")

        commands = self._build_restore_commands(vendor, snap.config_text)
        if not commands:
            raise ValueError("No restorable configuration commands could be derived from this snapshot")

        backup_description = (
            f"Automatic safety backup captured before restoring snapshot #{snapshot_id}"
        )
        restored_description = (
            f"Automatic post-restore snapshot after applying snapshot #{snapshot_id}"
        )

        driver = DriverFactory.get_driver(device)
        backup_snapshot_id: Optional[int] = None
        restored_snapshot_id: Optional[int] = None

        try:
            driver.connect()

            # Safety net: always keep the current running config before we push anything.
            current_config = driver.get_running_config()
            backup_snapshot_id = self.capture(
                device.id,
                label="backup",
                fetched_config=current_config,
                description=backup_description,
                owner_username=owner_username,
            )

            result = driver.push_config(commands)
            if not result.success:
                raise RuntimeError(result.error or "The device rejected the restore commands")

            restored_config = driver.get_running_config()
            restored_snapshot_id = self.capture(
                device.id,
                label="backup",
                fetched_config=restored_config,
                description=restored_description,
                owner_username=owner_username,
            )
        finally:
            try:
                driver.disconnect()
            except Exception as disc_exc:
                logger.warning("Disconnect after restore failed: %s", disc_exc)

        try:
            self.db.log_action(
                AuditLogEntry(
                    action="config_restore",
                    user_id=owner_username,
                    target=device.hostname,
                    details=json.dumps(
                        {
                            "device_id": device.id,
                            "snapshot_id": snapshot_id,
                            "vendor": vendor,
                            "backup_snapshot_id": backup_snapshot_id,
                            "restored_snapshot_id": restored_snapshot_id,
                            "commands_applied": len(commands),
                        },
                        ensure_ascii=False,
                    ),
                    created_at=datetime.utcnow().isoformat() + "Z",
                )
            )
        except Exception as audit_exc:
            logger.warning("Audit log write for snapshot restore failed: %s", audit_exc)

        logger.info(
            "Restored snapshot #%s to device %s using %d commands (backup=%s, post=%s)",
            snapshot_id,
            device.hostname,
            len(commands),
            backup_snapshot_id,
            restored_snapshot_id,
        )

        return {
            "snapshot_id": snapshot_id,
            "device_id": device.id,
            "hostname": device.hostname,
            "vendor": vendor,
            "backup_snapshot_id": backup_snapshot_id,
            "restored_snapshot_id": restored_snapshot_id,
            "commands_applied": len(commands),
            "message": (
                f"Snapshot #{snapshot_id} was restored to {device.hostname}. "
                f"A safety backup was saved as snapshot #{backup_snapshot_id}."
            ),
        }

    def diff(self, pre_id: int, post_id: int) -> str:
        """
        Return a unified diff between pre and post snapshot texts.
        Useful for audit reporting and rollback decisions.
        """
        import difflib

        pre = self.get(pre_id)
        post = self.get(post_id)
        if not pre or not post:
            return "! Could not load one or both snapshots for diff."

        diff_lines = list(
            difflib.unified_diff(
                pre.config_text.splitlines(),
                post.config_text.splitlines(),
                fromfile=f"pre-snapshot #{pre_id}",
                tofile=f"post-snapshot #{post_id}",
                lineterm="",
            )
        )
        return "\n".join(diff_lines) if diff_lines else "(no differences)"

    def _build_restore_commands(self, vendor: str, config_text: str) -> List[str]:
        vendor = _normalize_vendor(vendor)
        if vendor not in _RESTORE_SUPPORTED_VENDORS:
            return []

        section_delimiter = "!" if vendor in {"cisco_ios", "cisco_xe"} else "#"
        submode_exit = "exit" if vendor in {"cisco_ios", "cisco_xe"} else "quit"
        tokens = self._tokenize_snapshot_config(config_text, vendor, section_delimiter)

        commands: List[str] = []
        in_submode = False

        for idx, token in enumerate(tokens):
            token_type = token["type"]
            if token_type == "separator":
                if in_submode:
                    commands.append(submode_exit)
                    in_submode = False
                continue

            line = str(token["line"])
            indent = int(token["indent"])

            if indent == 0 and in_submode:
                commands.append(submode_exit)
                in_submode = False

            commands.append(line)

            next_cmd = self._next_command_token(tokens, idx)
            in_submode = bool(
                indent == 0
                and next_cmd is not None
                and int(next_cmd.get("indent", 0)) > 0
            )

        if in_submode:
            commands.append(submode_exit)

        return commands

    def _tokenize_snapshot_config(
        self,
        config_text: str,
        vendor: str,
        section_delimiter: str,
    ) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        for raw_line in (config_text or "").splitlines():
            line = raw_line.rstrip("\r")
            stripped = line.strip()
            if self._should_skip_restore_line(stripped, vendor):
                continue
            if stripped == section_delimiter:
                tokens.append({"type": "separator"})
                continue
            indent = len(line) - len(line.lstrip(" "))
            tokens.append({"type": "command", "line": stripped, "indent": indent})
        return tokens

    def _next_command_token(self, tokens: List[Dict[str, Any]], start_idx: int) -> Optional[Dict[str, Any]]:
        for idx in range(start_idx + 1, len(tokens)):
            if tokens[idx].get("type") == "command":
                return tokens[idx]
        return None

    def _should_skip_restore_line(self, stripped: str, vendor: str) -> bool:
        if not stripped:
            return True

        lower = stripped.lower()
        common_prefixes = (
            "show running-config",
            "display current-configuration",
            "show configuration",
            "show configuration |",
            "set cli screen-length 0",
            "screen-length 0 temporary",
            "terminal length 0",
        )
        if lower.startswith(common_prefixes):
            return True

        if re.match(r"^[A-Za-z0-9_.@:-]+[>#]\s*$", stripped):
            return True
        if re.match(r"^[<\[].+[>\]]\s*$", stripped):
            return True

        if vendor in {"cisco_ios", "cisco_xe"}:
            if lower.startswith("building configuration"):
                return True
            if lower.startswith("current configuration"):
                return True
            if stripped == "end":
                return True
        elif vendor == "huawei_vrp":
            if lower.startswith("return"):
                return True

        return False

    # ------------------------------------------------------------------
    # Write snapshot IDs back to the jobs table
    # ------------------------------------------------------------------

    def attach_to_job(
        self,
        db_manager: DatabaseManager,
        job_id: int,
        pre_id: Optional[int] = None,
        post_id: Optional[int] = None,
    ) -> None:
        """Update the jobs row with the snapshot references."""
        with db_manager._connect() as conn:
            if pre_id is not None:
                conn.execute(
                    "UPDATE jobs SET snapshot_pre_id = ? WHERE id = ?",
                    (pre_id, job_id),
                )
            if post_id is not None:
                conn.execute(
                    "UPDATE jobs SET snapshot_post_id = ? WHERE id = ?",
                    (post_id, job_id),
                )
