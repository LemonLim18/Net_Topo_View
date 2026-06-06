from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .normalizer import ConfigNormalizer, NormalizedConfig
from .policy_service import PolicyService
from .parser import parse, ParsedKey


def _norm(s: str) -> str:
    return str(s or "").strip()


def _norm_l(s: str) -> str:
    return _norm(s).lower()


def _severity_rank(sev: str) -> int:
    s = _norm_l(sev)
    if s == "critical":
        return 4
    if s == "high":
        return 3
    if s == "medium":
        return 2
    return 1


def _max_severity(a: str, b: str) -> str:
    return a if _severity_rank(a) >= _severity_rank(b) else b


def _key_for_line(line: str) -> str:
    """Heuristic key for changed-line pairing.

    The goal is to turn baseline+actual pairs into `changed_lines` when the same
    config object exists but with different value.
    """
    toks = [t for t in _norm(line).split(" ") if t]
    if not toks:
        return ""
    t1 = toks[0].lower()
    t2 = toks[1].lower() if len(toks) > 1 else ""
    if t1 in ("ip", "ipv6", "router", "interface", "line", "logging", "snmp-server", "ntp", "aaa"):
        return f"{t1} {t2}".strip()
    return t1


@dataclass
class DiffResult:
    missing_lines: List[str]
    extra_lines: List[str]
    changed_lines: List[Dict[str, str]]  # {key, baseline, actual}
    drift_pct: float


class ComplianceEvaluator:
    def __init__(self):
        self.normalizer = ConfigNormalizer()
        self.policy = PolicyService()

    def diff(
        self,
        baseline: NormalizedConfig,
        actual: NormalizedConfig,
        vendor: Optional[str] = None,
    ) -> DiffResult:
        # Parsed view for stronger changed-line pairing.
        pb = parse(vendor, baseline.lines)
        pa = parse(vendor, actual.lines)

        b_lines = set(baseline.lines)
        a_lines = set(actual.lines)
        missing = sorted([l for l in baseline.lines if l not in a_lines])
        extra = sorted([l for l in actual.lines if l not in b_lines])

        changed: List[Dict[str, str]] = []
        missing_set = set(missing)
        extra_set = set(extra)

        # Use parsed keys to pair value changes.
        for k in sorted(set(pb.objects.keys()) & set(pa.objects.keys()), key=lambda x: (x.section_type, x.section_id, x.command)):
            bl = pb.objects.get(k)
            al = pa.objects.get(k)
            if not bl or not al:
                continue
            if bl != al and bl in missing_set and al in extra_set:
                changed.append({"key": f"{k.section_type}:{k.section_id}:{k.command}", "baseline": bl, "actual": al})
                missing_set.discard(bl)
                extra_set.discard(al)

        missing_left = [l for l in missing if l in missing_set]
        extra_left = [l for l in extra if l in extra_set]

        denom = max(len(baseline.lines), 1)
        drift_pct = round(((len(missing_left) + len(extra_left) + len(changed)) / denom) * 100.0, 2)

        return DiffResult(
            missing_lines=missing_left,
            extra_lines=extra_left,
            changed_lines=changed,
            drift_pct=drift_pct,
        )

    def evaluate_policies(
        self,
        policies: List[Dict[str, Any]],
        device: Dict[str, Any],
        normalized_actual: NormalizedConfig,
    ) -> List[Dict[str, Any]]:
        """Evaluate policy rules against normalized actual config."""
        applicable = []
        for p in policies:
            if not p or not isinstance(p, dict):
                continue
            if not bool(p.get("enabled", True)):
                continue
            if self.policy.device_in_scope(device, p.get("scope") or {}):
                applicable.append(p)

        text = normalized_actual.text
        lines = normalized_actual.lines

        violations: List[Dict[str, Any]] = []
        for p in applicable:
            for r in (p.get("rules") or []):
                if not isinstance(r, dict):
                    continue
                action = _norm_l(r.get("action") or "")
                sev = _norm_l(r.get("severity") or "medium")
                rule_id = r.get("id")
                target = r.get("target")
                path = r.get("path")
                expected = r.get("expected")
                role = _norm_l(r.get("role") or "")

                needle = _norm(target or path or "")
                if not needle and action not in ("must_be_on_device_role", "must_not_be_on_device_role"):
                    continue

                def add(msg: str, evidence: str = ""):
                    violations.append(
                        {
                            "policy_id": p.get("policy_id"),
                            "policy_name": p.get("name"),
                            "rule_id": rule_id,
                            "severity": sev,
                            "message": msg,
                            "evidence": evidence,
                            "category": p.get("category"),
                        }
                    )

                # Role gating rules
                if action == "must_be_on_device_role":
                    if role and _norm_l(device.get("role") or "") != role:
                        add(f"device role must be '{role}'", evidence=str(device.get("role") or ""))
                    continue
                if action == "must_not_be_on_device_role":
                    if role and _norm_l(device.get("role") or "") == role:
                        add(f"device role must not be '{role}'", evidence=str(device.get("role") or ""))
                    continue

                if action in ("must_exist", "must_contain"):
                    if needle not in text:
                        add(f"missing required: {needle}")
                    continue

                if action in ("must_not_exist", "must_be_absent"):
                    if needle in text:
                        add(f"forbidden present: {needle}", evidence=needle)
                    continue

                if action == "must_match_regex":
                    try:
                        if not re.search(str(needle), text, re.M):
                            add(f"regex not matched: {needle}")
                    except Exception:
                        add(f"invalid regex: {needle}", evidence=str(needle))
                    continue

                if action == "must_equal":
                    # Prefer parsed-object matching when possible (vendor-aware section keys).
                    prefix = needle
                    found = next((ln for ln in lines if ln.startswith(prefix)), None)
                    if not found:
                        add(f"missing required setting: {needle}")
                        continue
                    actual_value = found[len(prefix):].strip()
                    if expected is None:
                        add(f"expected is required for must_equal: {needle}")
                        continue
                    if str(actual_value) != str(expected).strip():
                        add(f"value mismatch for {needle}: expected '{expected}' got '{actual_value}'", evidence=found)
                    continue

                if action == "must_be_in_set":
                    prefix = needle
                    found = next((ln for ln in lines if ln.startswith(prefix)), None)
                    if not found:
                        add(f"missing required setting: {needle}")
                        continue
                    actual_value = found[len(prefix):].strip()
                    allowed = expected if isinstance(expected, list) else [expected]
                    allowed_s = {str(x).strip() for x in allowed if str(x).strip()}
                    if actual_value not in allowed_s:
                        add(f"value not allowed for {needle}: '{actual_value}' not in {sorted(allowed_s)}", evidence=found)
                    continue

                if action == "must_be_in_range":
                    prefix = needle
                    found = next((ln for ln in lines if ln.startswith(prefix)), None)
                    if not found:
                        add(f"missing required setting: {needle}")
                        continue
                    actual_value = found[len(prefix):].strip()
                    try:
                        val = float(actual_value)
                    except Exception:
                        add(f"value not numeric for {needle}: '{actual_value}'", evidence=found)
                        continue
                    lo = None
                    hi = None
                    if isinstance(expected, dict):
                        lo = expected.get("min")
                        hi = expected.get("max")
                    elif isinstance(expected, (list, tuple)) and len(expected) >= 2:
                        lo, hi = expected[0], expected[1]
                    try:
                        if lo is not None and val < float(lo):
                            add(f"value too low for {needle}: {val} < {lo}", evidence=found)
                        elif hi is not None and val > float(hi):
                            add(f"value too high for {needle}: {val} > {hi}", evidence=found)
                    except Exception:
                        add(f"invalid range for {needle}: {expected}", evidence=str(expected))
                    continue

        return violations

    def score_and_classify(
        self,
        diff: DiffResult,
        violations: List[Dict[str, Any]],
    ) -> Tuple[int, str, str]:
        """Returns (score, severity, status)."""
        score = 100
        severity = "low"

        if violations:
            status = "violated"
            for v in violations:
                sev = _norm_l(v.get("severity") or "medium")
                severity = _max_severity(severity, sev)
                score -= self.policy.severity_weight(sev)
        else:
            has_drift = bool(diff.missing_lines or diff.extra_lines or diff.changed_lines)
            status = "drifted" if has_drift else "compliant"
            # Drift-only penalty & severity by drift percentage
            if has_drift:
                if diff.drift_pct >= 10:
                    severity = "high"
                    score -= 30
                elif diff.drift_pct >= 1:
                    severity = "medium"
                    score -= 15
                else:
                    severity = "low"
                    score -= 5

        if score < 0:
            score = 0
        return score, severity, status

    def evaluate_device(
        self,
        *,
        device: Dict[str, Any],
        baseline_raw: str,
        actual_raw: str,
        vendor: Optional[str],
        policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Full evaluate: normalize -> diff -> policy -> score/status."""
        # Policy-driven ignore patterns: merge all applicable patterns
        ignore: List[str] = []
        for p in policies:
            if not isinstance(p, dict):
                continue
            if not bool(p.get("enabled", True)):
                continue
            if not self.policy.device_in_scope(device, p.get("scope") or {}):
                continue
            norm = p.get("normalization") or {}
            pats = norm.get("ignore_patterns") if isinstance(norm, dict) else None
            if isinstance(pats, list):
                ignore.extend([str(x) for x in pats if str(x).strip()])

        baseline_n = self.normalizer.normalize(baseline_raw or "", vendor=vendor, ignore_patterns=ignore or None)
        actual_n = self.normalizer.normalize(actual_raw or "", vendor=vendor, ignore_patterns=ignore or None)

        d = self.diff(baseline_n, actual_n, vendor=vendor)
        violations = self.evaluate_policies(policies, device, actual_n)
        score, severity, status = self.score_and_classify(d, violations)

        reason = ""
        if status == "compliant":
            reason = "baseline and actual match after normalization; no policy violations"
        elif status == "drifted":
            reason = f"config drift detected after normalization (drift_pct={d.drift_pct}%)"
        else:
            reason = f"policy violations detected ({len(violations)} rule(s))"

        return {
            "status": status,
            "reason": reason,
            "compliance_score": score,
            "severity": severity,
            "drift_pct": d.drift_pct,
            "missing_lines": d.missing_lines,
            "extra_lines": d.extra_lines,
            "changed_lines": d.changed_lines,
            "violated_policies": violations,
            "baseline_normalized": {"text": baseline_n.text, "removed_count": baseline_n.removed_count},
            "actual_normalized": {"text": actual_n.text, "removed_count": actual_n.removed_count},
        }

