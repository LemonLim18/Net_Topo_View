import unittest


from ansible.intent_tree_engine import evaluate_intent_tree


class TestIntentTreeEnginePhase2VlanMissingFields(unittest.TestCase):
    def test_vlan_missing_vlan_id_emits_params_disambiguation(self):
        parsed_intent = {
            "action": "create_vlan",
            "feature": "vlan",
            "parameters": {
                "vlan_id": "",
                "vlan_name": "DATA",
            },
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Create VLAN DATA",
            parsed_intent=parsed_intent,
            resolved_hosts=["SW1"],
            db_manager=None,
        )
        self.assertFalse(res.needs_target_devices)
        self.assertIsNotNone(res.params_disambiguation)
        fields = res.params_disambiguation.get("fields") or []
        field_names = [f.get("field") for f in fields if isinstance(f, dict)]
        self.assertIn("vlan_id", field_names)


if __name__ == "__main__":
    unittest.main()

