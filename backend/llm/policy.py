"""
Stage 2 — Policy & Validation Engine.

Sits between the Intent Parser (Stage 1) and the Automation Generator
(Stage 3).  Every structured intent passes through this gate before any
config is generated or executed.

Checks performed:
  1. RBAC — does the user's role allow this action?
  2. Change window — is the change within the approved window?
  3. Pre-flight — does the target device exist and is it reachable?
  4. Naming policy — VLAN names, ACL names, etc.
  5. Feature restrictions — e.g. no BGP changes on access switches
  6. Blast radius — how many devices / links are affected?
  7. Conflict detection — VLAN ID already exists, IP overlap, etc.

Rules are loaded from ``policy_rules.yaml`` and can be extended.

Key principle: The LLM interprets. This engine **enforces**.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from llm.intent import ParsedIntent, IntentType, RiskLevel

logger = logging.getLogger("llm.policy")

_RULES_PATH = Path(__file__).resolve().parent / "policy_rules.yaml"


# -----------------------------------------------------------------------
# Policy models
# -----------------------------------------------------------------------

class PolicyViolation(BaseModel):
    rule_id: str
    severity: str = "error"          # error | warning
    message: str = ""


class BlastRadius(BaseModel):
    """Quantifies the scope of impact."""
    device_count: int = 0
    interface_count: int = 0
    link_count: int = 0
    level: str = "low"               # low | medium | high | critical

    def compute_level(self) -> str:
        if self.device_count >= 10:
            return "critical"
        if self.device_count >= 5:
            return "high"
        if self.device_count >= 2:
            return "medium"
        return "low"


class PolicyResult(BaseModel):
    """Outcome of the policy check."""
    approved: bool = True
    violations: List[PolicyViolation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    blast_radius: BlastRadius = Field(default_factory=BlastRadius)
    checked_rules: int = 0
    checked_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# -----------------------------------------------------------------------
# Rule definitions (loaded from YAML)
# -----------------------------------------------------------------------

class PolicyRule(BaseModel):
    rule_id: str
    name: str
    rule_type: str                  # rbac | change_window | naming | feature_restriction | blast_radius | conflict
    enabled: bool = True
    params: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------
# Policy Engine
# -----------------------------------------------------------------------

class PolicyEngine:
    """
    Declarative policy engine that validates structured intents against
    a set of configurable rules.

    Usage::

        engine = PolicyEngine(db_manager=db)
        result = engine.validate(intent, user_role="operator")
    """

    def __init__(self, db_manager=None):
        self._db = db_manager
        self._rules = self._load_rules()
        logger.info("PolicyEngine loaded %d rules", len(self._rules))

    # ----- Public API -----

    def validate(self, intent_json: str, user_role: str = "operator") -> str:
        """
        Validate a structured intent against all enabled policy rules.

        Args:
            intent_json:  JSON-encoded ``ParsedIntent``.
            user_role:    Authenticated user's role (admin/operator/auditor).

        Returns:
            JSON-encoded ``PolicyResult``.
        """
        intent = ParsedIntent.model_validate_json(intent_json)
        result = PolicyResult()

        # Non-config intents pass automatically
        if not intent.is_config_change():
            result.checked_rules = 0
            result.warnings.append("Read-only intent — no policy checks required.")
            return json.dumps(result.model_dump(), default=str)

        # Run each enabled rule
        for rule in self._rules:
            if not rule.enabled:
                continue

            checker = self._get_checker(rule.rule_type)
            if not checker:
                continue

            violations, warnings = checker(intent, rule, user_role)
            result.violations.extend(violations)
            result.warnings.extend(warnings)
            result.checked_rules += 1

        # Compute blast radius
        result.blast_radius = self._compute_blast_radius(intent)
        br_limit = self._get_rule_param("blast_radius_limit", "max_devices", 10)
        if result.blast_radius.device_count > br_limit:
            result.violations.append(PolicyViolation(
                rule_id="blast_radius_limit",
                severity="error",
                message=(
                    f"Blast radius ({result.blast_radius.device_count} devices) exceeds "
                    f"maximum allowed ({br_limit}). Reduce scope or request admin override."
                ),
            ))

        # Final verdict
        errors = [v for v in result.violations if v.severity == "error"]
        result.approved = len(errors) == 0

        logger.info(
            "Policy check: approved=%s violations=%d warnings=%d blast=%s",
            result.approved, len(result.violations), len(result.warnings),
            result.blast_radius.level,
        )
        return json.dumps(result.model_dump(), default=str)

    # ----- Rule loaders -----

    def _load_rules(self) -> List[PolicyRule]:
        """Load rules from the YAML file."""
        if not _RULES_PATH.exists():
            logger.warning("Policy rules file not found: %s — using defaults", _RULES_PATH)
            return self._default_rules()

        try:
            data = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
            rules = []
            for r in data.get("rules", []):
                rules.append(PolicyRule(**r))
            return rules
        except Exception as e:
            logger.error("Failed to load policy rules: %s", e)
            return self._default_rules()

    def _default_rules(self) -> List[PolicyRule]:
        """Hard-coded defaults if YAML is missing."""
        return [
            PolicyRule(rule_id="rbac_basic", name="RBAC Check", rule_type="rbac"),
            PolicyRule(rule_id="change_window", name="Change Window", rule_type="change_window",
                       params={"allowed_hours_utc": [0, 6], "block_weekends": False}),
            PolicyRule(rule_id="naming_policy", name="Naming Convention", rule_type="naming"),
            PolicyRule(rule_id="blast_radius_limit", name="Blast Radius Limit", rule_type="blast_radius",
                       params={"max_devices": 10}),
        ]

    def _get_rule_param(self, rule_id: str, key: str, default: Any = None) -> Any:
        for rule in self._rules:
            if rule.rule_id == rule_id:
                return rule.params.get(key, default)
        return default

    # ----- Checker dispatch -----

    def _get_checker(self, rule_type: str):
        checkers = {
            "rbac": self._check_rbac,
            "change_window": self._check_change_window,
            "naming": self._check_naming,
            "feature_restriction": self._check_feature_restriction,
            "conflict": self._check_conflict,
            "preflight": self._check_preflight,
        }
        return checkers.get(rule_type)

    # ----- Individual checkers -----

    def _check_rbac(
        self, intent: ParsedIntent, rule: PolicyRule, user_role: str,
    ) -> tuple[List[PolicyViolation], List[str]]:
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        if user_role == "auditor":
            violations.append(PolicyViolation(
                rule_id=rule.rule_id,
                severity="error",
                message="Auditor role cannot make configuration changes.",
            ))
            return violations, warnings

        # Operator restrictions
        high_risk_features = rule.params.get(
            "operator_blocked_features",
            ["bgp", "acl", "firewall", "nat"],
        )
        if user_role == "operator" and intent.feature in high_risk_features:
            violations.append(PolicyViolation(
                rule_id=rule.rule_id,
                severity="error",
                message=(
                    f"Operator role cannot modify '{intent.feature}'. "
                    f"Admin approval required for: {', '.join(high_risk_features)}."
                ),
            ))

        return violations, warnings

    def _check_change_window(
        self, intent: ParsedIntent, rule: PolicyRule, user_role: str,
    ) -> tuple[List[PolicyViolation], List[str]]:
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        # Admin overrides change window
        if user_role == "admin":
            return violations, warnings

        now = datetime.now(timezone.utc)
        allowed = rule.params.get("allowed_hours_utc", [0, 6])
        start_h, end_h = allowed[0], allowed[1]

        # Check weekend
        if rule.params.get("block_weekends", False) and now.weekday() >= 5:
            violations.append(PolicyViolation(
                rule_id=rule.rule_id,
                severity="error",
                message="Configuration changes are blocked on weekends. Contact admin for override.",
            ))
            return violations, warnings

        # Check hour window (e.g. allow 00:00–06:00 UTC)
        if not (start_h <= now.hour < end_h):
            warnings.append(
                f"Outside preferred change window ({start_h:02d}:00–{end_h:02d}:00 UTC). "
                f"Current time: {now.strftime('%H:%M')} UTC. Proceed with caution."
            )

        return violations, warnings

    def _check_naming(
        self, intent: ParsedIntent, rule: PolicyRule, user_role: str,
    ) -> tuple[List[PolicyViolation], List[str]]:
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        pattern = rule.params.get("name_pattern", r"^[A-Za-z][A-Za-z0-9_-]{2,30}$")

        # Check VLAN names
        if intent.feature == "vlan":
            vlans = intent.parameters.get("vlans", [])
            for v in vlans:
                name = v.get("name", "")
                if name and not re.match(pattern, name):
                    violations.append(PolicyViolation(
                        rule_id=rule.rule_id,
                        severity="error",
                        message=(
                            f"VLAN name '{name}' violates naming policy. "
                            f"Must match: {pattern}"
                        ),
                    ))

        # Check ACL names
        if intent.feature == "acl":
            acl_name = intent.parameters.get("name", "")
            if acl_name and not re.match(pattern, acl_name):
                violations.append(PolicyViolation(
                    rule_id=rule.rule_id,
                    severity="error",
                    message=f"ACL name '{acl_name}' violates naming policy.",
                ))

        return violations, warnings

    def _check_feature_restriction(
        self, intent: ParsedIntent, rule: PolicyRule, user_role: str,
    ) -> tuple[List[PolicyViolation], List[str]]:
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        restricted = rule.params.get("restricted_features", [])
        device_pattern = rule.params.get("device_pattern", "")

        if intent.feature in restricted and device_pattern:
            for dev in intent.resolved_devices:
                if re.match(device_pattern, dev, re.IGNORECASE):
                    violations.append(PolicyViolation(
                        rule_id=rule.rule_id,
                        severity="error",
                        message=(
                            f"Feature '{intent.feature}' is restricted on device "
                            f"'{dev}' (matches pattern '{device_pattern}')."
                        ),
                    ))

        return violations, warnings

    def _check_conflict(
        self, intent: ParsedIntent, rule: PolicyRule, user_role: str,
    ) -> tuple[List[PolicyViolation], List[str]]:
        """Check for VLAN ID conflicts, IP overlap, etc."""
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        if not self._db:
            return violations, warnings

        # VLAN conflict check
        if intent.feature == "vlan":
            vlans = intent.parameters.get("vlans", [])
            for v in vlans:
                vlan_id = v.get("id")
                if vlan_id:
                    existing = self._check_vlan_exists(vlan_id, intent.resolved_devices)
                    if existing:
                        warnings.append(
                            f"VLAN {vlan_id} may already exist on: {', '.join(existing)}. "
                            f"Applying will overwrite the existing configuration."
                        )

        # IP conflict check for interface configs
        if intent.feature == "interface":
            ip = intent.parameters.get("ip_address", "")
            if ip:
                conflict_dev = self._check_ip_conflict(ip)
                if conflict_dev:
                    violations.append(PolicyViolation(
                        rule_id=rule.rule_id,
                        severity="error",
                        message=f"IP {ip} is already assigned on device '{conflict_dev}'.",
                    ))

        return violations, warnings

    def _check_preflight(
        self, intent: ParsedIntent, rule: PolicyRule, user_role: str,
    ) -> tuple[List[PolicyViolation], List[str]]:
        """Verify target devices exist and are reachable."""
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        if not self._db:
            return violations, warnings

        inventory_missing_as_warning = rule.params.get("inventory_missing_as_warning", False)

        for dev in intent.resolved_devices:
            try:
                device = self._db.get_device_by_hostname(dev)
                if not device:
                    if inventory_missing_as_warning:
                        warnings.append(
                            f"Device '{dev}' not in Device Inventory — add it before running the playbook."
                        )
                    else:
                        violations.append(PolicyViolation(
                            rule_id=rule.rule_id,
                            severity="error",
                            message=f"Device '{dev}' not found in inventory.",
                        ))
                elif device.status == "down":
                    violations.append(PolicyViolation(
                        rule_id=rule.rule_id,
                        severity="error",
                        message=f"Device '{dev}' is DOWN — cannot apply changes.",
                    ))
                elif device.status == "unknown":
                    warnings.append(f"Device '{dev}' status is UNKNOWN — verify reachability first.")
            except Exception as e:
                logger.warning("Pre-flight check failed for %s: %s", dev, e)

        return violations, warnings

    # ----- Blast radius -----

    def _compute_blast_radius(self, intent: ParsedIntent) -> BlastRadius:
        br = BlastRadius(device_count=len(intent.resolved_devices))

        # Estimate interface / link impact from topology
        if self._db:
            topo = self._db.get_topology()
            nodes = topo.get("nodes", {}) if topo else {}
            for dev in intent.resolved_devices:
                node = nodes.get(dev, {})
                if isinstance(node, dict):
                    edges = node.get("edgeLink", {})
                    br.link_count += len(edges) if isinstance(edges, dict) else 0
                    ifaces = node.get("interfaceList", [{}])
                    iface_dict = ifaces[0] if ifaces else {}
                    br.interface_count += len(iface_dict) if isinstance(iface_dict, dict) else 0

        br.level = br.compute_level()
        return br

    # ----- DB helpers -----

    def _check_vlan_exists(self, vlan_id: int, device_hostnames: List[str]) -> List[str]:
        """Check if a VLAN ID already exists on any of the target devices by
        inspecting the last config snapshot for each device."""
        found_on: List[str] = []
        if not self._db:
            return found_on

        try:
            for hostname in device_hostnames:
                device = self._db.get_device_by_hostname(hostname)
                if not device:
                    continue
                history = self._db.get_config_history(device.id, limit=1)
                if not history:
                    continue
                config_text = history[0].config_text or ""
                # Cisco: "vlan 30", Huawei: "vlan 30", Junos: "vlan-id 30"
                if re.search(rf"\bvlan[\s-]?{vlan_id}\b", config_text, re.IGNORECASE):
                    found_on.append(hostname)
        except Exception as e:
            logger.warning("VLAN existence check failed: %s", e)

        return found_on

    def _check_ip_conflict(self, ip_address: str) -> Optional[str]:
        """Check if an IP address is already assigned to any interface."""
        if not self._db:
            return None
        try:
            with self._db._connect() as conn:
                row = conn.execute(
                    """
                    SELECT d.hostname FROM interfaces i
                    JOIN devices d ON d.id = i.device_id
                    WHERE i.ip_address = ?
                    LIMIT 1
                    """,
                    (ip_address,),
                ).fetchone()
                return row["hostname"] if row else None
        except Exception:
            return None
