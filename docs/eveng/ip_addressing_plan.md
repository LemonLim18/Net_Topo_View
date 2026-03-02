# CogniShield Lab — Master IP Addressing Plan
# ============================================================
# Based on actual EVE-NG topology (12 devices)
# Core 1 running config verified 2026-03-02
# ============================================================

## Device Inventory (actual)

| #  | Hostname          | Image           | Mgmt IP        | Vendor  | Role         |
|----|-------------------|-----------------|----------------|---------|--------------|
| 1  | Cisco_Core_1      | CSR 1000v       | 192.168.1.40   | Cisco   | Core         |
| 2  | Cisco_Core_2      | CSR 1000v       | 192.168.1.41   | Cisco   | Core         |
| 3  | Dist_Cisco_SW1    | IOL L3          | 192.168.1.42   | Cisco   | Distribution |
| 4  | Dist_Huawei_SW2   | CE 12800        | 192.168.1.43   | Huawei  | Distribution |
| 5  | Dist_Cisco_SW3    | IOL L3          | 192.168.1.44   | Cisco   | Distribution |
| 6  | Dist_Huawei_SW4   | CE 12800        | 192.168.1.45   | Huawei  | Distribution |
| 7  | Acc_Cisco_1       | IOL L2          | 192.168.1.46   | Cisco   | Access       |
| 8  | Acc_Cisco_2       | IOL L2          | 192.168.1.47   | Cisco   | Access       |
| 9  | Acc_Huawei_1      | CE 6800         | 192.168.1.48   | Huawei  | Access       |
| 10 | Acc_Cisco_3       | IOL L2          | 192.168.1.49   | Cisco   | Access       |
| 11 | Acc_Cisco_4       | IOL L2          | 192.168.1.50   | Cisco   | Access       |
| 12 | Acc_Juniper_1     | Juniper vEX     | 192.168.1.51   | Juniper | Access       |

## Access Switch Grouping

- **Block A** (Dist_Cisco_SW1 + Dist_Huawei_SW2):
  - Acc_Cisco_1 (.46) — IOL L2
  - Acc_Cisco_2 (.47) — IOL L2
  - Acc_Huawei_1 (.48) — CE 6800

- **Block B** (Dist_Cisco_SW3 + Dist_Huawei_SW4):
  - Acc_Cisco_3 (.49) — IOL L2
  - Acc_Cisco_4 (.50) — IOL L2
  - Acc_Juniper_1 (.51) — vEX

## Point-to-Point Links (L3, /30 subnets)

### Core ↔ Core
| Link                | Subnet        | Side A (Core 1)      | Side B (Core 2)      |
|---------------------|---------------|----------------------|----------------------|
| Core1 ↔ Core2       | 10.0.0.0/30   | Gi1 = 10.0.0.1      | Gi1 = 10.0.0.2      |

### Core 1 ↔ Distribution (from running config)
| Link                    | Subnet         | Core 1 side          | Dist side             |
|-------------------------|----------------|----------------------|-----------------------|
| Core1 ↔ Dist_Cisco_SW1  | 10.0.1.0/30   | Gi2 = 10.0.1.1      | e0/1 = 10.0.1.2      |
| Core1 ↔ Dist_Huawei_SW2 | 10.0.1.4/30   | Gi3 = 10.0.1.5      | GE1/0/1 = 10.0.1.6   |
| Core1 ↔ Dist_Cisco_SW3  | 10.1.1.8/30   | Gi4 = 10.1.1.9      | e0/1 = 10.1.1.10     |
| Core1 ↔ Dist_Huawei_SW4 | 10.1.1.12/30  | Gi5 = 10.1.1.13     | GE1/0/0 = 10.1.1.14  |

### Core 2 ↔ Distribution
| Link                    | Subnet         | Core 2 side          | Dist side             |
|-------------------------|----------------|----------------------|-----------------------|
| Core2 ↔ Dist_Cisco_SW1  | 10.0.2.0/30   | Gi2 = 10.0.2.1      | e0/2 = 10.0.2.2      |
| Core2 ↔ Dist_Huawei_SW2 | 10.0.2.4/30   | Gi3 = 10.0.2.5      | GE1/0/2 = 10.0.2.6   |
| Core2 ↔ Dist_Cisco_SW3  | 10.0.2.8/30   | Gi4 = 10.0.2.9      | e0/2 = 10.0.2.10     |
| Core2 ↔ Dist_Huawei_SW4 | 10.0.2.12/30  | Gi5 = 10.0.2.13     | GE1/0/1 = 10.0.2.14  |

### Distribution Pair Inter-Switch Links (L2 peer-link trunks — no IPs)
| Pair   | Side A                      | Side B                      | VLANs allowed          |
|--------|-----------------------------|-----------------------------|------------------------|
| Pair A | Dist_Cisco_SW1 e1/2         | Dist_Huawei_SW2 GE1/0/5    | 10, 20, 30, 100       |
| Pair B | Dist_Cisco_SW3 e1/2         | Dist_Huawei_SW4 GE1/0/5    | 10, 20, 30, 40, 100   |

## Distribution ↔ Access Links (L2 trunks — no IPs on the link)

### Block A (dual-homed: each access switch to BOTH Dist_Cisco_SW1 + Dist_Huawei_SW2)
| Dist side                         | Access side                  | VLANs allowed      |
|-----------------------------------|------------------------------|---------------------|
| Dist_Cisco_SW1 e0/3               | Acc_Cisco_1 e0/1             | 10, 20, 100         |
| Dist_Huawei_SW2 GE1/0/3           | Acc_Cisco_1 e1/0             | 10, 20, 100         |
| Dist_Cisco_SW1 e1/0               | Acc_Cisco_2 e0/1             | 10, 20, 100         |
| Dist_Huawei_SW2 GE1/0/4           | Acc_Cisco_2 e1/0             | 10, 20, 100         |
| Dist_Cisco_SW1 e1/1               | Acc_Huawei_1 GE1/0/1         | 10, 30, 100         |
| Dist_Huawei_SW2 GE1/0/0           | Acc_Huawei_1 GE1/0/5         | 10, 30, 100         |

### Block B (dual-homed: each access switch to BOTH Dist_Cisco_SW3 + Dist_Huawei_SW4)
| Dist side                         | Access side                  | VLANs allowed      |
|-----------------------------------|------------------------------|---------------------|
| Dist_Cisco_SW3 e0/3               | Acc_Cisco_3 e0/1             | 20, 30, 100         |
| Dist_Huawei_SW4 GE1/0/3           | Acc_Cisco_3 e1/0             | 20, 30, 100         |
| Dist_Cisco_SW3 e1/0               | Acc_Cisco_4 e0/1             | 20, 30, 100         |
| Dist_Huawei_SW4 GE1/0/4           | Acc_Cisco_4 e1/0             | 20, 30, 100         |
| Dist_Cisco_SW3 e1/1               | Acc_Juniper_1 ge-0/0/0       | 10, 40, 100         |
| Dist_Huawei_SW4 GE1/0/2           | Acc_Juniper_1 ge-0/0/1       | 10, 40, 100         |

## VLAN Plan

| VLAN | Name         | Subnet           | VRRP VIP (gateway) | Block A SVIs                              | Block B SVIs                              |
|------|------------- |------------------|--------------------|--------------------------------------------|-------------------------------------------|
| 10   | Engineering  | 10.10.10.0/24    | 10.10.10.254       | SW1 .1 (pri 110), SW2 .2 (pri 100)        | SW3 .4 (pri 100), SW4 .3 (pri 110)       |
| 20   | Operations   | 10.10.20.0/24    | 10.10.20.254       | SW1 .1 (pri 110), SW2 .2 (pri 100)        | SW3 .3 (pri 110), SW4 .4 (pri 100)       |
| 30   | Guest        | 10.10.30.0/24    | 10.10.30.254       | SW1 .2 (pri 100), SW2 .1 (pri 110)        | SW3 .3 (pri 110), SW4 .4 (pri 100)       |
| 40   | IoT          | 10.10.40.0/24    | 10.10.40.254       | — (not in Block A)                         | SW3 .2 (pri 100), SW4 .1 (pri 110)       |
| 100  | Management   | 192.168.1.0/24   | 192.168.1.1 (LAN GW)| —                                        | —                                         |

> **VRRP note:** Within each dist pair, one switch is VRRP-active (priority 110) and the other
> is standby (priority 100) per VLAN for load balancing. VIP = .254 on every data VLAN.
> Block A and Block B are separate L2 domains; both blocks reuse the same VLAN IDs and
> subnets, which OSPF sees as equal-cost paths (acceptable for lab).

## Management Interface Per Platform

| Platform     | Cloud0 port              | Where mgmt IP lives               |
|------------- |--------------------------|-------------------------------------|
| CSR 1000v    | GigabitEthernet6         | IP directly on Gi6                  |
| IOL L3       | Ethernet0/0              | IP directly on e0/0                 |
| CE 12800     | MEth0/0/0                | IP directly on MEth0/0/0            |
| IOL L2       | Ethernet0/0              | interface Vlan100 SVI + ip default-gateway |
| CE 6800      | MEth0/0/0                | IP directly on MEth0/0/0            |
| Juniper vEX  | em0                      | IP directly on em0                  |

## Credentials (all devices)

| Service | Username    | Password            |
|---------|-------------|---------------------|
| SSH     | cognishield | CogniShield@Lab2026 |
| Enable  | —           | CogniShield@Lab2026 |
| SNMP    | community   | cognishield (RO)    |
