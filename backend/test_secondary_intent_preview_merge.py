"""Preview merges secondary_intents into top-level params_disambiguation (schema-driven)."""

import unittest


class TestSecondaryIntentInterfaceCollection(unittest.TestCase):
    def test_collect_includes_secondary_intent_interfaces(self):
        from ansible.context_entity_validation import collect_user_interface_names_from_params

        primary = {"vlan_id": 30, "vlan_name": "X"}
        sec = [
            {
                "action": "configure_interface",
                "feature": "interface",
                "parameters": {"interfaces": [{"name": "GigabitEthernet1/0/1", "mode": "access"}]},
            }
        ]
        names = collect_user_interface_names_from_params(primary, secondary_intents=sec)
        self.assertIn("GigabitEthernet1/0/1", names)

    def test_collect_embedded_secondary_in_params(self):
        from ansible.context_entity_validation import collect_user_interface_names_from_params

        p = {
            "vlan_id": 1,
            "_secondary_intents": [
                {"parameters": {"interfaces": [{"name": "Ethernet0/1", "mode": "access"}]}}
            ],
        }
        names = collect_user_interface_names_from_params(p)
        self.assertIn("Ethernet0/1", names)


class TestSecondaryIntentPreviewMerge(unittest.TestCase):
    def test_configure_interface_secondary_adds_interfaces_field(self):
        from ansible.api import _append_secondary_intent_schema_disambiguation

        parsed_intent = {
            "action": "create_vlan",
            "feature": "vlan",
            "parameters": {"vlan_id": 10, "vlan_name": "HR"},
            "secondary_intents": [
                {
                    "action": "configure_interface",
                    "feature": "interface",
                    "parameters": {},
                }
            ],
        }
        out = _append_secondary_intent_schema_disambiguation(
            parsed_intent,
            ["Acc_Cisco_1"],
            None,
        )
        self.assertIsNotNone(out)
        fields = out.get("fields") or []
        self.assertTrue(any(f.get("field") == "interfaces" for f in fields))
        iface_f = next(f for f in fields if f.get("field") == "interfaces")
        self.assertEqual(iface_f.get("secondary_intent_index"), 0)


if __name__ == "__main__":
    unittest.main()
