import unittest


from ansible.reference_resolver import get_template_vars


class TestReferenceResolverVlanAllInterfacesFlag(unittest.TestCase):
    def test_interfaces_all_sentinel_sets_nettopo_flags(self):
        intent = {
            "target_hosts": ["SW1"],
            "vlan": {"id": 200, "name": "DATA"},
            "interfaces": [
                {"name": "all", "mode": "trunk", "allowed_vlans": "all"},
            ],
        }

        vars_out = get_template_vars(
            feature="vlan",
            intent=intent,
            _template_content="",
            planning_context=None,
        )

        self.assertTrue(vars_out.get("interfaces_config_with_vlan_use_nettopo"))
        self.assertEqual(vars_out.get("interfaces_binding_mode"), "trunk")
        self.assertEqual(vars_out.get("interfaces_binding_trunk_allowed_vlans"), "all")


if __name__ == "__main__":
    unittest.main()

