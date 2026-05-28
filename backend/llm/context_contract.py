from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Sequence


CONTEXT_DOMAIN_INVENTORY = "planned_inventory"
CONTEXT_DOMAIN_PLANNED_TOPOLOGY = "planned_topology"
CONTEXT_DOMAIN_LIVE_TOPOLOGY = "live_topology"
CONTEXT_DOMAIN_TELEMETRY = "telemetry"
CONTEXT_DOMAIN_CONFIG = "config_history"
CONTEXT_DOMAIN_INCIDENTS = "incidents"
CONTEXT_DOMAIN_DRIFT = "drift"
CONTEXT_DOMAIN_DEPLOYMENT = "deployment_status"
CONTEXT_DOMAIN_SECURITY = "security_events"
CONTEXT_DOMAIN_KNOWLEDGE = "knowledge_base"
CONTEXT_DOMAIN_SDN = "sdn_state"


class FreshnessClass(str, Enum):
    LIVE = "live"
    WARM = "warm"
    STALE = "stale"
    MISSING = "missing"


class QueryClass(str, Enum):
    LIVE_ONLY = "live_only"
    INVENTORY_ONLY = "inventory_only"
    PLANNED_VS_LIVE = "planned_vs_live"
    TROUBLESHOOTING = "troubleshooting"
    CHANGE_PLANNING = "change_planning"
    ARCHITECTURE_PLANNING = "architecture_planning"
    COMPLIANCE = "compliance"
    KNOWLEDGE_HEAVY = "knowledge_heavy"
    GENERAL = "general"


@dataclass(frozen=True)
class ContextDomainContract:
    domain: str
    canonical_sources: tuple[str, ...]
    fallback_sources: tuple[str, ...]
    description: str
    live_max_age_minutes: Optional[int] = None
    warm_max_age_minutes: Optional[int] = None


@dataclass(frozen=True)
class FreshnessMetadata:
    captured_at: Optional[str]
    ingested_at: Optional[str]
    source: str
    scope: str
    confidence: float
    freshness_class: str
    age_seconds: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "captured_at": self.captured_at,
            "ingested_at": self.ingested_at,
            "source": self.source,
            "scope": self.scope,
            "confidence": round(self.confidence, 2),
            "freshness_class": self.freshness_class,
            "age_seconds": self.age_seconds,
        }

    def to_prompt_text(self) -> str:
        captured_at = self.captured_at or "unknown"
        ingested_at = self.ingested_at or "unknown"
        age = f", age_seconds={self.age_seconds}" if self.age_seconds is not None else ""
        return (
            f"source={self.source}; scope={self.scope}; freshness={self.freshness_class}; "
            f"captured_at={captured_at}; ingested_at={ingested_at}; confidence={self.confidence:.2f}{age}"
        )


@dataclass(frozen=True)
class QueryRoutingDecision:
    query_class: str
    use_live_context: bool
    use_knowledge: bool
    include_inventory: bool
    include_telemetry: bool
    include_sdn: bool
    required_domains: tuple[str, ...]
    reasoning: str


AUTHORITY_MATRIX: Dict[str, ContextDomainContract] = {
    CONTEXT_DOMAIN_INVENTORY: ContextDomainContract(
        domain=CONTEXT_DOMAIN_INVENTORY,
        canonical_sources=("netbox",),
        fallback_sources=("local_db",),
        description="Planned inventory, management IPs, roles, sites, and source-of-truth attributes.",
        live_max_age_minutes=60,
        warm_max_age_minutes=1440,
    ),
    CONTEXT_DOMAIN_PLANNED_TOPOLOGY: ContextDomainContract(
        domain=CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
        canonical_sources=("netbox_topology_sync",),
        fallback_sources=("local_db_links",),
        description="Planned links and intended topology relationships.",
        live_max_age_minutes=60,
        warm_max_age_minutes=1440,
    ),
    CONTEXT_DOMAIN_LIVE_TOPOLOGY: ContextDomainContract(
        domain=CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        canonical_sources=("crawler_cache",),
        fallback_sources=("sdn_controller",),
        description="Discovered live topology, device status, neighbors, and current graph state.",
        live_max_age_minutes=10,
        warm_max_age_minutes=60,
    ),
    CONTEXT_DOMAIN_TELEMETRY: ContextDomainContract(
        domain=CONTEXT_DOMAIN_TELEMETRY,
        canonical_sources=("influxdb", "prometheus"),
        fallback_sources=(),
        description="Time-series operational metrics such as CPU, memory, and interface counters.",
        live_max_age_minutes=5,
        warm_max_age_minutes=15,
    ),
    CONTEXT_DOMAIN_CONFIG: ContextDomainContract(
        domain=CONTEXT_DOMAIN_CONFIG,
        canonical_sources=("config_snapshots",),
        fallback_sources=("running_config_cache",),
        description="Captured configuration history and recent snapshots.",
        live_max_age_minutes=30,
        warm_max_age_minutes=240,
    ),
    CONTEXT_DOMAIN_DEPLOYMENT: ContextDomainContract(
        domain=CONTEXT_DOMAIN_DEPLOYMENT,
        canonical_sources=("deployment_repository",),
        fallback_sources=("job_status_cache",),
        description="Change request, deployment job, and intent execution state.",
        live_max_age_minutes=10,
        warm_max_age_minutes=60,
    ),
    CONTEXT_DOMAIN_INCIDENTS: ContextDomainContract(
        domain=CONTEXT_DOMAIN_INCIDENTS,
        canonical_sources=("incident_store",),
        fallback_sources=("alert_cache",),
        description="Incidents, alerts, and active investigation state.",
        live_max_age_minutes=5,
        warm_max_age_minutes=30,
    ),
    CONTEXT_DOMAIN_DRIFT: ContextDomainContract(
        domain=CONTEXT_DOMAIN_DRIFT,
        canonical_sources=("drift_store",),
        fallback_sources=("config_compare_cache",),
        description="Detected divergence between intended and actual configuration state.",
        live_max_age_minutes=30,
        warm_max_age_minutes=240,
    ),
    CONTEXT_DOMAIN_SECURITY: ContextDomainContract(
        domain=CONTEXT_DOMAIN_SECURITY,
        canonical_sources=("security_events", "threshold_alerts"),
        fallback_sources=("threat_cache",),
        description="Threat detections, rogue device findings, and threshold-driven security alerts.",
        live_max_age_minutes=5,
        warm_max_age_minutes=30,
    ),
    CONTEXT_DOMAIN_KNOWLEDGE: ContextDomainContract(
        domain=CONTEXT_DOMAIN_KNOWLEDGE,
        canonical_sources=("vector_store",),
        fallback_sources=("knowledge_base_files",),
        description="Static documentation, SOPs, and vendor guidance.",
        live_max_age_minutes=1440,
        warm_max_age_minutes=10080,
    ),
    CONTEXT_DOMAIN_SDN: ContextDomainContract(
        domain=CONTEXT_DOMAIN_SDN,
        canonical_sources=("sdn_controller",),
        fallback_sources=(),
        description="Controller-managed topology and device state overlays.",
        live_max_age_minutes=5,
        warm_max_age_minutes=30,
    ),
}


LIVE_ONLY_TRIGGERS = (
    "live topology",
    "right now",
    "currently",
    "at the moment",
    "online",
    "offline",
    "current topology",
    "latest crawl",
    "last crawl",
    "neighbors",
    "find path",
    "path between",
    "is it up",
    "is it down",
)

INVENTORY_ONLY_TRIGGERS = (
    "inventory",
    "netbox",
    "source of truth",
    "management ip",
    "ip address",
    "ip of",
    "ip for",
    "ip",
    "mac address",
    "serial number",
    "serial",
    "device role",
    "device model",
    "vendor",
    "model",
    "site",
    "rack",
    "interfaces",
    "interface",
    "links",
    "link",
    "connections",
    "connection",
    "neighbors",
    "neighbor",
)

PLANNED_VS_LIVE_TRIGGERS = (
    "planned vs live",
    "planned versus live",
    "compare planned",
    "compare inventory",
    "inventory mismatch",
    "missing from topology",
    "not in inventory",
    "planned state",
    "actual state",
    "source of truth vs live",
)

TROUBLESHOOTING_TRIGGERS = (
    "troubleshoot",
    "diagnose",
    "debug",
    "why is",
    "why does",
    "packet loss",
    "latency",
    "flap",
    "errors",
    "alarm",
    "incident",
    "failure",
    "not working",
    "down",
    "unreachable",
)

CHANGE_PLANNING_TRIGGERS = (
    "configure",
    "change plan",
    "plan change",
    "deploy",
    "rollout",
    "rollback",
    "provision",
    "enable",
    "disable",
    "apply config",
)

COMPLIANCE_TRIGGERS = (
    "compliance",
    "policy",
    "cis",
    "baseline",
    "audit",
    "control",
    "standard",
)

KNOWLEDGE_HEAVY_TRIGGERS = (
    "how to",
    "what is",
    "what are",
    "explain",
    "describe",
    "best practice",
    "recommendation",
    "guideline",
    "command",
    "syntax",
)

ARCHITECTURE_META_TERMS = (
    "llm",
    "rag",
    "mcp",
    "agentic",
    "ollama",
    "gemini",
    "vector store",
    "embedding",
    "retrieval",
    "prompt",
    "inference",
    "controller",
    "runtime",
    "latency",
    "performance",
)

ARCHITECTURE_PLANNING_TRIGGERS = (
    "architecture",
    "system design",
    "design a",
    "design an",
    "implementation plan",
    "milestone",
    "roadmap",
    "blueprint",
    "refactor",
    "optimize",
    "production-grade",
    "high-performance",
    "scalable",
)


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_freshness(
    domain: str,
    captured_at: Optional[str],
    ingested_at: Optional[str],
    source: str,
    scope: str,
    confidence: float,
    now: Optional[datetime] = None,
) -> FreshnessMetadata:
    contract = AUTHORITY_MATRIX.get(domain)
    current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    captured_dt = _parse_timestamp(captured_at)
    ingested_dt = _parse_timestamp(ingested_at)
    reference = captured_dt or ingested_dt

    if reference is None:
        freshness_class = FreshnessClass.MISSING.value
        age_seconds = None
    else:
        age_seconds = max(0, int((current_time - reference).total_seconds()))
        live_limit = (contract.live_max_age_minutes * 60) if contract and contract.live_max_age_minutes is not None else None
        warm_limit = (contract.warm_max_age_minutes * 60) if contract and contract.warm_max_age_minutes is not None else None

        if live_limit is not None and age_seconds <= live_limit:
            freshness_class = FreshnessClass.LIVE.value
        elif warm_limit is not None and age_seconds <= warm_limit:
            freshness_class = FreshnessClass.WARM.value
        else:
            freshness_class = FreshnessClass.STALE.value

    return FreshnessMetadata(
        captured_at=captured_at,
        ingested_at=ingested_at,
        source=source,
        scope=scope,
        confidence=confidence,
        freshness_class=freshness_class,
        age_seconds=age_seconds,
    )


def missing_freshness(domain: str, source: str, scope: str, confidence: float = 0.0) -> FreshnessMetadata:
    return compute_freshness(
        domain=domain,
        captured_at=None,
        ingested_at=None,
        source=source,
        scope=scope,
        confidence=confidence,
    )


def build_authoritative_context_contract() -> str:
    lines = ["## Context Authority Contract"]
    for domain in (
        CONTEXT_DOMAIN_INVENTORY,
        CONTEXT_DOMAIN_PLANNED_TOPOLOGY,
        CONTEXT_DOMAIN_LIVE_TOPOLOGY,
        CONTEXT_DOMAIN_TELEMETRY,
        CONTEXT_DOMAIN_CONFIG,
        CONTEXT_DOMAIN_INCIDENTS,
        CONTEXT_DOMAIN_DRIFT,
        CONTEXT_DOMAIN_KNOWLEDGE,
    ):
        contract = AUTHORITY_MATRIX[domain]
        canonical = ", ".join(contract.canonical_sources)
        fallback = ", ".join(contract.fallback_sources) if contract.fallback_sources else "none"
        lines.append(f"- {domain}: primary={canonical}; fallback={fallback}")

    lines.append("")
    lines.append("## Query Taxonomy")
    lines.append("- live_only, inventory_only, planned_vs_live, troubleshooting, change_planning, architecture_planning, compliance, knowledge_heavy")
    return "\n".join(lines)


def build_source_matrix() -> list[dict[str, object]]:
    return [
        {
            "query_class": QueryClass.LIVE_ONLY.value,
            "primary_domains": [CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY],
            "fallback_domains": [CONTEXT_DOMAIN_SDN],
        },
        {
            "query_class": QueryClass.INVENTORY_ONLY.value,
            "primary_domains": [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY],
            "fallback_domains": [CONTEXT_DOMAIN_LIVE_TOPOLOGY],
        },
        {
            "query_class": QueryClass.PLANNED_VS_LIVE.value,
            "primary_domains": [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY, CONTEXT_DOMAIN_LIVE_TOPOLOGY],
            "fallback_domains": [CONTEXT_DOMAIN_SDN],
        },
        {
            "query_class": QueryClass.TROUBLESHOOTING.value,
            "primary_domains": [CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY, CONTEXT_DOMAIN_INCIDENTS],
            "fallback_domains": [CONTEXT_DOMAIN_KNOWLEDGE, CONTEXT_DOMAIN_DRIFT],
        },
        {
            "query_class": QueryClass.CHANGE_PLANNING.value,
            "primary_domains": [CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_CONFIG, CONTEXT_DOMAIN_KNOWLEDGE],
            "fallback_domains": [CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_DRIFT],
        },
        {
            "query_class": QueryClass.ARCHITECTURE_PLANNING.value,
            "primary_domains": [CONTEXT_DOMAIN_KNOWLEDGE],
            "fallback_domains": [],
        },
        {
            "query_class": QueryClass.COMPLIANCE.value,
            "primary_domains": [CONTEXT_DOMAIN_CONFIG, CONTEXT_DOMAIN_DRIFT, CONTEXT_DOMAIN_KNOWLEDGE],
            "fallback_domains": [CONTEXT_DOMAIN_INVENTORY],
        },
        {
            "query_class": QueryClass.KNOWLEDGE_HEAVY.value,
            "primary_domains": [CONTEXT_DOMAIN_KNOWLEDGE],
            "fallback_domains": [CONTEXT_DOMAIN_INVENTORY],
        },
    ]


def _contains_any(query: str, triggers: Sequence[str]) -> bool:
    return any(trigger in query for trigger in triggers)


def _is_instructional_knowledge_query(query: str) -> bool:
    instructional_prefixes = ("how to", "what is", "what are", "explain", "describe")
    instructional_hints = ("best practice", "recommendation", "guideline", "syntax", "example")
    return query.startswith(instructional_prefixes) or _contains_any(query, instructional_hints)


def _is_architecture_planning_query(query: str) -> bool:
    has_planning_shape = _contains_any(query, ARCHITECTURE_PLANNING_TRIGGERS)
    has_meta_system_scope = _contains_any(query, ARCHITECTURE_META_TERMS)
    return has_planning_shape and has_meta_system_scope


def _is_syslog_focus_query(query: str) -> bool:
    """
    Detect requests that are primarily asking for syslog or security-event evidence.

    These should not be forced into the telemetry-heavy troubleshooting path when
    the user only asked for log evidence.
    """
    syslog_focus_terms = (
        "syslog",
        "syslogs",
        "security event",
        "security events",
        "security log",
        "security logs",
        "log entry",
        "log entries",
    )
    telemetry_terms = (
        "cpu",
        "memory",
        "mem",
        "telemetry",
        "metric",
        "metrics",
        "traffic",
        "utilization",
        "usage",
        "packet loss",
        "latency",
        "counters",
        "statistics",
        "stats",
    )
    normalized = query.lower()
    return _contains_any(normalized, syslog_focus_terms) and not _contains_any(normalized, telemetry_terms)


def _is_telemetry_focus_query(query: str) -> bool:
    telemetry_terms = (
        "cpu",
        "memory",
        "mem",
        "telemetry",
        "metric",
        "metrics",
        "traffic",
        "utilization",
        "usage",
        "load",
        "counters",
        "statistics",
        "stats",
        "packet loss",
        "latency",
        "errors",
        "discards",
    )
    exclude_terms = (
        "syslog",
        "syslogs",
        "security event",
        "security events",
        "config backup",
        "config history",
        "configuration backup",
        "snapshot",
        "topology",
        "neighbors",
        "path between",
    )
    normalized = query.lower()
    return _contains_any(normalized, telemetry_terms) and not _contains_any(normalized, exclude_terms)


def _is_config_focus_query(query: str) -> bool:
    config_terms = (
        "config backup",
        "config backups",
        "configuration backup",
        "configuration backups",
        "config history",
        "running config",
        "running configuration",
        "config snapshot",
        "config snapshots",
        "backup config",
        "latest config",
        "last config",
        "previous config",
        "show config",
    )
    exclude_terms = (
        "change plan",
        "plan change",
        "deploy",
        "rollout",
        "rollback",
        "apply config",
    )
    normalized = query.lower()
    return _contains_any(normalized, config_terms) and not _contains_any(normalized, exclude_terms)


def _is_topology_focus_query(query: str) -> bool:
    topology_terms = (
        "live topology",
        "current topology",
        "latest crawl",
        "last crawl",
        "neighbors",
        "neighbor",
        "topology",
        "find path",
        "path between",
        "downstream",
        "upstream",
        "connected to",
        "what links",
        "blast radius",
        "impacted if",
    )
    exclude_terms = (
        "syslog",
        "telemetry",
        "metrics",
        "config backup",
        "config history",
    )
    normalized = query.lower()
    return _contains_any(normalized, topology_terms) and not _contains_any(normalized, exclude_terms)


def _contains_word(query: str, words: tuple[str, ...]) -> bool:
    import re
    pattern = r"\b(" + "|".join(re.escape(w) for w in words) + r")\b"
    return bool(re.search(pattern, query.lower()))


def extract_user_request_text(query: str) -> str:
    """
    Strip overlay-side mode and quote wrappers before routing.

    The System AI overlay sends instructions such as "Operating mode: Expert"
    around the real request. Routing must classify the user's request, not the
    wrapper text, otherwise inventory lookups get polluted by telemetry/config
    words from the operating-mode instructions.
    """
    raw = str(query or "").strip()
    marker = "User request:"
    marker_index = raw.lower().rfind(marker.lower())
    if marker_index >= 0:
        return raw[marker_index + len(marker):].strip()
    return raw


def classify_query(query: str) -> QueryRoutingDecision:
    user_query = extract_user_request_text(query)
    q_lower = user_query.lower()

    # Define operational keywords/triggers for whole-word matching
    TELEMETRY_KEYS = ("cpu", "mem", "memory", "telemetry", "metric", "metrics", "traffic", "utilization", "usage", "load", "counters", "statistics", "stats")
    CONFIG_HISTORY_KEYS = ("backup", "backups", "snapshot", "snapshots", "git", "history", "version", "versions", "previous config", "config history", "past config", "configuration backup")
    DEPLOYMENT_KEYS = ("approval", "approvals", "change request", "change requests", "cr", "crs", "job", "jobs", "intent", "intents", "deploy", "deployment", "deployments", "pending approval", "approval status")
    SECURITY_KEYS = ("syslog", "syslogs", "log", "logs", "security event", "security events", "threat", "threats", "rogue", "intrusion", "alert", "alerts")

    is_operational_query = (
        _contains_word(q_lower, TELEMETRY_KEYS) or
        _contains_word(q_lower, CONFIG_HISTORY_KEYS) or
        _contains_word(q_lower, DEPLOYMENT_KEYS) or
        _contains_word(q_lower, SECURITY_KEYS)
    )

    # --- M19 IMPROVEMENT: Check for hostnames FIRST ---
    # If the user is asking about a specific device, steer to inventory/live
    # instead of knowledge base, even if it starts with "what is".
    hostnames = _extract_hostname_hints(q_lower)
    if hostnames:
        if _is_syslog_focus_query(q_lower):
             return QueryRoutingDecision(
                query_class=QueryClass.TROUBLESHOOTING.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=False,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_SECURITY,),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with syslog-only intent",
             )
        # Check if they also have trend or incident intents
        has_trend_intent = any(t in q_lower for t in ("trend", "over time", "history", "historical", "last ", "past ", "baseline", "over the"))
        has_incident_intent = any(t in q_lower for t in ("incident", "anomaly", "alert", "breach", "spike", "latency", "packet loss", "errors", "root cause", "troubleshoot", "diagnose", "debug", "why", "down", "unreachable", "flap"))
        has_telemetry_intent = _contains_word(q_lower, TELEMETRY_KEYS) or _contains_word(q_lower, SECURITY_KEYS)

        if _contains_any(q_lower, PLANNED_VS_LIVE_TRIGGERS):
             return QueryRoutingDecision(
                query_class=QueryClass.PLANNED_VS_LIVE.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=True,
                include_sdn=True,
                required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY, CONTEXT_DOMAIN_LIVE_TOPOLOGY),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with planned-vs-live intent",
             )
        elif _contains_any(q_lower, CHANGE_PLANNING_TRIGGERS):
             return QueryRoutingDecision(
                query_class=QueryClass.CHANGE_PLANNING.value,
                use_live_context=True,
                use_knowledge=True,
                include_inventory=True,
                include_telemetry=True,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_KNOWLEDGE),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with change planning intent",
             )
        elif _is_config_focus_query(q_lower):
             return QueryRoutingDecision(
                query_class=QueryClass.COMPLIANCE.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=False,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_CONFIG,),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with config snapshot intent",
             )
        elif _is_topology_focus_query(q_lower):
             return QueryRoutingDecision(
                query_class=QueryClass.LIVE_ONLY.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=False,
                include_sdn=True,
                required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY,),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with live-topology intent",
             )
        elif _is_telemetry_focus_query(q_lower):
             return QueryRoutingDecision(
                query_class=QueryClass.LIVE_ONLY.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=True,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_TELEMETRY,),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with telemetry-only intent",
             )
        elif _contains_word(q_lower, COMPLIANCE_TRIGGERS):
             return QueryRoutingDecision(
                query_class=QueryClass.COMPLIANCE.value,
                use_live_context=True,
                use_knowledge=True,
                include_inventory=True,
                include_telemetry=False,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_CONFIG, CONTEXT_DOMAIN_KNOWLEDGE),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with compliance intent",
             )
        elif has_trend_intent:
             return QueryRoutingDecision(
                query_class=QueryClass.GENERAL.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=True,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_LIVE_TOPOLOGY),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with trend intent",
            )
        elif has_incident_intent or _contains_any(q_lower, TROUBLESHOOTING_TRIGGERS):
             return QueryRoutingDecision(
                query_class=QueryClass.TROUBLESHOOTING.value,
                use_live_context=True,
                use_knowledge=True,
                include_inventory=True,
                include_telemetry=True,
                include_sdn=True,
                required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY, CONTEXT_DOMAIN_INCIDENTS),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with incident/troubleshooting intent",
            )
        elif has_telemetry_intent or _contains_any(q_lower, LIVE_ONLY_TRIGGERS):
             return QueryRoutingDecision(
                query_class=QueryClass.LIVE_ONLY.value,
                use_live_context=True,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=True,
                include_sdn=True,
                required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)} with live/telemetry intent",
            )
        else:
             return QueryRoutingDecision(
                query_class=QueryClass.INVENTORY_ONLY.value,
                use_live_context=False,
                use_knowledge=False,
                include_inventory=True,
                include_telemetry=False,
                include_sdn=False,
                required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY),
                reasoning=f"query mentions specific device(s) {', '.join(hostnames)}, prioritizing inventory lookup",
            )

    if _contains_any(q_lower, PLANNED_VS_LIVE_TRIGGERS):
        return QueryRoutingDecision(
            query_class=QueryClass.PLANNED_VS_LIVE.value,
            use_live_context=True,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=True,
            include_sdn=True,
            required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY, CONTEXT_DOMAIN_LIVE_TOPOLOGY),
            reasoning="planned-versus-live comparison requires both source-of-truth and live-state domains",
        )

    if _is_architecture_planning_query(q_lower):
        return QueryRoutingDecision(
            query_class=QueryClass.ARCHITECTURE_PLANNING.value,
            use_live_context=False,
            use_knowledge=False,
            include_inventory=False,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(),
            reasoning="architecture and platform-design prompts should avoid live operational fetches unless the user explicitly asks for them",
        )

    if _is_topology_focus_query(q_lower):
        return QueryRoutingDecision(
            query_class=QueryClass.LIVE_ONLY.value,
            use_live_context=True,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=False,
            include_sdn=True,
            required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY,),
            reasoning="query requests topology evidence, so live topology should be primary without forcing telemetry",
        )

    if _is_telemetry_focus_query(q_lower):
        return QueryRoutingDecision(
            query_class=QueryClass.LIVE_ONLY.value,
            use_live_context=True,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=True,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_TELEMETRY,),
            reasoning="query requests telemetry evidence, so metrics should be primary without forcing topology details",
        )

    if _is_config_focus_query(q_lower):
        return QueryRoutingDecision(
            query_class=QueryClass.COMPLIANCE.value,
            use_live_context=True,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_CONFIG,),
            reasoning="query requests configuration snapshot or backup evidence, so config history should be primary",
        )

    # If it is a telemetry or log event query, route to Live/Troubleshooting state instead of inventory-only
    if _is_syslog_focus_query(q_lower):
        return QueryRoutingDecision(
            query_class=QueryClass.TROUBLESHOOTING.value,
            use_live_context=True,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_SECURITY,),
            reasoning="syslog-focused query should prioritize security events without forcing telemetry or topology",
        )

    if _contains_word(q_lower, TELEMETRY_KEYS) or _contains_word(q_lower, SECURITY_KEYS):
        return QueryRoutingDecision(
            query_class=QueryClass.TROUBLESHOOTING.value,
            use_live_context=True,
            use_knowledge=True,
            include_inventory=True,
            include_telemetry=True,
            include_sdn=True,
            required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY, CONTEXT_DOMAIN_INCIDENTS),
            reasoning="query requests metrics or logs, routing to troubleshooting class to enable telemetry and security incidents",
        )

    if _contains_any(q_lower, INVENTORY_ONLY_TRIGGERS) and not is_operational_query:
        return QueryRoutingDecision(
            query_class=QueryClass.INVENTORY_ONLY.value,
            use_live_context=False,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY),
            reasoning="inventory query should prefer planned-state sources before live discovery",
        )

    if _is_architecture_planning_query(q_lower) and not is_operational_query:
        return QueryRoutingDecision(
            query_class=QueryClass.ARCHITECTURE_PLANNING.value,
            use_live_context=False,
            use_knowledge=False,
            include_inventory=False,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(),
            reasoning="architecture and platform-design prompts should avoid live operational fetches unless the user explicitly asks for them",
        )

    if _is_instructional_knowledge_query(q_lower) and not is_operational_query:
        return QueryRoutingDecision(
            query_class=QueryClass.KNOWLEDGE_HEAVY.value,
            use_live_context=False,
            use_knowledge=True,
            include_inventory=False,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_KNOWLEDGE,),
            reasoning="instructional how-to and SOP-style questions should prefer curated knowledge before operational state",
        )

    if _contains_word(q_lower, COMPLIANCE_TRIGGERS):
        return QueryRoutingDecision(
            query_class=QueryClass.COMPLIANCE.value,
            use_live_context=True,
            use_knowledge=True,
            include_inventory=True,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_CONFIG, CONTEXT_DOMAIN_KNOWLEDGE),
            reasoning="compliance questions need policy knowledge plus planned and captured config state",
        )

    if _contains_any(q_lower, CHANGE_PLANNING_TRIGGERS):
        return QueryRoutingDecision(
            query_class=QueryClass.CHANGE_PLANNING.value,
            use_live_context=True,
            use_knowledge=True,
            include_inventory=True,
            include_telemetry=True,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_KNOWLEDGE),
            reasoning="change-planning requires target inventory plus current state and configuration guidance",
        )

    if _contains_any(q_lower, TROUBLESHOOTING_TRIGGERS):
        return QueryRoutingDecision(
            query_class=QueryClass.TROUBLESHOOTING.value,
            use_live_context=True,
            use_knowledge=True,
            include_inventory=True,
            include_telemetry=True,
            include_sdn=True,
            required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY, CONTEXT_DOMAIN_INCIDENTS),
            reasoning="troubleshooting should combine live state, telemetry, and relevant operational knowledge",
        )

    if _contains_any(q_lower, LIVE_ONLY_TRIGGERS):
        return QueryRoutingDecision(
            query_class=QueryClass.LIVE_ONLY.value,
            use_live_context=True,
            use_knowledge=False,
            include_inventory=True,
            include_telemetry=True,
            include_sdn=True,
            required_domains=(CONTEXT_DOMAIN_LIVE_TOPOLOGY, CONTEXT_DOMAIN_TELEMETRY),
            reasoning="live-state question should prioritize topology and telemetry without static docs",
        )

    if _contains_any(q_lower, KNOWLEDGE_HEAVY_TRIGGERS) and not is_operational_query:
        return QueryRoutingDecision(
            query_class=QueryClass.KNOWLEDGE_HEAVY.value,
            use_live_context=False,
            use_knowledge=True,
            include_inventory=False,
            include_telemetry=False,
            include_sdn=False,
            required_domains=(CONTEXT_DOMAIN_KNOWLEDGE,),
            reasoning="knowledge-heavy question needs documentation before operational state",
        )

    return QueryRoutingDecision(
        query_class=QueryClass.GENERAL.value,
        use_live_context=True,
        use_knowledge=False,
        include_inventory=True,
        include_telemetry=True,
        include_sdn=False,
        required_domains=(CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_LIVE_TOPOLOGY),
        reasoning="default to combined planned inventory and current live state",
    )


def _extract_hostname_hints(query: str) -> Set[str]:
    """Simple regex or pattern matcher for candidate hostnames."""
    # 1. Standard hostname patterns (alphanumeric with _, -, or . separators)
    # 2. Support names with spaces if they look like "Vendor Name Number" (e.g. Cisco Switch 1)
    #    Matched via keywords + following terms.
    
    import re
    hints = set()

    # Pattern A: Standard network hostnames (e.g., Core_Cisco_1, dist-01, spine.A)
    std_pattern = r"\b[a-zA-Z0-9_\-\.]{3,}\b"
    std_matches = re.findall(std_pattern, query)
    for m in std_matches:
        if any(c in m for c in "_-."):
            hints.add(m)
        elif any(c.isdigit() for c in m) and any(c.isalpha() for c in m) and not m.startswith(("what", "show", "tell")):
            hints.add(m)

    # Pattern B: Entity-led names (e.g., "switch cisco 1", "device core-01")
    # Captures the word following a common network prefix if it looks like a name.
    prefix_pattern = r"(?:switch|device|router|core|host|node|distribution|dist|access|spine|leaf|ip|model|vendor|interface|interfaces)\s+([a-zA-Z0-9\s_\-]{1,20})"
    prefix_matches = re.finditer(prefix_pattern, query, re.IGNORECASE)
    for match in prefix_matches:
        name = match.group(1).strip()
        # Clean up leading junk like "of" or "the"
        name = re.sub(r"^(?:of|the)\s+", "", name, flags=re.IGNORECASE).strip()
        # Clean up ending junk
        name = re.sub(r"(?:\sis|\sas|\sat|\swith).*$", "", name, flags=re.IGNORECASE).strip()
        if name:
            hints.add(name)

    excluded = {
        "show", "get", "post", "delete", "put", "generate", "playbook", "for", "the", "device", 
        "this", "status", "unstable", "why", "now", "ospf", "bgp", "vlan", "route", "acl", 
        "logs", "log", "config", "configuration", "fail", "impact", "blast", "down", 
        "disconnect", "reachability", "is", "flapping", "active", "down", "up", "a", "an", "of",
        "switch", "switches", "devices", "router", "routers", "core", "host", "hosts",
        "node", "nodes", "distribution", "dist", "access", "spine", "leaf", "ip", "ips", "model",
        "models", "vendor", "vendors", "interface", "interfaces", "link", "links", "connection",
        "connections", "neighbor", "neighbors", "port", "ports", "planned", "live", "state",
        "comparison", "mismatch", "history", "trend", "incidents", "alerts", "syslog", "syslogs",
        "compliance", "policy", "cis", "baseline", "audit", "control", "standard", "architecture",
        "design", "plan", "roadmap", "blueprint", "refactor", "optimize", "production-grade",
        "high-performance", "scalable", "production", "grade", "performance", "high",
        "zero-trust", "peerings", "peer", "session", "sessions", "all", "that", "these", "those",
        "under", "over", "between", "with", "without", "from", "into", "onto", "upon", "about",
        "above", "below", "during", "before", "after", "here", "there", "when", "where", "which",
        "who", "whom", "whose", "latency", "packet", "loss", "errors", "error", "alarm", "alarms",
        "incident", "failure", "failures", "working", "unreachable", "configure", "change",
        "deploy", "deployment", "deployments", "rollout", "rollback", "provision", "enable",
        "disable", "apply", "how", "what", "explain", "describe", "best", "practice", "recommendation",
        "guideline", "command", "syntax", "example", "cisco", "juniper", "huawei", "arista",
        "netbox", "management", "serial", "number", "site", "rack", "mac", "address", "addresses",
        "vlan", "vlans", "ospf", "bgp", "route", "routes", "acl", "acls", "qos", "policing", "dscp",
        "controller", "runtime", "vector", "store", "embedding", "retrieval", "prompt", "inference",
        "llm", "rag", "mcp", "agentic", "ollama", "gemini", "network-wide", "networkwide"
    }

    filtered_hints = set()
    for hint in hints:
        h_lower = hint.lower().strip()
        if not h_lower:
            continue
        if h_lower in excluded:
            continue
        parts = [p for p in re.split(r"[-_\.]", h_lower) if p]
        if not parts:
            continue
        has_digits = any(any(char.isdigit() for char in part) for part in parts)
        if not has_digits:
            if all(part in excluded for part in parts):
                continue
        filtered_hints.add(hint)

    return filtered_hints
