import asyncio
import json

from dashboard import api as dashboard_api
from db.manager import DatabaseManager


class _FakeRefreshState:
    def get_operation_statuses(self):
        return {
            "live_topology": {
                "name": "live_topology",
                "status": "success",
                "last_completed_at": "2026-03-09T10:15:00+00:00",
            },
            "inventory": {
                "name": "inventory",
                "status": "success",
                "last_completed_at": "2026-03-09T10:20:00+00:00",
            },
            "planned_topology": {
                "name": "planned_topology",
                "status": "success",
                "last_completed_at": "2026-03-09T10:18:00+00:00",
            },
        }


def test_build_context_health_summary_includes_step27_kpis(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "dashboard.db"))
    db.initialize()
    db.append_context_event_log(
        {
            "event_id": "evt-dashboard-1",
            "source": "netbox_sync",
            "action": "inventory_sync_completed",
            "summary": "inventory synced",
            "event_type": "refresh",
            "domains": ["planned_inventory"],
            "versions": {"planned_inventory": 3},
            "metadata": {},
            "refresh_mode": "none",
            "refresh_targets": [],
            "refresh_jobs": [],
            "listener_results": [],
            "downstream_targets": [],
            "timestamp": "2026-03-09T10:25:00+00:00",
        }
    )

    monkeypatch.setattr(dashboard_api, "get_refresh_state_manager", lambda: _FakeRefreshState())
    monkeypatch.setattr(
        dashboard_api,
        "get_state_embedding_status_snapshot",
        lambda: {"available": True, "last_embedded_at": "2026-03-09T10:22:00+00:00"},
    )
    monkeypatch.setattr(
        dashboard_api,
        "get_retrieval_telemetry_snapshot",
        lambda: {
            "total_queries": 4,
            "last_retrieved_at": "2026-03-09T10:24:00+00:00",
            "query_class_counts": {"inventory_only": 2, "live_only": 2},
            "partition_counts": {"planned_inventory_topology": 2, "live_network_state": 2},
            "partition_percentages": {"planned_inventory_topology": 50.0, "live_network_state": 50.0},
            "source_type_counts": {"planned_inventory": 2, "live_state": 2},
        },
    )
    monkeypatch.setattr(
        dashboard_api.network_tools,
        "compare_planned_vs_live",
        lambda hostname="": json.dumps(
            {
                "devices": [
                    {"hostname": "R1", "presence_state": "planned_and_live"},
                    {"hostname": "SW1", "presence_state": "live_only"},
                    {"hostname": "SW2", "presence_state": "planned_only"},
                ],
                "planned_only": ["SW2"],
                "live_only": ["SW1"],
                "inventory_source_metadata": {
                    "source": "netbox",
                    "captured_at": "2026-03-09T10:20:00+00:00",
                    "freshness_class": "live",
                },
                "live_source_metadata": {
                    "source": "crawler_cache",
                    "captured_at": "2026-03-09T10:15:00+00:00",
                    "freshness_class": "stale",
                },
            }
        ),
    )

    summary = dashboard_api._build_context_health_summary(db)

    assert summary["last_successful_crawl_at"] == "2026-03-09T10:15:00+00:00"
    assert summary["last_netbox_sync_at"] == "2026-03-09T10:20:00+00:00"
    assert summary["last_embedding_update_at"] == "2026-03-09T10:22:00+00:00"
    assert summary["stale_devices"]["count"] == 2
    assert summary["missing_mappings"]["count"] == 2
    assert summary["event_lag"]["latest_event_at"] == "2026-03-09T10:25:00+00:00"
    assert summary["retrieval_source_mix"]["total_queries"] == 4
    assert summary["retrieval_source_mix"]["partition_percentages"]["live_network_state"] == 50.0


def test_dashboard_summary_endpoint_includes_context_health(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "dashboard-summary.db"))
    db.initialize()
    dashboard_api.setup_dashboard(db)
    monkeypatch.setattr(
        dashboard_api,
        "_build_context_health_summary",
        lambda current_db: {"last_successful_crawl_at": "2026-03-09T10:15:00+00:00", "retrieval_source_mix": {"total_queries": 1}},
    )

    payload = asyncio.run(dashboard_api.get_dashboard_summary())

    assert payload["context_health"]["last_successful_crawl_at"] == "2026-03-09T10:15:00+00:00"
    assert payload["context_health"]["retrieval_source_mix"]["total_queries"] == 1