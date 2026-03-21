import unittest


from ansible.intent_tree_engine import evaluate_intent_tree


class TestIntentTreeEnginePhase3AclQosOspf(unittest.TestCase):
    def test_acl_missing_required_fields(self):
        parsed_intent = {
            "action": "configure_acl",
            "feature": "acl",
            "parameters": {"acl_name": "", "acl_type": None, "rules": []},
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Configure an ACL",
            parsed_intent=parsed_intent,
            resolved_hosts=["SW1"],
            db_manager=None,
        )
        self.assertFalse(res.needs_target_devices)
        self.assertIsNotNone(res.params_disambiguation)
        fields = res.params_disambiguation.get("fields") or []
        field_names = {f.get("field") for f in fields if isinstance(f, dict)}
        self.assertTrue({"acl_name", "acl_type", "rules"}.issubset(field_names))

    def test_qos_missing_policy_name_only(self):
        parsed_intent = {
            "action": "configure_qos",
            "feature": "qos",
            "parameters": {"policy_name": "", "qos_type": "class-map", "classification_rules": ["match any"]},
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Configure QoS policy",
            parsed_intent=parsed_intent,
            resolved_hosts=["SW1"],
            db_manager=None,
        )
        fields = res.params_disambiguation.get("fields") or []
        field_names = [f.get("field") for f in fields if isinstance(f, dict)]
        self.assertEqual(field_names, ["policy_name"])

    def test_ospf_missing_process_id_and_networks(self):
        parsed_intent = {
            "action": "configure_ospf",
            "feature": "ospf",
            "parameters": {"process_id": None, "networks": []},
            "device_deduction": {},
        }
        res = evaluate_intent_tree(
            intent_description="Configure OSPF",
            parsed_intent=parsed_intent,
            resolved_hosts=["SW1"],
            db_manager=None,
        )
        fields = res.params_disambiguation.get("fields") or []
        field_names = {f.get("field") for f in fields if isinstance(f, dict)}
        self.assertTrue({"process_id", "networks"}.issubset(field_names))


if __name__ == "__main__":
    unittest.main()

