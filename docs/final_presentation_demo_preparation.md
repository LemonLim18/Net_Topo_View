# CAT403 Final Presentation and System Demo Preparation

Project: CogniShield: LLM-Assisted and Approval-Governed Campus Network Automation and Operations Platform

Recommended time split:
- Slide presentation: 4.5 to 5 minutes
- System demo: 14 to 15 minutes
- Buffer/Q&A handling: keep 30 seconds flexible if the LLM or VM is slow

## Overall Positioning

Use this positioning early, before the software screens:

> CogniShield should be evaluated as a Computing Infrastructure project, not only as a web application. The delivered work includes both infrastructure and software: physical server setup, Proxmox hypervisor installation, VM provisioning, operating system and service configuration, EVE-NG simulated network deployment, network bridging, VPN access, ZTP/DHCP/TFTP support, telemetry services, and the controller application itself. In that sense, the project is closer to a small private infrastructure platform or lab-scale IaaS-style deployment than a pure SaaS web app relying on third-party hosted services.

## Slide Content

### Slide 1: Title

Title:
CogniShield: LLM-Assisted and Approval-Governed Campus Network Automation and Operations Platform

Subtitle:
CAT403 Computing Infrastructure Major Project

Presenter:
Lim Miin Ning, 163133

Supervisor:
Dr. Vaithegy A/P Doraisamy

Speaker notes:
> Good morning. My project is CogniShield, a lab-scale network automation and operations platform. The main idea is to combine infrastructure deployment, simulated network testing, topology visibility, AI-assisted playbook generation, human approval, monitoring, and configuration backup into one controller-style workspace.

Timing: 15 seconds

### Slide 2: Project Description

Slide bullets:
- Open-source, vendor-neutral network automation and operations platform
- Designed for campus-style, SME, lab, and mixed-vendor environments
- Combines planned inventory, live topology, monitoring, onboarding, backup, and Ansible automation
- Uses LLM assistance for request interpretation and playbook preparation
- Uses dry-run, approval, and verification before deployment

Speaker notes:
> CogniShield is not just a chatbot or a playbook generator. It is a controller-style workspace for practical network operations. The system helps users move from planned inventory and topology awareness into governed automation, where the LLM assists the workflow but does not directly control the network without review.

Timing: 30 seconds

### Slide 3: Quick Heads-Up: What I Will Demo

Slide bullets:
- Infrastructure layer: physical server, Proxmox, VMs, EVE-NG, VPN
- Day-0 onboarding: planned device, template, service profile, ZTP publish
- Topology awareness: planned topology vs real-time discovered topology
- Day-1/Day-2 operation: AI-assisted Ansible workflow, dry-run, approval, deployment
- Assurance: syslog, performance, SSH verification, backup and diff

Speaker notes:
> Before the live demo, this is the flow I want the examiner to expect. First I will show the infrastructure foundation, then I will onboard a new device, then I will show how the controller discovers it, monitors it, backs it up, and finally performs a governed automation change through Ansible Studio.

Timing: 35 seconds

### Slide 4: Problem Statement

Slide bullets:
- Commercial network controllers are powerful but expensive and proprietary
- SMEs, universities, and research labs often cannot access enterprise-grade platforms
- Network operations are fragmented across CLI, inventory, monitoring, backup, and automation tools
- Raw LLM output is unsafe for direct network execution
- Mixed-vendor and legacy environments still need practical, reviewable automation

Speaker notes:
> The problem is not that automation tools do not exist. The problem is accessibility and integration. Enterprise platforms such as commercial campus controllers are expensive and locked into vendor ecosystems. On the other hand, open tools often require engineers to manually combine inventory, monitoring, automation, and validation. Also, if we use an LLM directly, it may produce incomplete or unsafe configuration. So this project focuses on a governed and practical middle path.

Timing: 40 seconds

### Slide 5: System Objectives

Slide bullets:
- Build an accessible and vendor-neutral network operations platform
- Translate natural-language requests into validated multi-vendor Ansible workflows
- Support governed Day-1 and Day-2 operations through preview, dry-run, approval, and deployment
- Provide topology visibility, monitoring, backup, and partial Day-0 onboarding
- Demonstrate the system in a Proxmox-supported virtual lab environment

Speaker notes:
> The objectives are to build an accessible platform, implement an intelligent configuration workflow, and enforce safe operations through review and approval. The system mainly targets Day-1 and Day-2 operations, while also providing partial Day-0 onboarding through generated bootstrap configuration and ZTP publishing.

Timing: 35 seconds

### Slide 6: Clarification: Day-0, Day-1, Day-2

Slide bullets:
- Day-0: Initial device bootstrap before it becomes fully managed
- Day-1: First planned configuration after the device is reachable
- Day-2: Continuous operations, monitoring, change, backup, troubleshooting
- CogniShield focus: Day-1 and Day-2
- CogniShield support: partial Day-0 through templates and ZTP publish

Speaker notes:
> In networking, Day-0 means the initial bootstrap stage, such as giving a new device its management IP, SSH, SNMP, syslog, NTP, and base configuration. Day-1 means the first intentional configuration after the device is reachable. Day-2 means ongoing operations such as monitoring, changes, backup, troubleshooting, and compliance. CogniShield is strongest in Day-1 and Day-2, with partial Day-0 support to prove onboarding feasibility.

Timing: 40 seconds

### Slide 7: Highlight of Computing Infrastructure Components

Slide bullets:
- Physical server and local network setup
- Proxmox hypervisor hosting multiple VMs
- EVE-NG for simulated campus network devices
- ZTP server with DHCP and TFTP
- Telemetry services: Telegraf, InfluxDB, Prometheus/Grafana support
- Remote Ansible runner and local Ollama/Qwen model runtime
- VPN-based remote access instead of direct public exposure

Speaker notes:
> This is the most important scope clarification. As a Computing Infrastructure project, a major part of the work is the infrastructure foundation. I installed and configured the server environment, hypervisor, virtual machines, networking, simulated testbed, telemetry path, and supporting services before the controller software could be tested. The result is both hardware/infrastructure delivery and software delivery.

Timing: 45 seconds

### Slide 8: Module Diagram / Architecture

Slide bullets:
- Frontend: React, TypeScript, Vite, Tailwind/DaisyUI
- Backend: Python FastAPI, REST API, WebSocket services
- Data: SQLite, ChromaDB, InfluxDB, NetBox-related planned context
- Automation: Ansible Core, ansible-runner, SSH terminal
- AI: Gemini hosted option and local Ollama/Qwen option
- Lab: Proxmox VMs, EVE-NG, simulated Cisco/Huawei/Juniper devices

Speaker notes:
> The architecture is layered. The frontend provides the operator workspace. The backend coordinates inventory, topology, onboarding, monitoring, backup, AI workflow, and deployment APIs. The supporting services provide database, vector knowledge, telemetry, and automation execution. The lab layer uses EVE-NG to simulate a campus network with different vendors.

Timing: 40 seconds

### Slide 9: Project Implementation and Discussion

Slide bullets:
- Planned vs live topology visibility
- LLM-assisted but guardrail-governed playbook generation
- Clarification and preview before automation
- Dry-run and approval before deployment
- Syslog, performance, backup, diff, and SSH verification for assurance
- Honest limitation: MVP lab prototype, not a full SDN controller or fully autonomous IBN platform

Speaker notes:
> The key implementation idea is governance. The LLM is used as an assistant for interpretation and generation, but it is constrained by planned context, schema checks, preview, dry-run, approval, and verification. I also want to be clear that this is not a full SDN controller or a fully autonomous closed-loop platform. It is a functional MVP that demonstrates a realistic and defensible network automation workflow.

Timing: 45 seconds

### Slide 10: Conclusion and System Demo Transition

Slide bullets:
- CogniShield demonstrates a practical infrastructure-backed network operations platform
- Combines infrastructure deployment, virtual lab, AI assistance, Ansible automation, and operational assurance
- Provides value for education, research, SMEs, and cost-sensitive mixed-vendor environments
- Demo focus: complete flow from infrastructure to onboarding to governed automation

Speaker notes:
> In conclusion, CogniShield demonstrates how open tools can be integrated into a practical network operations platform. Its value is not only the web application, but the complete infrastructure-backed environment that supports the controller. I will now move into the live demo and show the flow from infrastructure setup to onboarding, discovery, monitoring, backup, and controlled automation.

Timing: 25 seconds

## Optional Appendix Slide: Entrepreneurship and SDG

Use only if time allows or during Q&A:

Slide bullets:
- Market opportunity: SMEs, universities, training labs, research environments
- Lower-cost alternative to proprietary controller ecosystems
- Extensible as a pre-commercial open-source or service-supported platform
- SDG 9 alignment: industry, innovation, and infrastructure

Speaker notes:
> The project has pre-commercialization potential as a lower-cost platform for training labs, SMEs, and research environments. It aligns with SDG 9 by supporting innovation in network infrastructure management and making automation concepts more accessible.

## Demo Script

Target: 15 minutes.

### Demo 0: Infrastructure Foundation

Show:
- Physical server image
- Proxmox screenshot
- VM list

Say:
> Before opening the application, I want to show the computing infrastructure foundation. This physical setup hosts the Proxmox server and network connectivity used for the project. Proxmox is the hypervisor that hosts the VMs required by CogniShield, including EVE-NG, telemetry services, the ZTP server, the Ansible and local LLM environment, and other supporting services.

> This is why the project should be seen as both hardware/infrastructure and software. The installation, setup, configuration, networking, operating system preparation, hypervisor configuration, VM provisioning, and simulated network testbed are part of the project deliverable.

Timing: 1 minute

### Demo 1: Login and Local-First Deployment

Show:
- CogniShield login page
- Login as Admin
- Dashboard at `192.168.1.116`

Say:
> I will log in as Admin because this demo uses privileged operations such as onboarding, ZTP publish, approval, backup, and deployment.

> The controller is hosted inside the local network. This is intentional because the target environment is SME/campus/lab style deployment, and because exposing a network controller directly to the public internet would be risky. Remote access is still possible through VPN, which gives authorized users access without publicly exposing the application.

Timing: 45 seconds

### Demo 2: EVE-NG and VPN Reachability

Show:
- EVE-NG topology
- Four network areas
- VPN connection
- Ping `192.168.1.248`

Say:
> This EVE-NG lab represents the simulated network site. It contains the private/home network, the public or WAN transit section, the campus network, and the OOB management/controller network. The OOB network is important because the controller manages devices through management IPs separately from the data-plane topology.

> To avoid confusion, I will first show the VPN connection. The controller can be operated locally, or remotely through VPN. A successful ping to `192.168.1.248` confirms that the VPN path can reach the EVE-NG network controller side.

Timing: 1 minute

### Demo 3: Prepared Blank Device

Show:
- New test device in EVE-NG
- Label with management IP and MAC
- OOB connection
- Neighbor connection to an active device

Say:
> For the onboarding demo, I use a prepared blank device. This is because ZTP requires the management MAC address and planned IP before the DHCP/TFTP mapping can be generated. Preparing the device saves time during the live demo while still showing the real onboarding flow.

> The neighbor device is already active so that after onboarding, LLDP or CDP can detect the neighbor relationship. Ping and SSH help verify reachability and identity, while LLDP/CDP helps identify physical or logical adjacency.

Timing: 45 seconds

### Demo 4: Planned Inventory and Topology

Show:
- Device registration
- Interfaces
- Links
- Planned topology
- Real-time topology

Say:
> I register the planned device, interfaces, MAC address, management IP, and expected links. This becomes the planned source of truth.

> In planned topology, the new device appears because it has been registered. In real-time topology, it should not appear yet because it has not completed initialization and discovery. This planned-versus-live difference is useful because it shows the gap between intended design and actual operational state.

Timing: 1 minute

### Demo 5: Template Studio and Service Profile

Show:
- Template Studio
- Onboard Device
- Service Profiles

Say:
> Template Studio stores reusable vendor-specific configuration templates. It helps generate consistent Day-0 bootstrap configuration instead of writing each initial config manually.

> Service Profiles define common infrastructure settings such as SNMP, syslog, NTP, and ZTP server details. These values are injected into onboarding templates so that every device receives consistent management and monitoring configuration.

Timing: 1 minute

### Demo 6: Day-0 Preview/Edit and ZTP Publish

Show:
- Generated Day-0 config
- Edit/save/revert option
- ZTP publish
- Job status or manifest

Say:
> The planned inventory and service profile values now contribute to the generated Day-0 configuration. The script can be edited for a specific device and saved, or reverted back to the default generated version if needed.

> When I publish to ZTP, the controller connects to the ZTP server, uploads the configuration file into the TFTP structure, and writes the DHCP host mapping. The mapping tells the server: this MAC address should receive this IP address and this configuration filename. This information is provided from CogniShield, so the controller acts as the centralized management platform.

Timing: 1.5 minutes

### Demo 7: Boot Device and Show DHCP/TFTP Retrieval

Show:
- EVE-NG device boot
- OOB interface monitoring
- Keyword filter for DHCP/TFTP/config filename
- Retrieved config

Say:
> Now I power on the blank device. During boot, it requests DHCP information through the OOB network. The ZTP server uses the MAC address to assign the planned IP and provide the TFTP filename.

> Here we can see the IP assignment and file retrieval. This confirms that the device is receiving the Day-0 configuration generated and published by the controller.

Timing: 1.5 minutes

### Demo 8: Real-Time Topology, Syslog, Performance

Show:
- Real-time topology
- New device appears
- Syslog/security events
- Performance page

Say:
> After initialization, the device can be discovered in the real-time topology. The neighbor relation can be determined through LLDP/CDP, while SSH and ping confirm reachability.

> The syslog view confirms that device logs can be sent back to the controller-side environment. Any important device event can be surfaced here for troubleshooting. After giving telemetry a short moment to populate, the performance view provides device and interface health context for Day-2 operation.

Timing: 1.5 minutes

### Demo 9: Baseline Backup

Show:
- Config Backup page
- Capture snapshot
- Add description/comment

Say:
> After the device is initialized, I capture a configuration backup as the baseline. This gives the operator a known-good state that can be reviewed, downloaded, compared, or restored later. A description helps future users understand why the snapshot was taken, for example, baseline after first ZTP onboarding.

Timing: 45 seconds

### Demo 10: Ansible Studio Automation

Show:
- Ansible Studio
- Pre-selected command
- Clarification process
- Preview modal
- Generated playbook

Recommended command:
> Create VLAN 67 named DEMO_VLAN on Acc_Cisco_1 and configure Ethernet0/1 as an access port in VLAN 67.

Say:
> Now I move from onboarding into Day-1 or Day-2 operation. Ansible Studio accepts a natural-language request and turns it into a governed automation workflow. The system may ask clarification questions if target device, interface, VLAN ID, or operation details are missing or ambiguous.

> The preview step is important because the system does not execute directly from an LLM response. It shows what it understood, then generates a playbook that can be reviewed.

Timing: 1.5 minutes

### Demo 11: Dry-Run, Approval, Deployment

Show:
- Dry-run/check
- Approval queue
- AI summary
- AI Review Q&A
- Approve
- Deploy

Say:
> Before deployment, the playbook goes through a dry-run or check stage. If the check succeeds, it can be submitted to approval. The approval queue provides human-in-the-loop governance. The reviewer can inspect the playbook, read the AI summary, ask questions, and only then approve deployment.

> Once approved, the configuration is deployed through the remote Ansible runner. This demonstrates the key safety pipeline: prompt, clarification, preview, playbook, dry-run, approval, deployment.

Timing: 2 minutes

### Demo 12: Verification, Post-Change Backup, Diff, Ops Center

Show:
- Syslog
- SSH terminal command verification
- New config backup
- Diff / AI explanation
- Network Ops Center overlay

Say:
> After deployment, I verify the result through syslog and direct SSH terminal access. Then I capture another configuration backup after the change. Comparing the baseline and post-change snapshot shows the actual difference, and AI can help summarize the change in plain language.

> Finally, the Network Ops Center overlay provides quick access to inventory, general AI, and network-specific AI assistance. This gives operators a faster way to move between operational context and AI-supported troubleshooting.

Timing: 1.5 minutes

## Slow LLM Handling Script

Use this if Qwen or local inference is slow:

> The local LLM may take longer because this deployment uses Qwen 2.5 on limited local hardware and lower quantization due to GPU memory constraints. This is a deliberate local-inference path to show on-premise feasibility, but it is slower than hosted models. The important part is that the LLM is not the only control layer; the workflow is still protected by schema checks, preview, dry-run, approval, and operator verification.

If waiting too long:

> While it is generating, I will explain what the next stages are: the system will produce a reviewable playbook, then I run check mode, submit for approval, review the AI summary, approve, deploy, and verify through syslog and SSH.

Backup option:
> If inference takes too long in the live session, I have a pre-selected or previously generated example ready so I can still demonstrate the full governance pipeline.

## Important Examiner Clarifications

Use these naturally during Q&A or slide transition:

- CogniShield is not claimed as a full SDN controller.
- CogniShield is not claimed as a fully autonomous IBN platform.
- The LLM is an assistant, not an unrestricted executor.
- The system is an MVP laboratory prototype evaluated in a Proxmox and EVE-NG environment.
- The project is Computing Infrastructure aligned because deployment, virtualization, networking, service integration, and testbed preparation are part of the deliverable.
- The software should be judged together with the infrastructure effort because the system was built as an end-to-end private network operations platform, not as a standalone SaaS-style web application.

## Closing Line

> Overall, CogniShield demonstrates a practical infrastructure-backed platform for network operations. It combines local deployment, virtualized lab infrastructure, planned and live topology, partial Day-0 onboarding, governed Ansible automation, monitoring, syslog, backup, and AI support into one controller-style workspace for SME, campus, lab, and mixed-vendor environments.
