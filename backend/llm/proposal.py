"""
M19: Proposal Manager — SQLite-persisted config proposals.

Manages the lifecycle of AI-generated configuration proposals:
    pending → approved → deployed
    pending → rejected
    deployed → rolled_back

Each proposal holds the generated config, rollback commands, diff,
and links to the Deploy Pipeline job once executed.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from pydantic import BaseModel

logger = logging.getLogger("llm.proposal")

_DB_PATH = Path(__file__).resolve().parent.parent / "db" / "netauto.db"


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

class ConfigProposal(BaseModel):
    """A single configuration change proposed by the AI agent."""
    id: str = ""
    status: str = "pending"
    user_prompt: str = ""
    target_device: str = ""
    target_device_id: int = 0
    vendor: str = ""
    feature: str = ""
    params: Dict[str, Any] = {}
    generated_config: str = ""
    rollback_config: str = ""
    diff_text: str = ""
    risk_level: str = "medium"
    ai_explanation: str = ""
    job_id: Optional[int] = None
    request_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Manager (SQLite persistence)
# ---------------------------------------------------------------------------

class ProposalManager:
    """
    CRUD operations for config proposals, backed by SQLite.

    Usage::

        mgr = ProposalManager()
        proposal = mgr.create(
            user_prompt="Configure OSPF on R1",
            target_device="R1",
            target_device_id=1,
            vendor="cisco_ios",
            feature="ospf",
            params={"process_id": 1, "area": 0, ...},
            generated_config="router ospf 1\\n ...",
            rollback_config="no router ospf 1",
            diff_text="--- current\\n+++ proposed\\n...",
            risk_level="medium",
            ai_explanation="This enables OSPF ...",
        )
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or str(_DB_PATH)

    # -- helpers --

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _row_to_proposal(self, row: sqlite3.Row) -> ConfigProposal:
        """Convert a DB row into a ConfigProposal."""
        return ConfigProposal(
            id=row["id"],
            status=row["status"],
            user_prompt=row["user_prompt"],
            target_device=row["target_device"],
            target_device_id=row["target_device_id"] or 0,
            vendor=row["vendor"],
            feature=row["feature"],
            params=json.loads(row["params"]) if row["params"] else {},
            generated_config=row["generated_config"],
            rollback_config=row["rollback_config"] or "",
            diff_text=row["diff_text"] or "",
            risk_level=row["risk_level"] or "medium",
            ai_explanation=row["ai_explanation"] or "",
            job_id=row["job_id"],
            request_id=row["request_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -- CRUD --

    def create(
        self,
        user_prompt: str,
        target_device: str,
        target_device_id: int,
        vendor: str,
        feature: str,
        params: Dict[str, Any],
        generated_config: str,
        rollback_config: str = "",
        diff_text: str = "",
        risk_level: str = "medium",
        ai_explanation: str = "",
    ) -> ConfigProposal:
        """Create and persist a new proposal. Returns the created proposal."""
        proposal_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO config_proposals
                    (id, status, user_prompt, target_device, target_device_id,
                     vendor, feature, params, generated_config, rollback_config,
                     diff_text, risk_level, ai_explanation)
                VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    user_prompt,
                    target_device,
                    target_device_id,
                    vendor,
                    feature,
                    json.dumps(params),
                    generated_config,
                    rollback_config,
                    diff_text,
                    risk_level,
                    ai_explanation,
                ),
            )
            conn.commit()
            logger.info("Created proposal %s for %s/%s on %s",
                        proposal_id, vendor, feature, target_device)
            return self.get(proposal_id)
        finally:
            conn.close()

    def get(self, proposal_id: str) -> Optional[ConfigProposal]:
        """Fetch a single proposal by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM config_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            return self._row_to_proposal(row) if row else None
        finally:
            conn.close()

    def approve(self, proposal_id: str) -> Optional[ConfigProposal]:
        """Transition proposal from pending → approved."""
        return self._update_status(proposal_id, "approved", from_status="pending")

    def reject(self, proposal_id: str) -> Optional[ConfigProposal]:
        """Transition proposal from pending → rejected."""
        return self._update_status(proposal_id, "rejected", from_status="pending")

    def mark_deployed(
        self, proposal_id: str, job_id: Optional[int], request_id: Optional[int]
    ) -> Optional[ConfigProposal]:
        """Transition proposal from approved → deployed, linking to the pipeline job."""
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE config_proposals
                SET status = 'deployed',
                    job_id = ?,
                    request_id = ?,
                    updated_at = datetime('now')
                WHERE id = ? AND status = 'approved'
                """,
                (job_id, request_id, proposal_id),
            )
            conn.commit()
            logger.info("Proposal %s marked as deployed (job=%s)", proposal_id, job_id)
            return self.get(proposal_id)
        finally:
            conn.close()

    def mark_rolled_back(self, proposal_id: str) -> Optional[ConfigProposal]:
        """Transition proposal from deployed → rolled_back."""
        return self._update_status(proposal_id, "rolled_back", from_status="deployed")

    def list_pending(self) -> List[ConfigProposal]:
        """Return all proposals in 'pending' status."""
        return self._list_by_status("pending")

    def list_all(self) -> List[ConfigProposal]:
        """Return all proposals, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM config_proposals ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_proposal(r) for r in rows]
        finally:
            conn.close()

    # -- internal helpers --

    def _update_status(
        self, proposal_id: str, new_status: str, from_status: str
    ) -> Optional[ConfigProposal]:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                UPDATE config_proposals
                SET status = ?, updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (new_status, proposal_id, from_status),
            )
            conn.commit()
            if cur.rowcount == 0:
                logger.warning(
                    "Status transition %s → %s failed for proposal %s (not found or wrong status)",
                    from_status, new_status, proposal_id,
                )
                return None
            logger.info("Proposal %s: %s → %s", proposal_id, from_status, new_status)
            return self.get(proposal_id)
        finally:
            conn.close()

    def _list_by_status(self, status: str) -> List[ConfigProposal]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM config_proposals WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
            return [self._row_to_proposal(r) for r in rows]
        finally:
            conn.close()
