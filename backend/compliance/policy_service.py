from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


_CATEGORY_ALLOWLIST = {
    "interface",
    "vlan",
    "ospf",
    "static_route",
    "acl",
    "snmp",
    "logging",
    "ntp",
}

_RULE_ACTION_ALLOWLIST = {
    "must_exist",
    "must_not_exist",
    "must_equal",
    "must_contain",
    "must_match_regex",
    "must_be_in_range",
    "must_be_in_set",
    "must_be_absent",
    "must_be_on_device_role",
    "must_not_be_on_device_role",
}

_SEVERITY_ALLOWLIST = {"low", "medium", "high", "critical"}


def _norm_key(s: str) -> str:
    return str(s or "").strip().lower().replace("-", "_")


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _policy_id_fallback(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return base[:64] or "policy"


@dataclass
class PolicyValidationResult:
    ok: bool
    issues: List[str]
    normalized: Optional[Dict[str, Any]] = None


class PolicyService:
    """Validates/normalizes policy objects and does basic scope filtering."""

    def validate_and_normalize(self, payload: Dict[str, Any]) -> PolicyValidationResult:
        issues: List[str] = []
        if not isinstance(payload, dict):
            return PolicyValidationResult(False, ["policy payload must be an object"], None)

        policy_id = str(payload.get("policy_id") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not policy_id:
            if name:
                policy_id = _policy_id_fallback(name)
            else:
                issues.append("policy_id is required (or provide name)")

        if not name:
            issues.append("name is required")

        category = _norm_key(payload.get("category") or "")
        if not category:
            issues.append("category is required")
        elif category not in _CATEGORY_ALLOWLIST:
            issues.append(f"category must be one of: {sorted(_CATEGORY_ALLOWLIST)}")

        scope = payload.get("scope") or {}
        if not isinstance(scope, dict):
            issues.append("scope must be an object")
            scope = {}

        rules = payload.get("rules")
        if not isinstance(rules, list) or not rules:
            issues.append("rules must be a non-empty array")
            rules = []

        norm_rules: List[Dict[str, Any]] = []
        for i, r in enumerate(rules):
            if not isinstance(r, dict):
                issues.append(f"rules[{i}] must be an object")
                continue
            action = _norm_key(r.get("action") or r.get("type") or "")
            if action not in _RULE_ACTION_ALLOWLIST:
                issues.append(f"rules[{i}].action must be one of: {sorted(_RULE_ACTION_ALLOWLIST)}")
            target = r.get("target")
            path = r.get("path") or r.get("cli_path") or r.get("object_path")
            expected = r.get("expected")
            severity = _norm_key(r.get("severity") or "medium")
            if severity not in _SEVERITY_ALLOWLIST:
                issues.append(f"rules[{i}].severity must be one of: {sorted(_SEVERITY_ALLOWLIST)}")
            if action in ("must_equal", "must_be_in_range", "must_be_in_set") and expected is None:
                issues.append(f"rules[{i}].expected is required for action={action}")
            if action in ("must_be_on_device_role", "must_not_be_on_device_role") and not r.get("role"):
                issues.append(f"rules[{i}].role is required for action={action}")
            if target is None and path is None:
                issues.append(f"rules[{i}] must include target or path")
            norm_rules.append(
                {
                    "id": str(r.get("id") or f"r{i+1}"),
                    "action": action,
                    "target": target,
                    "path": path,
                    "expected": expected,
                    "match_method": _norm_key(r.get("match_method") or r.get("match") or "line"),
                    "operator": _norm_key(r.get("operator") or ""),
                    "severity": severity,
                    "explanation": str(r.get("explanation") or r.get("note") or "").strip(),
                    "remediation": str(r.get("remediation") or "").strip(),
                    "role": str(r.get("role") or "").strip() or None,
                }
            )

        enabled = bool(payload.get("enabled", True))
        description = str(payload.get("description") or "").strip()
        normalization = payload.get("normalization") or {}
        thresholds = payload.get("thresholds") or {}
        if normalization is not None and not isinstance(normalization, dict):
            issues.append("normalization must be an object")
            normalization = {}
        if thresholds is not None and not isinstance(thresholds, dict):
            issues.append("thresholds must be an object")
            thresholds = {}

        normalized = {
            "policy_id": policy_id,
            "name": name,
            "description": description,
            "category": category,
            "scope": {
                "vendors": [str(x) for x in _as_list(scope.get("vendors")) if str(x).strip()],
                "device_roles": [str(x) for x in _as_list(scope.get("device_roles")) if str(x).strip()],
                "device_groups": [str(x) for x in _as_list(scope.get("device_groups")) if str(x).strip()],
                "devices": [str(x) for x in _as_list(scope.get("devices")) if str(x).strip()],
            },
            "rules": norm_rules,
            "normalization": normalization or {},
            "thresholds": thresholds or {},
            "enabled": enabled,
        }

        return PolicyValidationResult(ok=(len(issues) == 0), issues=issues, normalized=normalized if len(issues) == 0 else None)

    def device_in_scope(self, device: Dict[str, Any], scope: Dict[str, Any]) -> bool:
        """Best-effort scope match using local DB fields (vendor, role, hostname).

        NetBox groups are not stored in local DB by default; device_groups is ignored
        unless device includes a 'groups' list.
        """
        if not isinstance(scope, dict):
            return True
        vendors = {str(x).strip().lower() for x in _as_list(scope.get("vendors")) if str(x).strip()}
        roles = {str(x).strip().lower() for x in _as_list(scope.get("device_roles")) if str(x).strip()}
        devices = {str(x).strip().lower() for x in _as_list(scope.get("devices")) if str(x).strip()}
        groups = {str(x).strip().lower() for x in _as_list(scope.get("device_groups")) if str(x).strip()}

        hostname = str(device.get("hostname") or "").strip().lower()
        vendor = str(device.get("vendor") or "").strip().lower()
        role = str(device.get("role") or "").strip().lower()
        netbox_role_slug = str(device.get("netbox_role_slug") or "").strip().lower()

        if devices and hostname and hostname not in devices:
            return False
        if vendors and vendor and vendor not in vendors:
            return False
        if roles:
            # device_roles can refer to either local role keys or NetBox role slugs.
            if role and role in roles:
                pass
            elif netbox_role_slug and netbox_role_slug in roles:
                pass
            else:
                return False
        if groups:
            dgroups = device.get("groups") or []
            if isinstance(dgroups, str):
                dgroups = [dgroups]
            # Accept device_groups synthesized by NetBox scope resolver
            if isinstance(device.get("device_groups"), list):
                dgroups = list(dgroups) + list(device.get("device_groups") or [])
            if not any(str(g).strip().lower() in groups for g in (dgroups or [])):
                return False
        return True

    def severity_weight(self, severity: str) -> int:
        s = _norm_key(severity)
        if s == "critical":
            return 40
        if s == "high":
            return 30
        if s == "medium":
            return 15
        return 5

