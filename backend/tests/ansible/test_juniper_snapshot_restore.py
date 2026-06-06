from db.manager import DatabaseManager
from db.models import ConfigSnapshot, Device
from deploy.snapshots import SnapshotManager
from drivers.juniper_junos import JuniperJunosDriver
from drivers.models import DriverResult, DriverType


def _build_db(tmp_path):
    db_path = tmp_path / "juniper_snapshot_restore.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    return db


def test_junos_snapshot_sanitizer_keeps_hierarchical_config():
    driver = JuniperJunosDriver(host="192.0.2.10", username="tester", password="secret")

    payload = driver._sanitize_full_config_payload(
        "set cli screen-length 0\n"
        "tester@juniper> show configuration\n"
        "## Last commit: 2026-06-05\n"
        "system {\n"
        "    host-name Juniper_Acc_4;\n"
        "}\n"
        "tester@juniper>\n"
    )

    assert "set cli screen-length" not in payload
    assert "show configuration" not in payload
    assert "system {" in payload
    assert "host-name Juniper_Acc_4;" in payload


def test_juniper_restore_uses_full_config_loader(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    device_id = db.upsert_device(
        Device(
            hostname="Juniper_Acc_4",
            management_ip="192.168.1.46",
            vendor="juniper_junos",
            status="active",
        )
    )
    snapshot_id = db.save_config_snapshot(
        ConfigSnapshot(
            device_id=device_id,
            config_text="system {\n    host-name Juniper_Acc_4;\n}\n",
            captured_at="2026-06-05T10:00:00Z",
        )
    )

    calls = {"restore_payload": None, "push_config": False}

    class _FakeDriver:
        def connect(self):
            return None

        def disconnect(self):
            return None

        def get_running_config(self):
            return "system {\n    host-name current;\n}\n"

        def push_config(self, commands):
            calls["push_config"] = True
            return DriverResult(success=True, data=commands, driver_type=DriverType.SSH)

        def restore_full_config(self, config_text):
            calls["restore_payload"] = config_text
            return DriverResult(
                success=True,
                data={"loaded_lines": 3, "output": "load complete\nconfiguration check succeeds\ncommit complete"},
                driver_type=DriverType.SSH,
            )

    monkeypatch.setattr("deploy.snapshots.DriverFactory.get_driver", lambda device: _FakeDriver())

    result = SnapshotManager(db).restore_snapshot(snapshot_id, owner_username="tester")

    assert calls["push_config"] is False
    assert calls["restore_payload"] == "system {\n    host-name Juniper_Acc_4;\n}\n"
    assert result["vendor"] == "juniper_junos"
    assert result["commands_applied"] == 3
    assert result["backup_snapshot_id"] is not None
    assert result["restored_snapshot_id"] is not None
