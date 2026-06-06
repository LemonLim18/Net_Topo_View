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


class TestStripStaleSecondaryAfterPrimaryAuthoritative(unittest.TestCase):
    def test_clears_secondary_interfaces_when_primary_interfaces_sent(self):
        from ansible.multi_intent_operations import strip_stale_secondary_params_after_primary_authoritative

        parsed_intent = {
            "secondary_intents": [
                {
                    "action": "configure_interface",
                    "feature": "interface",
                    "parameters": {
                        "interfaces": [{"name": "GigabitEthernet1/0/1", "mode": "access"}],
                    },
                }
            ]
        }
        req_params = {"interfaces": [{"name": "Ethernet0/2", "mode": "access"}]}
        strip_stale_secondary_params_after_primary_authoritative(parsed_intent, req_params)
        sec_params = (parsed_intent["secondary_intents"][0] or {}).get("parameters") or {}
        self.assertNotIn("interfaces", sec_params)

    def test_skips_strip_when_secondary_slice_has_explicit_patch_in_req(self):
        """Patch list marks this slice as user-edited; strip must not remove its interfaces."""
        from ansible.multi_intent_operations import strip_stale_secondary_params_after_primary_authoritative

        parsed_intent = {
            "secondary_intents": [
                {
                    "action": "configure_interface",
                    "feature": "interface",
                    "parameters": {
                        "interfaces": [{"name": "GigabitEthernet1/0/1", "mode": "access"}],
                    },
                }
            ]
        }
        req_params = {
            "interfaces": [{"name": "Ethernet0/2", "mode": "access"}],
            "secondary_intents": [{"parameters": {"interfaces": [{"name": "Ethernet0/3", "mode": "trunk"}]}}],
        }
        strip_stale_secondary_params_after_primary_authoritative(parsed_intent, req_params)
        sec_params = (parsed_intent["secondary_intents"][0] or {}).get("parameters") or {}
        self.assertEqual(sec_params.get("interfaces", [{}])[0].get("name"), "GigabitEthernet1/0/1")


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
