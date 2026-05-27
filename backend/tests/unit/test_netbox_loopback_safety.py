import pytest
import pynetbox
from netbox.client import NetBoxClient

def test_netbox_client_loopback_safety(monkeypatch):
    called_urls = []
    def mock_pynetbox_api(url, token):
        called_urls.append(url)
        class FakeSession:
            verify = True
        class FakeApi:
            http_session = FakeSession()
        return FakeApi()

    monkeypatch.setattr(pynetbox, "api", mock_pynetbox_api)

    # 1. Test localhost:8000 loopback
    client = NetBoxClient(url="http://localhost:8000")
    assert client.api is None
    
    # 2. Test 127.0.0.1:8000 loopback
    client = NetBoxClient(url="http://127.0.0.1:8000")
    assert client.api is None

    # 3. Test 0.0.0.0:8000 loopback
    client = NetBoxClient(url="http://0.0.0.0:8000")
    assert client.api is None

    # 4. Test empty url
    client = NetBoxClient(url="")
    assert client.api is None

    # 5. Test normal URL (should connect)
    client = NetBoxClient(url="https://192.168.1.252")
    assert client.api is not None
    assert "https://192.168.1.252" in called_urls
