import os
import pynetbox
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

nb = pynetbox.api(os.environ.get("NETBOX_URL"), token=os.environ.get("NETBOX_TOKEN"))
nb.http_session.verify = False

try:
    dev = nb.dcim.devices.get(name="Acc_Cisco_1")
    if dev:
        print(f"Updating {dev.name}...")
        
        # Try updating serial
        dev.serial = "TEST-SERIAL-123"
        # Try updating os_version custom field
        dev.custom_fields["os_version"] = "v1.2.3"
        
        # Save
        success = dev.save()
        print(f"Save result: {success}")
        
        # Verify
        reloaded = nb.dcim.devices.get(id=dev.id)
        print(f"Reloaded Serial: {reloaded.serial}")
        print(f"Reloaded OS Version: {reloaded.custom_fields.get('os_version')}")
    else:
        print("Device not found.")

except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
