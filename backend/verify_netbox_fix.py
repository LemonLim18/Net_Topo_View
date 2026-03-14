import os
import sys
import logging

sys.path.append(os.path.join(os.getcwd(), 'backend'))

from netbox.client import NetBoxClient
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
load_dotenv()

nb = NetBoxClient.get_client()

try:
    print("Testing NetBoxClient metadata push with unique serial...")
    nb.update_device(
        name="Acc_Cisco_1",
        serial="FINAL-SERIAL-999",
        os_version="17.9.3",
        vendor="cisco_ios"
    )
    
    print("\nVerifying update in NetBox...")
    dev = nb.api.dcim.devices.get(name="Acc_Cisco_1")
    print(f"Name: {dev.name}")
    print(f"Serial: {dev.serial}")
    print(f"OS Version: {dev.custom_fields.get('os_version')}")

except Exception as e:
    print(f"Error: {e}")
