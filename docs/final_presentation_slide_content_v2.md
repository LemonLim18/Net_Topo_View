# Revised CAT403 Final Presentation Slide Content

Purpose:
This version is aligned more closely with the final report content. It keeps the presentation short, defensible, and suitable for a 5-minute project presentation before the 15-minute system demo.

Recommended delivery:
- 9 content slides plus title and thank-you/demo transition
- About 5 minutes total
- Keep the system demo for detailed proof of implementation

## Presentation Framing

Use this framing once near the beginning:

> CogniShield is a Computing Infrastructure project, so the deliverable is not only the controller software. The system is built on a physical and virtualized infrastructure foundation involving server setup, Proxmox, virtual machines, EVE-NG, network services, telemetry services, ZTP-related services and the controller application. Therefore, the project should be viewed as an infrastructure-backed network operations platform rather than only a standalone SaaS-style web application.

Do not over-repeat this point. Mention it clearly on the CI Components slide and again briefly before the demo.

## Slide 1: Title

Title:
CogniShield: LLM-Assisted and Approval-Governed Campus Network Automation and Operations Platform

Subtitle:
CAT403 Computing Infrastructure Major Project

Student:
Lim Miin Ning, 163133

Supervisor:
Dr. Vaithegy A/P Doraisamy

Speaker script:
> Good morning. My project is CogniShield, an LLM-assisted and approval-governed campus network automation and operations platform. The project focuses on making network configuration and operations more accessible, structured and safer for campus-style, SME, research and educational environments.

Time: 15 seconds

## Slide 2: Project Description

Main content:
- Open-source, vendor-neutral network automation and operations platform
- Designed for campus-style, SME, educational, research and mixed-vendor environments
- Converts natural-language configuration requests into governed Ansible-based workflows
- Combines planned inventory, live topology, AI support, dry-run validation, approval-governed deployment, onboarding assistance and configuration backup
- Positioned as a practical hybrid automation platform, not a full SDN controller or fully autonomous IBN system

Speaker script:
> CogniShield is proposed as an open-source and vendor-neutral platform for network automation and operations. Its purpose is to help convert natural-language configuration requests into governed Ansible-based workflows. The current implementation brings together planned inventory, live topology visibility, knowledge-base-assisted AI, playbook generation, dry-run validation, approval-based deployment, onboarding support and configuration backup in one controller-style workspace. Importantly, it is positioned as a practical hybrid automation platform, not as a full SDN controller or a fully autonomous Intent-Based Networking system.

Time: 35 seconds

## Slide 3: Background: Campus Network Operations

Main content:
- Campus networks connect multiple LANs across universities, companies or hospitals
- Commonly structured using a three-tier model:
  - Core layer: high-speed routing and external connectivity
  - Distribution layer: aggregation, routing, filtering, QoS and policy enforcement
  - Access layer: end-user device connectivity
- As networks grow, configuration, monitoring, troubleshooting and change management become harder to handle manually

Suggested visual:
- Three-tier campus network diagram from report, or a simplified core/distribution/access diagram

Speaker script:
> The background of this project is campus network operation. A campus network normally connects multiple LANs in an organization such as a university, company or hospital. It is commonly designed using a three-tier model: core, distribution and access. As the number of devices, users and services increases, tasks such as configuration, monitoring, troubleshooting and change management become more difficult for administrators to handle manually.

Time: 35 seconds

## Slide 4: Network Operation Lifecycle

Main content:
- Day-0: design and provisioning
  - Network design, management IP planning, baseline policy and initial onboarding
  - Example: ZTP or Plug and Play to bring factory-default devices online
- Day-1: deployment and configuration
  - Push service configurations such as OSPF, VLAN, ACL and interface settings
- Day-2: operations and optimization
  - Monitoring, backup, troubleshooting, auditing and drift handling
- CogniShield focus: primarily Day-1 and Day-2, with partial Day-0 onboarding support

Speaker script:
> The report frames network operations using the Day-0, Day-1 and Day-2 lifecycle. Day-0 is the preparation and onboarding phase, where devices are brought online with management reachability. Day-1 is the deployment phase, where service configurations such as OSPF, VLANs or ACLs are applied. Day-2 is the ongoing operations phase, involving monitoring, backup, troubleshooting, auditing and drift management. CogniShield mainly focuses on Day-1 and Day-2 operations, with partial Day-0 onboarding support through generated bootstrap configuration and ZTP-like publishing.

Time: 45 seconds

## Slide 5: Problem Statement

Main content:
1. Existing commercial controllers are powerful but expensive, proprietary and vendor-coupled
2. Repeated manual configuration across heterogeneous devices is time-consuming, inconsistent and error-prone
3. Accessible environments often lack an integrated and governed workflow connecting AI assistance, inventory context, validation, approval, backup and monitoring

Speaker script:
> The project addresses three main problems. First, commercial platforms such as Cisco DNA Center, Juniper Mist and Huawei iMaster NCE provide strong centralized features, but they are expensive, proprietary and less accessible to SMEs, students and research communities. Second, engineers still need to repeatedly prepare vendor-specific configurations across different routers, switches and operating systems, which increases human error and configuration drift. Third, many accessible environments lack a unified governed workflow that connects AI-assisted generation with inventory context, preview, dry-run validation, approval, backup, execution tracking and monitoring.

Time: 45 seconds

## Slide 6: System Objectives

Main content:
1. Develop an accessible, open-source and vendor-neutral network management platform for SMEs, universities, research and public communities
2. Implement an intelligent configuration workflow using LLMs to translate natural-language requests into validated multi-vendor automation playbooks
3. Establish an integrated and governed Day-1 and Day-2 operation framework with intent preview, dry-run validation, approval-based deployment, live topology visibility, configuration backup, execution tracking and monitoring

Speaker script:
> Based on the problem statements, the project has three objectives. The first is to build an accessible, open-source and vendor-neutral network management platform. The second is to implement an LLM-assisted configuration workflow that translates natural language into validated multi-vendor Ansible playbooks. The third is to establish a governed Day-1 and Day-2 operation framework with preview, dry-run, approval, topology visibility, backup, execution tracking and monitoring.

Time: 35 seconds

## Slide 7: Proposed Solution: CogniShield

Main content:
- Integrated control center for mixed-vendor, legacy, campus-style and SME environments
- Focuses on practical implemented capabilities instead of fully replicating enterprise controllers
- Three user-facing core areas:
  1. Structural visibility: dashboard, performance monitoring, planned/live/diff topology
  2. Secure automation pipeline: Ansible Studio, intent parser, clarification, dry-run, approval queue and deployment
  3. Daily administration support: SSH terminal, RAG knowledge base, configuration backup and AI chat/history

Speaker script:
> The proposed solution is CogniShield, an integrated control center for mixed-vendor, legacy, campus-style and SME environments. Rather than trying to fully replicate enterprise controllers, it focuses on practical capabilities implemented in the current system. The frontend-visible modules can be grouped into three areas: structural visibility through dashboard, performance and topology views; a secure automation pipeline through Ansible Studio, intent parsing, clarification, dry-run, approval and deployment; and daily administration support such as SSH terminal, knowledge base, configuration backup and AI chat history.

Time: 45 seconds

## Slide 8: LLM Role and Governance

Main content:
- LLM acts as an assistant, not an autonomous control authority
- Workflow:
  1. Interpret request and classify operation
  2. Extract parameters into structured intent
  3. Check against schemas, planned context and clarification logic
  4. Generate or fill Ansible playbook
  5. Preview, dry-run, approval and deployment
- Hybrid generation:
  - LLM-assisted parsing plus deterministic template filling
  - LLM-assisted YAML drafting plus validation
  - Deterministic template generation without LLM for explicit template mode

Speaker script:
> The LLM in CogniShield should be understood strictly as an assistant inside a governed workflow. It helps interpret natural-language requests, classify the operation and extract parameters. The result is then checked against operation schemas, planned-network context, clarification logic and preview flow. Playbook generation is hybrid: some workflows use LLM-assisted intent parsing followed by deterministic template filling, some use LLM-assisted YAML drafting with validation, and some can use deterministic templates without LLM. In all cases, the output still goes through preview, dry-run and approval before execution.

Time: 45 seconds

## Slide 9: CI Components and Implementation Architecture

Main content:
- Infrastructure foundation:
  - Physical server and local network setup
  - Proxmox hypervisor
  - Virtual machines for EVE-NG, telemetry, Ansible/local LLM and supporting services
  - VPN-based remote access instead of direct public exposure
- Software stack:
  - React + TypeScript frontend
  - FastAPI backend
  - SQLite for inventory, jobs, snapshots, policies and audit records
  - ChromaDB for knowledge retrieval
  - InfluxDB/Telegraf for telemetry
  - Ansible runner for deployment
  - EVE-NG for simulated Cisco, Huawei and Juniper devices

Speaker script:
> This slide is important for the Computing Infrastructure assessment. The project is not only software. It required physical server setup, Proxmox hypervisor configuration, VM provisioning, network setup, EVE-NG simulation, VPN access, telemetry services, ZTP-related services and controller deployment. On top of that infrastructure, the application uses React and TypeScript for the frontend, FastAPI for the backend, SQLite for records, ChromaDB for knowledge retrieval, InfluxDB and Telegraf for telemetry, Ansible runner for deployment and EVE-NG for the simulated multi-vendor lab. So the delivered work includes both infrastructure and software.

Time: 50 seconds

## Slide 10: Implementation Scope and Demo Transition

Main content:
- Implemented as a functional MVP in a Proxmox-supported lab environment
- Demonstrates:
  - Planned inventory and planned/live topology
  - Partial Day-0 onboarding with templates, service profiles and ZTP publish
  - LLM-assisted Ansible workflow with clarification, preview and generated playbook
  - Dry-run, approval, deployment and verification
  - Syslog, performance view, SSH terminal, backup and diff
- Scope boundary:
  - Not a full SDN controller
  - Not a full closed-loop autonomous controller
  - Not a full enterprise production platform

Speaker script:
> The current implementation is a functional MVP in a Proxmox-supported lab environment. It demonstrates planned inventory, planned and live topology, partial Day-0 onboarding, LLM-assisted Ansible workflow, dry-run validation, approval, deployment, syslog, performance monitoring, SSH verification, backup and diff. The scope is intentionally honest: it is not a full SDN controller, not a fully autonomous closed-loop controller and not a mature enterprise production platform. It is a practical and affordable hybrid automation platform for lab, SME, educational and mixed-vendor environments.

Time: 40 seconds

## Slide 11: System Demo Heads-Up

Main content:
Demo flow:
1. Infrastructure: physical setup, Proxmox and VMs
2. Access: login, local hosting and VPN reachability
3. Network lab: EVE-NG campus and OOB management network
4. Day-0 support: register planned device, generate config, publish to ZTP
5. Discovery: boot device, DHCP/TFTP retrieval, live topology and LLDP/CDP relation
6. Day-2 assurance: syslog, performance and baseline backup
7. Day-1/Day-2 automation: Ansible Studio, dry-run, approval, deployment and verification
8. Post-change: backup diff and Network Ops Center AI assistance

Speaker script:
> Before I start the demo, this is the flow. I will first show the infrastructure foundation, then the controller login and VPN/local access model. After that, I will show the EVE-NG lab, register and onboard a planned device, publish its Day-0 configuration through ZTP, boot it, verify discovery and topology, check syslog and performance, take a baseline backup, then perform a governed Ansible automation workflow with dry-run, approval, deployment, verification and post-change backup comparison.

Time: 40 seconds

## Slide 12: Thank You / Demo Start

Main content:
- Thank you
- Proceeding to system demonstration

Speaker script:
> Thank you. I will now proceed to the system demonstration.

Time: 5 seconds

## Total Estimated Time

- Slide 1: 15 seconds
- Slide 2: 35 seconds
- Slide 3: 35 seconds
- Slide 4: 45 seconds
- Slide 5: 45 seconds
- Slide 6: 35 seconds
- Slide 7: 45 seconds
- Slide 8: 45 seconds
- Slide 9: 50 seconds
- Slide 10: 40 seconds
- Slide 11: 40 seconds
- Slide 12: 5 seconds

Total: about 7 minutes if spoken fully.

To keep within 5 minutes:
- Keep Slide 3 and Slide 4 concise.
- Do not read all bullets on Slide 9; emphasize only infrastructure plus stack.
- Use Slide 11 as a quick transition only.

Recommended actual 5-minute version:
- Slide 1: 10 seconds
- Slide 2: 30 seconds
- Slide 3: 25 seconds
- Slide 4: 35 seconds
- Slide 5: 40 seconds
- Slide 6: 30 seconds
- Slide 7: 40 seconds
- Slide 8: 40 seconds
- Slide 9: 45 seconds
- Slide 10: 35 seconds
- Slide 11: 30 seconds
- Slide 12: 5 seconds

Total: about 5 minutes and 25 seconds. Slightly speed up Slide 9 or merge Slide 11 verbally if needed.

## Most Important Lines to Memorize

> CogniShield is not a full SDN controller or a fully autonomous IBN system. It is a practical, LLM-assisted and approval-governed network automation and operations platform.

> The LLM is used as an assistant for interpreting requests and preparing playbooks, but the final workflow is still controlled by schemas, planned context, clarification, preview, dry-run, approval and human verification.

> As a Computing Infrastructure project, the delivered system includes both infrastructure and software: physical server setup, Proxmox, VMs, EVE-NG, telemetry, ZTP-related services, VPN access and the controller application.

> The system focuses mainly on Day-1 and Day-2 operations, with partial Day-0 onboarding support to prove feasibility for bringing devices into the managed environment.
