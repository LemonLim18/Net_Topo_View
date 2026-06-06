#!/usr/bin/env bash
# =============================================================
# CogniShield Lab — VM 192.168.1.252 Setup  
# Services: NetBox, Prometheus, Grafana, SNMP Exporter
# Run as root on Ubuntu Server
# =============================================================
set -euo pipefail

echo "=== CogniShield VM-252 Setup (Monitoring + IPAM) ==="

# -----------------------------------------------------------
# 1. System basics
# -----------------------------------------------------------
apt-get update && apt-get upgrade -y
apt-get install -y curl wget gnupg2 apt-transport-https \
    software-properties-common net-tools

# -----------------------------------------------------------
# 2. Prometheus Configuration
# -----------------------------------------------------------
echo "--- Prometheus configuration ---"
PROM_DIR="/etc/prometheus"

if systemctl is-active --quiet prometheus; then
    echo "Prometheus is already running"
else
    systemctl enable --now prometheus 2>/dev/null || echo "Prometheus not installed — install first"
fi

# Write scrape config for CogniShield lab
cat > "${PROM_DIR}/cognishield_targets.yml" << 'EOF'
# CogniShield Lab — SNMP Exporter targets (12 devices)
# Auto-generated — edit via CogniShield or re-run setup script

# Core Routers (Cisco CSR 1000v)
- targets:
    - 192.168.1.40
    - 192.168.1.41
  labels:
    role: core
    vendor: cisco
    platform: csr1000v

# Distribution Switches — Cisco IOL L3
- targets:
    - 192.168.1.42
    - 192.168.1.44
  labels:
    role: distribution
    vendor: cisco
    platform: iol_l3

# Distribution Switches — Huawei CE 12800
- targets:
    - 192.168.1.43
    - 192.168.1.45
  labels:
    role: distribution
    vendor: huawei
    platform: ce12800

# Access Switches — Cisco IOL L2
- targets:
    - 192.168.1.46
    - 192.168.1.47
    - 192.168.1.49
    - 192.168.1.50
  labels:
    role: access
    vendor: cisco
    platform: iol_l2

# Access Switch — Huawei CE 6800
- targets:
    - 192.168.1.48
  labels:
    role: access
    vendor: huawei
    platform: ce6800

# Access Switch — Juniper vEX
- targets:
    - 192.168.1.51
  labels:
    role: access
    vendor: juniper
    platform: vex
EOF

# Update prometheus.yml with SNMP exporter job
cat > "${PROM_DIR}/prometheus.yml" << 'EOF'
global:
  scrape_interval: 30s
  evaluation_interval: 30s

rule_files:
  - "cognishield_rules.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: []

scrape_configs:
  # Prometheus self-monitoring
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]

  # SNMP Exporter — network devices
  - job_name: "snmp_exporter"
    scrape_interval: 30s
    scrape_timeout: 20s
    file_sd_configs:
      - files:
          - "cognishield_targets.yml"
    metrics_path: /snmp
    params:
      module: [if_mib]
      community: [cognishield]
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: localhost:9116    # SNMP exporter address

  # Node Exporter — VM self-monitoring
  - job_name: "node_exporter"
    static_configs:
      - targets: ["localhost:9100"]
        labels:
          instance: "vm-252"

  # CogniShield backend health
  - job_name: "cognishield"
    scrape_interval: 15s
    static_configs:
      - targets: ["192.168.1.3:8000"]
        labels:
          service: "cognishield-backend"
    metrics_path: /monitor/status
EOF

# Basic alerting rules
cat > "${PROM_DIR}/cognishield_rules.yml" << 'EOF'
groups:
  - name: cognishield_network
    interval: 30s
    rules:
      # Device unreachable
      - alert: DeviceDown
        expr: up{job="snmp_exporter"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Network device {{ $labels.instance }} is unreachable"
          description: "SNMP polling failed for {{ $labels.instance }} ({{ $labels.vendor }}/{{ $labels.role }})"

      # High interface errors
      - alert: HighInterfaceErrors
        expr: rate(ifInErrors[5m]) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High interface errors on {{ $labels.instance }}"
          description: "Interface {{ $labels.ifDescr }} has >10 errors/sec"

      # Interface down
      - alert: InterfaceDown
        expr: ifOperStatus == 2
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Interface down on {{ $labels.instance }}"
          description: "{{ $labels.ifDescr }} is operationally down"
EOF

systemctl restart prometheus 2>/dev/null || echo "Restart prometheus manually after install"
echo "Prometheus: http://192.168.1.252:9090"

# -----------------------------------------------------------
# 3. SNMP Exporter Configuration
# -----------------------------------------------------------
echo "--- SNMP Exporter configuration ---"
SNMP_EXP_DIR="/etc/snmp_exporter"
mkdir -p "$SNMP_EXP_DIR"

# Check if SNMP exporter is installed
if command -v snmp_exporter &> /dev/null || [ -f /usr/local/bin/snmp_exporter ]; then
    echo "SNMP Exporter binary found"
else
    echo "Installing SNMP Exporter..."
    SNMP_EXP_VERSION="0.26.0"
    cd /tmp
    wget -q "https://github.com/prometheus/snmp_exporter/releases/download/v${SNMP_EXP_VERSION}/snmp_exporter-${SNMP_EXP_VERSION}.linux-amd64.tar.gz" \
        -O snmp_exporter.tar.gz
    tar xzf snmp_exporter.tar.gz
    cp snmp_exporter-*/snmp_exporter /usr/local/bin/
    rm -rf snmp_exporter-* snmp_exporter.tar.gz

    # Create systemd service
    cat > /etc/systemd/system/snmp_exporter.service << 'EOF'
[Unit]
Description=SNMP Exporter for Prometheus
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/snmp_exporter --config.file=/etc/snmp_exporter/snmp.yml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now snmp_exporter
fi

echo "SNMP Exporter: http://192.168.1.252:9116"

# -----------------------------------------------------------
# 4. Grafana Configuration
# -----------------------------------------------------------
echo "--- Grafana configuration ---"

if systemctl is-active --quiet grafana-server; then
    echo "Grafana is already running"
else
    systemctl enable --now grafana-server 2>/dev/null || echo "Grafana not installed — install first"
fi

# Provision Prometheus data source
GRAFANA_PROV="/etc/grafana/provisioning/datasources"
mkdir -p "$GRAFANA_PROV"

cat > "${GRAFANA_PROV}/cognishield.yml" << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: true

  - name: InfluxDB
    type: influxdb
    access: proxy
    url: http://192.168.1.250:8086
    jsonData:
      version: Flux
      organization: cognishield
      defaultBucket: network_telemetry
    secureJsonData:
      token: cognishield-lab-token-2026
    editable: true
EOF

systemctl restart grafana-server 2>/dev/null || echo "Restart grafana-server manually"
echo "Grafana: http://192.168.1.252:3000  (admin / admin)"

# -----------------------------------------------------------
# 5. NetBox wiring
# -----------------------------------------------------------
echo "--- NetBox verification ---"
if systemctl is-active --quiet netbox; then
    echo "NetBox is running"
elif systemctl is-active --quiet netbox-rq; then
    echo "NetBox RQ worker is running (NetBox via gunicorn)"
else
    echo "NetBox status unknown — check manually: systemctl status netbox*"
fi

echo "NetBox: http://192.168.1.252:8000"
echo "
NOTE: Populate NetBox with lab devices using the script:
  python3 docs/eveng/populate_netbox.py
"

# -----------------------------------------------------------
# 6. Firewall rules
# -----------------------------------------------------------
echo "--- Firewall rules ---"
if command -v ufw &> /dev/null; then
    ufw allow 9090/tcp    # Prometheus
    ufw allow 9100/tcp    # Node Exporter
    ufw allow 9116/tcp    # SNMP Exporter
    ufw allow 3000/tcp    # Grafana
    ufw allow 8000/tcp    # NetBox
    echo "UFW rules added"
else
    echo "UFW not installed — ensure ports 9090, 9116, 3000, 8000 are open"
fi

# -----------------------------------------------------------
# 7. Verification
# -----------------------------------------------------------
echo ""
echo "=== Verification ==="
echo "Prometheus:    $(curl -s -o /dev/null -w '%{http_code}' http://localhost:9090/-/healthy 2>/dev/null || echo 'unreachable')"
echo "SNMP Exporter: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:9116/ 2>/dev/null || echo 'unreachable')"
echo "Grafana:       $(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/health 2>/dev/null || echo 'unreachable')"
echo "NetBox:        $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/ 2>/dev/null || echo 'unreachable')"
echo ""
echo "=== VM-252 Setup Complete ==="
echo "Next steps:"
echo "  1. Verify SNMP exporter can reach devices:"
echo "     curl 'http://localhost:9116/snmp?target=192.168.1.40&module=if_mib'"
echo "  2. Check Prometheus targets: http://192.168.1.252:9090/targets"
echo "  3. Import Grafana dashboards from docs/eveng/grafana_dashboards/"
echo "  4. Populate NetBox: python3 docs/eveng/populate_netbox.py"
