from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import HTTPException


ADVANCED_DIFFERENTIATOR_GATE_VERSION = "phase7_step19_v1"
_ADVANCED_DIFFERENTIATOR_ENV = "NETAUTO_ENABLE_ADVANCED_DIFFERENTIATORS"


def _feature_summary(feature_name: str) -> str:
    summaries = {
        "change_intelligence": "Deterministic change-outcome learning and policy simulation beyond the Milestone 3 minimum controller contract.",
        "digital_twin": "Advanced digital-twin reasoning surfaces that combine intended, actual, telemetry, incident, and drift context.",
        "autonomous_remediation": "Open-ended autonomous plan-and-execute workflows that move beyond Milestone 3 human-approved remediation boundaries.",
    }
    return summaries.get(feature_name, "Advanced differentiator reserved until Milestone 3 stability is proven.")


def build_advanced_differentiator_status(
    implementation_status: Optional[Dict[str, Any]] = None,
    feature_name: Optional[str] = None,
) -> Dict[str, Any]:
    readiness = str((implementation_status or {}).get("readiness") or "unknown")
    operator_override = os.getenv(_ADVANCED_DIFFERENTIATOR_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    stability_proven = readiness == "operationally_complete" and operator_override
    availability = "enabled" if stability_proven else "reserved"

    reasons = []
    if readiness != "operationally_complete":
        reasons.append(
            "Controller readiness is not yet operationally_complete, so advanced differentiators remain reserved."
        )
    if not operator_override:
        reasons.append(
            f"Operator unlock is not set; export {_ADVANCED_DIFFERENTIATOR_ENV}=1 only after full Milestone 3 validation is accepted."
        )

    features = {
        name: {
            "availability": availability,
            "summary": _feature_summary(name),
        }
        for name in ("change_intelligence", "digital_twin", "autonomous_remediation")
    }

    payload = {
        "version": ADVANCED_DIFFERENTIATOR_GATE_VERSION,
        "mode": "reserved_until_stable",
        "availability": availability,
        "stability_proven": stability_proven,
        "controller_readiness": readiness,
        "operator_unlock": operator_override,
        "unlock_env_var": _ADVANCED_DIFFERENTIATOR_ENV,
        "requirements": [
            "Complete the Milestone 3 evidence, safety, and bounded-autonomy work.",
            "Validate the controller through benchmark, resilience, routing, and safety regressions.",
            "Keep deployment and rollback behind explicit human approval.",
            f"Set {_ADVANCED_DIFFERENTIATOR_ENV}=1 only after operator acceptance of the stability proof.",
        ],
        "reasons": reasons,
        "features": features,
    }
    if feature_name:
        payload["feature"] = {
            "name": feature_name,
            "availability": features.get(feature_name, {"availability": availability})["availability"],
            "summary": _feature_summary(feature_name),
        }
    return payload


def ensure_advanced_differentiator_available(
    feature_name: str,
    implementation_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = build_advanced_differentiator_status(implementation_status, feature_name=feature_name)
    if payload["availability"] != "enabled":
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Advanced differentiator '{feature_name}' remains reserved until Milestone 3 stability is proven."
                ),
                "stability_gate": payload,
            },
        )
    return payload
