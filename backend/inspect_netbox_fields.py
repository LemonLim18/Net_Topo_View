import os
import pynetbox
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

nb = pynetbox.api(os.environ.get("NETBOX_URL"), token=os.environ.get("NETBOX_TOKEN"))
nb.http_session.verify = False

targets = ["Acc_Cisco_1", "Acc_Cisco_2", "Acc_Huawei_1"]

try:
    for name in targets:
        dev = nb.dcim.devices.get(name=name)
        if dev:
            os_ver = "N/A"
            if hasattr(dev, 'custom_fields'):
                os_ver = dev.custom_fields.get('os_version', 'N/A')
            print(f"DEVICE:{dev.name}|SERIAL:{getattr(dev, 'serial', 'N/A')}|OS:{os_ver}")
except Exception as e:
    print(f"Error: {e}")
