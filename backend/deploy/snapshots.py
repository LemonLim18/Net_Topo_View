"""
M4 Deployment Pipeline — Pre / Post Config Snapshot Manager.

Captures running config from a device via the M1 driver layer and stores
it in the `config_snapshots` table (M14 schema).  Called by the pipeline
at the SNAPSHOT_PRE and COMMIT stages.
"""

import logging
from datetime import datetime
from typing import Optional

from db.manager import DatabaseManager
from db.models import ConfigSnapshot
from drivers.factory import DriverFactory

logger = logging.getLogger("deploy.snapshots")


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
