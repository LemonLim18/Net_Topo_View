"""Unit tests for onboarding ZTP publish helpers."""

import unittest

from onboarding.ztp_publish import _normalize_mac, _render_dhcp_include_file


class TestNormalizeMac(unittest.TestCase):
    def test_normalizes_common_formats(self):
        self.assertEqual(_normalize_mac("AA-BB-CC-11-22-33"), "aa:bb:cc:11:22:33")
        self.assertEqual(_normalize_mac("aabb.cc11.2233"), "aa:bb:cc:11:22:33")
        self.assertEqual(_normalize_mac("aa:bb:cc:11:22:33"), "aa:bb:cc:11:22:33")

    def test_rejects_invalid_mac(self):
        self.assertIsNone(_normalize_mac(""))
        self.assertIsNone(_normalize_mac("aa:bb:cc:11:22"))
        self.assertIsNone(_normalize_mac("not-a-mac"))


class TestRenderDhcpInclude(unittest.TestCase):
    def test_renders_expected_host_blocks(self):
        content = _render_dhcp_include_file(
            [
                {
                    "device_id": 12,
                    "hostname": "Dist_Cisco_1",
                    "management_ip": "192.168.1.20",
                    "management_mac": "aa:bb:cc:11:22:33",
                    "filename": "ztp/cisco_ios/Dist_Cisco_1.cfg",
                },
                {
                    "device_id": 13,
                    "hostname": "Acc-Huawei-2",
                    "management_ip": "192.168.1.21",
                    "management_mac": "aa:bb:cc:11:22:44",
                    "filename": "ztp/huawei_vrp/Acc-Huawei-2.cfg",
                },
            ]
        )

        self.assertIn("host dist_cisco_1 {", content)
        self.assertIn("hardware ethernet aa:bb:cc:11:22:33;", content)
        self.assertIn("fixed-address 192.168.1.20;", content)
        self.assertIn("filename \"ztp/cisco_ios/Dist_Cisco_1.cfg\";", content)
        self.assertIn("host acc-huawei-2 {", content)


if __name__ == "__main__":
    unittest.main()
