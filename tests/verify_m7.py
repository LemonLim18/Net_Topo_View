import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.append(os.getcwd())

# Mock pynetbox before importing backend modules
mock_pynetbox = MagicMock()
sys.modules["pynetbox"] = mock_pynetbox

from backend.db.manager import DatabaseManager
from backend.netbox.sync_devices import DeviceSyncer

class TestNetBoxSync(unittest.TestCase):
    def setUp(self):
        # Mock DB Manager
        self.mock_db = MagicMock(spec=DatabaseManager)
        
    def test_sync_devices(self):
        # Setup Mock API Response via the sys.modules mock
        mock_api = MagicMock()
        # We need to ensure NetBoxClient.get_client().api uses this mock
        # backend.netbox.client imports pynetbox.api
        
        # Configure the mock that was already injected
        sys.modules["pynetbox"].api.return_value = mock_api
        
        # Mock Device
        mock_dev = MagicMock()
        mock_dev.name = "TestRouter"
        mock_dev.primary_ip4 = "192.168.1.100/24"
        mock_dev.platform.slug = "cisco-ios"
        mock_dev.status.value = "active"
        
        mock_api.dcim.devices.all.return_value = [mock_dev]
        
        # Run Syncer
        syncer = DeviceSyncer(self.mock_db)
        result = syncer.sync_all_devices()
        
        # Assertions
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["synced_count"], 1)
        
        # Verify DB Upsert call
        self.mock_db.upsert_device.assert_called_once()
        print("✅ Device Sync Verified (Mocked)")

if __name__ == "__main__":
    unittest.main()
