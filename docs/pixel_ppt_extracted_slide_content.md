# PIXEL PPT Extracted Slide Content

Source file: `C:\Users\limmi\OneDrive\Desktop\FYP Demo Material Folder\PIXEL PPT.pptx`

Note: Several slides are image-based, so their content was extracted through visual inspection of embedded slide images.

## Slide 1: Front Page

CAT403 Computing Infrastructure Major Project

CogniShield

A Large-Language-Assisted Campus Network Orchestration and Automation Platform

PIXEL 2026 Demo

Presented By:
Joshua Lim Miin Ning
limmiinning@gmail.com

Supervised By:
Dr Vaithegy A/P Doraisamy
vaithegy@usm.my

## Slide 2: Introduction Divider

Introduction

## Slide 3: Project Description

CogniShield

A Large-Language-Assisted Campus Network Orchestration and Automation Platform

"CogniShield is an open-source, vendor-neutral network operations platform that supports LLM-assisted configuration workflows, topology visibility, monitoring, backup, and approval-governed network automation."

## Slide 4: Introduction Flow

Introduction

CogniShield empowers network teams with automation and AI without giving up control, security, or visibility.

01 Network operations are still fragmented

CLI
Spreadsheets
Dashboards
Scripts

Network operations are still fragmented across CLI, spreadsheets, dashboards, and scripts.

02 Network automation helps reduce

Repeat Tasks
Save Time
Reduce Errors
Higher Productivity

Network automation helps reduce repetitive manual configuration work.

03 AI assistants are transforming

Understand Intent
Generate Code
Explain Complexity
Improve Quality

AI assistants are transforming software engineering workflows.

04 CogniShield brings LLM assistance into

Operator in Control
Network Automation
Secure
Observable
Auditable

CogniShield brings LLM assistance into network automation while keeping operators in control.

Footer:
Faster operations. Smarter decisions. Stronger networks. All with CogniShield.

## Slide 5: Problem Statements Divider

Problem Statements

## Slide 6: Problem Statements

Problem Statements

Expensive & Proprietary Controller Platforms

Manual, Repetitive, Error-Prone Multi-Vendor Configuration Work

Lack of Governed AI-Assisted Network Automation Workflow

## Slide 7: System Objectives Divider

System Objectives

## Slide 8: System Objectives

System Objectives

01 To Develop an Open-Source, Accessible Network Controller Platform

02 To Implement an LLM-Assisted Validated Automation Workflow

03 To Build an Integrated Governed Day-1/Day-2 Operations framework

Visual labels:
Open Source Software
Day 0
Day 1
Day 2
Design
Deployment
Operations

## Slide 9: Proposed Solution Divider

Proposed Solution

## Slide 10: Proposed Solution

Proposed Solution

CogniShield - the unified platform for modern, intelligent and governed network operations.

Center:
CogniShield
Unified - Intelligent - Governed
Network Operations

01 Centralized Controller-Style Workspace

CogniShield unifies network operations into one web-based platform.

Labels:
Inventory
Topology
Monitoring
Backup

02 Unified Operational Modules

Inventory, topology, onboarding, monitoring, backup, AI assistance, and Ansible automation.

03 Governed Network Automation

Intent Parsing -> Ansible Playbook Generation -> Dry-Run Simulation -> Approval Workflow -> Deployment -> Verification

Converts operator requests into Ansible workflows with dry-run, approval, deployment, and verification.

04 Local and Secure Deployment

Web Access
VPN
Local AI Assistant
Network Devices

Locally hosted, VPN-accessible, and deployable on practical infrastructure.

Footer:
Unified Operations
Governed Automation
Secure by Design
Better Visibility
Operational Efficiency
Built for Network Operators

## Slide 11: System Module Divider

System Module

## Slide 12: Module Diagram

Module Diagram

CogniShield System

Network Topology Visualization Module

Purpose:
Visualize and compare planned and live network topologies.

Features:
Planned Topology View
Live Topology View
Topology Comparison (Diff)
Device & Connection Details

LLM Network Automation Module

Purpose:
Intent parsing, Ansible playbook generation, dry-run, approval, deployment, verification.

Features:
Intent Parsing
Ansible Playbook Generation
Dry-Run Simulation
Approval Workflow
Deployment & Verification

Operations Support Module

Purpose:
Streamline day-to-day operations with tools for onboarding, troubleshooting, backup, and AI assistance.

Features:
Onboarding
Templates
Service Profiles
SSH Terminal
Syslog, Backup & Diff, AI Assistant

Performance Metric Monitoring Module

Purpose:
Monitor network performance and system health.

Features:
Performance Metrics
Traffic Statistics
Alerts & Notifications
Trend Analysis & Reports
System Health Overview

## Slide 13: System Architecture Divider

System Architecture

## Slide 14: System Architecture

System Architecture

Proxmox

The Proxmox environment is used as the virtualization layer to host and isolate the different modules and services of the system. Each major component is deployed in its own virtual machine, making the lab setup easier to manage, scale, and recover while simulating a real-world deployment.

Major areas:
User/Operator
Web Browser
Dockerized Frontend Service
Frontend
React
D3.js
Jinja2 Template
Monaco Editor
xterm.js
Recharts
Dockerized Backend Service
Backend Services
Uvicorn
FastAPI
NetBox
PyNetBox
Paramiko
External Systems
Ansible Runner
Qwen 2.5-4B
Ollama
DeepSeek-16B MoE
Gemini API
Lab / Simulation
Virtual Network Device
EVE-NG Emulator
Cisco
Huawei
Juniper
Data Stores & Observability
SQLite
NTP
ChromaDB
DHCP Service
TFTP Service
Syslog
Telegraf
SNMP Exporter
InfluxDB
Prometheus
Grafana
Ansible Vault

Key flows shown:
Load UI
Topology View
Monitoring Charts
SSH Terminal
Config Editing
Inventory Sync
Topology Discovery
NetBox API
SSH / LLDP / CDP
Check / Deploy Request
Local Inference
Hosted Inference
Knowledge Retrieval
Application Data
Update DHCP & Boot File Mapping
Metrics Query
Retrieve Syslog Data
Synchronize clock time
Initialize IP & Boot File
Zero-Touch Provisioning (ZTP)
SNMP Metric Polling
Metric Storing
Dashboard Query
Secret Lookup

## Slide 15: Impact & Benefits Divider

Impact & Benefits

## Slide 16: Impact & Benefits

Impact & Benefits

CogniShield delivers measurable value across teams, institutions, businesses, and learners.

01 Network Teams

Faster configuration deployment, fewer manual errors, better multi-vendor consistency.

02 Universities

Practical learning environment for network automation and AI-assisted operations.

03 SMEs

Entry-level automation platform without expensive controller licensing.

04 Students

Hands-on exposure to intent parsing, Ansible automation, topology, monitoring, and verification.

Footer:
Making AI-assisted network automation more accessible, practical, and governable.

## Slide 17: Innovation Divider

Innovation

## Slide 18: Innovation

Innovation

CogniShield brings a new paradigm to network automation by combining openness, intelligence, and governance.

CogniShield
Intelligent - Open - Governed

Center:
CogniShield
Unified - Intelligent - Governed
Network Automation

01 Open-Source Controller-Style Platform

Network automation platform built around accessible and open tooling.

02 Local LLM Integration

LLM support for interpreting network operation requests.

03 Governed Automation Workflow

From operator request to verified device change.

04 Vendor-Neutral Architecture

Designed for mixed-vendor network environments.

05 Affordable Local Deployment

Runs on practical local infrastructure.

Footer:
Not just AI generation - governed AI-assisted network automation.

## Slide 19: Entrepreneurship Opportunities Divider

Entrepreneurship Opportunities

## Slide 20: Entrepreneurship Opportunity

Entrepreneurship Opportunity

CogniShield has strong potential to create impact and commercial value across education, businesses, and service providers.

CogniShield
Intelligent - Open - Governed

01 Target Customers

Universities
Polytechnics
SMEs
MSPs

02 Possible Business Model

Community - Free
Professional - Subscription
Enterprise - Support Contract
Academic - Campus Licensing

03 Potential Revenue Streams

Deployment services
Training workshops
Support contracts
Enterprise features

Footer:
Affordable, vendor-neutral network operations tooling for education, SMEs, and service providers.

## Slide 21: System Demo Workflow Divider

System Demo Workflow

## Slide 22: CogniShield Demo Flow

System Demo Workflow

Continuous & Intelligent Network Lifecycle

01 Infrastructure

Proxmox & VM

02 System Hosting

Local & VPN

03 Network Lab

EVE-NG
Digital Twin

04 Day-0 Provisioning

Register -> Generate Config -> Publish ZTP

05 Device Initialization

Boot -> DHCP/TFTP -> Baseline Capture -> Discovery

06 Day-1 Automation

Prompt -> Dry Run -> Approval -> Deploy -> Verify

07 Day-2 Assurance

Monitor -> Analyze -> Backup

08 Post-Change Operations

Backup Diff Analysis -> AI Assistance

## Slide 23: Demo Time

Demo Time

## Slide 24: Proxmox Server & Local Network Setup

Proxmox Server & Local Network Setup

Visual content:
Physical lab setup photo showing local server/laptop/network equipment.
Network/infrastructure technician illustration.

## Slide 25: Conclusion Divider

Conclusion

## Slide 26: Conclusion

Conclusion

1. CogniShield successfully achieved a functional MVP-oriented network operations platform.

2. The project unified fragmented topology visibility, inventory context, automation workflow, and monitoring support.

3. It demonstrated governed AI-assisted operations rather than unrestricted autonomous control.

4. It proved the feasibility of building a controller-style platform with open-source tools and accessible local infrastructure.

5. It provides a meaningful foundation for future strengthening and extension.

## Slide 27: Thank You

Thank You !!
