# CAT403 Final Presentation Content: Required Slides Only

Scope:
This version uses only the required template sections:
- Project Description
- Problem Statement
- System Objectives
- Highlight(s) of CI Components
- Module Diagram
- Project Implementation / Discussions
- Conclusion

Target duration:
- Around 5 minutes before the system demo
- Keep the detailed proof for the 15-minute live demo

## Slide 1: Project Description

Slide content:
- CogniShield is an open-source, vendor-neutral, LLM-assisted network automation and operations platform.
- It is designed for campus-style, SME, educational, research and mixed-vendor environments.
- It converts natural-language configuration requests into governed Ansible-based workflows.
- The current implementation combines:
  - planned inventory and live topology visibility
  - knowledge-base-assisted AI support
  - Ansible playbook generation
  - dry-run validation and approval-governed deployment
  - onboarding assistance and configuration backup
- Scope: mainly Day-1 and Day-2 operations, with partial Day-0 onboarding support.

Speaker script:
> CogniShield is an open-source and vendor-neutral network automation and operations platform. It is designed for campus-style, SME, educational, research and mixed-vendor environments. The main idea is to help convert natural-language configuration requests into governed Ansible-based workflows. The current implementation combines planned inventory, live topology visibility, knowledge-base-assisted AI, playbook generation, dry-run validation, approval-based deployment, onboarding assistance and configuration backup in one controller-style workspace.
>
> The system mainly focuses on Day-1 and Day-2 network operations. Day-1 means deployment and configuration, while Day-2 means ongoing operations such as monitoring, backup, troubleshooting and change management. It also provides partial Day-0 onboarding support, where Day-0 refers to initial device provisioning and bootstrap configuration.

Time: 45 seconds

## Slide 2: Problem Statement

Slide content:
1. Commercial network controllers are powerful but expensive, proprietary and vendor-coupled.
2. Repeated manual configuration across routers, switches and vendors is time-consuming, inconsistent and error-prone.
3. Accessible environments often lack a unified governed workflow for inventory, topology, AI-assisted configuration, validation, approval, backup, execution tracking and monitoring.

Speaker script:
> The project addresses three main problems. First, existing commercial network controllers such as Cisco DNA Center, Juniper Mist and Huawei iMaster NCE provide strong centralized features, but they are expensive, proprietary and less accessible to SMEs, universities, students and research communities.
>
> Second, many engineers still need to repeatedly prepare vendor-specific configurations across different routers, switches, interfaces and operating systems. As the network grows, this becomes time-consuming and increases the risk of human error, inconsistent implementation and configuration drift.
>
> Third, many accessible environments still use disconnected tools for inventory, topology, configuration preparation, validation, backup, approval, execution and monitoring. Even when LLMs can assist with configuration generation, their output should not be executed directly without a governed human-in-the-loop workflow.

Time: 55 seconds

## Slide 3: System Objectives

Slide content:
1. Develop an accessible, open-source and vendor-neutral network management platform for SMEs, universities, research and public communities.
2. Implement an intelligent configuration workflow using LLMs to translate natural-language requests into validated multi-vendor automation playbooks.
3. Establish an integrated and governed Day-1 and Day-2 operation framework with:
   - intent preview
   - dry-run validation
   - approval-based deployment
   - live topology visibility
   - configuration backup
   - execution tracking
   - operational monitoring

Speaker script:
> The first objective is to develop an accessible, open-source and vendor-neutral network management platform for SMEs, universities, research and public communities.
>
> The second objective is to implement an intelligent configuration workflow that uses LLMs to translate natural-language requests into validated multi-vendor automation playbooks.
>
> The third objective is to establish an integrated and governed Day-1 and Day-2 operation framework. This includes intent preview, dry-run validation, approval-based deployment, live topology visibility, configuration backup, execution tracking and operational monitoring.

Time: 40 seconds

## Slide 4: Highlight(s) of CI Components

Slide content:
- Physical and virtual infrastructure:
  - physical server and local network setup
  - Proxmox hypervisor
  - multiple virtual machines for controller-related services
  - EVE-NG simulated campus network
- Network and service infrastructure:
  - OOB management network
  - VPN-based remote access
  - DHCP/TFTP ZTP server
  - telemetry and monitoring services
- Controller software infrastructure:
  - React + TypeScript frontend
  - FastAPI backend
  - SQLite, ChromaDB, InfluxDB/Telegraf
  - Ansible runner and local/hosted LLM support

Speaker script:
> This slide is important for the Computing Infrastructure scope. CogniShield is not only a software application. The project also includes the hardware and infrastructure foundation required to run and test the controller system.
>
> The infrastructure work includes physical server setup, Proxmox hypervisor installation and configuration, virtual machine provisioning, operating system setup, network configuration, EVE-NG simulated campus network deployment, VPN access, ZTP-related DHCP and TFTP services, and telemetry services.
>
> On top of this infrastructure, the software controller is built using React and TypeScript for the frontend, FastAPI for the backend, SQLite for records, ChromaDB for knowledge retrieval, InfluxDB and Telegraf for monitoring, and Ansible runner for deployment. Therefore, the delivered product should be viewed as both infrastructure and software, closer to a private infrastructure-backed controller platform rather than a pure SaaS web application.

Time: 55 seconds

## Slide 5: Module Diagram

Slide content:
Recommended module grouping:

1. Visibility and Inventory Layer
   - Overview dashboard
   - Planned inventory
   - Planned topology, live topology and topology difference
   - Device performance view

2. Governed Automation Layer
   - Ansible Studio
   - Intent parser and clarification
   - Playbook generation
   - Dry-run/check
   - Approval queue
   - Deployment execution

3. Operational Support Layer
   - Template Studio
   - Service Profiles
   - Onboarding/ZTP publish
   - SSH terminal
   - Syslog/security events
   - Configuration backup and diff
   - AI chat / Network Ops Center

4. Infrastructure and Data Layer
   - FastAPI backend
   - SQLite database
   - ChromaDB knowledge store
   - InfluxDB/Telegraf telemetry
   - Ansible runner
   - EVE-NG and Proxmox lab

Speaker script:
> The module diagram can be explained in four layers. The first layer is visibility and inventory, which includes the dashboard, planned inventory, planned and live topology, topology difference and performance view.
>
> The second layer is the governed automation layer. This is centered on Ansible Studio, where the system handles intent parsing, clarification, playbook generation, dry-run checking, approval and deployment.
>
> The third layer is operational support, including Template Studio, Service Profiles, onboarding and ZTP publish, SSH terminal, syslog, configuration backup and AI support.
>
> The final layer is the infrastructure and data layer, where FastAPI, SQLite, ChromaDB, InfluxDB, Telegraf, Ansible runner, EVE-NG and Proxmox provide the runtime foundation.

Time: 50 seconds

## Slide 6: Project Implementation / Discussions

Slide content:
- Implemented as a functional MVP in a Proxmox-supported lab environment.
- Frontend-visible controller workspace built with React and FastAPI.
- LLM role:
  - interprets natural-language requests
  - classifies operation
  - extracts structured intent
  - assists playbook preparation
- Governance:
  - schema and planned-context checking
  - clarification and preview
  - dry-run validation
  - approval before deployment
  - verification through syslog, SSH, performance view and backup diff
- Important boundary:
  - not a full SDN controller
  - not a fully autonomous closed-loop controller
  - not a complete enterprise production platform

Speaker script:
> The system is implemented as a functional MVP in a Proxmox-supported lab environment. The controller workspace is built with a React frontend and FastAPI backend, while the lab network is simulated in EVE-NG.
>
> A key implementation discussion is the role of the LLM. The LLM is not treated as an autonomous control authority. It acts as an assistant that helps interpret natural-language requests, classify operations and extract structured intent. The final automation artifact is still controlled by schemas, planned-network context, clarification, preview, dry-run validation and human approval.
>
> This is why CogniShield is described as LLM-assisted and approval-governed. It is not a full SDN controller, not a fully autonomous closed-loop controller and not a complete enterprise production platform. Its contribution is a practical and defensible workflow for lab, SME, educational and mixed-vendor environments.

Time: 55 seconds

## Slide 7: Conclusion

Slide content:
- CogniShield demonstrates a practical hybrid automation platform for network operations.
- It improves accessibility by using open and modular components instead of proprietary controller ecosystems.
- It reduces manual configuration effort through LLM-assisted and Ansible-based workflows.
- It improves operational safety through preview, dry-run, approval, monitoring, backup and verification.
- It aligns with Computing Infrastructure by combining:
  - hardware setup
  - virtualization
  - network services
  - simulated test environment
  - controller software development
- Next: system demo from infrastructure setup to onboarding, discovery, automation, approval and verification.

Speaker script:
> In conclusion, CogniShield demonstrates a practical hybrid automation platform for network operations. It improves accessibility by using open and modular components instead of depending fully on proprietary controller ecosystems. It also reduces manual configuration effort through LLM-assisted and Ansible-based workflows, while improving safety through preview, dry-run, approval, monitoring, backup and verification.
>
> From the Computing Infrastructure perspective, the project combines hardware setup, virtualization, network services, simulated test environment and controller software development. I will now proceed to the system demo, where I will show the flow from infrastructure setup to onboarding, discovery, automation, approval and verification.

Time: 40 seconds

## Total Timing

- Project Description: 45 seconds
- Problem Statement: 55 seconds
- System Objectives: 40 seconds
- Highlight(s) of CI Components: 55 seconds
- Module Diagram: 50 seconds
- Project Implementation / Discussions: 55 seconds
- Conclusion: 40 seconds

Estimated total: 5 minutes 20 seconds

To stay under 5 minutes:
- Shorten Problem Statement by saying only one sentence per problem.
- Shorten Module Diagram by explaining the four layers without listing every module.
- Keep CI Components strong but concise, because this is important for assessment.

## Very Short 5-Minute Speaker Version

Use this if you need to speak faster:

1. Project Description:
> CogniShield is an open-source, vendor-neutral, LLM-assisted network automation and operations platform for campus-style, SME, educational and mixed-vendor environments. It converts natural-language configuration requests into governed Ansible-based workflows and combines planned inventory, live topology, AI support, dry-run validation, approval-based deployment, onboarding assistance and configuration backup. It focuses mainly on Day-1 and Day-2 operations, with partial Day-0 onboarding support.

2. Problem Statement:
> The first problem is that commercial network controllers are powerful but expensive, proprietary and vendor-coupled. The second problem is that repeated manual configuration across heterogeneous devices is time-consuming and error-prone. The third problem is that accessible environments often lack a unified governed workflow connecting AI assistance, inventory context, validation, approval, backup and monitoring.

3. System Objectives:
> The objectives are to build an accessible vendor-neutral platform, implement an LLM-assisted workflow that translates natural language into validated multi-vendor playbooks, and establish a governed Day-1 and Day-2 operation framework with preview, dry-run, approval, topology visibility, backup, execution tracking and monitoring.

4. CI Components:
> As a Computing Infrastructure project, CogniShield includes both infrastructure and software. The infrastructure work includes physical server setup, Proxmox, virtual machines, EVE-NG, local networking, VPN access, DHCP/TFTP ZTP services and telemetry services. The software stack includes React, FastAPI, SQLite, ChromaDB, InfluxDB, Telegraf, Ansible runner and LLM support.

5. Module Diagram:
> The system can be viewed in four layers: visibility and inventory, governed automation, operational support, and infrastructure/data. Visibility includes dashboard, inventory, topology and performance. Governed automation includes Ansible Studio, intent parsing, dry-run, approval and deployment. Operational support includes onboarding, templates, service profiles, SSH terminal, syslog, backup and AI support. Infrastructure and data includes FastAPI, databases, telemetry, Ansible, EVE-NG and Proxmox.

6. Implementation / Discussion:
> The implementation is a functional MVP in a Proxmox-supported lab environment. The LLM acts only as an assistant for request interpretation and playbook preparation. It does not execute directly. The workflow is protected by schema checks, planned context, clarification, preview, dry-run, approval and verification. Therefore, the system is not a full SDN controller or fully autonomous IBN platform, but a practical LLM-assisted and approval-governed automation platform.

7. Conclusion:
> CogniShield demonstrates a practical hybrid network operations platform that improves accessibility, reduces manual configuration effort and supports safer change control. It also aligns with Computing Infrastructure because the delivered work includes hardware setup, virtualization, network services, simulated testing and controller software. I will now proceed to the system demo.
