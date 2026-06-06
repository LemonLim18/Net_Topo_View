#!/usr/bin/env bash
# =============================================================
# CogniShield Lab — VM 192.168.1.250 Setup
# Services: OpenDaylight SDN Controller, InfluxDB 2.x, Telegraf
# Run as root on Ubuntu Server
# =============================================================
set -euo pipefail

echo "=== CogniShield VM-250 Setup (SDN + Telemetry) ==="

# -----------------------------------------------------------
# 1. System basics
# -----------------------------------------------------------
apt-get update && apt-get upgrade -y
apt-get install -y curl wget gnupg2 apt-transport-https \
    software-properties-common net-tools snmp snmp-mibs-downloader \
    openjdk-17-jre-headless unzip

# Enable MIBs
sed -i 's/^mibs :$/# mibs :/' /etc/snmp/snmp.conf 2>/dev/null || true

# -----------------------------------------------------------
# 2. InfluxDB 2.x (should already be installed — verify/setup)
# -----------------------------------------------------------
echo "--- InfluxDB setup ---"
if systemctl is-active --quiet influxdb; then
    echo "InfluxDB is already running"
else
    echo "Starting InfluxDB..."
    systemctl enable --now influxdb
fi

# Initial setup (idempotent — will fail gracefully if already done)
influx setup \
    --org cognishield \
    --bucket network_telemetry \
    --username admin \
    --password CogniShield@Lab2026 \
    --token cognishield-lab-token-2026 \
    --retention 30d \
    --force 2>/dev/null || echo "InfluxDB already initialized"

# Create additional buckets if needed
influx bucket create --name syslog_events --retention 7d --org cognishield 2>/dev/null || true
influx bucket create --name config_snapshots --retention 90d --org cognishield 2>/dev/null || true

echo "InfluxDB: http://192.168.1.250:8086"

# -----------------------------------------------------------
# 3. Telegraf (should already be installed — deploy config)
# -----------------------------------------------------------
echo "--- Telegraf config deployment ---"
if systemctl is-active --quiet telegraf; then
    echo "Telegraf is already running"
else
    systemctl enable --now telegraf
fi

# Backup existing config
cp /etc/telegraf/telegraf.conf /etc/telegraf/telegraf.conf.bak.$(date +%Y%m%d) 2>/dev/null || true

echo "
NOTE: Deploy the CogniShield telegraf.conf from the project root:
  scp telegraf.conf root@192.168.1.250:/etc/telegraf/telegraf.conf
  ssh root@192.168.1.250 'systemctl restart telegraf'
"

# -----------------------------------------------------------
# 4. OpenDaylight (Karaf-based SDN Controller)
# -----------------------------------------------------------
echo "--- OpenDaylight installation ---"
ODL_VERSION="0.18.3"
ODL_DIR="/opt/opendaylight"

if [ -d "$ODL_DIR" ]; then
    echo "OpenDaylight directory exists at $ODL_DIR"
else
    echo "Downloading OpenDaylight..."
    mkdir -p /opt
    cd /tmp

    # Download ODL (Argon release)
    wget -q "https://nexus.opendaylight.org/content/repositories/opendaylight.release/org/opendaylight/integration/karaf/${ODL_VERSION}/karaf-${ODL_VERSION}.tar.gz" \
        -O odl.tar.gz || {
        echo "Primary download failed, trying alternative..."
        wget -q "https://nexus.opendaylight.org/content/repositories/public/org/opendaylight/integration/karaf/${ODL_VERSION}/karaf-${ODL_VERSION}.tar.gz" \
            -O odl.tar.gz || {
            echo "WARNING: Could not download ODL automatically."
            echo "Manual install: Download from https://docs.opendaylight.org/en/latest/downloads.html"
            echo "Extract to $ODL_DIR"
        }
    }

    if [ -f odl.tar.gz ]; then
        tar xzf odl.tar.gz -C /opt/
        mv /opt/karaf-* "$ODL_DIR"
        rm -f odl.tar.gz
    fi
fi

# Configure ODL features
if [ -d "$ODL_DIR" ]; then
    echo "Installing ODL features..."

    # Create startup feature config
    cat > "$ODL_DIR/etc/org.apache.karaf.features.cfg.custom" << 'FEAT'
# CogniShield required ODL features:
# - odl-restconf          : RESTCONF northbound API
# - odl-l2switch-switch   : L2 forwarding / MAC learning
# - odl-mdsal-apidocs     : Swagger API docs
# - odl-dluxapps-applications : DLUX web UI
FEAT

    # Create systemd service
    cat > /etc/systemd/system/opendaylight.service << EOF
[Unit]
Description=OpenDaylight SDN Controller
After=network.target

[Service]
Type=forking
Environment=JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ExecStart=${ODL_DIR}/bin/start
ExecStop=${ODL_DIR}/bin/stop
User=root
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable opendaylight

    echo "
OpenDaylight installed at $ODL_DIR
To start:  systemctl start opendaylight
To access: http://192.168.1.250:8181/apidoc/explorer/index.html
Credentials: admin / admin

After first start, install features via Karaf CLI:
  ssh -p 8101 karaf@localhost   (password: karaf)
  feature:install odl-restconf odl-l2switch-switch-ui odl-mdsal-apidocs odl-dluxapps-applications
"
else
    echo "WARNING: ODL not installed. See manual instructions above."
fi

# -----------------------------------------------------------
# 5. Firewall rules
# -----------------------------------------------------------
echo "--- Firewall rules ---"
if command -v ufw &> /dev/null; then
    ufw allow 8086/tcp    # InfluxDB
    ufw allow 8181/tcp    # ODL RESTCONF
    ufw allow 8101/tcp    # ODL Karaf SSH
    ufw allow 6633/tcp    # OpenFlow
    ufw allow 6653/tcp    # OpenFlow 1.3
    ufw allow 6514/udp    # Syslog
    ufw allow 2055/udp    # NetFlow
    ufw allow 161/udp     # SNMP (outbound polling)
    echo "UFW rules added"
else
    echo "UFW not installed — ensure ports 8086, 8181, 6514, 2055 are open"
fi

# -----------------------------------------------------------
# 6. Verification
# -----------------------------------------------------------
echo ""
echo "=== Verification ==="
echo "InfluxDB:    $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8086/health)"
echo "Telegraf:    $(systemctl is-active telegraf)"
echo "ODL:         $(systemctl is-active opendaylight 2>/dev/null || echo 'not started yet')"
echo ""
echo "=== VM-250 Setup Complete ==="
echo "Next steps:"
echo "  1. Deploy telegraf.conf from CogniShield project"
echo "  2. Start OpenDaylight: systemctl start opendaylight"
echo "  3. Install ODL features via Karaf CLI"
echo "  4. Test RESTCONF: curl -u admin:admin http://192.168.1.250:8181/restconf/operational/network-topology:network-topology"
