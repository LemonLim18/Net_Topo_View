import unittest


from ansible.reference_resolver import _sanitize_ios_l2_trunk_yaml_keys, get_template_vars


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

    def test_nettopo_trunk_missing_allowed_defaults_to_vlan_id_not_all(self):
        intent = {
            "target_hosts": ["SW1"],
            "vlan": {"id": 200, "name": "DATA"},
            "interfaces": [
                {"name": "all", "mode": "trunk"},
            ],
        }

        vars_out = get_template_vars(
            feature="vlan",
            intent=intent,
            _template_content="",
            planning_context=None,
        )

        self.assertTrue(vars_out.get("interfaces_config_with_vlan_use_nettopo"))
        self.assertEqual(vars_out.get("interfaces_binding_trunk_allowed_vlans"), [200])

    def test_trunk_merge_unions_inventory_allowed_when_planning_present(self):
        intent = {
            "target_hosts": ["SW1"],
            "vlan": {"id": 10, "name": "DATA"},
            "interfaces": [
                {"name": "Gi0/1", "mode": "trunk", "allowed_vlans": [10]},
            ],
        }
        planning_context = {
            "devices": [
                {
                    "hostname": "SW1",
                    "interfaces": [
                        {"name": "Gi0/1", "mode": "trunk", "allowed_vlans": [100, 200]},
                    ],
                }
            ]
        }
        vars_out = get_template_vars(
            feature="vlan",
            intent=intent,
            _template_content="",
            planning_context=planning_context,
        )
        ifaces = vars_out.get("interfaces") or []
        trunk = next(i for i in ifaces if i.get("mode") == "trunk")
        self.assertEqual(trunk.get("allowed_vlans"), [10, 100, 200])

    def test_ios_l2_interfaces_yaml_uses_allowed_vlans_not_trunk_vlans(self):
        """cisco.ios.ios_l2_interfaces expects trunk.allowed_vlans (module API)."""
        intent = {
            "target_hosts": ["SW1"],
            "vlan": {"id": 10, "name": "V"},
            "interfaces": [{"name": "Gi0/1", "mode": "trunk", "allowed_vlans": [10]}],
        }
        vars_out = get_template_vars(
            feature="vlan",
            intent=intent,
            _template_content="",
            planning_context=None,
        )
        cfg = vars_out.get("interfaces_config_with_vlan") or []
        self.assertEqual(len(cfg), 1)
        trunk = cfg[0].get("trunk") or {}
        self.assertIn("allowed_vlans", trunk)
        self.assertNotIn("trunk_vlans", trunk)
        self.assertEqual(trunk.get("allowed_vlans"), "10")

    def test_sanitize_rewrites_trunk_vlans_yaml_key(self):
        raw = """            trunk:
              trunk_vlans: '10'
"""
        fixed = _sanitize_ios_l2_trunk_yaml_keys(raw)
        self.assertIn("allowed_vlans:", fixed)
        self.assertNotIn("trunk_vlans:", fixed)


if __name__ == "__main__":
    unittest.main()

