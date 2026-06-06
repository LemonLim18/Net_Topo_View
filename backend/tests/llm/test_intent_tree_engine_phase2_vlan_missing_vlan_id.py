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

    def test_explicit_target_hosts_skips_bulk_needs_target_devices_heuristic(self):
        """User multi-selected 3 hosts in the UI — do not ask for targets again."""
        parsed_intent = {
            "action": "create_vlan",
            "feature": "vlan",
            "parameters": {"vlan_id": 120, "vlan_name": "WEB_Vlan"},
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Create VLAN 120 named WEB_Vlan on SW1 and SW2",
            parsed_intent=parsed_intent,
            resolved_hosts=["H1", "H2", "H3"],
            db_manager=None,
            explicit_targets_from_request=True,
        )
        self.assertFalse(res.needs_target_devices)


if __name__ == "__main__":
    unittest.main()

