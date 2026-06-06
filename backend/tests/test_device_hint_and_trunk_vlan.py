"""Tests for spurious device tokens and VLAN candidate collection for preview."""

import unittest


class TestSpuriousDeviceHints(unittest.TestCase):
    def test_trunk_port_are_not_device_hints(self):
        from ansible.context_entity_validation import is_spurious_device_hint_token

        self.assertTrue(is_spurious_device_hint_token("trunk"))
        self.assertTrue(is_spurious_device_hint_token("port"))
        self.assertTrue(is_spurious_device_hint_token("advertise"))
        self.assertFalse(is_spurious_device_hint_token("SW1"))

    def test_interface_tokens_not_hostnames(self):
        from ansible.context_entity_validation import is_spurious_device_hint_token

        self.assertTrue(is_spurious_device_hint_token("GigabitEthernet1/0/2"))


class TestVlanCandidatesForPreview(unittest.TestCase):
    def test_collect_includes_intent_vlan_id(self):
        from ansible.context_entity_validation import collect_vlan_id_candidates_for_preview

        parsed = {
            "parameters": {"vlan_id": 10, "vlan_name": "X"},
            "secondary_intents": [],
        }
        ids = collect_vlan_id_candidates_for_preview(parsed, None)
        self.assertIn(10, ids)


if __name__ == "__main__":
    unittest.main()
