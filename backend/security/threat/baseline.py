"""
M13b / M22: Threat Detection — Traffic & Metric Baseline Manager
=================================================================
Computes and stores rolling baselines (mean + stddev) for key per-device
metrics:  CPU utilisation, memory usage, interface error counters,
and interface traffic (bytes-in / bytes-out).

Baselines are persisted in the `traffic_baselines` table and consumed by
``AnomalyDetector.check_traffic_baseline()`` (anomaly_detector.py) to
decide whether a real-time metric constitutes an anomaly.

How it works
------------
* ``update(device_id, metric, value)`` appends a sample to a lightweight
  circular buffer (last N=100 samples) stored as JSON in the DB.
* ``get_baseline(device_id, metric)`` returns (mean, stddev) computed from
  the stored buffer.
* The scheduler (M16) can call ``update_all_from_telemetry()`` every cycle
  to keep baselines fresh from InfluxDB telemetry data.

Usage::

    bl = BaselineManager(db_manager)
    bl.update(device_id=1, metric="cpu_util", value=34.5)
    mean, std = bl.get_baseline(1, "cpu_util")
"""

import json
import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from db.manager import DatabaseManager

logger = logging.getLogger("security.threat.baseline")

_BUFFER_SIZE = 100   # number of samples to keep per (device, metric) pair

_DDL = """
CREATE TABLE IF NOT EXISTS traffic_baselines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL,
    metric      TEXT    NOT NULL,
    samples     TEXT    NOT NULL,     -- JSON list of float samples (circular)
    mean        REAL,
    stddev      REAL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL,
    UNIQUE(device_id, metric)
);
"""


class BaselineManager:
    """
    Rolling metric baseline manager.

    Stores circular sample buffers per (device_id, metric) and exposes
    (mean, stddev) to anomaly detectors.
    """

    def __init__(self, db_manager: DatabaseManager, buffer_size: int = _BUFFER_SIZE):
        self.db = db_manager
        self.buffer_size = buffer_size
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def update(self, device_id: int, metric: str, value: float) -> None:
        """
        Add a new sample for (device_id, metric).
        Recomputes mean and stddev immediately.
        """
        row = self._load_row(device_id, metric)
        if row:
            samples: List[float] = json.loads(row["samples"])
        else:
            samples = []

        samples.append(float(value))
        if len(samples) > self.buffer_size:
            samples = samples[-self.buffer_size:]   # keep most recent

        mean, stddev = self._stats(samples)
        ts = datetime.now(tz=timezone.utc).isoformat()

        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO traffic_baselines
                    (device_id, metric, samples, mean, stddev, sample_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, metric) DO UPDATE SET
                    samples       = excluded.samples,
                    mean          = excluded.mean,
                    stddev        = excluded.stddev,
                    sample_count  = excluded.sample_count,
                    updated_at    = excluded.updated_at
                """,
                (device_id, metric, json.dumps(samples), mean, stddev, len(samples), ts),
            )

    def update_batch(self, readings: List[Dict]) -> None:
        """
        Bulk update from a list of ``{"device_id": int, "metric": str, "value": float}``
        dicts — typically fed from the telemetry pipeline.
        """
        for item in readings:
            try:
                self.update(item["device_id"], item["metric"], item["value"])
            except Exception as exc:
                logger.warning("BaselineManager.update_batch error: %s | item=%s", exc, item)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_baseline(
        self, device_id: int, metric: str
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Return (mean, stddev) for *metric* on *device_id*.

        Returns (None, None) if fewer than 10 samples have been collected
        (not enough data to form a reliable baseline).
        """
        row = self._load_row(device_id, metric)
        if not row:
            return None, None
        if row["sample_count"] < 10:
            return None, None
        return row["mean"], row["stddev"]

    def all_baselines_for_device(self, device_id: int) -> Dict[str, Tuple[float, float]]:
        """Return {metric: (mean, stddev)} for every metric on *device_id*."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT metric, mean, stddev, sample_count FROM traffic_baselines WHERE device_id = ?",
                (device_id,),
            ).fetchall()
        result: Dict[str, Tuple[float, float]] = {}
        for row in rows:
            if row["sample_count"] >= 10:
                result[row["metric"]] = (row["mean"], row["stddev"])
        return result

    def is_anomalous(
        self, device_id: int, metric: str, value: float, z_threshold: float = 3.0
    ) -> bool:
        """
        Return True if *value* is more than *z_threshold* standard deviations
        above the baseline mean.
        """
        mean, stddev = self.get_baseline(device_id, metric)
        if mean is None or stddev is None or stddev == 0:
            return False
        z_score = (value - mean) / stddev
        return z_score > z_threshold

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_row(self, device_id: int, metric: str) -> Optional[dict]:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM traffic_baselines WHERE device_id = ? AND metric = ?",
                (device_id, metric),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _stats(samples: List[float]) -> Tuple[float, float]:
        n = len(samples)
        if n == 0:
            return 0.0, 0.0
        mean = sum(samples) / n
        variance = sum((x - mean) ** 2 for x in samples) / max(n - 1, 1)
        return round(mean, 4), round(math.sqrt(variance), 4)
