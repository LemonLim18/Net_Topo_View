from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from config_engine.differ import ConfigDiffer


logger = logging.getLogger("config_engine.rollback_planner")


ROLLBACK_PLANNER_VERSION = "phase3_step11_v1"


def build_rollback_plan(
    *,
    db_manager,
    hostname: str,
    vendor: str,
    feature: str,
    params: Dict[str, Any],
    generated_config: str,
    template_rollback_config: str = "",
) -> Dict[str, Any]:
    differ = ConfigDiffer()

    snapshot_record: Dict[str, Any] = {
        "available": False,
        "captured_at": None,
        "config_hash": None,
        "device_id": 0,
    }
    current_config = ""

    if db_manager and hostname:
        try:
            device = db_manager.get_device_by_hostname(hostname)
            if device:
                snapshot_record["device_id"] = int(getattr(device, "id", 0) or 0)
                history = db_manager.get_config_history(device.id, limit=1)
                if history:
                    latest = history[0]
                    current_config = latest.config_text or ""
                    snapshot_record.update(
                        {
                            "available": True,
                            "captured_at": latest.captured_at,
                            "config_hash": latest.config_hash,
                        }
                    )
        except Exception as exc:
            logger.debug("Rollback planner could not load config history for %s: %s", hostname, exc)

    diff_result = differ.full_diff(
        current_config,
        generated_config or "",
        from_label=f"{hostname or 'device'} current",
        to_label=f"{hostname or 'device'} proposed",
    )

    template_reverse_available = bool((template_rollback_config or "").strip())
    strategy_chain = []
    if snapshot_record["available"]:
        strategy_chain.append("runtime_pre_change_snapshot_restore")
    if template_reverse_available:
        strategy_chain.append("template_reverse_commands")
    if not strategy_chain:
        strategy_chain.append("manual_reconstruction_required")

    if snapshot_record["available"]:
        strategy = "snapshot_aware_diff_restore"
    elif template_reverse_available:
        strategy = "known_reverse_operations"
    else:
        strategy = "manual_reconstruction_required"

    summary = []
    if snapshot_record["available"]:
        summary.append(
            f"Latest config snapshot available from {snapshot_record['captured_at']} for runtime restore."
        )
    else:
        summary.append("No config snapshot is available, so runtime pre-change restore is not guaranteed.")
    if diff_result.is_identical:
        summary.append("Proposed config matches the latest known config snapshot.")
    else:
        summary.append(differ.summary(current_config, generated_config or ""))
    if template_reverse_available:
        summary.append("Known reverse commands are available as a targeted fallback.")
    else:
        summary.append("No targeted reverse-command template is available.")

    return {
        "version": ROLLBACK_PLANNER_VERSION,
        "hostname": hostname,
        "vendor": vendor,
        "feature": feature,
        "params": dict(params or {}),
        "strategy": strategy,
        "strategy_chain": strategy_chain,
        "latest_snapshot": snapshot_record,
        "template_reverse_available": template_reverse_available,
        "runtime_restore_available": bool(snapshot_record["available"]),
        "rollback_config": template_rollback_config or "",
        "diff_against_snapshot": diff_result.to_dict(),
        "summary": " ".join(summary),
    }