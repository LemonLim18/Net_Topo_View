import unittest

from ansible.context_entity_validation import validate_static_route_next_hop_ip_against_context


class TestStaticRouteNextHopIpValidation(unittest.TestCase):
    def test_next_hop_ip_in_interface_subnet_is_valid(self):
        planning_context = {
            "devices": [
                {
                    "hostname": "R1",
                    "interfaces": [
                        {"name": "Gi0/0", "ip_address": "10.0.0.1", "subnet_mask": "255.255.255.0"},
                    ],
                }
            ]
        }
        params = {"destination": "0.0.0.0/0", "next_hop": "10.0.0.2"}
        r = validate_static_route_next_hop_ip_against_context(params, ["R1"], planning_context)
        self.assertTrue(r.ok)

    def test_next_hop_ip_outside_known_subnets_is_invalid(self):
        planning_context = {
            "devices": [
                {
                    "hostname": "R1",
                    "interfaces": [
                        {"name": "Gi0/0", "ip_address": "10.0.0.1", "subnet_mask": "255.255.255.0"},
                    ],
                }
            ]
        }
        params = {"destination": "0.0.0.0/0", "next_hop": "192.168.99.1"}
        r = validate_static_route_next_hop_ip_against_context(params, ["R1"], planning_context)
        self.assertFalse(r.ok)
        self.assertTrue(any(e.get("kind") == "next_hop" for e in r.invalid_entities))


if __name__ == "__main__":
    unittest.main()

