import logging
import os
from typing import List
from backend.db.manager import DatabaseManager
from backend.db.models import Device

logger = logging.getLogger("monitor.telegraf_gen")

# Configuration Constants (move to M15/Vault in production)
DEFAULT_COMMUNITY = "public"
DEFAULT_SNMP_VERSION = 2
SYSLOG_PORT = 6514
NETFLOW_PORT = 2055
MDT_GRPC_PORT = 57000

TELEGRAF_TEMPLATE_HEADER = """
# Global Agent Configuration
[agent]
  interval = "60s"
  round_interval = true
  metric_batch_size = 1000
  metric_buffer_limit = 10000
  collection_jitter = "0s"
  flush_interval = "10s"
  flush_jitter = "0s"
  precision = ""
  hostname = "NetTopoController"
  omit_hostname = false

# Output to InfluxDB
[[outputs.influxdb_v2]]
  urls = ["http://localhost:8086"]
  token = "$INFLUX_TOKEN"
  organization = "net_topo_org"
  bucket = "net_metrics"

# -------------------------------------------------------------
# GLOBAL LISTENERS (Push Telemetry)
# -------------------------------------------------------------

# 1. Syslog Receiver
[[inputs.syslog]]
  server = "tcp://:{syslog_port}"
  syslog_standard = "RFC5424"

# 2. NetFlow/IPFIX Receiver
[[inputs.netflow]]
  service_address = "udp://:{netflow_port}"
  protocol = "v9"

# 3. Model-Driven Telemetry (Cisco MDT via GRPC)
[[inputs.cisco_telemetry_mdt]]
  transport = "grpc"
  service_address = ":{mdt_port}"

""".format(syslog_port=SYSLOG_PORT, netflow_port=NETFLOW_PORT, mdt_port=MDT_GRPC_PORT)

def generate_snmp_input(devices: List[Device]) -> str:
    """Generates [[inputs.snmp]] blocks."""
    targets = [d.management_ip for d in devices if d.management_ip and d.status != 'down']
    if not targets: return ""
    
    agents = ", ".join([f'"{ip}:161"' for ip in targets])
    return f"""
# -------------------------------------------------------------
# POLLERS: SNMP
# -------------------------------------------------------------
[[inputs.snmp]]
  agents = [{agents}]
  version = {DEFAULT_SNMP_VERSION}
  community = "{DEFAULT_COMMUNITY}"
  interval = "60s"
  timeout = "5s"
  retries = 3

  [[inputs.snmp.field]]
    name = "hostname"
    oid = "RFC1213-MIB::sysName.0"
    is_tag = true

  [[inputs.snmp.field]]
    name = "uptime"
    oid = "RFC1213-MIB::sysUpTime.0"

  [[inputs.snmp.table]]
    name = "interface"
    inherit_tags = ["hostname"]
    oid = "IF-MIB::ifTable"
    [[inputs.snmp.table.field]]
      name = "ifDescr"
      oid = "IF-MIB::ifDescr"
      is_tag = true
    [[inputs.snmp.table.field]]
      name = "ifInOctets"
      oid = "IF-MIB::ifInOctets"
    [[inputs.snmp.table.field]]
      name = "ifOutOctets"
      oid = "IF-MIB::ifOutOctets"
"""

def generate_ping_input(devices: List[Device]) -> str:
    """Generates [[inputs.ping]] for all devices."""
    targets = [d.management_ip for d in devices if d.management_ip and d.status != 'down']
    if not targets: return ""
    
    urls = ", ".join([f'"{ip}"' for ip in targets])
    return f"""
# -------------------------------------------------------------
# POLLERS: ICMP (Ping)
# -------------------------------------------------------------
[[inputs.ping]]
  urls = [{urls}]
  count = 3
  ping_interval = 1.0
  timeout = 1.0
"""

def generate_gnmi_input(devices: List[Device]) -> str:
    """Generates [[inputs.gnmi]] blocks (Experimental)."""
    # Filter for devices likely to support gNMI (e.g., newer Cisco/Juniper)
    targets = [d for d in devices if d.status != 'down' and d.vendor in ['cisco_ios', 'juniper_junos']]
    if not targets: return ""

    config = "\n# -------------------------------------------------------------\n# POLLERS: gNMI\n# -------------------------------------------------------------\n"
    
    for d in targets:
        config += f"""
[[inputs.gnmi]]
  addresses = ["{d.management_ip}:57400"]
  username = "admin"
  password = "password"
  # Default subscription
  [[inputs.gnmi.subscription]]
    name = "interfaces"
    path = "/interfaces/interface/state/counters"
    subscription_mode = "sample"
    sample_interval = "10s"
"""
    return config

def generate_netconf_block(devices: List[Device]) -> str:
    """Generates [[inputs.netconf]] (simulated via exec/script usually, but Telegraf uses 'exec' or specialized plugins).
    Since pure 'inputs.netconf' isn't standard in basic Telegraf, we'll placeholder it or use 'exec'.
    Actually, Telegraf has 'inputs.netconf' in recent versions or third-party.
    We will assume specific plugin availability or use 'exec' wrapper.
    For this request, let's output a generic block for completeness.
    """
    targets = [d for d in devices if d.status != 'down' and d.vendor in ['juniper_junos', 'huawei_vrp']]
    if not targets: return ""
    
    # Placeholder for NETCONF polling
    return "\n# NETCONF monitoring not fully standardized in this generator version.\n"

class TelegrafConfigGenerator:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def regenerate_config(self, output_path: str = "telegraf.conf") -> str:
        try:
            devices = self.db.get_all_devices()
            logger.info(f"Generating Multi-Protocol Telegraf config for {len(devices)} devices.")
            
            content = TELEGRAF_TEMPLATE_HEADER
            content += generate_syslog_input() # Helper if needed, but we put it in HEADER
            content += generate_snmp_input(devices)
            content += generate_ping_input(devices)
            content += generate_gnmi_input(devices)
            content += generate_netconf_block(devices)
            
            with open(output_path, "w") as f:
                f.write(content)
            
            logger.info(f"Telegraf config written to {output_path}")
            return content
        except Exception as e:
            logger.error(f"Failed to generate Telegraf config: {e}")
            raise

def generate_syslog_input():
    # Already in header
    return ""

if __name__ == "__main__":
    db = DatabaseManager("net_topo.db")
    gen = TelegrafConfigGenerator(db)
    print(gen.regenerate_config())
