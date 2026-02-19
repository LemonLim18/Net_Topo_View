import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Mock pynetbox before importing backend modules (dependency of NetBoxClient)
mock_pynetbox = MagicMock()
sys.modules["pynetbox"] = mock_pynetbox

# Add backend to path
sys.path.append(os.getcwd())

from backend.db.manager import DatabaseManager
from backend.drift.comparator import DriftComparator
from backend.db.models import Device, ConfigSnapshot

class TestDriftDetection(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock(spec=DatabaseManager)
        self.comparator = DriftComparator(self.mock_db)
        
    def test_drift_detected(self):
        # 1. Mock Device
        mock_dev = Device(hostname="R1", vendor="cisco_ios", id=1)
        self.mock_db.get_device_by_hostname.return_value = mock_dev
        
        # 2. Mock Actual Config (History)
        mock_snapshot = ConfigSnapshot(
            device_id=1, 
            config_text="hostname R1\n! Actual Config\ninterface Gi0/0\n  ip address 10.0.0.1 255.255.255.0",
            captured_at="2023-01-01T00:00:00"
        )
        self.mock_db.get_config_history.return_value = [mock_snapshot]
        
        # 3. Mock Template Renderer (M3) to return "Intended Config"
        # We mock the _fetch_intended_config method on the comparator itself for simplicity,
        # or we could mock the renderer calls. Let's mock the internal helper since we know
        # M3 templates might not fully be there yet.
        self.comparator._fetch_intended_config = MagicMock(return_value="hostname R1\n! Intended Config (Golden)\ninterface Gi0/0\n  ip address 10.0.0.2 255.255.255.0")
        
        # 4. Run Check
        event = self.comparator.check_drift("R1")
        
        # 5. Assertions
        self.assertIsNotNone(event)
        self.assertTrue(event.has_drift)
        self.assertEqual(event.severity, "medium") # "interface" keyword triggers medium
        print("✅ Drift Detected: 10.0.0.1 vs 10.0.0.2")

    def test_no_drift(self):
        # 1. Mock Device
        mock_dev = Device(hostname="R2", vendor="cisco_ios", id=2)
        self.mock_db.get_device_by_hostname.return_value = mock_dev
        
        # 2. Mock Identical Configs
        config = "hostname R2\nline vty 0 4\n transport input ssh"
        self.mock_db.get_config_history.return_value = [ConfigSnapshot(device_id=2, config_text=config)]
        self.comparator._fetch_intended_config = MagicMock(return_value=config)
        
        # 3. Run Check
        event = self.comparator.check_drift("R2")
        
        # 4. Assertions
        self.assertIsNotNone(event)
        self.assertFalse(event.has_drift)
        print("✅ No Drift Detected for Identical Configs")

if __name__ == "__main__":
    unittest.main()
