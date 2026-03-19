import unittest


from ansible.intent_tree_engine import evaluate_intent_tree


class _Dev:
    def __init__(self, hostname: str):
        self.hostname = hostname


class _FakeDb:
    def __init__(self, n: int):
        self._n = n

    def get_all_devices(self):
        return [_Dev(f"dev{i}") for i in range(self._n)]


class TestIntentTreeEnginePhase2StaticRoute(unittest.TestCase):
    def test_static_route_missing_destination_and_next_hop(self):
        parsed_intent = {
            "action": "configure_static_route",
            "feature": "static_route",
            "parameters": {
                "destination": "",
                "next_hop": None,
            },
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Configure a static route",
            parsed_intent=parsed_intent,
            resolved_hosts=["SW1"],
            db_manager=None,
        )

        self.assertFalse(res.needs_target_devices)
        self.assertIsNotNone(res.params_disambiguation)
        fields = res.params_disambiguation.get("fields") or []
        field_names = {f.get("field") for f in fields if isinstance(f, dict)}
        self.assertIn("destination", field_names)
        self.assertIn("next_hop", field_names)

    def test_static_route_missing_next_hop_only(self):
        parsed_intent = {
            "action": "configure_static_route",
            "feature": "static_route",
            "parameters": {
                "destination": "0.0.0.0/0",
                "next_hop": "",
            },
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Configure a static route",
            parsed_intent=parsed_intent,
            resolved_hosts=["SW1"],
            db_manager=None,
        )
        fields = res.params_disambiguation.get("fields") or []
        field_names = [f.get("field") for f in fields if isinstance(f, dict)]
        self.assertEqual(field_names, ["next_hop"])

    def test_tree_requests_target_devices_when_resolution_is_unclear(self):
        # "all"/"switch" not present => no explicit broad-scope exemption.
        parsed_intent = {
            "action": "configure_static_route",
            "feature": "static_route",
            "parameters": {"destination": "0.0.0.0/0", "next_hop": "192.0.2.1"},
            "device_deduction": {
                "mapping": [
                    {"hostname": "h1", "reason": "matched by inventory similarity"},
                    {"hostname": "h2", "reason": "matched by inventory similarity"},
                    {"hostname": "h3", "reason": "matched by inventory similarity"},
                    {"hostname": "h4", "reason": "matched by inventory similarity"},
                ]
            },
        }
        # resolved_hosts length 5; n_inv=10 => needs_target_devices True by heuristic.
        res = evaluate_intent_tree(
            intent_description="Configure a static route",
            parsed_intent=parsed_intent,
            resolved_hosts=["d1", "d2", "d3", "d4", "d5"],
            db_manager=_FakeDb(n=10),
        )
        self.assertTrue(res.needs_target_devices)


if __name__ == "__main__":
    unittest.main()

