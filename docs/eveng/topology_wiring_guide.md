# CogniShield Lab — 3-Tier Campus Topology Wiring Guide
# EVE-NG: 192.168.1.248
# ============================================================

## Device Inventory (12 Devices)

| Role         | Hostname         | Image            | Mgmt IP         | Mgmt Intf        | Vendor  |
|------------- |----------------- |----------------- |---------------- |----------------- |---------|
| Core         | Cisco_Core_1     | CSR 1000v        | 192.168.1.40    | GigabitEthernet6 | Cisco   |
| Core         | Cisco_Core_2     | CSR 1000v        | 192.168.1.41    | GigabitEthernet6 | Cisco   |
| Distribution | Dist_Cisco_SW1   | Cisco IOL L3     | 192.168.1.42    | Ethernet0/0      | Cisco   |
| Distribution | Dist_Huawei_SW2  | Huawei CE 12800  | 192.168.1.43    | MEth0/0/0        | Huawei  |
| Distribution | Dist_Cisco_SW3   | Cisco IOL L3     | 192.168.1.44    | Ethernet0/0      | Cisco   |
| Distribution | Dist_Huawei_SW4  | Huawei CE 12800  | 192.168.1.45    | MEth0/0/0        | Huawei  |
| Access       | Acc_Cisco_1      | Cisco IOL L2     | 192.168.1.46    | Vlan100 SVI      | Cisco   |
| Access       | Acc_Cisco_2      | Cisco IOL L2     | 192.168.1.47    | Vlan100 SVI      | Cisco   |
| Access       | Acc_Huawei_1     | Huawei CE 6800   | 192.168.1.48    | MEth0/0/0        | Huawei  |
| Access       | Acc_Cisco_3      | Cisco IOL L2     | 192.168.1.49    | Vlan100 SVI      | Cisco   |
| Access       | Acc_Cisco_4      | Cisco IOL L2     | 192.168.1.50    | Vlan100 SVI      | Cisco   |
| Access       | Acc_Juniper_1    | Juniper vEX      | 192.168.1.51    | em0              | Juniper |

## IP Addressing

- **Management VLAN 100**: 192.168.1.0/24 (bridged to host LAN via EVE-NG Cloud0)
- **Data VLANs**:
  - VLAN 10: 10.10.10.0/24 (Engineering)
  - VLAN 20: 10.10.20.0/24 (Operations)
  - VLAN 30: 10.10.30.0/24 (Guest)
  - VLAN 40: 10.10.40.0/24 (IoT/Sensors)
  - VLAN 100: 192.168.1.0/24 (Management — used by IOL L2 access switches)

### Point-to-Point Subnets (/30)

| Link                            | Subnet        | Side A (IP)  | Side B (IP)  |
|-------------------------------- |-------------- |------------- |------------- |
| Core1 Gi1 ↔ Core2 Gi1          | 10.0.0.0/30   | .1           | .2           |
| Core1 Gi2 ↔ Dist_Cisco_SW1 e0/1| 10.0.1.0/30   | .1           | .2           |
| Core1 Gi3 ↔ Dist_Huawei_SW2 GE1/0/0 | 10.0.1.4/30 | .5        | .6           |
| Core1 Gi4 ↔ Dist_Cisco_SW3 e0/1| 10.1.1.8/30   | .9           | .10          |
| Core1 Gi5 ↔ Dist_Huawei_SW4 GE1/0/0 | 10.1.1.12/30| .13       | .14          |
| Core2 Gi2 ↔ Dist_Cisco_SW1 e0/2| 10.0.2.0/30   | .1           | .2           |
| Core2 Gi3 ↔ Dist_Huawei_SW2 GE1/0/1 | 10.0.2.4/30| .5         | .6           |
| Core2 Gi4 ↔ Dist_Cisco_SW3 e0/2| 10.0.2.8/30   | .9           | .10          |
| Core2 Gi5 ↔ Dist_Huawei_SW4 GE1/0/1 | 10.0.2.12/30| .13       | .14          |

## Layer Interconnections

### Core ↔ Core (OSPF Area 0)
```
Cisco_Core_1  Gi1 ──── Gi1  Cisco_Core_2     10.0.0.0/30
```

### Core ↔ Distribution (OSPF Area 0 — each core connects to all 4 dist)
```
Cisco_Core_1  Gi2 ──── e0/1     Dist_Cisco_SW1     10.0.1.0/30
Cisco_Core_1  Gi3 ──── GE1/0/1  Dist_Huawei_SW2    10.0.1.4/30
Cisco_Core_1  Gi4 ──── e0/1     Dist_Cisco_SW3     10.1.1.8/30
Cisco_Core_1  Gi5 ──── GE1/0/0  Dist_Huawei_SW4    10.1.1.12/30

Cisco_Core_2  Gi2 ──── e0/2     Dist_Cisco_SW1     10.0.2.0/30
Cisco_Core_2  Gi3 ──── GE1/0/2  Dist_Huawei_SW2    10.0.2.4/30
Cisco_Core_2  Gi4 ──── e0/2     Dist_Cisco_SW3     10.0.2.8/30
Cisco_Core_2  Gi5 ──── GE1/0/1  Dist_Huawei_SW4    10.0.2.12/30
```

### Distribution Pair Inter-Switch Links (L2 trunk — peer link)
```
Pair A:  Dist_Cisco_SW1  e1/2  ──── GE1/0/5  Dist_Huawei_SW2   trunk: VLAN 10,20,30,100
Pair B:  Dist_Cisco_SW3  e1/2  ──── GE1/0/5  Dist_Huawei_SW4   trunk: VLAN 10,20,30,40,100
```

### Distribution ↔ Access (L2 trunks — dual-homed, each access switch connects to BOTH dist)

**Block A** (Dist_Cisco_SW1 + Dist_Huawei_SW2) — all 3 access switches dual-homed:
```
Dist_Cisco_SW1   e0/3    ──── e0/1    Acc_Cisco_1      trunk: VLAN 10,20,100
Dist_Huawei_SW2  GE1/0/3 ──── e1/0    Acc_Cisco_1      trunk: VLAN 10,20,100

Dist_Cisco_SW1   e1/0    ──── e0/1    Acc_Cisco_2      trunk: VLAN 10,20,100
Dist_Huawei_SW2  GE1/0/4 ──── e1/0    Acc_Cisco_2      trunk: VLAN 10,20,100

Dist_Cisco_SW1   e1/1    ──── GE1/0/1 Acc_Huawei_1     trunk: VLAN 10,30,100
Dist_Huawei_SW2  GE1/0/0 ──── GE1/0/5 Acc_Huawei_1     trunk: VLAN 10,30,100
```
VRRP virtual IPs: VLAN 10 → 10.10.10.254, VLAN 20 → 10.10.20.254, VLAN 30 → 10.10.30.254
Dist_Cisco_SW1 active for VLAN 10,20 | Dist_Huawei_SW2 active for VLAN 30

**Block B** (Dist_Cisco_SW3 + Dist_Huawei_SW4) — all 3 access switches dual-homed:
```
Dist_Cisco_SW3   e0/3    ──── e0/1    Acc_Cisco_3      trunk: VLAN 20,30,100
Dist_Huawei_SW4  GE1/0/3 ──── e1/0    Acc_Cisco_3      trunk: VLAN 20,30,100

Dist_Cisco_SW3   e1/0    ──── e0/1    Acc_Cisco_4      trunk: VLAN 20,30,100
Dist_Huawei_SW4  GE1/0/4 ──── e1/0    Acc_Cisco_4      trunk: VLAN 20,30,100

Dist_Cisco_SW3   e1/1    ──── ge-0/0/0  Acc_Juniper_1  trunk: VLAN 10,40,100
Dist_Huawei_SW4  GE1/0/2 ──── ge-0/0/1  Acc_Juniper_1  trunk: VLAN 10,40,100
```
VRRP virtual IPs: VLAN 10 → 10.10.10.254, VLAN 20 → 10.10.20.254, VLAN 30 → 10.10.30.254, VLAN 40 → 10.10.40.254
Dist_Cisco_SW3 active for VLAN 20,30 | Dist_Huawei_SW4 active for VLAN 10,40

### Management Cloud
| Platform       | Cloud0 Interface     | Notes                                        |
|--------------- |--------------------- |----------------------------------------------|
| CSR 1000v      | GigabitEthernet6     | Direct IP on Gi6                             |
| Cisco IOL L3   | Ethernet0/0          | Direct IP on e0/0                            |
| Cisco IOL L2   | Ethernet0/0          | Access VLAN 100 port + Vlan100 SVI + default-gw |
| Huawei CE12800 | MEth0/0/0            | Direct IP (management Ethernet)              |
| Huawei CE6800  | MEth0/0/0            | Direct IP (management Ethernet)              |
| Juniper vEX    | em0                  | Direct IP + static default route             |

## EVE-NG Setup Steps

1. **Create new lab** in EVE-NG web UI (http://192.168.1.248)
2. **Add Management Cloud**: Type = "Management(Cloud0)"
3. **Add nodes** (12 devices per table above)
4. **Wire management interfaces** to Cloud0 (one port per device)
5. **Wire inter-device links** per the wiring diagram above
6. **Start all nodes**
7. **Apply per-device configs** from `docs/eveng/configs/`:
   - `core1_cisco_csr1000v.cfg` → Cisco_Core_1
   - `core2_cisco_csr1000v.cfg` → Cisco_Core_2
   - `dist1_cisco_iol3.cfg` → Dist_Cisco_SW1
   - `dist2_huawei_ce12800.cfg` → Dist_Huawei_SW2
   - `dist3_cisco_iol3.cfg` → Dist_Cisco_SW3
   - `dist4_huawei_ce12800.cfg` → Dist_Huawei_SW4
   - `acc1_cisco_iol2.cfg` → Acc_Cisco_1
   - `acc2_cisco_iol2.cfg` → Acc_Cisco_2
   - `acc3_huawei_ce6800.cfg` → Acc_Huawei_1
   - `acc4_cisco_iol2.cfg` → Acc_Cisco_3
   - `acc5_cisco_iol2.cfg` → Acc_Cisco_4
   - `acc6_juniper_vex.cfg` → Acc_Juniper_1
8. **Verify SNMP** from 192.168.1.250:
   ```bash
   snmpwalk -v2c -c cognishield 192.168.1.40 sysDescr
   ```
9. **Verify SSH** from CogniShield host:
   ```bash
   ssh cognishield@192.168.1.40
   ```

## OSPF Design

- **Area 0**: All core (2) + distribution (4) devices = 6 OSPF speakers
- **Router IDs**: Management IPs (.40–.45)
- **Passive interfaces**: Management interface (Gi6 / e0/0 / MEth), access-facing trunk ports
- Access switches do NOT run OSPF (L2 only, use `ip default-gateway 192.168.1.1`)

## Credentials

| Service     | Username     | Password              |
|------------ |------------- |---------------------- |
| SSH         | cognishield  | CogniShield@Lab2026   |
| Enable      | —            | CogniShield@Lab2026   |
| SNMP        | community    | cognishield (RO)      |
