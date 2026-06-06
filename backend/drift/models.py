from dataclasses import dataclass
from typing import Optional

@dataclass
class DriftEvent:
    """
    Represents a detected configuration drift.
    """
    device_id: int
    hostname: str
    timestamp: str  # ISO-8601
    has_drift: bool
    diff_text: str  # Unity detailed diff
    severity: str = "low"  # low, medium, high
    id: Optional[int] = None

    def to_dict(self):
        return {
            "device_id": self.device_id,
            "hostname": self.hostname,
            "timestamp": self.timestamp,
            "has_drift": self.has_drift,
            "diff_text": self.diff_text,
            "severity": self.severity
        }
