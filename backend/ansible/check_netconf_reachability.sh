#!/usr/bin/env bash
# Check TCP reachability to a host:port (default 830 for NETCONF).
# Run this on the same machine that runs the Ansible remote runner (e.g. Linux VM).
#
# Usage:
#   ./check_netconf_reachability.sh <IP> [port]
# Example:
#   ./check_netconf_reachability.sh 192.168.1.50
#   ./check_netconf_reachability.sh 192.168.1.50 830
#
# Get <IP> from Ansible Studio → Job → Inventory tab → ansible_host for the Huawei/Juniper host.

set -e
HOST="${1:?Usage: $0 <IP> [port]}"
PORT="${2:-830}"

echo "Checking TCP $HOST:$PORT (NETCONF default 830)..."
if command -v nc &>/dev/null; then
  if nc -zv "$HOST" "$PORT" 2>&1; then
    echo "OK: port $PORT is open."
  else
    echo "FAIL: port $PORT unreachable or closed."
    exit 1
  fi
else
  python3 -c "
import socket
import sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('$HOST', $PORT))
    print('OK: port $PORT is open.')
except Exception as e:
    print('FAIL: port $PORT unreachable or closed:', e, file=sys.stderr)
    sys.exit(1)
finally:
    s.close()
"
fi
