import pynetbox

# These are the values from your .env file
NETBOX_URL = 'http://192.168.1.252'
NETBOX_TOKEN = 'a1c56312392af7ffcd83f0802dc45b1edda5196d'

# Initialize the API
nb = pynetbox.api(
    url=NETBOX_URL,
    token=NETBOX_TOKEN
)

# Disable SSL verification for lab environment
import requests
session = requests.Session()
session.verify = False
nb.http_session = session

try:
    # Request basic system status
    status = nb.status()
    print(f"[OK] Connection Successful!")
    print(f"NetBox Version: {status.get('netbox-version', 'Unknown')}")
except Exception as e:
    print(f"[FAIL] Connection Failed: {e}")
