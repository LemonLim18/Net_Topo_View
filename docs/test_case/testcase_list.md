# ✅ Network Automation Intent Validation Checklist

---

## 1️⃣ VLAN

- [/] Create VLAN 67 named ShuYing on access switch 1, 2, and 3  


- [/] Create VLAN 120 named WEB_Vlan on SW1 and SW2  


- [/ *] Create VLAN 30 and apply it as access VLAN on GigabitEthernet1/0/1 on SW1  
**Comment:** Can work but too slow after I select the planned network context device, then wait long then enter vlan name, then wait for , then wait again, figure out why later on. It feels like it uses one category yet merge two categories


Could Use: [Interface **`GigabitEthernet1/0/2`** is not present on **Acc_Cisco_1** in the planned network inventory. Please select a valid interface from the list below. Closest in inventory: Ethernet1/0, Ethernet0/2, Ethernet0/3.]



- [/] Create VLAN 10 and allow it on trunk port GigabitEthernet1/0/2 on SW1  

- [/] Create VLAN 200 named Guest and set native VLAN 200 on GigabitEthernet1/0/3  
**Comment** It asks which VLAN should be allowed in this case, which the only option is VLAN 200. Should I include all or how?

- [/] Create VLAN on devices *(generic / ambiguous test)*  


## 2️⃣ Interface

- [/] Admin-down all interfaces on SW1  
**Comment**

- [/] Shut down GigabitEthernet1/0/1 on router 2  
**Comment**


- [/] Configure GigabitEthernet1/0/1 and GigabitEthernet1/0/2 as access ports on SW1 :

After selecting the devices from the UI picker, it asks me which access vlan should I applied to all selected interfaces. Do you think for this prompt, is it necessary to specify the vlan to be applied for the selected access interface. 

never include the management interface or the interface with the management ip address inside the UI picker, they should not be exposed for user to select in any case.

- [/] Configure GigabitEthernet1/0/5 as trunk and allow VLANs 10, 20, 67 on SW1  

- [/] Set GigabitEthernet1/0/3 with IP address 192.168.10.1/24 on SW1  

- [/] Set GigabitEthernet1/0/3 as routed interface with IP address 192.168.10.1/24 on SW1  

- [/] Set GigabitEthernet1/0/3 with IP address 192.168.10.1  

- [/] Configure interfaces on devices *(generic / ambiguous test)*  
**Comment:** It's good but it takes some time, stucking on this part, identify the reason behind it, and make it faster.


**Reminder**: For the application of the vlan to the trunk/access, that specific vlan need to be created first before it can be applied, else the "changed=0" will show up, because that "VLAN does not exist"

Configure interfaces on devices
Ansible AI
Applying device selection and refreshing the preview…


---

## 3️⃣ OSPF

- [ ] Configure OSPF process 1 on router 2  


- [ ] Add network 192.168.1.0/24 to OSPF area 0 on router 2  


- [ ] Set router ID 1.1.1.1 and advertise 10.0.0.0/24 in area 0 on SW1  

- [ ] Make GigabitEthernet1/0/1 passive in OSPF on router 1  


- [ ] Enable OSPF redistribution of static routes on router 2  


- [ ] Configure OSPF on devices *(generic / ambiguous test)*  


---

## 4️⃣ Static Route

- [ ] Add a static route to 10.10.10.0/24 via 192.168.1.1 on SW1  


- [ ] Add a default route on router 2  

- [ ] Add a static route to 172.16.0.0/16 via GigabitEthernet0/1 on router 2  


- [ ] Add a static route to 192.168.50.0/24 with next hop 10.0.0.2 on SW1  


- [ ] Add a static route for the management network on devices  



- [ ] Create a route on devices *(generic / ambiguous test)*  

---

## 5️⃣ ACL

- [ ] Create ACL 2000 to permit SSH from 192.168.1.0/24 on SW1  


- [ ] Create ACL named BLOCK_HTTP to deny TCP port 80 on SW1  


- [ ] Apply ACL 3000 inbound on GigabitEthernet1/0/1 on SW1  


- [ ] Apply ACL BLOCK_HTTP outbound on GigabitEthernet1/0/2 on SW1  

- [ ] Create ACL to deny ICMP and apply it on access switch 3  


- [ ] Add ACL rule on devices *(generic / ambiguous test)*  


## 6️⃣ QoS

- [ ] Create QoS policy VOICE_POLICY on SW1  


- [ ] Configure QoS to prioritize voice traffic on GigabitEthernet1/0/1  


- [ ] Apply QoS policy IN_POLICY inbound on GigabitEthernet1/0/2 on SW1  

- [ ] Mark traffic with DSCP 46 for voice on access switch 2  


- [ ] Create policing policy for HTTP traffic on SW1  
**Pipeline Trace**


  **Parsed Intent**  


- [ ] Configure QoS on devices *(generic / ambiguous test)*  

---


---

## ⚠️ Ambiguity / Validation Tests

- [ ] Configure VLAN on SW1  

- [ ] Admin-down all interfaces on SW1 and SW3  


- [ ] Apply ACL inbound on GigabitEthernet1/0/1  

- [ ] Add static route via next hop 192.168.1.254  

- [ ] Put QoS on the uplink port  

- [ ] Add OSPF network for the access network  
