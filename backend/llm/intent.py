"""
Stage 1 — Structured Intent Parser.

Converts free-form human language into a validated, machine-readable
intent object (JSON).  This is the first layer in the translation
pipeline:

    Human Language → **Structured Intent** → Policy Engine → Automation
                    ^^^^^^^^^^^^^^^^^^^^^^^^^
                    (this module)

The LLM is used ONLY for understanding — it never touches devices.
It outputs a strict Pydantic model.  Temperature = 0.

Key principle: the LLM interprets, the system enforces.
"""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("llm.intent")

_ROLE_ALIASES = {
    "access": "access",
    "access_switch": "access",
    "access-switch": "access",
    "switch": "access",
    "switches": "access",
    "distribution": "distribution",
    "distribution_switch": "distribution",
    "distribution-switch": "distribution",
    "dist": "distribution",
    "core": "core",
    "core_router": "core",
    "core-router": "core",
    "router": "core",
    "routers": "core",
    "firewall": "firewall",
    "network-device": "network-device",
}

_VENDOR_ALIASES = {
    "cisco": "cisco_ios",
    "cisco-ios": "cisco_ios",
    "cisco_ios": "cisco_ios",
    "cisco-xe": "cisco_ios",
    "cisco_xe": "cisco_ios",
    "juniper": "juniper_junos",
    "juniper-junos": "juniper_junos",
    "juniper_junos": "juniper_junos",
    "junos": "juniper_junos",
    "huawei": "huawei_vrp",
    "huawei-vrp": "huawei_vrp",
    "huawei_vrp": "huawei_vrp",
    "vrp": "huawei_vrp",
}


# -----------------------------------------------------------------------
# Intent Types
# -----------------------------------------------------------------------

class IntentType(str, Enum):
    """The kind of action the user wants."""
    CONFIGURE   = "configure"      # create/modify/delete config
    TROUBLESHOOT = "troubleshoot"  # diagnose an issue
    QUERY       = "query"          # read-only information request
    AUDIT       = "audit"          # security or compliance check
    ROLLBACK    = "rollback"       # undo a previous change
    DEPLOY      = "deploy"         # execute a pending change


class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    CRITICAL = "critical"


# -----------------------------------------------------------------------
# Parsed Intent (the structured output)
# -----------------------------------------------------------------------

class IntentScope(BaseModel):
    """Which devices / locations are targeted."""
    device_hostnames: List[str] = Field(default_factory=list, description="Explicit device hostnames")
    device_role: Optional[str] = Field(None, description="e.g. access_switch, core_router, distribution")
    building: Optional[str] = Field(None, description="Physical location filter")
    vendor_filter: Optional[str] = Field(None, description="e.g. cisco_ios, huawei_vrp")
    all_devices: bool = Field(False, description="Apply to every discovered device")


class ParsedIntent(BaseModel):
    """
    The canonical structured intent object produced by the parser.

    This replaces raw human language for all downstream stages.
    """
    id: str = ""
    intent_type: IntentType = IntentType.QUERY
    action: str = ""                              # e.g. "create_vlan", "configure_ospf"
    feature: str = ""                             # template feature key: vlan, ospf, bgp, ...
    vendor: str = ""                              # cisco_ios | huawei_vrp | juniper_junos
    parameters: Dict[str, Any] = Field(default_factory=dict)
    scope: IntentScope = Field(default_factory=IntentScope)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    justification: str = ""                       # why this change is needed
    raw_user_input: str = ""
    requires_disambiguation: bool = False
    disambiguation_message: str = ""
    resolved_devices: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_config_change(self) -> bool:
        return self.intent_type in (IntentType.CONFIGURE, IntentType.DEPLOY)


# -----------------------------------------------------------------------
# Prompt for constrained LLM parsing
# -----------------------------------------------------------------------

_PARSE_PROMPT = """\
You are an **intent parser** for a network automation controller.
Your job is to convert the user's natural-language request into a \
strict JSON object.  Output ONLY valid JSON — no commentary, no markdown fences.

## JSON Schema
{{
  "intent_type": "configure | troubleshoot | query | audit | rollback | deploy",
  "action": "<verb>_<noun>  e.g. create_vlan, configure_ospf, show_topology",
  "feature": "<template key>  one of: vlan, ospf, bgp, interface, acl, static_route, ntp, logging, banner, ssh_harden, qos, firewall  (or empty for non-config intents)",
  "vendor": "cisco_ios | huawei_vrp | juniper_junos  (or empty if not specified)",
  "parameters": {{ ... template parameters matching the feature schema ... }},
  "scope": {{
    "device_hostnames": ["R1", "SW1"],
    "device_role": "access_switch | core_router | distribution | null",
    "building": "A | B | null",
    "vendor_filter": "cisco_ios | null",
    "all_devices": false
  }},
  "risk_level": "low | medium | high | critical",
  "justification": "<one-line summary of why this change is needed>"
}}

## Available Templates & Their Required Parameters
{template_schemas}

## Current Network Topology
{topology_context}

## Rules
1. If the user mentions a specific device (e.g. "R1", "SW3"), put it in scope.device_hostnames.
2. If the user says "all switches" or "across building A", use device_role / building / all_devices.
3. **Scope by layer**: If the user says "access switches", "all access", "access layer only", set scope.device_role to "access_switch" and leave all_devices false. If they say "distribution" or "core" only, set device_role to "distribution" or "core_router" respectively. Use all_devices true only when they clearly mean the entire network (e.g. "all devices", "every switch in the network").
4. For CONFIGURE intents, you MUST fill in "feature" and "parameters" matching the template schema.
5. If you cannot determine the target device, set intent_type="query", action="clarify", \
   and put a clarifying question in justification.
6. risk_level heuristic: bgp/acl/firewall/nat = high; ospf/vlan/stp = medium; ntp/logging/banner = low.
7. For read-only requests (show topology, list devices, check status), use intent_type="query".
8. For security scan requests, use intent_type="audit".

## User Request
{user_message}

Output the JSON object now:
"""


# -----------------------------------------------------------------------
# Intent Parser
# -----------------------------------------------------------------------

class IntentParser:
    """
    Parses free-form chat messages into structured ``ParsedIntent`` objects.

    Uses Gemini (temperature=0) with a strict output-schema prompt.
    Falls back to a simple rule-based classifier for non-config intents.
    """

    def __init__(self, provider=None, db_manager=None):
        self._provider = provider
        self._db = db_manager

    # ----- public -----

    def parse(self, user_message: str, topology_context: str = "") -> str:
        """
        Parse a user message into a structured intent (returns JSON string).

        This is designed to be called as an LLM *tool* — so the return
        type is ``str`` (JSON-encoded ``ParsedIntent``).
        """
        import uuid
        import asyncio

        # Fast-path: simple queries that don't need LLM
        quick = self._try_rule_based(user_message)
        if quick:
            quick.id = str(uuid.uuid4())[:8]
            quick.raw_user_input = user_message
            return json.dumps(quick.model_dump(), default=str)

        # Build template schema context for the prompt
        template_schemas = self._get_template_schemas()

        prompt = _PARSE_PROMPT.format(
            template_schemas=template_schemas,
            topology_context=topology_context or "(no topology loaded)",
            user_message=user_message,
        )

        # Call LLM synchronously (we're inside a tool execution context)
        if self._provider:
            from llm.provider import LLMMessage
            loop: Any = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Already inside an async context — use a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self._call_llm(prompt))
                    raw_json = future.result(timeout=30)
            else:
                raw_json = asyncio.run(self._call_llm(prompt))
        else:
            # No provider — return a generic query intent
            raw_json = json.dumps({
                "intent_type": "query",
                "action": "unknown",
                "feature": "",
                "vendor": "",
                "parameters": {},
                "scope": {"device_hostnames": [], "all_devices": False},
                "risk_level": "low",
                "justification": user_message,
            })

        # Parse and validate
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            # LLM returned garbage — extract JSON substring
            data = self._extract_json(raw_json)

        intent = self._build_intent(data, user_message)
        intent.id = str(uuid.uuid4())[:8]

        # Resolve device scope against live topology
        intent.resolved_devices = self._resolve_devices(intent)

        if intent.is_config_change() and not intent.resolved_devices:
            intent.requires_disambiguation = True
            intent.disambiguation_message = (
                f"Could not resolve target devices for this intent. "
                f"Scope: {intent.scope.model_dump()}. "
                f"Please specify device hostnames explicitly (e.g. R1, SW1)."
            )

        logger.info(
            "Parsed intent: type=%s action=%s feature=%s devices=%s risk=%s",
            intent.intent_type, intent.action, intent.feature,
            intent.resolved_devices, intent.risk_level,
        )
        return json.dumps(intent.model_dump(), default=str)

    # ----- LLM call -----

    async def _call_llm(self, prompt: str) -> str:
        from llm.provider import LLMMessage
        response = await self._provider.generate(
            messages=[LLMMessage(role="user", content=prompt)],
            tools=None,
            temperature=0.0,
        )
        return response.content.strip()

    # ----- Rule-based fast path -----

    def _try_rule_based(self, msg: str) -> Optional[ParsedIntent]:
        """Handle obvious query/audit intents without LLM."""
        lower = msg.lower().strip()

        query_patterns = [
            "show topology", "list devices", "list all devices",
            "show devices", "what is the topology", "show path",
            "find path", "show interfaces", "how many devices",
        ]
        for pat in query_patterns:
            if pat in lower:
                return ParsedIntent(
                    intent_type=IntentType.QUERY,
                    action="show_info",
                    risk_level=RiskLevel.LOW,
                    justification="Information query",
                )

        if any(w in lower for w in ("audit", "security scan", "cis benchmark", "compliance check")):
            return ParsedIntent(
                intent_type=IntentType.AUDIT,
                action="security_audit",
                risk_level=RiskLevel.LOW,
                justification="Security audit request",
            )

        return None

    # ----- Helpers -----

    def _get_template_schemas(self) -> str:
        """Load all template schemas for prompt injection."""
        try:
            from config_engine.renderer import TemplateRenderer
            renderer = TemplateRenderer()
            templates = renderer.list_templates()
            schemas_dir = Path(__file__).resolve().parent.parent / "config_engine" / "schemas"

            lines = []
            seen = set()
            for t in templates:
                feat = t.get("feature", "")
                if feat in seen:
                    continue
                seen.add(feat)
                schema_path = schemas_dir / f"{feat}.json"
                if schema_path.exists():
                    schema = json.loads(schema_path.read_text())
                    lines.append(f"### {feat}\n```json\n{json.dumps(schema, indent=2)}\n```")
            return "\n\n".join(lines) if lines else "(no schemas available)"
        except Exception as e:
            logger.warning("Could not load template schemas: %s", e)
            return "(schemas unavailable)"

    def _extract_json(self, text: str) -> dict:
        """Try to extract a JSON object from LLM output that may have extra text."""
        # Try to find { ... } block
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if start == -1:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        continue
        return {"intent_type": "query", "action": "unknown", "justification": text[:200]}

    def _build_intent(self, data: dict, user_message: str) -> ParsedIntent:
        """Build a ParsedIntent from raw LLM JSON output with safety defaults."""
        scope_data = data.get("scope", {})
        scope = IntentScope(
            device_hostnames=scope_data.get("device_hostnames", []),
            device_role=scope_data.get("device_role"),
            building=scope_data.get("building"),
            vendor_filter=scope_data.get("vendor_filter"),
            all_devices=scope_data.get("all_devices", False),
        )

        # Map string to enum safely
        try:
            intent_type = IntentType(data.get("intent_type", "query"))
        except ValueError:
            intent_type = IntentType.QUERY

        try:
            risk_level = RiskLevel(data.get("risk_level", "medium"))
        except ValueError:
            risk_level = RiskLevel.MEDIUM

        return ParsedIntent(
            intent_type=intent_type,
            action=data.get("action", "unknown"),
            feature=data.get("feature", ""),
            vendor=_normalize_vendor(data.get("vendor")),
            parameters=data.get("parameters", {}),
            scope=scope,
            risk_level=risk_level,
            justification=data.get("justification", ""),
            raw_user_input=user_message,
        )

    def _resolve_devices(self, intent: ParsedIntent) -> List[str]:
        """
        Expand the intent scope into concrete device hostnames using
        the live topology and device inventory.
        """
        if not self._db:
            return list(intent.scope.device_hostnames)

        topo = self._db.get_topology()
        nodes = topo.get("nodes", {}) if topo else {}
        all_hosts = list(nodes.keys())
        inventory_records = self._inventory_records()
        inventory_by_hostname = {
            str(record.get("hostname") or ""): record
            for record in inventory_records
            if str(record.get("hostname") or "")
        }

        # 1. Explicit hostnames — verify they exist; if scope also has device_role, filter to that role
        if intent.scope.device_hostnames:
            resolved = []
            for h in intent.scope.device_hostnames:
                match = next(
                    (
                        n
                        for n in list(inventory_by_hostname.keys()) + all_hosts
                        if n.lower() == h.lower()
                    ),
                    None,
                )
                if match:
                    resolved.append(match)
                else:
                    logger.warning("Device '%s' not found in topology", h)
            # When user said "access switches" (etc.), filter to that role
            requested_role = _normalize_role(intent.scope.device_role)
            if not requested_role and intent.raw_user_input:
                requested_role = _infer_role_from_user_input(intent.raw_user_input)
            # Fallback: list has both Acc_ and Core_/Dist_ → assume "access only" and keep Acc_*
            if not requested_role and resolved:
                has_access = any(_role_from_hostname(n) == "access" for n in resolved)
                has_other = any(_role_from_hostname(n) in ("distribution", "core") for n in resolved)
                if has_access and has_other:
                    requested_role = "access"
            if requested_role and resolved:
                role_filtered = [
                    name for name in resolved
                    if _role_matches(name, requested_role, inventory_by_hostname)
                ]
                if role_filtered:
                    resolved = role_filtered
                    logger.info("Filtered to %s role: %d devices", requested_role, len(resolved))
            return resolved

        # 2. all_devices flag
        if intent.scope.all_devices:
            return sorted(inventory_by_hostname.keys() or all_hosts)

        # 3. Role / building / vendor filter — query planned inventory
        try:
            requested_vendor = _normalize_vendor(intent.scope.vendor_filter or intent.vendor)
            requested_role = _normalize_role(intent.scope.device_role)
            if not requested_role and intent.raw_user_input:
                requested_role = _infer_role_from_user_input(intent.raw_user_input)
            requested_site = _normalize_site(intent.scope.building)

            resolved: List[str] = []
            for hostname, record in inventory_by_hostname.items():
                record_vendor = _normalize_vendor(record.get("vendor"))
                record_role = _normalize_role(record.get("role"))
                record_site = _normalize_site(record.get("site"))

                if requested_vendor and record_vendor != requested_vendor:
                    continue
                if requested_site and record_site != requested_site:
                    continue
                # Role: match inventory role, or infer from hostname (Acc_/Dist_/Core_) when DB role is generic/missing
                if requested_role:
                    role_match = record_role == requested_role
                    if not role_match:
                        inferred = _role_from_hostname(hostname)
                        role_match = inferred == requested_role
                    if not role_match:
                        continue
                resolved.append(hostname)

            return sorted(resolved)
        except Exception as e:
            logger.warning("Device resolution query failed: %s", e)
            return list(intent.scope.device_hostnames)

    def _inventory_records(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []

        try:
            from rag.context_bundle import ContextBundleBuilder

            bundle = ContextBundleBuilder(db_manager=self._db).build_bundle(
                include_inventory=True,
                include_planned_topology=False,
                include_telemetry=False,
                include_sdn=False,
                include_config=False,
                include_deployment=False,
                include_incidents=False,
                include_threats=False,
                include_drift=False,
                include_knowledge=False,
                include_freshness_warnings=False,
            )
            records = list(bundle.inventory.devices or [])
        except Exception as exc:
            logger.debug("Context-backed inventory lookup failed: %s", exc)

        if records:
            return records

        return [
            {
                "hostname": device.hostname,
                "vendor": device.vendor,
                "role": device.role,
                "site": None,
            }
            for device in self._db.get_all_devices()
            if device.hostname
        ]


def _role_from_hostname(hostname: str) -> str:
    """Infer device role from hostname prefix (e.g. Acc_, Dist_, Core_) when DB role is missing or generic."""
    name = (hostname or "").strip()
    lower = name.lower()
    if lower.startswith("acc_"):
        return "access"
    if lower.startswith("dist_"):
        return "distribution"
    if lower.startswith("core_"):
        return "core"
    return ""


def _infer_role_from_user_input(raw: str) -> str:
    """Infer device_role from user text when the LLM did not set scope.device_role (e.g. 'access switches' -> access)."""
    lower = (raw or "").lower()
    if "access" in lower and ("switch" in lower or "layer" in lower or "only" in lower):
        return "access"
    if "access switch" in lower or "access switches" in lower or "all access" in lower:
        return "access"
    if "distribution" in lower or " dist " in lower or "dist switch" in lower:
        return "distribution"
    if "core" in lower and ("switch" in lower or "router" in lower or "layer" in lower):
        return "core"
    return ""


def _role_matches(
    hostname: str,
    requested_role: str,
    inventory_by_hostname: Dict[str, Any],
) -> bool:
    """True if device matches requested role (inventory role or hostname prefix)."""
    record = inventory_by_hostname.get(hostname, {})
    record_role = _normalize_role(record.get("role"))
    if record_role == requested_role:
        return True
    inferred = _role_from_hostname(hostname)
    return inferred == requested_role


def _normalize_vendor(value: Any) -> str:
    lowered = str(value or "").strip().lower().replace(" ", "_")
    if not lowered:
        return ""
    return _VENDOR_ALIASES.get(lowered, lowered)


def _normalize_role(value: Any) -> str:
    lowered = str(value or "").strip().lower().replace(" ", "_")
    if not lowered:
        return ""
    return _ROLE_ALIASES.get(lowered, lowered)


def _normalize_site(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
