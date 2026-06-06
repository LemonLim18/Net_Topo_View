#!/usr/bin/env python3
"""
Test NETCONF connection to a Huawei (or any NETCONF) device.
Run on the same host as the Ansible remote runner (e.g. Linux VM).

Requires: pip install ncclient

Usage:
  python3 test_netconf_huawei.py <host> [username] [password] [port]
  python3 test_netconf_huawei.py 192.168.1.48
  python3 test_netconf_huawei.py 192.168.1.48 cognishield 'Admin@Lab2026' 830

Defaults: username=cognishield, password=Admin@Lab2026, port=830.
Password can also be set via env NETCONF_PASSWORD.
"""
from __future__ import print_function

import os
import sys

def main():
    host = (sys.argv[1:2] or [None])[0]
    if not host:
        print("Usage: %s <host> [username] [password] [port]" % (sys.argv[0] or "test_netconf_huawei.py"), file=sys.stderr)
        print("  Defaults: username=cognishield, password=Admin@Lab2026, port=830", file=sys.stderr)
        sys.exit(2)

    username = (sys.argv[2:3] or ["cognishield"])[0]
    password = (sys.argv[3:4] or [os.environ.get("NETCONF_PASSWORD", "Admin@Lab2026")])[0]
    port = int((sys.argv[4:5] or ["830"])[0])

    try:
        from ncclient import manager
    except ImportError:
        print("Error: ncclient not installed. Run: pip install ncclient", file=sys.stderr)
        sys.exit(1)

    print("Connecting to %s:%s as %s ..." % (host, port, username))
    try:
        with manager.connect(
            host=host,
            port=port,
            username=username,
            password=password,
            hostkey_verify=False,
            device_params={"name": "huawei"},
            timeout=30,
        ) as m:
            # Get server capabilities (already done at connect); run one simple get to confirm session
            caps = m.server_capabilities
            print("OK: NETCONF connection established.")
            print("  Session ID: %s" % (m.session_id if hasattr(m, "session_id") else "N/A"))
            print("  Capabilities: %d" % len(caps))
            # Optional: get running config (can be large on Huawei)
            try:
                config = m.get_config(source="running")
                data = getattr(config, "data_xml", None) or str(getattr(config, "data", "") or "")
                print("  get_config(running): %d chars" % len(data))
                if data and len(data) < 500:
                    print("  Preview: %s" % data[:500])
                elif data:
                    print("  Preview: %s..." % data[:300])
            except Exception as e:
                print("  get_config: %s (connection still OK)" % e)
    except Exception as e:
        print("FAIL: %s" % e, file=sys.stderr)
        sys.exit(1)

    print("Done.")
    sys.exit(0)


if __name__ == "__main__":
    main()
