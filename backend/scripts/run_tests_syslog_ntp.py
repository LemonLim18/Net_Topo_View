#!/usr/bin/env python3
"""Quick test runner: Syslog ingest + NTP template injection

Runs a UDP syslog sender against the project's `SyslogReceiver` and
renders the `day-0` template to verify the NTP server line is injected.

Exit codes:
  0 - all tests passed
  2 - one or more tests failed
"""
import socket
import threading
import time
import sys
import os

# Ensure backend package root is on sys.path when running this script directly
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_THIS_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

try:
    from security.threat.syslog_receiver import SyslogReceiver
    from config_engine.renderer import TemplateRenderer
except Exception as e:
    print("ERROR: Failed to import project modules:", e)
    print("Make sure dependencies are installed in the backend virtualenv.")
    raise


def find_free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_syslog():
    port = find_free_udp_port()
    events = []
    ev = threading.Event()

    def cb(event):
        events.append(event)
        ev.set()

    receiver = SyslogReceiver(port=port, callback=cb)
    receiver.start()
    time.sleep(0.05)

    # Send a Cisco-style ACL deny message
    msg = "%SEC-6-IPACCESSLOGP: list 101 denied tcp 192.168.1.100(12345) -> 10.0.0.5(80), 1 packet"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(msg.encode("utf-8"), ("127.0.0.1", port))
    finally:
        sock.close()

    got = ev.wait(timeout=3.0)
    receiver.stop()

    if not got or not events:
        print("SYSLOG TEST FAILED: no events received")
        return False

    event = events[0]
    print("SYSLOG TEST PASSED:", event.event_type, event.severity, event.source_ip)
    return True


def test_ntp():
    renderer = TemplateRenderer()
    params = {
        "hostname": "test-device",
        "mgmt_ip": "10.0.0.2",
        "subnet_mask": "255.255.255.0",
        "gateway": "10.0.0.1",
        "mgmt_interface": "GigabitEthernet0/0",
        "vendor": "cisco_ios",
        "ssh_user": "admin",
        "ssh_password": "admin",
        "ntp_server_ip": "192.168.1.252",
    }

    rendered = renderer.render("cisco_ios", "day-0", params)
    if "ntp server 192.168.1.252" not in rendered:
        print("NTP TEST FAILED: ntp server line missing")
        print("--- Rendered output ---\n", rendered)
        return False

    print("NTP TEST PASSED")
    return True


def main():
    ok1 = ok2 = False
    try:
        ok1 = test_syslog()
    except Exception as e:
        print("SYSLOG TEST ERROR:", e)

    try:
        ok2 = test_ntp()
    except Exception as e:
        print("NTP TEST ERROR:", e)

    if ok1 and ok2:
        print("ALL TESTS PASSED")
        return 0
    else:
        print("SOME TESTS FAILED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
