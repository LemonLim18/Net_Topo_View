from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

from incidents.store import IncidentStore
from llm.context_contract import (
    CONTEXT_DOMAIN_CONFIG,
    CONTEXT_DOMAIN_DEPLOYMENT,
    CONTEXT_DOMAIN_DRIFT,
    CONTEXT_DOMAIN_INCIDENTS,
    CONTEXT_DOMAIN_INVENTORY,
    CONTEXT_DOMAIN_LIVE_TOPOLOGY,
    CONTEXT_DOMAIN_SECURITY,
)
from llm.intent import ParsedIntent
from llm.policy import PolicyEngine


CHANGE_INTELLIGENCE_VERSION = "phase9_step29_v1"

_SEVERITY_WEIGHTS = {
    "critical": 20,
    "high": 15,
    "medium": 8,
    "low": 3,
    "info": 1,
}


def _coerce_intent(intent_input: str | Dict[str, Any] | ParsedIntent) -> ParsedIntent:
    if isinstance(intent_input, ParsedIntent):
        return intent_input
    if isinstance(intent_input, str):
        return ParsedIntent.model_validate_json(intent_input)
    return ParsedIntent.model_validate(intent_input)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _topology_node(db_manager: Any, hostname: str) -> Dict[str, Any]:
    topology = db_manager.get_topology() if db_manager else {}
    nodes = topology.get("nodes", {}) if isinstance(topology, dict) else {}
    for node_name, node_data in nodes.items():
        if str(node_name).lower() == hostname.lower() and isinstance(node_data, dict):
            return {"hostname": str(node_name), **node_data}
    return {}


def _get_incidents(db_manager: Any, hostnames: Iterable[str]) -> List[Dict[str, Any]]:
    device_names = {hostname.lower() for hostname in hostnames}
    store = IncidentStore(getattr(db_manager, "_db_path", "db/netauto.db"))
    incidents = []
    for row in store.list_all(limit=100):
        affected = _json_loads(row.get("affected_devices"), [])
        if not isinstance(affected, list):
            affected = []
        if device_names and not any(str(item).lower() in device_names for item in affected):
            continue
        if row.get("status") not in {"open", "investigating", "approved"}:
            continue
        incidents.append(row)
    return incidents


def _get_security_events(db_manager: Any, devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not db_manager:
        return []
    device_ids = {device.get("id") for device in devices if device.get("id") is not None}
    ips = {
        str(device.get("management_ip") or "").strip()
        for device in devices
        if str(device.get("management_ip") or "").strip()
    }
    with db_manager._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM security_events ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    events = []
    for row in rows:
        record = dict(row)
        if device_ids and record.get("device_id") in device_ids:
            events.append(record)
            continue
        if ips and (
            str(record.get("source_ip") or "") in ips
            or str(record.get("dest_ip") or "") in ips
        ):
            events.append(record)
    return events


def _get_similar_intents(db_manager: Any, intent: ParsedIntent, devices: List[str]) -> List[Dict[str, Any]]:
    if not db_manager:
        return []
    with db_manager._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM intents ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    similar: List[Dict[str, Any]] = []
    requested = {device.lower() for device in devices}
    for row in rows:
        record = dict(row)
        if record.get("id") == intent.id:
            continue
        resolved = _json_loads(record.get("resolved_devices"), [])
        resolved_names = {str(item).lower() for item in resolved if str(item).strip()}
        same_feature = bool(intent.feature) and record.get("feature") == intent.feature
        same_action = bool(intent.action) and record.get("action") == intent.action
        device_overlap = bool(requested and resolved_names.intersection(requested))
        if same_feature or same_action or device_overlap:
            similar.append(
                {
                    "id": record.get("id"),
                    "feature": record.get("feature"),
                    "action": record.get("action"),
                    "status": record.get("status", "parsed"),
                    "risk_level": record.get("risk_level", "medium"),
                    "resolved_devices": resolved,
                    "policy_approved": record.get("policy_approved"),
                    "created_at": record.get("created_at"),
                    "blast_radius": record.get("blast_radius", 0),
                }
            )
    return similar[:10]


def _get_job_history(db_manager: Any, devices: List[str], feature: str) -> Dict[str, Any]:
    if not db_manager or not devices:
        return {"jobs": [], "proposals": []}
    placeholders = ",".join("?" for _ in devices)
    with db_manager._connect() as conn:
        jobs = conn.execute(
            (
                "SELECT j.id, j.status, j.stage, j.request_id, j.device_id, j.updated_at, "
                "d.hostname, cr.status AS request_status "
                "FROM jobs j "
                "JOIN devices d ON d.id = j.device_id "
                "LEFT JOIN change_requests cr ON cr.id = j.request_id "
                f"WHERE lower(d.hostname) IN ({placeholders}) "
                "ORDER BY j.updated_at DESC, j.id DESC LIMIT 25"
            ),
            [device.lower() for device in devices],
        ).fetchall()
        proposals = conn.execute(
            (
                "SELECT id, status, target_device, feature, risk_level, request_id, job_id, created_at "
                "FROM config_proposals "
                f"WHERE lower(target_device) IN ({placeholders}) "
                + ("AND feature = ? " if feature else "")
                + "ORDER BY created_at DESC LIMIT 25"
            ),
            [device.lower() for device in devices] + ([feature] if feature else []),
        ).fetchall()
    return {
        "jobs": [dict(row) for row in jobs],
        "proposals": [dict(row) for row in proposals],
    }


def _get_relevant_context_events(db_manager: Any, devices: List[str]) -> List[Dict[str, Any]]:
    if not db_manager or not hasattr(db_manager, "get_context_event_log"):
        return []
    lowered = {device.lower() for device in devices}
    relevant = []
    for event in db_manager.get_context_event_log(limit=50):
        metadata = event.get("metadata") or {}
        hostname = str(metadata.get("hostname") or "").lower()
        if hostname and hostname in lowered:
            relevant.append(event)
            continue
        affected = metadata.get("affected_devices") or []
        if any(str(item).lower() in lowered for item in affected):
            relevant.append(event)
            continue
        request_id = metadata.get("request_id")
        if request_id is not None and not devices:
            relevant.append(event)
    return relevant[:15]


def _summarize_intent_memory(similar_intents: List[Dict[str, Any]], devices: List[str]) -> Dict[str, Any]:
    status_counts = Counter(str(item.get("status") or "unknown") for item in similar_intents)
    overlapping = 0
    device_set = {device.lower() for device in devices}
    for item in similar_intents:
        similar_devices = {str(name).lower() for name in item.get("resolved_devices") or []}
        if similar_devices.intersection(device_set):
            overlapping += 1
    return {
        "total_similar": len(similar_intents),
        "status_counts": dict(status_counts),
        "device_overlap_count": overlapping,
        "recent_similar": similar_intents[:5],
    }


def _summarize_outcomes(job_history: Dict[str, Any]) -> Dict[str, Any]:
    jobs = job_history.get("jobs", [])
    proposals = job_history.get("proposals", [])
    job_counts = Counter(str(item.get("status") or "unknown") for item in jobs)
    proposal_counts = Counter(str(item.get("status") or "unknown") for item in proposals)

    guards: List[str] = []
    if job_counts.get("failed") or job_counts.get("rolled_back"):
        guards.append("Require manual checkpoint and rollback validation because recent similar jobs failed or rolled back.")
    if proposal_counts.get("rejected"):
        guards.append("Review previous rejected proposals for the same device or feature before approving a new rollout.")
    if not guards:
        guards.append("No recent failed similar changes were found; normal staged approval remains appropriate.")

    return {
        "job_outcomes": dict(job_counts),
        "proposal_outcomes": dict(proposal_counts),
        "recent_jobs": jobs[:5],
        "recent_proposals": proposals[:5],
        "recommended_guards": guards,
    }


def _planned_live_conflicts(devices: List[Dict[str, Any]], db_manager: Any) -> List[Dict[str, Any]]:
    conflicts = []
    for device in devices:
        hostname = str(device.get("hostname") or "")
        live = _topology_node(db_manager, hostname)
        if not live:
            conflicts.append({
                "hostname": hostname,
                "code": "missing_live_presence",
                "summary": f"{hostname} exists in planned inventory but is absent from live topology.",
            })
            continue
        planned_ip = str(device.get("management_ip") or "")
        live_ip = str(live.get("mgmt_ip") or "")
        if planned_ip and live_ip and planned_ip != live_ip:
            conflicts.append({
                "hostname": hostname,
                "code": "management_ip_mismatch",
                "summary": f"{hostname} has planned IP {planned_ip} but live IP {live_ip}.",
            })
        if live.get("down"):
            conflicts.append({
                "hostname": hostname,
                "code": "device_down",
                "summary": f"{hostname} is currently down in live topology.",
            })
    return conflicts


def _build_root_cause_graph(
    intent: ParsedIntent,
    devices: List[Dict[str, Any]],
    incidents: List[Dict[str, Any]],
    drift_events: List[Any],
    security_events: List[Dict[str, Any]],
    context_events: List[Dict[str, Any]],
    planned_live_conflicts: List[Dict[str, Any]],
    db_manager: Any,
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = [
        {
            "id": f"intent:{intent.id or 'pending'}",
            "kind": "intent",
            "label": intent.action or intent.feature or intent.intent_type.value,
            "domain": CONTEXT_DOMAIN_DEPLOYMENT,
        }
    ]
    edges: List[Dict[str, Any]] = []
    hotspot_signals: Dict[str, List[str]] = defaultdict(list)

    for device in devices:
        hostname = str(device.get("hostname") or "")
        live = _topology_node(db_manager, hostname)
        nodes.extend(
            [
                {
                    "id": f"planned:{hostname}",
                    "kind": "device",
                    "label": f"{hostname} planned",
                    "domain": CONTEXT_DOMAIN_INVENTORY,
                    "state": device.get("status"),
                },
                {
                    "id": f"live:{hostname}",
                    "kind": "device",
                    "label": f"{hostname} live",
                    "domain": CONTEXT_DOMAIN_LIVE_TOPOLOGY,
                    "state": "down" if live.get("down") else ("up" if live else "missing"),
                },
            ]
        )
        edges.append({"source": f"intent:{intent.id or 'pending'}", "target": f"planned:{hostname}", "relation": "targets"})
        edges.append({"source": f"intent:{intent.id or 'pending'}", "target": f"live:{hostname}", "relation": "targets"})

    for conflict in planned_live_conflicts:
        hostname = conflict["hostname"]
        conflict_id = f"conflict:{hostname}:{conflict['code']}"
        nodes.append(
            {
                "id": conflict_id,
                "kind": "conflict",
                "label": conflict["summary"],
                "domain": CONTEXT_DOMAIN_LIVE_TOPOLOGY,
            }
        )
        edges.append({"source": f"planned:{hostname}", "target": conflict_id, "relation": "disagrees_with"})
        edges.append({"source": f"live:{hostname}", "target": conflict_id, "relation": "disagrees_with"})
        hotspot_signals[hostname].append(conflict["code"])

    for incident in incidents[:6]:
        incident_id = str(incident.get("id"))
        nodes.append(
            {
                "id": f"incident:{incident_id}",
                "kind": "incident",
                "label": incident.get("anomaly_type") or incident_id,
                "domain": CONTEXT_DOMAIN_INCIDENTS,
                "severity": incident.get("severity", "medium"),
            }
        )
        for hostname in _json_loads(incident.get("affected_devices"), []):
            edges.append({"source": f"planned:{hostname}", "target": f"incident:{incident_id}", "relation": "active_incident"})
            hotspot_signals[str(hostname)].append("incident")

    for drift in drift_events[:6]:
        hostname = str(getattr(drift, "hostname", "") or "")
        node_id = f"drift:{hostname}:{getattr(drift, 'id', 'latest')}"
        nodes.append(
            {
                "id": node_id,
                "kind": "drift",
                "label": f"Drift on {hostname}",
                "domain": CONTEXT_DOMAIN_DRIFT,
                "severity": getattr(drift, "severity", "medium"),
            }
        )
        edges.append({"source": f"planned:{hostname}", "target": node_id, "relation": "drift_detected"})
        hotspot_signals[hostname].append("drift")

    for security_event in security_events[:6]:
        hostname = ""
        device_id = security_event.get("device_id")
        for device in devices:
            if device.get("id") == device_id:
                hostname = str(device.get("hostname") or "")
                break
        node_id = f"security:{security_event.get('id', security_event.get('event_type', 'evt'))}"
        nodes.append(
            {
                "id": node_id,
                "kind": "security",
                "label": security_event.get("event_type", "security_event"),
                "domain": CONTEXT_DOMAIN_SECURITY,
                "severity": security_event.get("severity", "medium"),
            }
        )
        if hostname:
            edges.append({"source": f"planned:{hostname}", "target": node_id, "relation": "security_signal"})
            hotspot_signals[hostname].append("security")

    for event in context_events[:6]:
        metadata = event.get("metadata") or {}
        hostname = str(metadata.get("hostname") or "")
        node_id = f"event:{event.get('event_id')}"
        nodes.append(
            {
                "id": node_id,
                "kind": "event",
                "label": event.get("summary", "context_event"),
                "domain": CONTEXT_DOMAIN_DEPLOYMENT,
            }
        )
        if hostname:
            edges.append({"source": f"planned:{hostname}", "target": node_id, "relation": "recent_change_event"})
            hotspot_signals[hostname].append("recent_change")

    hotspots = []
    for hostname, signals in hotspot_signals.items():
        hotspots.append(
            {
                "hostname": hostname,
                "evidence_count": len(signals),
                "signals": sorted(signals),
            }
        )
    hotspots.sort(key=lambda item: item["evidence_count"], reverse=True)

    return {
        "nodes": nodes,
        "edges": edges,
        "hotspots": hotspots,
        "graph_summary": (
            "Cross-domain graph links planned and live device state with incidents, drift, security, and recent change events "
            "so approval-time reasoning can explain why a device is risky right now."
        ),
    }


def _risk_level_from_score(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _compute_cross_domain_risk(
    devices: List[Dict[str, Any]],
    policy_result: Dict[str, Any],
    incidents: List[Dict[str, Any]],
    drift_events: List[Any],
    security_events: List[Dict[str, Any]],
    job_history: Dict[str, Any],
    planned_live_conflicts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    score = 0
    factors: List[str] = []
    domain_scores = {
        CONTEXT_DOMAIN_DEPLOYMENT: 0,
        CONTEXT_DOMAIN_INCIDENTS: 0,
        CONTEXT_DOMAIN_DRIFT: 0,
        CONTEXT_DOMAIN_SECURITY: 0,
        CONTEXT_DOMAIN_LIVE_TOPOLOGY: 0,
    }

    blast = ((policy_result.get("blast_radius") or {}).get("device_count") or 0)
    blast_points = min(20, int(blast) * 4)
    score += blast_points
    domain_scores[CONTEXT_DOMAIN_DEPLOYMENT] += blast_points
    if blast_points:
        factors.append(f"blast_radius:{blast} devices")

    error_count = len([item for item in policy_result.get("violations", []) if item.get("severity") == "error"])
    warning_count = len(policy_result.get("warnings", []))
    if error_count:
        policy_points = min(35, error_count * 18)
        score += policy_points
        domain_scores[CONTEXT_DOMAIN_DEPLOYMENT] += policy_points
        factors.append(f"policy_errors:{error_count}")
    if warning_count:
        warning_points = min(10, warning_count * 3)
        score += warning_points
        domain_scores[CONTEXT_DOMAIN_DEPLOYMENT] += warning_points
        factors.append(f"policy_warnings:{warning_count}")

    for incident in incidents:
        points = _SEVERITY_WEIGHTS.get(str(incident.get("severity", "medium")).lower(), 8)
        score += points
        domain_scores[CONTEXT_DOMAIN_INCIDENTS] += points
    if incidents:
        factors.append(f"active_incidents:{len(incidents)}")

    for drift in drift_events:
        points = _SEVERITY_WEIGHTS.get(str(getattr(drift, "severity", "medium")).lower(), 8)
        score += points
        domain_scores[CONTEXT_DOMAIN_DRIFT] += points
    if drift_events:
        factors.append(f"drift_events:{len(drift_events)}")

    for event in security_events:
        points = _SEVERITY_WEIGHTS.get(str(event.get("severity", "medium")).lower(), 8)
        score += points
        domain_scores[CONTEXT_DOMAIN_SECURITY] += points
    if security_events:
        factors.append(f"security_events:{len(security_events)}")

    failed_jobs = len([item for item in job_history.get("jobs", []) if item.get("status") in {"failed", "rolled_back"}])
    if failed_jobs:
        points = min(20, failed_jobs * 10)
        score += points
        domain_scores[CONTEXT_DOMAIN_DEPLOYMENT] += points
        factors.append(f"recent_failed_jobs:{failed_jobs}")

    if planned_live_conflicts:
        points = min(20, len(planned_live_conflicts) * 8)
        score += points
        domain_scores[CONTEXT_DOMAIN_LIVE_TOPOLOGY] += points
        factors.append(f"planned_live_conflicts:{len(planned_live_conflicts)}")

    for device in devices:
        status = str(device.get("status") or "").lower()
        if status in {"down", "unknown"}:
            score += 10
            domain_scores[CONTEXT_DOMAIN_LIVE_TOPOLOGY] += 10
            factors.append(f"inventory_status:{device.get('hostname')}={status}")

    score = min(100, score)
    return {
        "score": score,
        "level": _risk_level_from_score(score),
        "factors": factors,
        "domain_scores": domain_scores,
        "requires_manual_review": score >= 50 or not policy_result.get("approved", True),
    }


def _simulate_what_if(
    intent: ParsedIntent,
    devices: List[Dict[str, Any]],
    policy_result: Dict[str, Any],
    incidents: List[Dict[str, Any]],
    drift_events: List[Any],
    planned_live_conflicts: List[Dict[str, Any]],
    risk: Dict[str, Any],
    outcome_learning: Dict[str, Any],
    db_manager: Any,
) -> Dict[str, Any]:
    blockers = [item.get("message", "policy violation") for item in policy_result.get("violations", []) if item.get("severity") == "error"]
    warnings = list(policy_result.get("warnings", []))
    warnings.extend(conflict.get("summary", "planned/live conflict") for conflict in planned_live_conflicts)

    for device in devices:
        hostname = str(device.get("hostname") or "")
        live = _topology_node(db_manager, hostname)
        if live.get("down"):
            blockers.append(f"{hostname} is down in live topology and should not receive approval-time changes until reachability recovers.")

    if incidents:
        warnings.append("Active incidents already affect the target scope; approval should assume unstable operating conditions.")
    if drift_events:
        warnings.append("Current drift exists on at least one target device; planned config may stack on top of uncontrolled changes.")

    predicted_impacts = [
        "Config history will diverge from the previous approved snapshot until post-change verification completes.",
        "Deployment status, live topology, and drift views will all need refresh after execution.",
    ]
    if intent.feature:
        predicted_impacts.append(f"Feature-specific impact is expected around {intent.feature} on {', '.join(intent.resolved_devices or []) or 'the targeted scope' }.")

    preconditions = [
        "Policy validation must pass with no blocking violations.",
        "A current config snapshot should exist for every target device.",
        "Post-change verification and rollback commands must be reviewed before approval.",
    ]
    preconditions.extend(outcome_learning.get("recommended_guards", []))

    if blockers:
        decision = "reject"
    elif risk["level"] in {"high", "critical"}:
        decision = "manual_review"
    elif warnings:
        decision = "approve_with_caution"
    else:
        decision = "approve"

    return {
        "decision": decision,
        "blockers": blockers,
        "warnings": warnings[:10],
        "predicted_impacts": predicted_impacts,
        "preconditions": preconditions[:10],
        "explanation": (
            f"If approved now, the change would touch {len(devices)} device(s) with a current {risk['level']} risk profile. "
            f"Decision is {decision} because the analyzer combines policy state, current incidents, drift, recent change outcomes, and planned/live conflicts."
        ),
    }


def analyze_change_intelligence(
    db_manager: Any,
    intent_input: str | Dict[str, Any] | ParsedIntent,
    user_role: str = "operator",
) -> Dict[str, Any]:
    if db_manager is None:
        raise ValueError("Database manager is required for change intelligence analysis")

    intent = _coerce_intent(intent_input)
    resolved_devices = list(intent.resolved_devices or intent.scope.device_hostnames or [])
    devices: List[Dict[str, Any]] = []
    for hostname in resolved_devices:
        device = db_manager.get_device_by_hostname(hostname)
        if device:
            devices.append(
                {
                    "id": device.id,
                    "hostname": device.hostname,
                    "management_ip": device.management_ip,
                    "vendor": device.vendor,
                    "status": device.status,
                    "role": device.role,
                    "source": device.source,
                }
            )
        else:
            devices.append({"id": None, "hostname": hostname, "status": "missing", "role": None, "source": "unknown"})

    policy_result = json.loads(PolicyEngine(db_manager=db_manager).validate(intent.model_dump_json(), user_role=user_role))
    similar_intents = _get_similar_intents(db_manager, intent, resolved_devices)
    incidents = _get_incidents(db_manager, resolved_devices)
    drift_events = [
        event for event in db_manager.get_drift_events(limit=50)
        if str(getattr(event, "hostname", "")).lower() in {device.lower() for device in resolved_devices}
    ]
    security_events = _get_security_events(db_manager, devices)
    job_history = _get_job_history(db_manager, resolved_devices, intent.feature)
    context_events = _get_relevant_context_events(db_manager, resolved_devices)
    planned_live_conflicts = _planned_live_conflicts(devices, db_manager)

    intent_memory = _summarize_intent_memory(similar_intents, resolved_devices)
    outcome_learning = _summarize_outcomes(job_history)
    risk = _compute_cross_domain_risk(
        devices,
        policy_result,
        incidents,
        drift_events,
        security_events,
        job_history,
        planned_live_conflicts,
    )
    root_cause_graph = _build_root_cause_graph(
        intent,
        devices,
        incidents,
        drift_events,
        security_events,
        context_events,
        planned_live_conflicts,
        db_manager,
    )
    what_if = _simulate_what_if(
        intent,
        devices,
        policy_result,
        incidents,
        drift_events,
        planned_live_conflicts,
        risk,
        outcome_learning,
        db_manager,
    )

    return {
        "analysis_version": CHANGE_INTELLIGENCE_VERSION,
        "intent": {
            "id": intent.id,
            "intent_type": intent.intent_type.value,
            "action": intent.action,
            "feature": intent.feature,
            "risk_level": intent.risk_level.value,
            "vendor": intent.vendor,
            "resolved_devices": resolved_devices,
            "justification": intent.justification,
        },
        "intent_memory": intent_memory,
        "change_outcome_learning": outcome_learning,
        "root_cause_graph": root_cause_graph,
        "cross_domain_risk": risk,
        "what_if_simulation": what_if,
        "policy_result": policy_result,
        "evidence": {
            "devices": devices,
            "active_incidents": incidents,
            "drift_events": [
                {
                    "hostname": getattr(event, "hostname", None),
                    "severity": getattr(event, "severity", None),
                    "timestamp": getattr(event, "timestamp", None),
                    "has_drift": getattr(event, "has_drift", None),
                }
                for event in drift_events
            ],
            "security_events": security_events,
            "planned_live_conflicts": planned_live_conflicts,
            "recent_context_events": context_events,
        },
        "approval_recommendation": what_if["decision"],
    }