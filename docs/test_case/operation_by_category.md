# ✅ Network Automation Intent Validation Checklist

---

## 1️⃣ VLAN

- [ /] Create VLAN 67 named **ShuYing** on access switch 1, 2, and 3  
  **Current Behavior:**  



- [ /] Create VLAN 120 named **WEB_Vlan** on SW1 and SW2  
**Current Behavior:**  


- [ ] Create VLAN 30 and apply it as access VLAN on GigabitEthernet1/0/1 on SW1  
**Current Behavior:**  


- [ ] Create VLAN 10 and allow it on trunk port GigabitEthernet1/0/2 on SW1  
**Current Behavior:**  


- [ ] Create VLAN 200 named **Guest** and set native VLAN 200 on GigabitEthernet1/0/3  
**Current Behavior:**  


- [ ] Create VLAN on devices *(generic / ambiguous test)*  
**Current Behavior:**  


---

## 2️⃣ Interface

- [ ] Admin-down all interfaces on SW1  
**Current Behavior:**  


- [ ] Shut down GigabitEthernet1/0/1 on router 2  
**Current Behavior:**  


- [ ] Configure GigabitEthernet1/0/1 and GigabitEthernet1/0/2 as access ports on SW1  
**Current Behavior:**  


- [ ] Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1  
**Current Behavior:**  


- [ ] Set GigabitEthernet1/0/3 as routed interface with IP address 192.168.10.1/24 on SW1  
**Current Behavior:**  


- [ ] Configure interfaces on devices *(generic / ambiguous test)*  
**Current Behavior:**  


---

## 3️⃣ ACL

- [ ] Create ACL 2000 to permit SSH from 192.168.1.0/24 on SW1  
**Current Behavior:**  


- [ ] Create ACL named **BLOCK_HTTP** to deny TCP port 80 on SW1  
**Current Behavior:**  


- [ ] Apply ACL 3000 inbound on GigabitEthernet1/0/1 on SW1  
**Current Behavior:**  


- [ ] Apply ACL **BLOCK_HTTP** outbound on GigabitEthernet1/0/2 on SW1  
**Current Behavior:**  


- [ ] Create ACL to deny ICMP and apply it on access switch 3  
**Current Behavior:**  


- [ ] Add ACL rule on devices *(generic / ambiguous test)*  
**Current Behavior:**  


---

## 4️⃣ QoS

- [ ] Create QoS policy **VOICE_POLICY** on SW1  
**Current Behavior:**  


- [ ] Configure QoS to prioritize voice traffic on GigabitEthernet1/0/1  
**Current Behavior:**  


- [ ] Apply QoS policy **IN_POLICY** inbound on GigabitEthernet1/0/2 on SW1  
**Current Behavior:**  


- [ ] Mark traffic with DSCP 46 for voice on access switch 2  
**Current Behavior:**  


- [ ] Create policing policy for HTTP traffic on SW1  
**Current Behavior:**  


- [ ] Configure QoS on devices *(generic / ambiguous test)*  
**Current Behavior:**  


---

## 5️⃣ Static Route

- [ ] Add a static route to 10.10.10.0/24 via 192.168.1.1 on SW1  
**Current Behavior:**  


- [ ] Add a default route on router 2  
**Current Behavior:**  


- [ ] Add a static route to 172.16.0.0/16 via GigabitEthernet0/1 on router 2  
**Current Behavior:**  


- [ ] Add a static route to 192.168.50.0/24 with next hop 10.0.0.2 on SW1  
**Current Behavior:**  


- [ ] Add a static route for the management network on devices  
**Current Behavior:**  


- [ ] Create a route on devices *(generic / ambiguous test)*  
**Current Behavior:**  


---

## 6️⃣ OSPF

- [ ] Configure OSPF process 1 on router 2  
**Current Behavior:**  


- [ ] Add network 192.168.1.0/24 to OSPF area 0 on router 2  
**Current Behavior:**  


- [ ] Set router ID 1.1.1.1 and advertise 10.0.0.0/24 in area 0 on SW1  
**Current Behavior:**  


- [ ] Make GigabitEthernet1/0/1 passive in OSPF on router 1  
**Current Behavior:**  


- [ ] Enable OSPF redistribution of static routes on router 2  
**Current Behavior:**  


- [ ] Configure OSPF on devices *(generic / ambiguous test)*  
**Current Behavior:**  


---

## ⚠️ Ambiguity / Validation Tests

- [ ] Configure VLAN on SW1  
**Current Behavior:**  


- [ ] Admin-down all interfaces on SW1 and SW3  
**Current Behavior:**  


- [ ] Apply ACL inbound on GigabitEthernet1/0/1  
**Current Behavior:**  


- [ ] Add static route via next hop 192.168.1.254  
**Current Behavior:**  (paste output here)


- [ ] Put QoS on the uplink port  
**Current Behavior:**  (paste output here)


- [ ] Add OSPF network for the access network  
**Current Behavior:**  (paste output here)