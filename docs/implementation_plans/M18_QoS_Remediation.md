# M18: QoS Configuration & AI Auto-Remediation — Implementation Plan

## Goal
To introduce Quality of Service (QoS) capabilities into the network controller based on the standard 5-step QoS model:
1. **Classification**: Identifying traffic types (Voice, Video, Web, Downloads) via ACLs/protocols.
2. **Marking (Coloring)**: Tagging traffic with DSCP values (EF, AF41, BE, CS1).
3. **Policing and Shaping**: Rate-limiting heavy traffic (Downloads) and smoothing bursty delays.
4. **Queuing**: Placing packets in priority queues.
5. **Scheduling**: Determining which queue is serviced first (PQ, LLQ, WFQ).

This plan enables the system to detect traffic anomalies (via `AnomalyDetector`) and automatically generate and deploy these complex QoS policies using the AI Playbook Engine.

## Proposed Changes

### Phase 1: QoS Configuration Templates (Config Engine)

We create Jinja2 templates mapping the 5 QoS steps to vendor-specific Modular QoS CLI (MQC).

#### 1. Cisco IOS Configuration (`backend/templates/cisco_ios/qos.j2`)
**Maps directly to Steps 1-5 (MQC)**
```jinja2
! Step 1: Classification (Class Maps)
{% for c_map in class_maps %}
class-map match-any {{ c_map.name }}
{% for match in c_map.matches %}
 match protocol {{ match }}
{% endfor %}
!
{% endfor %}

! Steps 2-5: Marking, Shaping/Policing, Queuing, Scheduling (Policy Maps)
policy-map {{ policy_name }}
{% for p_class in policy_classes %}
 class {{ p_class.class_name }}
  ! Step 2: Marking
  {% if p_class.dscp %}
  set dscp {{ p_class.dscp }}
  {% endif %}
  ! Step 5 & 4: Scheduling & Queuing (LLQ / Priority)
  {% if p_class.priority_percent %}
  priority percent {{ p_class.priority_percent }}
  {% endif %}
  ! Step 3: Shaping / Policing
  {% if p_class.shape_average %}
  shape average {{ p_class.shape_average }}
  {% elif p_class.police_rate %}
  police {{ p_class.police_rate }} conform-action transmit exceed-action drop
  {% endif %}
{% endfor %}
!

! Apply to Interface
interface {{ interface }}
 service-policy {{ direction }} {{ policy_name }}
```

#### 2. Huawei VRP Configuration (`backend/templates/huawei_vrp/qos.j2`)
**Scope:** `traffic classifier`, `traffic behavior`, `traffic policy`.
```jinja2
# Step 1: Classification
{% for c_map in class_maps %}
traffic classifier {{ c_map.name }} operator or
{% for match in c_map.matches %}
 if-match protocol {{ match }}
{% endfor %}
#
{% endfor %}

# Steps 2-4: Marking, Shaping/Policing, Queuing Behavior
{% for p_class in policy_classes %}
traffic behavior {{ p_class.class_name }}_behavior
 # Step 2: Marking
 {% if p_class.dscp %}
 remark dscp {{ p_class.dscp }}
 {% endif %}
 # Step 3: Policing / Shaping
 {% if p_class.police_rate %}
 car cir {{ p_class.police_rate / 1000 }} 
 {% elif p_class.shape_average %}
 gts cir {{ p_class.shape_average / 1000 }}
 {% endif %}
 # Step 5: Scheduling 
 {% if p_class.priority == "ef" %}
 queue ef bandwidth pct {{ p_class.priority_percent }}
 {% endif %}
#
{% endfor %}

# Combine into Policy
traffic policy {{ policy_name }}
{% for p_class in policy_classes %}
 classifier {{ p_class.class_name }} behavior {{ p_class.class_name }}_behavior
{% endfor %}
#

interface {{ interface }}
 traffic-policy {{ policy_name }} {{ direction }}
```

#### 3. Juniper Junos Configuration (`backend/templates/juniper_junos/qos.j2`)
**Scope:** Class-of-Service (CoS) forwarding classes, schedulers, and classifiers.
```jinja2
# Step 1 & 4: Classification and Queuing (Forwarding Classes)
{% for c_map in class_maps %}
set class-of-service forwarding-classes class {{ c_map.name }} queue-num {{ loop.index0 }}
{% endfor %}

# Step 2 & 1: Marking based on input (Classifiers)
set class-of-service classifiers dscp BA_CLASSIFIER import default
{% for p_class in policy_classes %}
{% if p_class.dscp %}
set class-of-service classifiers dscp BA_CLASSIFIER forwarding-class {{ p_class.class_name }} loss-priority low code-points {{ p_class.dscp }}
{% endif %}
{% endfor %}

# Step 3 & 5: Policing, Shaping, Scheduling (Schedulers)
{% for p_class in policy_classes %}
set class-of-service schedulers {{ p_class.class_name }}_SCHEDULer transmit-rate {{ p_class.shape_average | default('remainder') }}
{% if p_class.priority_percent %}
set class-of-service schedulers {{ p_class.class_name }}_SCHEDULer priority strict-high
{% endif %}
{% endfor %}

# Apply
set class-of-service interfaces {{ interface }} unit 0 classifiers dscp BA_CLASSIFIER
```

---

### Phase 2: Schema Definition (`qos.json`)

To ensure the LLM agent and config engine follow the 5-step rules, we enforce it via JSON Schema.

#### [NEW] `backend/config_engine/schemas/qos.json`
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "QoS Policy",
  "type": "object",
  "properties": {
    "policy_name": { "type": "string" },
    "interface": { "type": "string" },
    "direction": { "enum": ["input", "output"] },
    "class_maps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "matches": { "type": "array", "items": { "type": "string" } }
        }
      }
    },
    "policy_classes": {
        "type": "array",
        "items": {
           "type": "object",
           "properties": {
               "class_name": { "type": "string" },
               "dscp": { "type": "string" },
               "priority_percent": { "type": "number" },
               "shape_average": { "type": "number" },
               "police_rate": { "type": "number" }
           }
        }
    }
  },
  "required": ["policy_name", "interface", "direction", "class_maps", "policy_classes"]
}
```

---

### Phase 3: AI Playbook Integration (Auto-Remediation)

#### [MODIFY] `backend/security/response/playbooks.py`
Add the workflow that triggers upon detection of an anomaly.

```python
    "qos_remediation_response": Playbook(
        name="qos_remediation_response",
        description="Auto-remediate bandwidth spikes by deploying a restrictive QoS policy (5-step classification)",
        trigger_event_types=["anomaly"], 
        severity_threshold="high",
        mode="semi_auto", 
        steps=[
            PlaybookStep(1, "log", "Log severe traffic anomaly", auto_execute=True),
            PlaybookStep(2, "alert", "Alert Network Engineering: Severe congestion detected", auto_execute=True),
            PlaybookStep(3, "generate_qos", "Generate 5-step QoS policy to limit heavy flows (protect VoIP, police Downloads)", auto_execute=True),
            PlaybookStep(4, "apply_qos", "Deploy QoS policy to affected interface via M4 (requires approval)", auto_execute=False),
        ],
    ),
```

#### [MODIFY] `backend/security/response/responder.py`
Add `_handle_qos_generation()` to generate the payload matching `.qos.json` ensuring Voice gets DSCP EF/LLQ, and anomalous traffic gets Policed.

---

### Phase 4: LLM Agent Integration

#### [MODIFY] `backend/llm/network_tools.py`
Add tool `apply_qos_policy` so the AI can manually intervene using natural language:
> "The network is slow. Prioritize VoIP on r1 interface Gi0/1 and limit file downloads to 50Mbps."

```python
def apply_qos_policy(hostname: str, interface: str, direction: str, rules_json: str) -> str:
    """
    Generate and deploy a 5-step QoS policy (Classify, Mark, Police/Shape, Queue, Schedule).
    rules_json must match the qos.json schema.
    """
```
