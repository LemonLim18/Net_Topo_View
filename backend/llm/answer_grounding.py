from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


_PLANNED_SCOPES = {"planned_inventory", "planned_topology"}
_LIVE_SCOPES = {
    "live_topology",
    "telemetry",
    "config_history",
    "incidents",
    "drift",
    "security_alerts",
    "incident_state",
    "drift_state",
    "device_configs",
    "change_management",
    "deployment_status",
    "sdn_state",
    "device_metrics",
}
_INFERRED_SCOPES = {"knowledge_documents"}

_TARGET_STATE_BY_QUERY_CLASS = {
    "inventory_only": "Requested planned or source-of-truth inventory state for the queried device or devices.",
    "planned_vs_live": "Requested a comparison between planned source-of-truth state and current live network state.",
    "live_only": "Requested current live operational state from authoritative network evidence.",
    "troubleshooting": "Requested an operational diagnosis grounded in live state, telemetry, incident, and config evidence.",
    "change_planning": "Requested a proposed target configuration or change plan that remains subject to approval.",
    "compliance": "Requested the current compliance state against intended policy or configuration evidence.",
    "knowledge_heavy": "Requested curated documentation or SOP guidance rather than a direct live-state assertion.",
    "general": "Requested a grounded network-state answer based on the authoritative evidence gathered for this turn.",
}

_DOCUMENTATION_SCOPES = {"knowledge_documents"}

_PREFERRED_EVIDENCE_FAMILIES = {
    "inventory_only": ["planned"],
    "planned_vs_live": ["planned", "live"],
    "live_only": ["live"],
    "troubleshooting": ["live"],
    "change_planning": ["planned", "documentation"],
    "compliance": ["live", "documentation"],
    "knowledge_heavy": ["documentation"],
    "general": ["planned", "live"],
}

_SUPPORTING_EVIDENCE_FAMILIES = {
    "inventory_only": ["live"],
    "planned_vs_live": [],
    "live_only": ["planned"],
    "troubleshooting": ["documentation", "planned"],
    "change_planning": ["live"],
    "compliance": ["planned"],
    "knowledge_heavy": ["planned"],
    "general": ["documentation"],
}


def _decode_tool_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"message": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": raw}


def _iter_source_metadata(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    keys = ("source_metadata", "inventory_source_metadata", "live_source_metadata")
    for key in keys:
        metadata = payload.get(key)
        if not isinstance(metadata, dict):
            continue
        if "source" in metadata and "scope" in metadata:
            yield metadata
            continue
        for value in metadata.values():
            if isinstance(value, dict) and "source" in value and "scope" in value:
                yield value


def _normalize_source_entry(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": str(metadata.get("source") or "unknown"),
        "scope": str(metadata.get("scope") or "unknown"),
        "captured_at": metadata.get("captured_at"),
        "freshness_class": str(metadata.get("freshness_class") or "unknown"),
        "age_seconds": metadata.get("age_seconds"),
        "confidence": float(metadata.get("confidence") or 0.0),
    }


def _dedupe_source_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries:
        key = (entry.get("source"), entry.get("scope"), entry.get("captured_at"), entry.get("age_seconds"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _answer_scope_from_sources(query_class: str, source_age: List[Dict[str, Any]]) -> str:
    scopes = {str(entry.get("scope") or "unknown") for entry in source_age}
    has_planned = bool(scopes & _PLANNED_SCOPES)
    has_live = bool(scopes & _LIVE_SCOPES)
    has_inferred = bool(scopes & _INFERRED_SCOPES)

    if query_class == "planned_vs_live" or (has_planned and has_live):
        return "mixed"
    if has_planned and not has_live and not has_inferred:
        return "planned"
    if has_live and not has_planned and not has_inferred:
        return "live"
    if has_inferred and not has_planned and not has_live:
        return "inferred"
    if has_planned or has_live:
        return "mixed" if has_inferred else ("planned" if has_planned else "live")

    if query_class == "inventory_only":
        return "planned"
    if query_class in {"live_only", "troubleshooting"}:
        return "live"
    if query_class == "knowledge_heavy":
        return "inferred"
    return "inferred"


def _evidence_family_for_scope(scope: str) -> str:
    normalized = str(scope or "unknown")
    if normalized in _PLANNED_SCOPES:
        return "planned"
    if normalized in _LIVE_SCOPES:
        return "live"
    if normalized in _DOCUMENTATION_SCOPES:
        return "documentation"
    return "unknown"


def _observed_evidence_families(source_age: List[Dict[str, Any]]) -> List[str]:
    families: List[str] = []
    seen = set()
    for entry in source_age:
        family = _evidence_family_for_scope(str(entry.get("scope") or "unknown"))
        if family == "unknown" or family in seen:
            continue
        seen.add(family)
        families.append(family)
    return families


def _build_evidence_authority(query_class: str, source_age: List[Dict[str, Any]], conflicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    preferred_families = list(_PREFERRED_EVIDENCE_FAMILIES.get(query_class, _PREFERRED_EVIDENCE_FAMILIES["general"]))
    supporting_families = list(_SUPPORTING_EVIDENCE_FAMILIES.get(query_class, _SUPPORTING_EVIDENCE_FAMILIES["general"]))
    observed_families = _observed_evidence_families(source_age)
    preferred_present = any(family in observed_families for family in preferred_families)
    supporting_used = [family for family in observed_families if family in supporting_families]
    unexpected_families = [
        family for family in observed_families
        if family not in preferred_families and family not in supporting_families
    ]
    mixed_authority = len(observed_families) > 1
    substitution_risk = bool(observed_families) and not preferred_present
    requires_disclosure = mixed_authority or bool(conflicts) or bool(supporting_used) or bool(unexpected_families)

    if substitution_risk:
        summary = (
            f"Observed evidence is {', '.join(observed_families)}, but this query class expects "
            f"{', '.join(preferred_families)} as primary authority."
        )
    elif requires_disclosure:
        summary = (
            f"Primary authority families for this query are {', '.join(preferred_families)}; "
            f"observed families are {', '.join(observed_families)}."
        )
    else:
        summary = (
            f"Observed evidence matches the primary authority family for this query class: "
            f"{', '.join(preferred_families)}."
        )

    return {
        "query_class": query_class,
        "preferred_families": preferred_families,
        "supporting_families": supporting_families,
        "observed_families": observed_families,
        "preferred_evidence_present": preferred_present,
        "supporting_families_used": supporting_used,
        "unexpected_families": unexpected_families,
        "mixed_authority": mixed_authority,
        "substitution_risk": substitution_risk,
        "requires_disclosure": requires_disclosure,
        "summary": summary,
    }


def _candidate_hostnames_from_args(tool_args: Dict[str, Dict[str, Any]]) -> List[str]:
    hostnames: List[str] = []
    seen = set()
    for arguments in tool_args.values():
        if not isinstance(arguments, dict):
            continue
        hostname = arguments.get("hostname")
        if not isinstance(hostname, str) or not hostname.strip():
            continue
        key = hostname.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        hostnames.append(hostname.strip())
    return hostnames


def _conflict(
    code: str,
    summary: str,
    severity: str = "medium",
    hostname: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    item = {"code": code, "summary": summary, "severity": severity}
    if hostname:
        item["hostname"] = hostname
    if details:
        item["details"] = details
    return item


def _detect_compare_conflicts(payload: Dict[str, Any], requested_hostnames: List[str]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    requested = {hostname.lower() for hostname in requested_hostnames}
    for device in payload.get("devices", []):
        if not isinstance(device, dict):
            continue
        hostname = str(device.get("hostname") or "")
        if requested and hostname.lower() not in requested:
            continue
        presence_state = str(device.get("presence_state") or "unknown")
        if presence_state == "planned_only":
            conflicts.append(
                _conflict(
                    "device_presence_mismatch",
                    f"{hostname} exists in planned inventory but is absent from live topology.",
                    severity="high",
                    hostname=hostname,
                    details={"presence_state": presence_state},
                )
            )
        elif presence_state == "live_only":
            conflicts.append(
                _conflict(
                    "device_presence_mismatch",
                    f"{hostname} is visible in live topology but absent from planned inventory.",
                    severity="high",
                    hostname=hostname,
                    details={"presence_state": presence_state},
                )
            )

        if device.get("management_ip_alignment") == "mismatch":
            conflicts.append(
                _conflict(
                    "management_ip_mismatch",
                    f"{hostname} has different management IP values in planned and live state.",
                    severity="high",
                    hostname=hostname,
                    details={
                        "planned_management_ip": device.get("planned_management_ip"),
                        "live_management_ip": device.get("live_management_ip"),
                    },
                )
            )
    return conflicts


def _detect_inventory_live_conflicts(
    inventory_payload: Optional[Dict[str, Any]],
    live_payload: Optional[Dict[str, Any]],
    requested_hostnames: List[str],
) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    inventory_device = inventory_payload.get("device") if isinstance(inventory_payload, dict) else None
    if requested_hostnames:
        requested = requested_hostnames[0]
    else:
        requested = str((inventory_device or {}).get("hostname") or (live_payload or {}).get("hostname") or "")

    if inventory_payload and inventory_payload.get("error") and isinstance(live_payload, dict) and live_payload.get("hostname"):
        conflicts.append(
            _conflict(
                "inventory_missing_live_present",
                f"{requested or live_payload.get('hostname')} is present in live topology but missing from planned inventory lookup.",
                severity="high",
                hostname=requested or str(live_payload.get("hostname")),
            )
        )
    if live_payload and live_payload.get("error") and isinstance(inventory_device, dict):
        hostname = str(inventory_device.get("hostname") or requested)
        conflicts.append(
            _conflict(
                "live_missing_inventory_present",
                f"{hostname} is present in planned inventory but missing from live topology lookup.",
                severity="high",
                hostname=hostname,
            )
        )

    if isinstance(inventory_device, dict) and isinstance(live_payload, dict) and not live_payload.get("error"):
        hostname = str(inventory_device.get("hostname") or live_payload.get("hostname") or requested)
        inventory_ip = inventory_device.get("management_ip")
        live_ip = live_payload.get("mgmt_ip")
        if inventory_ip and live_ip and str(inventory_ip) != str(live_ip):
            conflicts.append(
                _conflict(
                    "management_ip_mismatch",
                    f"{hostname} has different management IP values in planned inventory and live topology.",
                    severity="high",
                    hostname=hostname,
                    details={"planned_management_ip": inventory_ip, "live_management_ip": live_ip},
                )
            )
        inventory_status = str(inventory_device.get("status") or "")
        live_status = str(live_payload.get("status") or "")
        if inventory_status.lower() == "active" and live_status.lower() == "down":
            conflicts.append(
                _conflict(
                    "operational_state_conflict",
                    f"{hostname} is active in planned inventory but currently down in live topology.",
                    severity="high",
                    hostname=hostname,
                    details={"inventory_status": inventory_status, "live_status": live_status},
                )
            )
    return conflicts


def _detect_config_conflicts(config_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(config_payload, dict):
        return []
    hostname = str(config_payload.get("hostname") or "")
    if config_payload.get("note"):
        return [
            _conflict(
                "config_snapshot_missing",
                f"{hostname or 'Device'} has no recent running-config snapshot.",
                severity="medium",
                hostname=hostname or None,
                details={"note": config_payload.get("note")},
            )
        ]
    metadata = config_payload.get("source_metadata") or {}
    if isinstance(metadata, dict) and metadata.get("freshness_class") in {"missing", "stale"}:
        return [
            _conflict(
                "config_snapshot_stale",
                f"{hostname or 'Device'} configuration evidence is {metadata.get('freshness_class')}.",
                severity="medium",
                hostname=hostname or None,
                details={"freshness_class": metadata.get("freshness_class")},
            )
        ]
    return []


def _detect_incident_conflicts(incidents_payload: Optional[Dict[str, Any]], requested_hostnames: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(incidents_payload, dict):
        return []
    requested = {hostname.lower() for hostname in requested_hostnames}
    conflicts: List[Dict[str, Any]] = []
    for incident in incidents_payload.get("incidents", []):
        if not isinstance(incident, dict):
            continue
        affected = incident.get("affected_devices") or []
        if isinstance(affected, str):
            try:
                affected = json.loads(affected)
            except json.JSONDecodeError:
                affected = [affected]
        affected = [str(item) for item in affected]
        if requested and not {item.lower() for item in affected}.intersection(requested):
            continue
        status = str(incident.get("status") or "open").lower()
        if status not in {"open", "active", "new", "investigating"}:
            continue
        conflicts.append(
            _conflict(
                "active_incident",
                f"Active incident {incident.get('id', '?')} affects {', '.join(affected) or 'the queried device'}.",
                severity="high" if str(incident.get("severity") or "").lower() in {"critical", "high"} else "medium",
                hostname=affected[0] if len(affected) == 1 else None,
                details={
                    "incident_id": incident.get("id"),
                    "severity": incident.get("severity"),
                    "status": incident.get("status"),
                },
            )
        )
    return conflicts


def _detect_drift_conflicts(drift_payload: Optional[Dict[str, Any]], requested_hostnames: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(drift_payload, dict):
        return []
    requested = {hostname.lower() for hostname in requested_hostnames}
    conflicts: List[Dict[str, Any]] = []
    for event in drift_payload.get("events", []):
        if not isinstance(event, dict) or not event.get("has_drift"):
            continue
        hostname = str(event.get("hostname") or "")
        if requested and hostname.lower() not in requested:
            continue
        conflicts.append(
            _conflict(
                "config_drift_detected",
                f"{hostname} has detected configuration drift.",
                severity="high" if str(event.get("severity") or "").lower() == "high" else "medium",
                hostname=hostname or None,
                details={"severity": event.get("severity"), "timestamp": event.get("timestamp")},
            )
        )
    return conflicts


def _detect_telemetry_conflicts(telemetry_payload: Optional[Dict[str, Any]], requested_hostnames: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(telemetry_payload, dict):
        return []
    conflicts: List[Dict[str, Any]] = []
    hostname = str(telemetry_payload.get("hostname") or (requested_hostnames[0] if requested_hostnames else ""))
    metadata = telemetry_payload.get("source_metadata") or {}
    if isinstance(metadata, dict) and metadata.get("freshness_class") in {"missing", "stale"}:
        conflicts.append(
            _conflict(
                "telemetry_unavailable",
                f"Telemetry evidence for {hostname or 'the queried device'} is {metadata.get('freshness_class')}.",
                severity="medium",
                hostname=hostname or None,
                details={"freshness_class": metadata.get("freshness_class")},
            )
        )
    for baseline in telemetry_payload.get("anomaly_baselines", []):
        if not isinstance(baseline, dict):
            continue
        current = baseline.get("current")
        threshold = baseline.get("threshold")
        if current is None or threshold is None:
            continue
        try:
            current_value = float(current)
            threshold_value = float(threshold)
        except (TypeError, ValueError):
            continue
        if current_value > threshold_value:
            conflicts.append(
                _conflict(
                    "telemetry_threshold_breach",
                    f"Telemetry anomaly for {hostname or 'the queried device'} exceeds threshold ({current_value} > {threshold_value}).",
                    severity="high",
                    hostname=hostname or None,
                    details={
                        "incident_id": baseline.get("incident_id"),
                        "current": current_value,
                        "baseline": baseline.get("baseline"),
                        "threshold": threshold_value,
                    },
                )
            )
    return conflicts


def detect_answer_conflicts(
    query_class: str,
    payloads_by_tool: Dict[str, Dict[str, Any]],
    tool_args: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    requested_hostnames = _candidate_hostnames_from_args(tool_args)
    conflicts: List[Dict[str, Any]] = []
    compare_payload = payloads_by_tool.get("compare_planned_vs_live")

    if isinstance(compare_payload, dict):
        conflicts.extend(_detect_compare_conflicts(compare_payload, requested_hostnames))

    conflicts.extend(
        _detect_inventory_live_conflicts(
            payloads_by_tool.get("get_inventory_device"),
            payloads_by_tool.get("get_device_details"),
            requested_hostnames,
        )
    )
    conflicts.extend(_detect_config_conflicts(payloads_by_tool.get("get_device_running_config")))
    conflicts.extend(_detect_incident_conflicts(payloads_by_tool.get("get_recent_incidents"), requested_hostnames))
    conflicts.extend(_detect_drift_conflicts(payloads_by_tool.get("get_recent_drift_events"), requested_hostnames))
    conflicts.extend(_detect_telemetry_conflicts(payloads_by_tool.get("get_device_telemetry_snapshot"), requested_hostnames))

    if query_class == "planned_vs_live" and not conflicts and isinstance(compare_payload, dict):
        if compare_payload.get("planned_only") or compare_payload.get("live_only"):
            conflicts.append(
                _conflict(
                    "state_divergence_detected",
                    "Planned and live state differ for at least one queried device.",
                    severity="high",
                )
            )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for conflict in conflicts:
        key = (conflict.get("code"), conflict.get("hostname"), conflict.get("summary"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(conflict)
    return deduped


def _confidence_score(
    source_age: List[Dict[str, Any]],
    answer_scope: str,
    tool_names: List[str],
    errors: List[str],
    conflicts: List[Dict[str, Any]],
) -> float:
    if not source_age:
        base = 0.32
    else:
        avg_confidence = sum(entry.get("confidence", 0.0) for entry in source_age) / len(source_age)
        freshness_bonus = 0.0
        for entry in source_age:
            freshness = entry.get("freshness_class")
            if freshness == "live":
                freshness_bonus += 0.08
            elif freshness == "warm":
                freshness_bonus += 0.03
            elif freshness == "stale":
                freshness_bonus -= 0.08
            elif freshness == "missing":
                freshness_bonus -= 0.14
        freshness_bonus /= max(len(source_age), 1)
        base = avg_confidence + freshness_bonus

    if len(tool_names) >= 2:
        base += 0.08
    if answer_scope == "mixed" and any(name == "compare_planned_vs_live" for name in tool_names):
        base += 0.08
    if answer_scope == "inferred":
        base -= 0.12
    if errors:
        base -= min(0.25, 0.08 * len(errors))
    if conflicts:
        penalty = 0.0
        for conflict in conflicts:
            severity = str(conflict.get("severity") or "medium").lower()
            if severity == "high":
                penalty += 0.08
            elif severity == "medium":
                penalty += 0.04
            else:
                penalty += 0.02
        base -= min(0.24, penalty)

    return max(0.05, min(0.99, round(base, 2)))


def _confidence_level(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def _overall_freshness_class(source_age: List[Dict[str, Any]]) -> str:
    freshness_values = [str(entry.get("freshness_class") or "unknown") for entry in source_age]
    if not freshness_values:
        return "unknown"
    if "missing" in freshness_values:
        return "missing"
    if "stale" in freshness_values:
        return "stale"
    if "unknown" in freshness_values:
        return "unknown"
    if "warm" in freshness_values:
        return "warm"
    if all(value == "live" for value in freshness_values):
        return "live"
    return "unknown"


def _derive_assumptions(
    *,
    query_class: str,
    source_age: List[Dict[str, Any]],
    answer_scope: str,
    errors: List[str],
    conflicts: List[Dict[str, Any]],
    knowledge_used: bool,
    evidence_authority: Dict[str, Any],
) -> List[str]:
    assumptions: List[str] = []
    freshness_class = _overall_freshness_class(source_age)

    if answer_scope == "mixed":
        assumptions.append("Planned and live evidence were combined for this answer and may not represent the same capture instant.")
    if knowledge_used:
        assumptions.append("Curated knowledge context was treated as supporting guidance rather than as a substitute for authoritative live or planned state.")
    if freshness_class in {"warm", "stale", "missing", "unknown"}:
        assumptions.append(
            f"At least one evidence source is {freshness_class}, so the answer may lag behind the current network state."
        )
    if errors:
        assumptions.append("One or more authoritative reads failed, so the answer relies on the successful evidence that remained available.")
    if not source_age:
        assumptions.append("No per-tool evidence snapshots were captured for this turn, so the response is limited to prompt-context grounding.")
    if query_class == "knowledge_heavy":
        assumptions.append("Documentation guidance still needs confirmation against current inventory or live state before execution.")
    if conflicts:
        assumptions.append("Cross-source discrepancies are preserved instead of being flattened into a single certainty claim.")
    if evidence_authority.get("supporting_families_used"):
        assumptions.append(
            f"Supporting evidence families ({', '.join(evidence_authority['supporting_families_used'])}) supplement the primary authority rather than replace it."
        )
    if evidence_authority.get("unexpected_families"):
        assumptions.append(
            f"Unexpected evidence families ({', '.join(evidence_authority['unexpected_families'])}) are disclosed explicitly instead of being treated as equivalent authority."
        )

    deduped: List[str] = []
    seen = set()
    for item in assumptions:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:4]


def _current_state_summary(grounding: Dict[str, Any]) -> str:
    answer_scope = grounding.get("answer_scope", "inferred")
    freshness_class = grounding.get("freshness_class", "unknown")
    sources = grounding.get("source_used") or []
    source_text = ", ".join(str(source) for source in sources) if sources else "prompt context only"
    conflicts = grounding.get("conflicts") or []
    sentence = f"Grounded in {answer_scope} evidence from {source_text} with overall freshness {freshness_class}."
    evidence_authority = grounding.get("evidence_authority") or {}
    if evidence_authority.get("observed_families"):
        sentence += f" Observed evidence families: {', '.join(evidence_authority['observed_families'])}."
    if conflicts:
        sentence += f" {len(conflicts)} discrepancy signal(s) are currently present across sources."
    return sentence


def _target_state_summary(query_class: str) -> str:
    return _TARGET_STATE_BY_QUERY_CLASS.get(
        query_class,
        _TARGET_STATE_BY_QUERY_CLASS["general"],
    )


def _risk_summary(grounding: Dict[str, Any]) -> str:
    conflicts = grounding.get("conflicts") or []
    evidence_authority = grounding.get("evidence_authority") or {}
    if conflicts:
        top_conflict = conflicts[0]
        return str(top_conflict.get("summary") or "Cross-source discrepancy detected.")
    if evidence_authority.get("substitution_risk"):
        return str(evidence_authority.get("summary") or "The response is not grounded in the preferred authority family for this query class.")
    if evidence_authority.get("requires_disclosure"):
        return str(evidence_authority.get("summary") or "Multiple evidence families were used and must be interpreted explicitly.")

    freshness_class = grounding.get("freshness_class", "unknown")
    if freshness_class == "stale":
        return "Some evidence is stale, so very recent network changes may not yet be reflected."
    if freshness_class == "missing":
        return "Some required evidence is missing, so the answer should not be treated as fully complete."
    if freshness_class == "warm":
        return "Some evidence is warm rather than live, so near-real-time operational changes may require a refresh."

    confidence = grounding.get("confidence") or {}
    if confidence.get("level") == "low":
        return "Confidence is low because the evidence set is limited or degraded."
    return "No material cross-source conflict was detected in the evidence gathered for this turn."


def _verification_plan(query_class: str, freshness_class: str) -> str:
    refresh_prefix = "Refresh the relevant evidence first, then " if freshness_class in {"warm", "stale", "missing", "unknown"} else ""
    plan_by_query_class = {
        "inventory_only": "re-run inventory lookup or planned-vs-live comparison before making an operational decision.",
        "planned_vs_live": "re-run planned-vs-live comparison and confirm the live crawl before acting on any mismatch.",
        "live_only": "re-check live topology and telemetry before taking action if the operational state could have changed.",
        "troubleshooting": "re-check live topology, telemetry, incidents, and running-config evidence before remediation.",
        "change_planning": "review the proposal, validate the target intent, and complete pre-approval validation before execution.",
        "compliance": "re-check current config and drift evidence before marking the device compliant or non-compliant.",
        "knowledge_heavy": "confirm the documentation guidance against current inventory or live state before applying it.",
        "general": "repeat the authoritative reads closest to the operational decision before acting.",
    }
    return refresh_prefix + plan_by_query_class.get(query_class, plan_by_query_class["general"])


def build_answer_grounding(
    query_class: str,
    tool_results: Dict[str, Any],
    tool_args: Optional[Dict[str, Dict[str, Any]]] = None,
    knowledge_used: bool = False,
) -> Dict[str, Any]:
    tool_names = list(tool_results.keys())
    payloads_by_tool = {name: _decode_tool_payload(raw) for name, raw in tool_results.items()}
    payloads = list(payloads_by_tool.values())
    errors = [payload.get("error") for payload in payloads if payload.get("error")]

    source_entries: List[Dict[str, Any]] = []
    source_used: List[str] = []
    for payload in payloads:
        raw_source_used = payload.get("source_used")
        if isinstance(raw_source_used, str):
            source_used.append(raw_source_used)
        elif isinstance(raw_source_used, list):
            source_used.extend(str(item) for item in raw_source_used if item)

        for metadata in _iter_source_metadata(payload):
            normalized = _normalize_source_entry(metadata)
            source_entries.append(normalized)
            source_used.append(normalized["source"])

    if knowledge_used and "vector_store" not in source_used:
        source_used.append("vector_store")
        source_entries.append(
            {
                "source": "vector_store",
                "scope": "knowledge_documents",
                "captured_at": None,
                "freshness_class": "warm",
                "age_seconds": None,
                "confidence": 0.7,
            }
        )

    source_age = _dedupe_source_entries(source_entries)
    deduped_sources: List[str] = []
    seen_sources = set()
    for source in source_used:
        if source in seen_sources:
            continue
        seen_sources.add(source)
        deduped_sources.append(source)

    answer_scope = _answer_scope_from_sources(query_class, source_age)
    conflicts = detect_answer_conflicts(query_class, payloads_by_tool, tool_args or {})
    evidence_authority = _build_evidence_authority(query_class, source_age, conflicts)
    confidence_score = _confidence_score(source_age, answer_scope, tool_names, errors, conflicts)
    confidence_level = _confidence_level(confidence_score)
    freshness_class = _overall_freshness_class(source_age)

    if source_age:
        reason = f"Computed from {len(source_age)} authoritative evidence snapshot(s)"
    else:
        reason = "No authoritative tool evidence was captured for this turn; answer is grounded only by prompt context"

    if errors:
        reason = f"{reason}; degraded by tool errors"
    if conflicts:
        reason = f"{reason}; {len(conflicts)} discrepancy signal(s) detected across sources"

    assumptions = _derive_assumptions(
        query_class=query_class,
        source_age=source_age,
        answer_scope=answer_scope,
        errors=errors,
        conflicts=conflicts,
        knowledge_used=knowledge_used,
        evidence_authority=evidence_authority,
    )

    confidence = {
        "level": confidence_level,
        "score": confidence_score,
        "reason": reason,
    }

    evidence_record = {
        "version": "phase2_step6_v1",
        "source_used": deduped_sources,
        "source_age": source_age,
        "freshness_class": freshness_class,
        "confidence": confidence,
        "answer_scope": answer_scope,
        "conflicts": conflicts,
        "assumptions": assumptions,
        "evidence_authority": evidence_authority,
    }

    structured_rationale = {
        "current_state": _current_state_summary(
            {
                "answer_scope": answer_scope,
                "freshness_class": freshness_class,
                "source_used": deduped_sources,
                "conflicts": conflicts,
                "evidence_authority": evidence_authority,
            }
        ),
        "target_state": _target_state_summary(query_class),
        "key_risk_or_uncertainty": _risk_summary(
            {
                "conflicts": conflicts,
                "freshness_class": freshness_class,
                "confidence": confidence,
            }
        ),
        "verification_plan": _verification_plan(query_class, freshness_class),
    }

    return {
        "query_class": query_class,
        "answer_scope": answer_scope,
        "freshness_class": freshness_class,
        "source_used": deduped_sources,
        "source_age": source_age,
        "tool_evidence": tool_names,
        "conflicts": conflicts,
        "confidence": confidence,
        "assumptions": assumptions,
        "evidence_authority": evidence_authority,
        "evidence_record": evidence_record,
        "structured_rationale": structured_rationale,
    }


def format_operator_rationale(grounding: Dict[str, Any]) -> str:
    rationale = grounding.get("structured_rationale") or {}
    current_state = rationale.get("current_state")
    target_state = rationale.get("target_state")
    risk = rationale.get("key_risk_or_uncertainty")
    verification_plan = rationale.get("verification_plan")
    assumptions = grounding.get("assumptions") or []

    lines = []
    if current_state:
        lines.append(f"Current state: {current_state}")
    if target_state:
        lines.append(f"Target state: {target_state}")
    if risk:
        lines.append(f"Key risk or uncertainty: {risk}")
    if assumptions:
        lines.append(f"Assumptions: {' | '.join(str(item) for item in assumptions[:2])}")
    if verification_plan:
        lines.append(f"Verification plan: {verification_plan}")
    return "\n".join(lines)


def format_grounding_summary(grounding: Dict[str, Any]) -> str:
    answer_scope = grounding.get("answer_scope", "inferred")
    sources = grounding.get("source_used") or []
    source_text = ", ".join(str(source) for source in sources) if sources else "prompt_context_only"

    age_parts = []
    for entry in grounding.get("source_age") or []:
        source = entry.get("source", "unknown")
        age_seconds = entry.get("age_seconds")
        freshness = entry.get("freshness_class", "unknown")
        if age_seconds is None:
            age_parts.append(f"{source}:unknown/{freshness}")
        else:
            age_parts.append(f"{source}:{age_seconds}s/{freshness}")
    age_text = ", ".join(age_parts) if age_parts else "unknown"

    confidence = grounding.get("confidence") or {}
    level = confidence.get("level", "low")
    score = confidence.get("score", 0.0)
    return (
        f"Grounding: {answer_scope}; sources={source_text}; "
        f"source_age={age_text}; confidence={level} ({score})."
    )


def format_conflict_summary(grounding: Dict[str, Any]) -> str:
    conflicts = grounding.get("conflicts") or []
    if not conflicts:
        return ""
    snippets = []
    for conflict in conflicts[:4]:
        severity = str(conflict.get("severity") or "medium").upper()
        snippets.append(f"[{severity}] {conflict.get('summary', 'discrepancy detected')}")
    return "Conflicts: " + " | ".join(snippets)