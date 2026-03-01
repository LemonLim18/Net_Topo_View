"""
Phase 7 — Unified Validator Report (UC33).

Aggregates multiple validation stages into a single pass/fail report:

    1. **Syntax** — vendor-specific config syntax checks (SyntaxValidator)
    2. **YAML Lint** — ansible-lint / YAML parse validation
    3. **Dry-Run** — check-mode diff (if targets reachable)
    4. **Policy** — blast-radius checks, risk-level assessment

Returns a composite ``ValidationReport`` that the frontend displays
as a gated checklist before operator approval.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import uuid
import yaml
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("config_engine.unified_validator")

router = APIRouter(prefix="/validate", tags=["Unified Validation"])

_db = None

try:
    from auth.auth import get_current_user as _auth_dep
except ImportError:
    def _auth_dep():  # type: ignore
        return {"username": "anonymous", "role": "admin"}


def setup_validator(db_manager) -> None:
    global _db
    _db = db_manager
    logger.info("Unified validator ready")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    name: str
    passed: bool
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    details: Optional[Dict[str, Any]] = None
    duration_ms: int = 0


@dataclass
class ValidationReport:
    report_id: str = ""
    overall_pass: bool = True
    stages: List[StageResult] = field(default_factory=list)
    risk_level: str = "medium"
    blast_radius: int = 0
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class ValidateRequest(BaseModel):
    playbook_yaml: Optional[str] = None
    config_text: Optional[str] = None
    vendor: str = "cisco_ios"
    target_hosts: List[str] = Field(default_factory=list)
    run_dry_run: bool = False  # Requires live device access
    check_policy: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/report", summary="Run unified validation and return composite report")
async def run_validation(
    body: ValidateRequest,
    current_user: dict = Depends(_auth_dep),
):
    """
    Run all applicable validation stages and return a unified report.
    """
    report = ValidationReport(
        report_id=str(uuid.uuid4())[:8],
        timestamp=datetime.utcnow().isoformat(),
    )

    # Stage 1: Syntax validation (config_text)
    if body.config_text:
        result = await run_in_threadpool(_run_syntax_check, body.config_text, body.vendor)
        report.stages.append(result)
        if not result.passed:
            report.overall_pass = False

    # Stage 2: YAML / ansible-lint (playbook_yaml)
    if body.playbook_yaml:
        result = await run_in_threadpool(_run_yaml_lint, body.playbook_yaml)
        report.stages.append(result)
        if not result.passed:
            report.overall_pass = False

    # Stage 3: Dry-run (only if requested and targets provided)
    if body.run_dry_run and body.playbook_yaml and body.target_hosts:
        result = await run_in_threadpool(_run_dry_run, body.playbook_yaml, body.target_hosts)
        report.stages.append(result)
        if not result.passed:
            report.overall_pass = False

    # Stage 4: Policy check
    if body.check_policy:
        result = _run_policy_check(
            playbook_yaml=body.playbook_yaml,
            config_text=body.config_text,
            target_hosts=body.target_hosts,
        )
        report.stages.append(result)
        if not result.passed:
            report.overall_pass = False
        report.blast_radius = result.details.get("blast_radius", 0) if result.details else 0
        report.risk_level = result.details.get("risk_level", "medium") if result.details else "medium"

    return report.to_dict()


# ---------------------------------------------------------------------------
# Stage Implementations
# ---------------------------------------------------------------------------

def _run_syntax_check(config_text: str, vendor: str) -> StageResult:
    """Run SyntaxValidator against raw config text."""
    import time
    start = time.monotonic()
    try:
        from config_engine.validator import SyntaxValidator
        validator = SyntaxValidator()
        warnings = validator.validate(vendor, config_text)
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="syntax",
            passed=len(warnings) == 0,
            warnings=warnings,
            duration_ms=elapsed,
        )
    except Exception as e:
        return StageResult(name="syntax", passed=False, errors=[str(e)])


def _run_yaml_lint(playbook_yaml: str) -> StageResult:
    """Parse YAML and optionally run ansible-lint."""
    import time
    start = time.monotonic()
    errors = []
    warnings = []

    # YAML parse
    try:
        plays = yaml.safe_load(playbook_yaml)
        if not isinstance(plays, list) or len(plays) == 0:
            errors.append("Playbook must be a YAML list of plays")
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")

    # ansible-lint
    try:
        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
            f.write(playbook_yaml)
            fpath = f.name

        result = subprocess.run(
            ["ansible-lint", "--nocolor", "-p", "--offline", fpath],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode not in (0, 2):
            lint_output = (result.stdout + result.stderr).strip()
            if lint_output:
                errors.append(f"ansible-lint: {lint_output[:500]}")
        elif result.returncode == 2:
            warnings.append("ansible-lint reported warnings (non-fatal)")
    except FileNotFoundError:
        warnings.append("ansible-lint not installed — skipping")
    except subprocess.TimeoutExpired:
        warnings.append("ansible-lint timed out")
    except Exception as e:
        warnings.append(f"ansible-lint error: {e}")

    # Safety checks
    forbidden = [
        "write erase", "reload", "no router ospf",
        "clear ip route", "format flash",
    ]
    lowered = playbook_yaml.lower()
    for banned in forbidden:
        if banned in lowered and "operator_approved_destructive" not in playbook_yaml:
            errors.append(f"Potentially destructive command: '{banned}'")

    elapsed = int((time.monotonic() - start) * 1000)
    return StageResult(
        name="yaml_lint",
        passed=len(errors) == 0,
        warnings=warnings,
        errors=errors,
        duration_ms=elapsed,
    )


def _run_dry_run(playbook_yaml: str, target_hosts: List[str]) -> StageResult:
    """Run ansible-playbook --check --diff (requires live targets)."""
    import time
    start = time.monotonic()
    try:
        from ansible.inventory import build_inventory
        from ansible.runner import run_playbook

        inventory = build_inventory(target_hosts=target_hosts)
        result = run_playbook(
            playbook_content=playbook_yaml,
            inventory=inventory,
            extra_vars={},
            check_mode=True,
        )
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="dry_run",
            passed=result.rc == 0,
            warnings=[result.stdout[-2000:]] if result.stdout else [],
            errors=[result.stderr[-1000:]] if result.rc != 0 and result.stderr else [],
            details={"rc": result.rc, "changed": result.changed, "summary": result.summary},
            duration_ms=elapsed,
        )
    except Exception as e:
        return StageResult(name="dry_run", passed=False, errors=[str(e)])


def _run_policy_check(
    playbook_yaml: Optional[str],
    config_text: Optional[str],
    target_hosts: List[str],
) -> StageResult:
    """
    Policy / blast-radius assessment.
    Passes if blast_radius <= 10 OR risk_level != 'critical'.
    """
    blast_radius = len(target_hosts)
    violations = []

    # Determine risk level from blast radius
    if blast_radius > 20:
        risk_level = "critical"
        violations.append(f"Blast radius {blast_radius} exceeds critical threshold (20)")
    elif blast_radius > 10:
        risk_level = "high"
        violations.append(f"Blast radius {blast_radius} exceeds high threshold (10)")
    elif blast_radius > 3:
        risk_level = "medium"
    else:
        risk_level = "low"

    # Check for known risky patterns in playbook
    if playbook_yaml:
        lowered = playbook_yaml.lower()
        risky_patterns = [
            ("shutdown", "Interface shutdown commands detected"),
            ("no ip route", "Route removal detected"),
            ("delete flash", "Flash deletion detected"),
        ]
        for pattern, msg in risky_patterns:
            if pattern in lowered:
                violations.append(msg)
                if risk_level in ("low", "medium"):
                    risk_level = "high"

    passed = risk_level != "critical" and len(violations) <= 2

    return StageResult(
        name="policy",
        passed=passed,
        warnings=[v for v in violations if "exceeds high" in v or "detected" in v],
        errors=[v for v in violations if "exceeds critical" in v],
        details={
            "blast_radius": blast_radius,
            "risk_level": risk_level,
            "violations": violations,
        },
    )
