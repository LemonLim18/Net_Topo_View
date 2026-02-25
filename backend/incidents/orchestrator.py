"""
Incident Response Orchestrator — the closed-loop conductor.

Bridges anomaly detection → incident creation → LLM analysis →
playbook generation → real-time UI alerts → approved deployment.

Lifecycle::

    AnomalyDetector → detect_and_ingest() → analyze_incident()
                                                ├─ RCA + playbook (LLM)
                                                └─ broadcast to UI
    User reviews → approve → execute_approved_playbook()
                                ├─ ansible --check (dry-run)
                                └─ ansible apply   (if check passes)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Dict, List, Optional

from db.manager import DatabaseManager
from incidents.store import IncidentStore
from security.models import SecurityEvent
from security.threat.anomaly_detector import AnomalyDetector

logger = logging.getLogger("incidents.orchestrator")

# How far back we look for security events each scan cycle
_LOOKBACK_MINUTES = 10

# Dedup window — don't create duplicate incidents for same source+type
_DEDUP_WINDOW_MINUTES = 30


class IncidentOrchestrator:
    """
    Central conductor for the automated incident response pipeline.

    Injected with all necessary dependencies at startup via ``backend.py``
    lifespan so it can be called by the scheduler, API endpoints, or
    other backend modules.
    """

    def __init__(
        self,
        db: DatabaseManager,
        agent: Any,            # NetworkAgent (typed as Any to avoid circular import)
        builder: Any = None,   # PlaybookBuilder (optional)
        broadcast_fn: Optional[Callable[..., Coroutine]] = None,
    ):
        self.db = db
        self._agent = agent
        self._builder = builder
        self._broadcast = broadcast_fn
        self._detector = AnomalyDetector()
        self._store = IncidentStore()

    # ------------------------------------------------------------------
    # 1. Detection & Ingestion
    # ------------------------------------------------------------------

    def detect_and_ingest(self) -> List[str]:
        """
        Scan recent security events for anomalies, create incidents for
        any new ones, and return a list of newly created incident IDs.

        Deduplicates: if an incident with the same ``anomaly_type`` +
        ``source_ip`` already exists within the dedup window, skip.
        """
        events = self._fetch_recent_events()
        if not events:
            logger.debug("No recent security events to analyse")
            return []

        anomalies = self._detector.analyze_events(events)
        if not anomalies:
            logger.debug("No anomalies detected in %d events", len(events))
            return []

        # Also save the anomaly events themselves to security_events
        for anomaly in anomalies:
            self._save_security_event(anomaly)

        new_ids: List[str] = []
        for anomaly in anomalies:
            # Dedup check
            if self._is_duplicate(anomaly):
                logger.debug(
                    "Skipping duplicate anomaly: %s from %s",
                    anomaly.event_type, anomaly.source_ip,
                )
                continue

            affected = self._resolve_affected_devices(anomaly)
            metrics = {}
            if anomaly.raw_data and isinstance(anomaly.raw_data, dict):
                metrics = {
                    k: float(v) for k, v in anomaly.raw_data.items()
                    if isinstance(v, (int, float))
                }

            inc_id = self._store.create(
                anomaly_type=anomaly.event_type,
                severity=anomaly.severity,
                affected_devices=affected,
                metrics=metrics,
            )
            new_ids.append(inc_id)
            logger.info(
                "Created incident %s for %s (severity=%s, devices=%s)",
                inc_id, anomaly.event_type, anomaly.severity, affected,
            )

        return new_ids

    # ------------------------------------------------------------------
    # 2. AI Analysis (RCA + Playbook Generation)
    # ------------------------------------------------------------------

    async def analyze_incident(self, incident_id: str) -> bool:
        """
        Feed incident context to the LLM for root-cause analysis and
        generate a remediation Ansible playbook.

        Returns True if analysis was written successfully.
        """
        row = self._store.get(incident_id)
        if not row:
            logger.error("Incident %s not found", incident_id)
            return False

        # Collect context for the LLM
        affected = json.loads(row.get("affected_devices") or "[]")
        configs: Dict[str, str] = {}
        for hostname in affected:
            cfg = self._get_device_config(hostname)
            if cfg:
                configs[hostname] = cfg[:4000]  # cap per device

        # Build the analysis prompt
        analysis_prompt = (
            f"Incident {incident_id}:\n"
            f"Type: {row['anomaly_type']}\n"
            f"Severity: {row['severity']}\n"
            f"Affected devices: {affected}\n"
            f"Metrics: current={row.get('metrics_current')}, "
            f"baseline={row.get('metrics_baseline')}, "
            f"threshold={row.get('metrics_threshold')}\n\n"
        )
        if configs:
            analysis_prompt += "Device configs:\n"
            for h, c in configs.items():
                analysis_prompt += f"--- {h} ---\n{c[:2000]}\n"

        # Get RCA from LLM
        rca = await self._llm_rca(analysis_prompt, row["anomaly_type"], affected)

        # Generate playbook
        playbook_yaml = ""
        if self._builder and affected:
            playbook_yaml = await self._generate_playbook(
                row["anomaly_type"], affected, rca, incident_id
            )

        # Write to incident store
        ok = self._store.update_analysis(
            incident_id,
            logs=json.loads(row.get("logs") or "[]"),
            configs=configs,
            telemetry={},
            root_cause_summary=rca.get("summary", ""),
            confidence=rca.get("confidence", 0.0),
            possible_causes=rca.get("possible_causes", []),
            proposed_description=rca.get("proposed_description", ""),
            proposed_playbook=playbook_yaml,
            estimated_impact=rca.get("estimated_impact", ""),
            rollback_plan=rca.get("rollback_plan", "Re-apply previous config snapshot."),
        )

        if ok:
            logger.info("Analysis written for incident %s", incident_id)
        return ok

    # ------------------------------------------------------------------
    # 3. Real-time Broadcast
    # ------------------------------------------------------------------

    async def broadcast_incident(
        self, incident_id: str, event_type: str = "incident_created"
    ) -> None:
        """Push an incident event to all connected telemetry WS clients."""
        if self._broadcast is None:
            return

        row = self._store.get(incident_id)
        if not row:
            return

        payload = {
            "type": event_type,
            "incident": {
                "id": row["id"],
                "anomaly_type": row["anomaly_type"],
                "severity": row["severity"],
                "affected_devices": json.loads(row.get("affected_devices") or "[]"),
                "status": row["status"],
                "root_cause_summary": row.get("root_cause_summary") or "",
                "created_at": row.get("created_at", ""),
            },
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:
            await self._broadcast(payload)
            logger.debug("Broadcast %s for %s", event_type, incident_id)
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", incident_id, exc)

    # ------------------------------------------------------------------
    # 4. Playbook Execution (post-approval)
    # ------------------------------------------------------------------

    async def execute_approved_playbook(self, incident_id: str) -> Dict[str, Any]:
        """
        Execute the approved remediation playbook for an incident.

        Flow:
          1. Load playbook YAML from incident row
          2. Run in --check mode (dry-run)
          3. If check passes, run for real
          4. Update incident status

        Returns dict with execution result details.
        """
        row = self._store.get(incident_id)
        if not row:
            return {"success": False, "error": "Incident not found"}
        if row["status"] != "approved":
            return {"success": False, "error": f"Incident is '{row['status']}', must be 'approved'"}

        playbook_yaml = row.get("proposed_playbook") or ""
        if not playbook_yaml.strip():
            return {"success": False, "error": "No playbook attached to incident"}

        affected = json.loads(row.get("affected_devices") or "[]")

        try:
            from ansible.runner import run_playbook, AnsibleRunResult
            from ansible.inventory import build_inventory

            inventory = build_inventory(target_hosts=affected)

            # Phase 1: Dry-run
            check_result: AnsibleRunResult = run_playbook(
                playbook_content=playbook_yaml,
                inventory=inventory,
                extra_vars={"incident_id": incident_id},
                check_mode=True,
                timeout=120,
            )
            if not check_result.success:
                logger.warning(
                    "Dry-run failed for incident %s: rc=%d",
                    incident_id, check_result.rc,
                )
                return {
                    "success": False,
                    "phase": "check",
                    "rc": check_result.rc,
                    "stdout": check_result.stdout[-4000:],
                    "stderr": check_result.stderr[-2000:],
                }

            # Phase 2: Apply
            apply_result: AnsibleRunResult = run_playbook(
                playbook_content=playbook_yaml,
                inventory=inventory,
                extra_vars={"incident_id": incident_id},
                check_mode=False,
                timeout=300,
            )

            if apply_result.success:
                self._store.set_status(incident_id, "resolved")
                logger.info("Incident %s remediated successfully", incident_id)
            else:
                logger.warning(
                    "Apply failed for incident %s: rc=%d",
                    incident_id, apply_result.rc,
                )

            return {
                "success": apply_result.success,
                "phase": "apply",
                "rc": apply_result.rc,
                "changed": apply_result.changed,
                "stdout": apply_result.stdout[-4000:],
                "stderr": apply_result.stderr[-2000:],
                "stats": apply_result.stats,
            }

        except ImportError:
            return {"success": False, "error": "ansible-runner not available"}
        except Exception as exc:
            logger.exception("Execution failed for incident %s", incident_id)
            return {"success": False, "error": str(exc)}

    # ==================================================================
    # Private helpers
    # ==================================================================

    def _fetch_recent_events(self) -> List[SecurityEvent]:
        """Load security_events from the last N minutes."""
        try:
            with self.db._connect() as conn:
                cutoff = (
                    datetime.utcnow() - timedelta(minutes=_LOOKBACK_MINUTES)
                ).isoformat()
                rows = conn.execute(
                    "SELECT * FROM security_events WHERE created_at > ? "
                    "ORDER BY created_at DESC LIMIT 200",
                    (cutoff,),
                ).fetchall()
                return [
                    SecurityEvent(
                        id=r["id"],
                        event_type=r["event_type"],
                        severity=r["severity"],
                        source_ip=r.get("source_ip") or "",
                        dest_ip=r.get("dest_ip") or "",
                        device_id=r.get("device_id"),
                        description=r.get("description") or "",
                        raw_data=json.loads(r["raw_data"]) if r.get("raw_data") else None,
                        status=r.get("status") or "new",
                        created_at=r.get("created_at"),
                    )
                    for r in rows
                ]
        except Exception as exc:
            logger.warning("Failed to fetch recent events: %s", exc)
            return []

    def _save_security_event(self, event: SecurityEvent) -> None:
        """Persist an anomaly event back to the security_events table."""
        try:
            with self.db._connect() as conn:
                conn.execute(
                    """INSERT INTO security_events
                       (event_type, severity, source_ip, dest_ip, device_id,
                        description, raw_data, status)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        event.event_type, event.severity,
                        event.source_ip, event.dest_ip,
                        event.device_id, event.description,
                        json.dumps(event.raw_data) if event.raw_data else None,
                        "new",
                    ),
                )
        except Exception as exc:
            logger.warning("Failed to save anomaly event: %s", exc)

    def _is_duplicate(self, anomaly: SecurityEvent) -> bool:
        """Check if a matching incident already exists within the dedup window."""
        cutoff = (
            datetime.utcnow() - timedelta(minutes=_DEDUP_WINDOW_MINUTES)
        ).isoformat()
        recent = self._store.list_all(limit=50)
        for row in recent:
            if (
                row["anomaly_type"] == anomaly.event_type
                and row.get("created_at", "") > cutoff
            ):
                # Same type within window — check affected devices overlap
                existing_devices = json.loads(row.get("affected_devices") or "[]")
                if anomaly.source_ip and anomaly.source_ip in str(existing_devices):
                    return True
        return False

    def _resolve_affected_devices(self, anomaly: SecurityEvent) -> List[str]:
        """Map an anomaly to affected device hostnames."""
        devices: List[str] = []
        try:
            with self.db._connect() as conn:
                if anomaly.device_id:
                    row = conn.execute(
                        "SELECT hostname FROM devices WHERE id = ?",
                        (anomaly.device_id,),
                    ).fetchone()
                    if row:
                        devices.append(row["hostname"])
                elif anomaly.source_ip:
                    row = conn.execute(
                        "SELECT hostname FROM devices WHERE mgmt_ip = ?",
                        (anomaly.source_ip,),
                    ).fetchone()
                    if row:
                        devices.append(row["hostname"])
        except Exception:
            pass

        if not devices and anomaly.source_ip:
            devices.append(anomaly.source_ip)
        return devices

    def _get_device_config(self, hostname: str) -> Optional[str]:
        """Fetch latest config snapshot for a device."""
        try:
            with self.db._connect() as conn:
                row = conn.execute(
                    """SELECT cs.config_text FROM config_snapshots cs
                       JOIN devices d ON d.id = cs.device_id
                       WHERE d.hostname = ?
                       ORDER BY cs.created_at DESC LIMIT 1""",
                    (hostname,),
                ).fetchone()
                return row["config_text"] if row else None
        except Exception:
            return None

    async def _llm_rca(
        self, prompt: str, anomaly_type: str, affected: List[str]
    ) -> Dict[str, Any]:
        """Ask the LLM for structured root-cause analysis."""
        system = (
            "You are a network incident analyst. Analyse the incident below and "
            "return ONLY valid JSON (no markdown, no prose) matching this schema:\n"
            "{\n"
            '  "summary": "one-paragraph root cause summary",\n'
            '  "confidence": 0.85,\n'
            '  "possible_causes": ["cause 1", "cause 2"],\n'
            '  "proposed_description": "what the remediation playbook should do",\n'
            '  "estimated_impact": "expected impact of the fix",\n'
            '  "rollback_plan": "how to revert if fix fails"\n'
            "}"
        )
        fallback = {
            "summary": f"{anomaly_type} detected on {affected}",
            "confidence": 0.5,
            "possible_causes": [anomaly_type],
            "proposed_description": f"Remediate {anomaly_type}",
            "estimated_impact": "Medium",
            "rollback_plan": "Re-apply previous config snapshot.",
        }
        if self._agent is None:
            return fallback
        try:
            from llm.provider import LLMMessage
            provider = self._agent._provider
            messages = [
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=prompt),
            ]
            resp = await provider.generate(messages, tools=None, temperature=0.1)
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            return json.loads(raw)
        except Exception as exc:
            logger.warning("LLM RCA failed: %s — using fallback", exc)
            return fallback

    async def _generate_playbook(
        self,
        anomaly_type: str,
        affected: List[str],
        rca: Dict[str, Any],
        incident_id: str,
    ) -> str:
        """Generate a remediation playbook via PlaybookBuilder or raw LLM."""
        if self._builder is not None:
            try:
                intent = {
                    "description": rca.get("proposed_description", f"Remediate {anomaly_type}"),
                    "target_hosts": affected,
                    "vendor": "cisco_ios",
                    "feature": anomaly_type,
                    "params": {},
                }
                rag_ctx = ""
                if self._agent:
                    try:
                        rag_ctx = self._agent._context.build_full_context()
                    except Exception:
                        pass
                playbook_yaml = await self._builder.build(
                    intent=intent,
                    rag_context=rag_ctx,
                    run_id=incident_id,
                )
                return playbook_yaml
            except Exception as exc:
                logger.warning("PlaybookBuilder failed: %s — trying LLM fallback", exc)

        # LLM fallback
        if self._agent is None:
            return ""
        try:
            from llm.provider import LLMMessage
            provider = self._agent._provider
            messages = [
                LLMMessage(
                    role="system",
                    content=(
                        "You are a network automation engineer. "
                        "Generate a valid Ansible playbook in YAML only — no prose, no markdown fences. "
                        f"Target hosts: {affected}. "
                        "Use cisco.ios.ios_config module for Cisco IOS configuration tasks."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=f"Write an Ansible playbook to: {rca.get('proposed_description', anomaly_type)}",
                ),
            ]
            resp = await provider.generate(messages, tools=None, temperature=0.1)
            raw = resp.content.strip()
            if "```" in raw:
                parts = raw.split("```")
                for part in parts:
                    stripped = part.lstrip("yaml\nYAML\n").strip()
                    if stripped.startswith("---") or stripped.startswith("- name"):
                        return stripped
            return raw
        except Exception as exc:
            logger.warning("LLM playbook generation failed: %s", exc)
            return ""
