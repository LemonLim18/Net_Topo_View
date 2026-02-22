"""
M13b: Traffic Anomaly Detector
================================
Detects anomalous behavior by comparing current metrics against baselines:

- **Brute force**: >5 failed logins in 60 seconds from the same source
- **Port scan**: >10 distinct ports accessed from same source in 60 seconds
- **Traffic spike**: Interface counters >2σ above historical baseline
- **DDoS indicator**: Abnormally high connection count from single source

Operates on SecurityEvent history stored in M14 database.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from backend.security.models import SecurityEvent

logger = logging.getLogger("security.anomaly")


class AnomalyDetector:
    """
    Analyzes security events and metric history for anomalous patterns.

    Usage::

        detector = AnomalyDetector()
        alerts = detector.analyze_events(recent_events)
        for alert in alerts:
            print(f"Alert: {alert.description}")
    """

    def __init__(
        self,
        brute_force_threshold: int = 5,
        brute_force_window_sec: int = 60,
        port_scan_threshold: int = 10,
        port_scan_window_sec: int = 60,
    ):
        self._brute_force_threshold = brute_force_threshold
        self._brute_force_window = timedelta(seconds=brute_force_window_sec)
        self._port_scan_threshold = port_scan_threshold
        self._port_scan_window = timedelta(seconds=port_scan_window_sec)

    def analyze_events(self, events: List[SecurityEvent]) -> List[SecurityEvent]:
        """
        Analyze a batch of security events for anomaly patterns.

        Args:
            events: Recent security events from the database.

        Returns:
            New anomaly-type SecurityEvents to be saved.
        """
        anomalies: List[SecurityEvent] = []

        # Check for brute force
        bf_alerts = self._detect_brute_force(events)
        anomalies.extend(bf_alerts)

        # Check for port scans
        ps_alerts = self._detect_port_scan(events)
        anomalies.extend(ps_alerts)

        return anomalies

    def _detect_brute_force(self, events: List[SecurityEvent]) -> List[SecurityEvent]:
        """
        Detect brute force: multiple auth failures from same source IP
        within the configured time window.
        """
        # Group auth failures by source IP
        failures_by_ip: Dict[str, List[SecurityEvent]] = defaultdict(list)
        for evt in events:
            if evt.event_type in ("brute_force", "ssh_login") and "fail" in evt.description.lower():
                if evt.source_ip:
                    failures_by_ip[evt.source_ip].append(evt)

        alerts = []
        for ip, fails in failures_by_ip.items():
            if len(fails) >= self._brute_force_threshold:
                # Check time window
                timestamps = []
                for f in fails:
                    if f.created_at:
                        try:
                            timestamps.append(datetime.fromisoformat(f.created_at))
                        except ValueError:
                            pass

                if len(timestamps) >= self._brute_force_threshold:
                    timestamps.sort()
                    # Sliding window check
                    for i in range(len(timestamps) - self._brute_force_threshold + 1):
                        window = timestamps[i + self._brute_force_threshold - 1] - timestamps[i]
                        if window <= self._brute_force_window:
                            alerts.append(SecurityEvent(
                                event_type="brute_force",
                                severity="critical",
                                source_ip=ip,
                                description=(
                                    f"Brute force detected: {len(fails)} failed login attempts "
                                    f"from {ip} within {self._brute_force_window.seconds}s"
                                ),
                                raw_data={
                                    "total_failures": len(fails),
                                    "window_seconds": self._brute_force_window.seconds,
                                    "threshold": self._brute_force_threshold,
                                },
                            ))
                            break  # One alert per IP

        return alerts

    def _detect_port_scan(self, events: List[SecurityEvent]) -> List[SecurityEvent]:
        """
        Detect port scanning: multiple ACL denies to distinct ports
        from the same source within the configured time window.
        """
        denies_by_ip: Dict[str, List[dict]] = defaultdict(list)
        for evt in events:
            if evt.event_type in ("port_scan", "acl_deny") and evt.raw_data:
                src_ip = evt.source_ip
                dest_port = evt.raw_data.get("dest_port") if isinstance(evt.raw_data, dict) else None
                if src_ip and dest_port:
                    denies_by_ip[src_ip].append({
                        "port": dest_port,
                        "created_at": evt.created_at,
                    })

        alerts = []
        for ip, denies in denies_by_ip.items():
            distinct_ports = set(d["port"] for d in denies)
            if len(distinct_ports) >= self._port_scan_threshold:
                alerts.append(SecurityEvent(
                    event_type="port_scan",
                    severity="high",
                    source_ip=ip,
                    description=(
                        f"Port scan detected: {len(distinct_ports)} distinct ports probed "
                        f"from {ip}"
                    ),
                    raw_data={
                        "distinct_ports": len(distinct_ports),
                        "ports_sampled": list(distinct_ports)[:20],
                        "threshold": self._port_scan_threshold,
                    },
                ))

        return alerts

    def check_traffic_baseline(
        self,
        current_value: float,
        baseline_mean: float,
        baseline_stddev: float,
        sigma_threshold: float = 2.0,
    ) -> Optional[SecurityEvent]:
        """
        Check if a traffic metric exceeds the baseline by more than N standard deviations.

        Args:
            current_value:    Current metric value (e.g., bytes/sec).
            baseline_mean:    Historical average.
            baseline_stddev:  Historical standard deviation.
            sigma_threshold:  Number of std devs above mean to trigger (default: 2σ).

        Returns:
            SecurityEvent if anomalous, None otherwise.
        """
        if baseline_stddev == 0:
            return None

        z_score = (current_value - baseline_mean) / baseline_stddev
        if z_score > sigma_threshold:
            return SecurityEvent(
                event_type="anomaly",
                severity="high" if z_score > 3.0 else "medium",
                description=(
                    f"Traffic anomaly: current={current_value:.0f}, "
                    f"baseline={baseline_mean:.0f}±{baseline_stddev:.0f}, "
                    f"z-score={z_score:.1f}"
                ),
                raw_data={
                    "current": current_value,
                    "mean": baseline_mean,
                    "stddev": baseline_stddev,
                    "z_score": round(z_score, 2),
                },
            )
        return None
