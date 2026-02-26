"""
M8 Drift Detection — Compliance Scoring.

Computes per-device and network-wide compliance percentages based on
recent drift events stored in the DB.

Score formula (per device)
--------------------------
Base score: 100 points.
Deductions (applied once per finding severity, not per diff line):
  critical : –30
  high     : –20
  medium   : –10
  low      :  –3

Score is clamped to [0, 100].

The resulting scores are written into the `drift_scores` table (created
automatically if it does not exist) so the dashboard can read them without
re-querying all drift events every time.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from db.manager import DatabaseManager

logger = logging.getLogger("drift.scoring")

_DEDUCTIONS: Dict[str, int] = {
    "critical": 30,
    "high":     20,
    "medium":   10,
    "low":       3,
}

_DDL = """
CREATE TABLE IF NOT EXISTS drift_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL,
    hostname    TEXT    NOT NULL,
    score       REAL    NOT NULL,
    total_drift INTEGER NOT NULL DEFAULT 0,
    calculated_at TEXT  NOT NULL
);
"""


class DriftScorer:
    """
    Calculates and persists compliance scores for all devices that have
    at least one recorded drift event.

    Usage
    -----
    scorer = DriftScorer(db_manager)
    report = scorer.calculate_all()   # dict of hostname → score
    avg    = scorer.network_average()
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Calculation
    # ------------------------------------------------------------------

    def score_device(self, device_id: int, hostname: str) -> float:
        """
        Compute a compliance score for a single device based on its recent
        drift events (last 30 days).

        Returns a float in [0.0, 100.0].
        """
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT severity, COUNT(*) as cnt
                FROM drift_events
                WHERE device_id = ?
                  AND has_drift = 1
                  AND timestamp >= datetime('now', '-30 days')
                GROUP BY severity
                """,
                (device_id,),
            ).fetchall()

        score = 100.0
        total = 0
        for row in rows:
            sev = (row["severity"] or "low").lower()
            cnt = row["cnt"]
            deduction = _DEDUCTIONS.get(sev, 3)
            score -= deduction * cnt
            total += cnt

        score = max(0.0, min(100.0, score))
        self._persist(device_id, hostname, score, total)
        return score

    def calculate_all(self) -> Dict[str, float]:
        """
        Score every device that has at least one drift event.

        Returns {hostname: score, ...}
        """
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT d.id as device_id, d.hostname
                FROM devices d
                JOIN drift_events de ON de.device_id = d.id
                """,
            ).fetchall()

        results: Dict[str, float] = {}
        for row in rows:
            score = self.score_device(row["device_id"], row["hostname"])
            results[row["hostname"]] = score
            logger.debug("Device %s compliance score: %.1f", row["hostname"], score)
        return results

    def network_average(self) -> float:
        """
        Return the mean compliance score across all scored devices.

        Reads from `drift_scores` (the last saved score per device).
        Returns 100.0 if no devices have been scored.
        """
        with self.db._connect() as conn:
            row = conn.execute(
                """
                SELECT AVG(s.score) as avg_score
                FROM drift_scores s
                INNER JOIN (
                    SELECT device_id, MAX(id) as max_id
                    FROM drift_scores
                    GROUP BY device_id
                ) latest ON latest.device_id = s.device_id AND latest.max_id = s.id
                """
            ).fetchone()

        avg = row["avg_score"] if row and row["avg_score"] is not None else 100.0
        return round(avg, 2)

    def get_scores(self) -> List[Dict]:
        """Return the latest score for every device (for the dashboard API)."""
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM drift_scores s
                INNER JOIN (
                    SELECT device_id, MAX(id) as max_id
                    FROM drift_scores
                    GROUP BY device_id
                ) latest ON latest.device_id = s.device_id AND latest.max_id = s.id
                ORDER BY s.score ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, device_id: int, hostname: str, score: float, total: int) -> None:
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO drift_scores (device_id, hostname, score, total_drift, calculated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    hostname,
                    round(score, 2),
                    total,
                    datetime.now(tz=timezone.utc).isoformat(),
                ),
            )
