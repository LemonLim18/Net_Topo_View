from compliance.evaluator import ComplianceEvaluator
from compliance.normalizer import ConfigNormalizer


def test_normalizer_removes_noise_and_comments():
    raw = """
! Last configuration was updated at 00:01
!
hostname SW1

ip ssh version 2
! another comment
"""
    n = ConfigNormalizer().normalize(raw, vendor="cisco_ios")
    assert "last configuration" not in n.text.lower()
    assert "!" not in n.text
    assert "hostname SW1".lower() in n.text.lower()


def test_evaluator_diff_and_policy_violation():
    ev = ComplianceEvaluator()
    device = {"hostname": "SW1", "vendor": "cisco_ios", "role": "access"}
    baseline = "hostname SW1\nip ssh version 2\n"
    actual = "hostname SW1\ntransport input telnet\n"
    policies = [
        {
            "policy_id": "p1",
            "name": "Require SSH",
            "enabled": True,
            "category": "logging",
            "scope": {"vendors": ["cisco_ios"]},
            "rules": [
                {"id": "r1", "action": "must_exist", "target": "ip ssh version 2", "severity": "high"},
                {"id": "r2", "action": "must_not_exist", "target": "transport input telnet", "severity": "high"},
            ],
        }
    ]
    out = ev.evaluate_device(device=device, baseline_raw=baseline, actual_raw=actual, vendor="cisco_ios", policies=policies)
    assert out["status"] in ("violated", "drifted")
    assert len(out["violated_policies"]) >= 1
    assert out["compliance_score"] < 100


def test_evaluator_no_drift_compliant():
    ev = ComplianceEvaluator()
    device = {"hostname": "SW1", "vendor": "cisco_ios", "role": "access"}
    cfg = "hostname SW1\nip ssh version 2\n"
    out = ev.evaluate_device(device=device, baseline_raw=cfg, actual_raw=cfg, vendor="cisco_ios", policies=[])
    assert out["status"] == "compliant"
    assert out["compliance_score"] == 100
    assert out["drift_pct"] == 0.0


def test_changed_line_pairing_uses_vendor_parser():
    ev = ComplianceEvaluator()
    device = {"hostname": "R1", "vendor": "cisco_ios", "role": "core"}
    baseline = "hostname R1\nip ssh version 2\n"
    actual = "hostname R1\nip ssh version 1\n"
    out = ev.evaluate_device(device=device, baseline_raw=baseline, actual_raw=actual, vendor="cisco_ios", policies=[])
    assert out["status"] == "drifted"
    assert len(out["changed_lines"]) >= 1

