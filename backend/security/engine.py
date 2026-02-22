"""
M13a: Security Audit Engine
=============================
Orchestrates running all applicable security checks against a device's
running configuration and computes a security score.
"""

import logging
from typing import List, Optional

from backend.security.models import AuditResult, SecurityAudit
from backend.security.rules import SECURITY_RULES, get_rules_for_vendor
from backend.security.checks.cisco_ios import CHECK_REGISTRY

logger = logging.getLogger("security.engine")


class SecurityAuditEngine:
    """
    Runs CIS-style security audits against device running configs.

    Usage::

        engine = SecurityAuditEngine()
        audit = engine.run_audit(device_id=1, config_text="...", vendor="cisco_ios")
        print(f"Score: {audit.score}%")
        for r in audit.results:
            print(f"  [{r.status}] {r.rule.id}: {r.rule.description}")
    """

    def __init__(self):
        self._check_registry = CHECK_REGISTRY

    def run_audit(
        self,
        device_id: int,
        config_text: str,
        vendor: str = "cisco_ios",
    ) -> SecurityAudit:
        """
        Run all applicable security checks for a device.

        Args:
            device_id:   Database ID of the device.
            config_text: The device's running configuration text.
            vendor:      Vendor type (cisco_ios, huawei_vrp, juniper_junos).

        Returns:
            SecurityAudit with score and individual results.
        """
        rules = get_rules_for_vendor(vendor)
        results: List[AuditResult] = []

        for rule in rules:
            check_fn = self._check_registry.get(rule.check_fn)
            if check_fn is None:
                logger.warning("No check function '%s' for rule %s", rule.check_fn, rule.id)
                results.append(AuditResult(
                    rule=rule, status="skip",
                    evidence=f"Check function '{rule.check_fn}' not implemented",
                ))
                continue

            try:
                result = check_fn(config_text, vendor)
                # Attach the actual rule (check functions use a dummy)
                result.rule = rule
                results.append(result)
            except Exception as e:
                logger.error("Check %s failed: %s", rule.id, e)
                results.append(AuditResult(
                    rule=rule, status="error",
                    evidence=str(e),
                ))

        # Compute score
        total = len(results)
        passed = sum(1 for r in results if r.status == "pass")
        failed = sum(1 for r in results if r.status == "fail")
        critical = sum(1 for r in results if r.status == "fail" and r.rule.severity == "critical")
        high = sum(1 for r in results if r.status == "fail" and r.rule.severity == "high")
        medium = sum(1 for r in results if r.status == "fail" and r.rule.severity == "medium")
        low = sum(1 for r in results if r.status == "fail" and r.rule.severity == "low")

        score = (passed / total * 100) if total > 0 else 0.0

        return SecurityAudit(
            device_id=device_id,
            score=round(score, 1),
            total_checks=total,
            passed=passed,
            failed=failed,
            critical=critical,
            high=high,
            medium=medium,
            low=low,
            results=results,
        )

    def run_audit_all(self, db) -> List[SecurityAudit]:
        """
        Run security audits on all devices with stored configs.

        Args:
            db: DatabaseManager instance.

        Returns:
            List of SecurityAudit results.
        """
        audits = []
        devices = db.get_all_devices()

        for device in devices:
            # Get the latest config snapshot
            history = db.get_config_history(device.id, limit=1)
            if not history:
                logger.info("No config for device %s, skipping audit", device.hostname)
                continue

            config_text = history[0].config_text
            vendor = device.vendor or "cisco_ios"

            audit = self.run_audit(device.id, config_text, vendor)
            audits.append(audit)
            logger.info("Audited %s: score=%.1f%% (%d/%d passed)",
                         device.hostname, audit.score, audit.passed, audit.total_checks)

        return audits
