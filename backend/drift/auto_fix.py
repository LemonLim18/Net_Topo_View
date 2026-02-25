import logging
import re
from typing import Optional, List
from db.manager import DatabaseManager
from drift.models import DriftEvent

logger = logging.getLogger("drift.auto_fix")

# Patterns we can safely auto-remediate (low-risk, deterministic)
_MISSING_LINE_RE = re.compile(r"^\+\s*(.+)$", re.MULTILINE)   # lines added in intended
_REMOVED_LINE_RE = re.compile(r"^-\s*(.+)$", re.MULTILINE)    # lines removed from intended

# Allowlist of safe-to-push config lines (regex patterns)
_SAFE_PATTERNS = [
    re.compile(r"^ntp\s+"),
    re.compile(r"^logging\s+"),
    re.compile(r"^banner\s+"),
    re.compile(r"^snmp-server\s+"),
    re.compile(r"^ip\s+name-server\s+"),
    re.compile(r"^ip\s+domain[\s-]"),
    re.compile(r"^service\s+"),
    re.compile(r"^clock\s+"),
    re.compile(r"^hostname\s+"),
]


class AutoRemediator:
    """
    Analyzes drift events and generates remediation commands.
    Integrates with the M4 Deployment Pipeline for safe execution.
    """
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def generate_fix(self, event: DriftEvent) -> Optional[List[str]]:
        """
        Parse the unified diff and return safe CLI commands to restore the
        intended configuration.

        Only generates fixes for:
        - severity == "low" drift
        - Lines that match the safe-pattern allowlist (NTP, logging, etc.)

        Returns None when no auto-fix is possible.
        """
        if not event.has_drift:
            return None

        if event.severity not in ("low",):
            logger.info(
                "Skipping auto-fix for %s severity drift on %s",
                event.severity, event.hostname,
            )
            return None

        commands: List[str] = []
        diff = event.diff_text or ""

        # Lines present in intended ('+') but missing from actual → need to be added
        for m in _MISSING_LINE_RE.finditer(diff):
            line = m.group(1).strip()
            if not line or line.startswith("!") or line.startswith("#"):
                continue
            if any(pat.match(line) for pat in _SAFE_PATTERNS):
                commands.append(line)

        # Lines present in actual ('-') that shouldn't be → negate them
        for m in _REMOVED_LINE_RE.finditer(diff):
            line = m.group(1).strip()
            if not line or line.startswith("!") or line.startswith("#"):
                continue
            if any(pat.match(line) for pat in _SAFE_PATTERNS):
                commands.append(f"no {line}")

        if not commands:
            logger.info("No safe auto-fixable lines found for %s", event.hostname)
            return None

        logger.info(
            "Generated %d auto-fix commands for %s: %s",
            len(commands), event.hostname, commands,
        )
        return commands

    def apply_fix(self, event: DriftEvent) -> bool:
        """
        Generate fix commands and push them through the M4 Deployment Pipeline.

        Creates a change request tagged as 'drift-autofix' so it goes through
        the standard approve → snapshot → apply → verify → commit cycle.

        Returns True if the change request was created, False otherwise.
        """
        fix_cmds = self.generate_fix(event)
        if not fix_cmds:
            logger.info("No fix generated for %s.", event.hostname)
            return False

        try:
            from deploy.pipeline import DeploymentPipeline
            pipeline = DeploymentPipeline(self.db)

            # Create a change request
            req_id = pipeline.create_change_request(
                title=f"Drift auto-fix: {event.hostname}",
                requester="drift-autofix",
                description=(
                    f"Automated remediation for low-severity drift detected at "
                    f"{event.timestamp}.\nCommands:\n" + "\n".join(fix_cmds)
                ),
            )

            # Add the device job
            job_id = pipeline.add_job(req_id, event.device_id)

            # Write the generated config directly into the job
            config_text = "\n".join(fix_cmds)
            with self.db._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET generated_config = ?, stage = 'plan', status = 'pending' WHERE id = ?",
                    (config_text, job_id),
                )

            logger.info(
                "Created drift auto-fix change request %d / job %d for %s (%d commands)",
                req_id, job_id, event.hostname, len(fix_cmds),
            )
            return True

        except Exception as e:
            logger.error("Failed to create auto-fix change request for %s: %s", event.hostname, e)
            return False
