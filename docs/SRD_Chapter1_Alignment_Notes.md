# SRD Chapter 1 Alignment Notes

This note explains how the earlier proposal wording and figures were adjusted to match the **current implemented system** more strictly.

## Main Corrections

### 1. Replace overstated identity claims

The following phrases were too strong for the current implementation and should be avoided in Chapter 1:

- `intent-based, AI-driven campus network automation system`
- `fully LLM-integrated automation and orchestration system`
- `adaptive real-time remediation`
- `fully autonomous network controller`
- `SDN controller for centralized control` as a core delivered claim

These were replaced with stricter wording:

- `LLM-assisted network configuration and operations platform`
- `hybrid AI-assisted automation controller`
- `Ansible-based, approval-governed workflow`
- `Day-1 and Day-2 focused with partial Day-0 support`
- `not a full SDN controller, not a strict full IBN platform, and not a fully closed-loop autonomous controller`

### 2. Tighten the project scope

The revised chapter only claims features that are **currently exposed in the frontend-visible system** or clearly supported by the current implementation, including:

- overview dashboard
- planned inventory and live topology views
- knowledge base / RAG management
- Ansible Studio
- playbook preview, dry-run, approval, execution
- compliance, drift, monitoring, security, incidents
- onboarding support
- config backup
- intent history and intent parser workbench

It intentionally avoids presenting hidden or backend-only features as part of the core delivered scope.

### 3. Reframe the LLM role

The old wording made the LLM sound like the central autonomous decision-maker.  
The revised wording makes it clear that the LLM:

- assists with request interpretation and playbook generation
- is constrained by schema and context
- is followed by clarification, dry-run, and approval
- does not directly act as a fully autonomous controller

### 4. Reframe SDN and IBN claims

The revised Chapter 1 is aligned with the strict technical position already established:

- **SDN:** not in the strict architectural sense
- **Full Intent-Based Networking:** not yet
- **Most accurate label:** LLM-assisted intent-driven network automation and operations platform

## Figure Review

## Figure 1

**Status:** Keep  
**Reason:** It is a background illustration of the hierarchical campus network model and does not overclaim the project scope.

## Figure 2

**Status:** Keep  
**Reason:** It is useful as a background illustration showing the idea of a network management zone connected to the campus network.

## Figure 3

**Status:** Replace or clearly relabel  
**Reason:** The earlier figure is no longer fully aligned with the current implemented system because it visually suggests:

- a core **SDN Controller** as part of the main delivered architecture
- broader always-on centralized control than the project currently exposes
- additional ecosystem components such as **SOAR** and broader autonomous remediation implication

This creates the wrong expectation that the current system is already:

- full SDN
- full IBN
- full end-to-end autonomous controller

The revised chapter therefore includes a replacement current-scope high-level architecture diagram that better reflects the implemented system.

## Textual Areas Most Needing Replacement

If you are updating the SRD manually in another editor, the highest-priority sections to replace are:

1. **Abstract**
2. **1.1 Background** final project-introduction paragraph
3. **1.4 System Objectives**
4. **1.5 Proposed Solution**
5. **1.6 Benefits / Impact / Significance**
6. **1.7 Uniqueness**
7. **1.8 Summary**
8. **Figure 3 caption and surrounding explanation**

## Practical Positioning to Preserve Throughout the SRD

Use these scope statements consistently:

- The system is **LLM-assisted**, not LLM-only.
- The system is **prompt-first with governed fallback**, not form-only and not fully autonomous.
- The system is centered on **Ansible-based, approval-governed automation**.
- The current implementation is strongest in **Day-1 and Day-2 operations**.
- **Partial Day-0 onboarding support** exists, but not full zero-touch autonomous provisioning.
- The project supports **legacy and mixed-vendor environments**.
- The platform is **not yet strict SDN** and **not yet full IBN**.

## Related Current-Implementation Evidence

Useful references when defending the revised wording:

- [backend.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/backend.py)
- [App.tsx](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/src/App.tsx)
- [AnsibleStudioView.tsx](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/AnsibleStudioView.tsx)
- [IntentParserView.tsx](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/frontend/views/IntentParserView.tsx)
- [onboarding/api.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/onboarding/api.py)
- [ansible/api.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/ansible/api.py)
- [dashboard/api.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/dashboard/api.py)
- [compliance/api.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/compliance/api.py)
- [security/api.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/security/api.py)
- [rag/api.py](/C:/Users/limmi/OneDrive/Desktop/NetTopoView/backend/rag/api.py)
