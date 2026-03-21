import unittest


from ansible.context_entity_validation import (
    collect_user_interface_names_from_params,
    should_validate_interface_names_against_inventory,
    validate_interface_params_against_context,
)


class TestContextEntityValidationNonInterfaceCategories(unittest.TestCase):
    def test_extract_acl_apply_to_strips_in_out(self):
        params = {"apply_to": ["Gi0/1 in", "Gi0/2 out", "Gi0/3"]}
        tokens = collect_user_interface_names_from_params(params)
        self.assertIn("Gi0/1", tokens)
        self.assertIn("Gi0/2", tokens)
        self.assertIn("Gi0/3", tokens)
        self.assertTrue(all(" in" not in t.lower() and " out" not in t.lower() for t in tokens))

    def test_validate_acl_apply_to_invalid_interface_triggers_interface_kind(self):
        planning_context = {
            "devices": [
                {"hostname": "sw1", "interfaces": [{"name": "Gi0/1"}, {"name": "Gi0/2"}]},
            ]
        }
        params = {"apply_to": ["Gi0/9 in"]}
        r = validate_interface_params_against_context(params, ["sw1"], planning_context)
        self.assertFalse(r.ok)
        self.assertTrue(any(e.get("kind") == "interface" for e in r.invalid_entities))

    def test_should_validate_acl_when_apply_to_contains_interface_like(self):
        params = {"apply_to": ["Gi0/9 in"]}
        self.assertTrue(
            should_validate_interface_names_against_inventory("configure_acl", "acl", params)
        )

    def test_next_hop_ip_is_not_treated_as_interface(self):
        planning_context = {
            "devices": [
                {"hostname": "sw1", "interfaces": [{"name": "Gi0/1"}]},
            ]
        }
        params = {"next_hop": "192.0.2.1"}
        tokens = collect_user_interface_names_from_params(params)
        self.assertEqual(tokens, [])

        r = validate_interface_params_against_context(params, ["sw1"], planning_context)
        self.assertTrue(r.ok)

    def test_validate_qos_interface_apply_invalid_interface(self):
        planning_context = {
            "devices": [
                {"hostname": "sw1", "interfaces": [{"name": "Gi0/1"}]},
            ]
        }
        params = {"interface_apply": ["Gi0/2 out"]}
        r = validate_interface_params_against_context(params, ["sw1"], planning_context)
        self.assertFalse(r.ok)
        self.assertTrue(any(e.get("kind") == "interface" for e in r.invalid_entities))

    def test_validate_ospf_passive_interfaces(self):
        planning_context = {
            "devices": [
                {"hostname": "sw1", "interfaces": [{"name": "Gi0/1"}, {"name": "Gi0/2"}]},
            ]
        }
        params = {"passive_interfaces": ["Gi0/3"]}
        r = validate_interface_params_against_context(params, ["sw1"], planning_context)
        self.assertFalse(r.ok)
        self.assertTrue(any(e.get("kind") == "interface" for e in r.invalid_entities))


if __name__ == "__main__":
    unittest.main()

