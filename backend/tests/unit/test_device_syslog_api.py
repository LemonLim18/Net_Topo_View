import asyncio

from db.manager import DatabaseManager
from db.models import Device
from device_syslog import api as syslog_api


def test_syslog_sources_merge_historical_rows_resolved_by_ip(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "syslog.db"))
    db.initialize()
    device_id = db.upsert_device(
        Device(hostname="Juniper_Acc_4", management_ip="192.168.1.46", vendor="juniper_junos")
    )

    with db._connect() as conn:
        conn.execute(
            """INSERT INTO device_syslog_events
               (source_ip, source_host, device_id, raw_message, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("192.168.1.46", "Juniper_Acc_4", None, "historical event", "2026-05-25T23:16:44+08:00"),
        )
        conn.execute(
            """INSERT INTO device_syslog_events
               (source_ip, source_host, device_id, raw_message, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("192.168.1.46", "Juniper_Acc_4", device_id, "current event", "2026-05-29T00:52:24+08:00"),
        )

    monkeypatch.setattr(syslog_api, "_db", lambda: db)

    payload = asyncio.run(syslog_api.list_syslog_sources())

    assert payload["items"] == [
        {
            "source_ip": "192.168.1.46",
            "source_host": "Juniper_Acc_4",
            "device_id": device_id,
            "device_hostname": "Juniper_Acc_4",
            "event_count": 2,
            "last_seen": "2026-05-29T00:52:24+08:00",
        }
    ]
