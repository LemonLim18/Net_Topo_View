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

- [/] Configure OSPF process 1 on router 2 Configure OSPF process 1 on router 2  


- [/] Add network 192.168.1.0/24 to OSPF area 0 on router 2  


- [/] Set router ID 1.1.1.1 and advertise 10.0.0.0/24 in area 0 on SW1  

- [/] Make GigabitEthernet1/0/1 passive in OSPF on router 1  

**Reminder**: The router id can be avoided in this prompt.


- [/] Enable OSPF redistribution of static routes on router 2  


- [/] Configure OSPF on devices *(generic / ambiguous test)*  



**Reminder**: Collect OSPF missing fields in one pass (ordered, consecutive)

Updated configure_ospf_conditional_disambiguation_fields() in backend/ansible/intent_validation.py
It now returns all missing fields at once in order:
network_address
prefix_length
area
So user can fill them in one flow, no extra backend re-ask cycle.


Do this for other operations, to ensure a one-go question asking.
---

## 4️⃣ Static Route

- [/] Add a static route to 10.10.10.0/24 via 192.168.1.1 on SW1  

**Preview Modal:**

- [/] Add a default route on router 2  

- [/] Add a static route to 172.16.0.0/16 via GigabitEthernet0/1 on router 2  


- [/] Add a static route to 192.168.50.0/24 with next hop 10.0.0.2 on SW1  


- [Skip] Add a static route for the management network on devices 
**Reminder**: For this kind of case, will be put on hold first. The first things to do is to store the management network information, so that mapping can be done by storing the map between the network information(name, and the network segment/mask). Then, two ways can be used to resolve for the static route punya outgoing interface or the nexthop ip address: The first one is to make the admin to register every static route already inside the netbox to get the information predefined. The second one is let the system have the knowledge of the all network topo graph, then from that path determine the route from what devices to what devices, then recommend the shortest link to reach the management. Choose the closest next neighbor and then get its information thru the lldp, and construct it.

- [] Create a static route between the device A to device B.
**Reminder TO-DO LIST**: Do the same thing by recommending the shortest path between the devices and then get those info, and then create the static route. The second advanced things can be done is to show a graph which the user can click the path nodes from the source device to the target device. And based on the neighbor info retrieved, the static route will be created for all involved nodes, with the correct info.

**Reminder TO-DO LIST**: For the UI in the Ansible studio chat, allows when user presses up and down, it can go back to the previous and the next prompt accordingly.

And for the RAG library, implement the max height will overflow-y-scroll for the document stored, to avoid excessive height.

Also, please modify it so that Ansible chat, after the successful generation of the playbook, will explain what it has done, as what we normally expect to see after the prompt generation is done by LLM. Something like explanation, summary and possible next prompt.



- [/] Create a route on devices *(generic / ambiguous test)*

**Note**: Can branch into two categories, either the static or ospf, then require for the missing required parameters. 

---

## 5️⃣ ACL

- [/] Create ACL 2000 to permit SSH from 192.168.1.0/24 on SW1  
**TO-DO**: Turn the following into the option picker, not input text.
Clarifying for some missing information
Question 1 of 1
ACL type (ipv4, ipv6, mac, or l2)?
ipv4


- [/] Create ACL named BLOCK_HTTP to deny TCP port 80 on SW1  


- [/] Apply ACL 3000 inbound on GigabitEthernet1/0/1 on SW1  


- [/] Apply ACL BLOCK_HTTP outbound on GigabitEthernet1/0/2 on SW1  

- [*] Create ACL to deny ICMP and apply it on access switch 3  

**Notification**: The ACL definition can be done on the Huawei device, but the ACL application on the interface cannot be done on Huawei CE6800/12800. AR1000V can do the ACL traffic, but it lacks alotta features including lldp which is required.


- [/] Add ACL rule on devices *(generic / ambiguous test)*  

Prompt:
Now, I would like to do for the prompt of category QoS, so it can be done in this way:
1. Create the reference template for all the Cisco, Huawei, Juniper, multi-vendor
2. Cross-checked for all the required parameters, and ensure the correct category specific intent schema and the operation-specific intent schema are created to provide the info of required parameters for the specific operations.
3. 

## 6️⃣ QoS

- [/] Create QoS policy VOICE_POLICY on SW1  


- [*] Configure QoS to prioritize voice traffic on GigabitEthernet1/0/1  
Reminder: fIX this tommorrow, the dry run works but the actual playbook contains wrong commands, as it accespots 1. ❌ Invalid match statement
match DSCP

👉 This is wrong syntax

✅ Correct:

match dscp ef

or

match dscp 46
2. ❌ No prioritization action

Your policy-map:

policy-map VOICE_POLICY
 class VOICE_POLICY_CLASS

👉 This does nothing. You didn’t tell it to prioritize.

✅ You MUST include:

priority
3. ❌ Interface mismatch

Prompt says:

GigabitEthernet1/0/1

Your config:

interface Ethernet0/1

👉 This will fail in real deployment.

4. ❌ Weak class-map logic
class-map match-any VOICE_POLICY_CLASS
 match dscp ef

👉 This is OK, but your version:

match DSCP

- [ ] Apply QoS policy IN_POLICY inbound on GigabitEthernet1/0/2 on SW1  

- [ ] Mark traffic with DSCP 46 for voice on access switch 2  


- [ ] Create policing policy for HTTP traffic on SW1  

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
