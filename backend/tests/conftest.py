import pytest

@pytest.fixture(autouse=True)
def reset_network_tools_cache():
    try:
        from llm import network_tools
        network_tools._netbox_inventory_cache["records"] = None
        network_tools._netbox_inventory_cache["fetched_at"] = 0.0
        network_tools._netbox_inventory_cache["failed_at"] = 0.0
    except ImportError:
        pass
