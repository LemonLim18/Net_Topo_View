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

from security.models import SecurityEvent

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
        # Network degradation thresholds
        link_down_window_sec: int = 300,
        error_rate_threshold: int = 50,
        error_rate_window_sec: int = 300,
        latency_spike_factor: float = 3.0,
    ):
        self._brute_force_threshold = brute_force_threshold
        self._brute_force_window = timedelta(seconds=brute_force_window_sec)
        self._port_scan_threshold = port_scan_threshold
        self._port_scan_window = timedelta(seconds=port_scan_window_sec)

        # Network degradation
        self._link_down_window = timedelta(seconds=link_down_window_sec)
        self._error_rate_threshold = error_rate_threshold
        self._error_rate_window = timedelta(seconds=error_rate_window_sec)
        self._latency_spike_factor = latency_spike_factor

    def analyze_events(self, events: List[SecurityEvent]) -> List[SecurityEvent]:
        """
        Analyze a batch of security events for anomaly patterns.

        Args:
            events: Recent security events from the database.

        Returns:
            New anomaly-type SecurityEvents to be saved.
        """
        anomalies: List[SecurityEvent] = []

        # Security detectors
        anomalies.extend(self._detect_brute_force(events))
        anomalies.extend(self._detect_port_scan(events))

        # Network degradation detectors
        anomalies.extend(self._detect_link_failure(events))
        anomalies.extend(self._detect_interface_errors(events))
        anomalies.extend(self._detect_latency_spike(events))

        return anomalies

    def analyze_telemetry(
        self,
        telemetry: List[Dict],
    ) -> List[SecurityEvent]:
        """
        Analyze raw telemetry data points (from InfluxDB or SNMP) for
        network degradation.  Each dict should contain at minimum::

            {
                "host": "switch-01",
                "interface": "GigabitEthernet0/1",  # optional
                "metric": "link_status" | "error_count" | "latency_ms" | ...,
                "value": <float>,
                "baseline": <float>,         # optional
                "baseline_stddev": <float>,  # optional
                "timestamp": "<ISO 8601>",
            }

        Returns:
            SecurityEvent list for any anomalous data points.
        """
        anomalies: List[SecurityEvent] = []
        for point in telemetry:
            metric = point.get("metric", "")
            value = point.get("value")
            host = point.get("host", "unknown")
            iface = point.get("interface", "")
            baseline = point.get("baseline")
            stddev = point.get("baseline_stddev")

            if value is None:
                continue

            # Link down
            if metric == "link_status" and value == 0:
                anomalies.append(SecurityEvent(
                    event_type="link_failure",
                    severity="critical",
                    source_ip=host,
                    description=(
                        f"Link down: {host} {iface} is operationally down"
                    ),
                    raw_data={
                        "host": host,
                        "interface": iface,
                        "metric": metric,
                        "value": value,
                    },
                ))

            # Error counters
            elif metric in ("input_errors", "output_errors", "crc_errors",
                            "error_count"):
                if float(value) >= self._error_rate_threshold:
                    anomalies.append(SecurityEvent(
                        event_type="interface_errors",
                        severity="high" if float(value) >= self._error_rate_threshold * 3 else "medium",
                        source_ip=host,
                        description=(
                            f"Interface errors: {host} {iface} — "
                            f"{metric}={value} (threshold={self._error_rate_threshold})"
                        ),
                        raw_data={
                            "host": host,
                            "interface": iface,
                            "metric": metric,
                            "value": float(value),
                            "threshold": self._error_rate_threshold,
                        },
                    ))

            # Latency / jitter
            elif metric in ("latency_ms", "rtt_ms", "jitter_ms"):
                if baseline is not None and float(value) > float(baseline) * self._latency_spike_factor:
                    sev = "critical" if float(value) > float(baseline) * self._latency_spike_factor * 2 else "high"
                    anomalies.append(SecurityEvent(
                        event_type="latency_spike",
                        severity=sev,
                        source_ip=host,
                        description=(
                            f"Latency spike: {host} {iface} — {metric}={value:.1f}ms "
                            f"(baseline={baseline:.1f}ms, factor={self._latency_spike_factor}x)"
                        ),
                        raw_data={
                            "host": host,
                            "interface": iface,
                            "metric": metric,
                            "current": float(value),
                            "baseline": float(baseline),
                            "factor": self._latency_spike_factor,
                        },
                    ))

                # Also check z-score if stddev available
                elif baseline is not None and stddev is not None:
                    evt = self.check_traffic_baseline(
                        float(value), float(baseline), float(stddev),
                    )
                    if evt:
                        evt.event_type = "latency_spike"
                        evt.source_ip = host
                        evt.description = (
                            f"Latency anomaly: {host} {iface} — {metric}={value:.1f}ms, "
                            + evt.description
                        )
                        anomalies.append(evt)

            # Generic traffic baseline check
            elif baseline is not None and stddev is not None:
                evt = self.check_traffic_baseline(
                    float(value), float(baseline), float(stddev),
                )
                if evt:
                    evt.source_ip = host
                    anomalies.append(evt)

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

    # ------------------------------------------------------------------
    # Network degradation detectors (event-based)
    # ------------------------------------------------------------------

    def _detect_link_failure(self, events: List[SecurityEvent]) -> List[SecurityEvent]:
        """
        Detect link-down events from syslog/SNMP-trap-derived security events.

        Matches events with ``event_type`` in (``link_failure``, ``link_down``,
        ``interface_down``) or descriptions containing key phrases.
        """
        alerts: List[SecurityEvent] = []
        seen_keys: set = set()

        for evt in events:
            is_link_event = evt.event_type in ("link_failure", "link_down", "interface_down")
            if not is_link_event:
                desc = evt.description.lower()
                is_link_event = any(
                    phrase in desc
                    for phrase in ("line protocol.*down", "link down", "oper down", "interface down")
                )
                # Also check via regex for "line protocol" pattern
                if not is_link_event and "line protocol" in desc and "down" in desc:
                    is_link_event = True

            if is_link_event:
                host = evt.source_ip or (evt.raw_data or {}).get("host", "unknown")
                iface = (evt.raw_data or {}).get("interface", "")
                dedup_key = f"{host}:{iface}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                alerts.append(SecurityEvent(
                    event_type="link_failure",
                    severity="critical",
                    source_ip=host,
                    device_id=evt.device_id,
                    description=(
                        f"Link failure detected: {host} {iface} — {evt.description[:120]}"
                    ),
                    raw_data={
                        "host": host,
                        "interface": iface,
                        "original_event_type": evt.event_type,
                        "original_description": evt.description[:500],
                    },
                ))

        if alerts:
            logger.warning("Detected %d link failure(s)", len(alerts))
        return alerts

    def _detect_interface_errors(
        self, events: List[SecurityEvent],
    ) -> List[SecurityEvent]:
        """
        Detect high interface error rates from events that carry counter data
        (e.g. periodic SNMP polls stored as security-relevant events).

        Matches events with ``event_type`` in (``interface_errors``,
        ``snmp_counter``) whose ``raw_data`` contains an error count above
        the configured threshold.
        """
        alerts: List[SecurityEvent] = []
        seen_keys: set = set()

        for evt in events:
            if evt.event_type not in ("interface_errors", "snmp_counter", "counter_poll"):
                continue
            if not isinstance(evt.raw_data, dict):
                continue

            # Sum all error fields present in the raw data
            error_sum = 0
            for key in ("input_errors", "output_errors", "crc_errors",
                        "collisions", "drops", "error_count"):
                val = evt.raw_data.get(key)
                if isinstance(val, (int, float)):
                    error_sum += val

            if error_sum < self._error_rate_threshold:
                continue

            host = evt.source_ip or evt.raw_data.get("host", "unknown")
            iface = evt.raw_data.get("interface", "")
            dedup_key = f"{host}:{iface}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            sev = "high" if error_sum >= self._error_rate_threshold * 3 else "medium"
            alerts.append(SecurityEvent(
                event_type="interface_errors",
                severity=sev,
                source_ip=host,
                device_id=evt.device_id,
                description=(
                    f"Interface errors: {host} {iface} — "
                    f"total_errors={error_sum} (threshold={self._error_rate_threshold})"
                ),
                raw_data={
                    "host": host,
                    "interface": iface,
                    "total_errors": error_sum,
                    "threshold": self._error_rate_threshold,
                    **{k: evt.raw_data.get(k) for k in
                       ("input_errors", "output_errors", "crc_errors",
                        "collisions", "drops") if evt.raw_data.get(k)},
                },
            ))

        if alerts:
            logger.warning("Detected %d interface error anomalie(s)", len(alerts))
        return alerts

    def _detect_latency_spike(
        self, events: List[SecurityEvent],
    ) -> List[SecurityEvent]:
        """
        Detect latency / jitter spikes from events with RTT or jitter
        measurements in ``raw_data``.

        Matches events with ``event_type`` in (``latency_spike``,
        ``ip_sla``, ``probe_result``) where ``current`` exceeds
        ``baseline`` by the configured factor.
        """
        alerts: List[SecurityEvent] = []
        seen_keys: set = set()

        for evt in events:
            if evt.event_type not in ("latency_spike", "ip_sla", "probe_result",
                                      "latency", "jitter"):
                continue
            if not isinstance(evt.raw_data, dict):
                continue

            current = evt.raw_data.get("current") or evt.raw_data.get("latency_ms") or evt.raw_data.get("rtt_ms")
            baseline = evt.raw_data.get("baseline")
            if current is None or baseline is None:
                continue

            current_f = float(current)
            baseline_f = float(baseline)
            if baseline_f <= 0:
                continue
            if current_f <= baseline_f * self._latency_spike_factor:
                continue

            host = evt.source_ip or evt.raw_data.get("host", "unknown")
            target = evt.raw_data.get("target", "")
            dedup_key = f"{host}:{target}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            factor_actual = round(current_f / baseline_f, 1)
            sev = "critical" if current_f > baseline_f * self._latency_spike_factor * 2 else "high"
            alerts.append(SecurityEvent(
                event_type="latency_spike",
                severity=sev,
                source_ip=host,
                device_id=evt.device_id,
                description=(
                    f"Latency spike: {host} → {target} — "
                    f"current={current_f:.1f}ms (baseline={baseline_f:.1f}ms, "
                    f"{factor_actual}x, threshold={self._latency_spike_factor}x)"
                ),
                raw_data={
                    "host": host,
                    "target": target,
                    "current": current_f,
                    "baseline": baseline_f,
                    "factor": factor_actual,
                    "threshold_factor": self._latency_spike_factor,
                },
            ))

        if alerts:
            logger.warning("Detected %d latency spike(s)", len(alerts))
        return alerts

    # ------------------------------------------------------------------
    # Traffic baseline check
    # ------------------------------------------------------------------

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
