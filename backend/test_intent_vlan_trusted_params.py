"""Regression: LLM-filled vlan_id/vlan_name must not suppress the preview wizard."""

import unittest

from ansible.intent_validation import vlan_schema_fields_missing_from_user_prompt_respecting_params


class TestVlanTrustedParams(unittest.TestCase):
    def test_llm_vlan_name_does_not_suppress_without_trusted_key(self):
        missing = vlan_schema_fields_missing_from_user_prompt_respecting_params(
            "create vlan 100 on Gi0/1",
            {"vlan_name": "Invented", "vlan_id": 100},
            None,
        )
        self.assertIn("vlan_name", missing)

    def test_req_params_vlan_name_suppresses_after_wizard(self):
        missing = vlan_schema_fields_missing_from_user_prompt_respecting_params(
            "create vlan 100 on Gi0/1",
            {"vlan_name": "CORP", "vlan_id": 100},
            {"vlan_name"},
        )
        self.assertNotIn("vlan_name", missing)


if __name__ == "__main__":
    unittest.main()
