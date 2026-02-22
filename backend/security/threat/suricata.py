"""
M13b: Suricata IDS Integration
================================
Parses Suricata's ``eve.json`` alert output and converts entries into
SecurityEvent objects that can be stored in the M14 database.

Suricata writes one JSON object per line (JSONL format) to ``eve.json``.
This module provides:

1. **SuricataParser** — parses individual eve.json alert entries
2. **SuricataWatcher** — watches an eve.json file for new alerts (tail -f style)
3. **parse_webhook_payload** — processes alerts received via REST webhook

Usage::

    from backend.security.threat.suricata import SuricataParser, SuricataWatcher

    # Parse a single alert JSON
    parser = SuricataParser()
    event = parser.parse_alert(alert_dict)

    # Watch eve.json file
    watcher = SuricataWatcher("/var/log/suricata/eve.json", callback=on_alert)
    watcher.start()  # runs in background thread
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from backend.security.models import SecurityEvent

logger = logging.getLogger("security.suricata")


# ---------------------------------------------------------------------------
# Severity Mapping: Suricata severity (1=highest) → our severity labels
# ---------------------------------------------------------------------------
_SURICATA_SEVERITY_MAP = {
    1: "critical",
    2: "high",
    3: "medium",
    4: "low",
}


class SuricataParser:
    """
    Parses individual Suricata eve.json alert entries into SecurityEvent objects.

    Suricata eve.json alert format::

        {
          "timestamp": "2024-11-17T08:42:11.123456+0000",
          "event_type": "alert",
          "src_ip": "192.168.1.100",
          "dest_ip": "10.0.0.5",
          "src_port": 54321,
          "dest_port": 445,
          "proto": "TCP",
          "alert": {
            "action": "allowed",
            "gid": 1,
            "signature_id": 2024897,
            "rev": 3,
            "signature": "ET EXPLOIT SMB Double Pulsar Probe",
            "category": "Attempted User Privilege Gain",
            "severity": 1
          }
        }
    """

    def parse_alert(self, entry: Dict[str, Any]) -> Optional[SecurityEvent]:
        """
        Parse a single eve.json entry into a SecurityEvent.

        Args:
            entry: Parsed JSON dict from eve.json.

        Returns:
            SecurityEvent if this is an alert, None otherwise.
        """
        # Only process "alert" event types
        event_type = entry.get("event_type", "")
        if event_type != "alert":
            return None

        alert = entry.get("alert", {})
        if not alert:
            return None

        # Map severity
        suricata_severity = alert.get("severity", 4)
        severity = _SURICATA_SEVERITY_MAP.get(suricata_severity, "low")

        # Build description
        signature = alert.get("signature", "Unknown signature")
        category = alert.get("category", "")
        sig_id = alert.get("signature_id", 0)
        description = f"[SID:{sig_id}] {signature}"
        if category:
            description += f" ({category})"

        return SecurityEvent(
            event_type="suricata",
            severity=severity,
            source_ip=entry.get("src_ip", ""),
            dest_ip=entry.get("dest_ip", ""),
            description=description,
            raw_data={
                "timestamp": entry.get("timestamp"),
                "proto": entry.get("proto"),
                "src_port": entry.get("src_port"),
                "dest_port": entry.get("dest_port"),
                "alert": alert,
            },
        )

    def parse_many(self, entries: List[Dict[str, Any]]) -> List[SecurityEvent]:
        """Parse multiple eve.json entries, returning only valid alerts."""
        events = []
        for entry in entries:
            event = self.parse_alert(entry)
            if event:
                events.append(event)
        return events


class SuricataWatcher:
    """
    Watches a Suricata eve.json file for new alerts (tail -f style).

    Runs in a background thread, calls the provided callback for each
    new SecurityEvent detected.

    Usage::

        def on_alert(event: SecurityEvent):
            db.save_security_event(event)

        watcher = SuricataWatcher("/var/log/suricata/eve.json", callback=on_alert)
        watcher.start()
        # ... later ...
        watcher.stop()
    """

    def __init__(
        self,
        eve_json_path: str,
        callback: Callable[[SecurityEvent], None],
        poll_interval: float = 1.0,
    ):
        self._path = eve_json_path
        self._callback = callback
        self._poll_interval = poll_interval
        self._parser = SuricataParser()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_position = 0

    def start(self):
        """Start watching in a background thread."""
        if self._running:
            return
        self._running = True
        # Seek to end of file so we only process NEW alerts
        if os.path.exists(self._path):
            self._last_position = os.path.getsize(self._path)
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logger.info("Suricata watcher started: %s", self._path)

    def stop(self):
        """Stop the watcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Suricata watcher stopped")

    def _watch_loop(self):
        """Poll the file for new lines."""
        while self._running:
            try:
                if not os.path.exists(self._path):
                    time.sleep(self._poll_interval)
                    continue

                file_size = os.path.getsize(self._path)
                if file_size < self._last_position:
                    # File was rotated / truncated
                    self._last_position = 0

                if file_size > self._last_position:
                    with open(self._path, "r") as f:
                        f.seek(self._last_position)
                        new_lines = f.readlines()
                        self._last_position = f.tell()

                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            event = self._parser.parse_alert(entry)
                            if event:
                                self._callback(event)
                        except json.JSONDecodeError:
                            logger.debug("Skipping non-JSON line in eve.json")

            except Exception as e:
                logger.error("Suricata watcher error: %s", e)

            time.sleep(self._poll_interval)


def parse_webhook_payload(payload: Dict[str, Any]) -> Optional[SecurityEvent]:
    """
    Parse a Suricata alert received via REST webhook.

    This is called by the security API endpoint ``POST /security/alerts/suricata``.
    The payload should be a single Suricata eve.json alert entry.
    """
    parser = SuricataParser()
    return parser.parse_alert(payload)
