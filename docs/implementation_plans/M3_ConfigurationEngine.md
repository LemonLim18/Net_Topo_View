# M3: Configuration Engine — Implementation Plan

Generate vendor-specific CLI/config from structured parameters using **Jinja2 templates**. Includes diff preview, rollback generation, and basic syntax validation. This is the "writing" counterpart to M1's "reading."

## Architecture

```text
User / LLM Agent
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                   Config Engine (backend/config_engine/)      │
│                                                              │
│  ┌────────────┐   ┌────────────┐   ┌────────────────────┐   │
│  │  Renderer  │──▶│   Differ   │──▶│  Syntax Validator   │   │
│  │ (Jinja2)   │   │ (difflib)  │   │ (per-vendor rules)  │   │
│  └─────┬──────┘   └────────────┘   └────────────────────┘   │
│        │                                                     │
│        ▼                                                     │
│  ┌──────────────────────────────────────────────────────┐    │
│  │            Template Library (backend/templates/)      │    │
│  │                                                       │    │
│  │  cisco_ios/     huawei_vrp/     juniper_junos/        │    │
│  │  ├── ospf.j2    ├── ospf.j2     ├── ospf.j2           │    │
│  │  ├── bgp.j2     ├── bgp.j2      ├── bgp.j2            │    │
│  │  ├── acl.j2     ├── acl.j2      ├── firewall.j2       │    │
│  │  ├── ...        ├── ...          ├── ...                │    │
│  │  └── undo_*     └── undo_*       └── undo_*            │    │
│  └──────────────────────────────────────────────────────┘    │
│        │                                                     │
│        ▼                                                     │
│  ┌──────────────────────────────────────────────────────┐    │
│  │       Parameter Schemas (backend/config_engine/schemas/)  │
│  │       ospf.json, bgp.json, interface.json, ...        │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  M4: Deploy Pipeline (apply to device)
```

## Data Flow

```text
1. Input:   { vendor: "cisco_ios", feature: "ospf", params: { process_id: 1, ... } }
2. Validate: Check params against schemas/ospf.json
3. Render:   Load templates/cisco_ios/ospf.j2  →  render with params  →  CLI string
4. Undo:     Load templates/cisco_ios/undo_ospf.j2  →  render  →  rollback CLI
5. Diff:     Compare new config vs current running config  →  unified diff
6. Output:   { config: "...", rollback: "...", diff: "...", warnings: [...] }
```

## Files

### Config Engine Core

| File | Class / Purpose |
|------|----------------|
| `backend/config_engine/__init__.py` | Package exports |
| `backend/config_engine/renderer.py` | `TemplateRenderer` — Jinja2 load, render, list available templates |
| `backend/config_engine/differ.py` | `ConfigDiffer` — unified/side-by-side diff using `difflib` |
| `backend/config_engine/validator.py` | `SyntaxValidator` — per-vendor validation rules |
| `backend/config_engine/schemas/` | JSON schemas defining required params per template |

### Template Library (10 features × 3 vendors = 30 templates + 30 undo templates)

| Feature | Cisco IOS | Huawei VRP | Juniper Junos |
|---------|-----------|------------|---------------|
| OSPF | `ospf.j2` + `undo_ospf.j2` | `ospf.j2` + `undo_ospf.j2` | `ospf.j2` + `undo_ospf.j2` |
| BGP | `bgp.j2` + `undo_bgp.j2` | `bgp.j2` + `undo_bgp.j2` | `bgp.j2` + `undo_bgp.j2` |
| Interface | `interface.j2` + `undo_interface.j2` | Same | Same |
| ACL | `acl.j2` + `undo_acl.j2` | `acl.j2` + `undo_acl.j2` | `firewall.j2` + `undo_firewall.j2` |
| Static Route | `static_route.j2` + `undo_static_route.j2` | Same | Same |
| VLAN | `vlan.j2` + `undo_vlan.j2` | Same | Same |
| NTP | `ntp.j2` + `undo_ntp.j2` | Same | Same |
| Logging | `logging.j2` + `undo_logging.j2` | Same | Same |
| Banner | `banner.j2` + `undo_banner.j2` | Same | Same |
| SSH Hardening | `ssh_harden.j2` + `undo_ssh_harden.j2` | Same | Same |

### Parameter Schemas

Each template has a matching JSON schema defining required/optional parameters:

```json
// schemas/ospf.json
{
  "required": ["process_id", "router_id", "networks"],
  "properties": {
    "process_id": { "type": "integer", "description": "OSPF process ID" },
    "router_id":  { "type": "string",  "description": "Router ID (x.x.x.x)" },
    "networks":   { "type": "array",   "items": {
      "type": "object",
      "properties": {
        "network":  { "type": "string" },
        "wildcard": { "type": "string" },
        "area":     { "type": "integer" }
      }
    }}
  }
}
```

## Key Interfaces

```python
class TemplateRenderer:
    def render(self, vendor: str, feature: str, params: dict) -> str:
        """Render a config template. Returns CLI commands as string."""

    def render_undo(self, vendor: str, feature: str, params: dict) -> str:
        """Render the matching rollback template."""

    def list_templates(self, vendor: str = None) -> List[dict]:
        """List available templates, optionally filtered by vendor."""

class ConfigDiffer:
    def unified_diff(self, old: str, new: str) -> str:
        """Return unified diff string."""

    def side_by_side(self, old: str, new: str) -> List[Tuple[str, str]]:
        """Return side-by-side comparison lines."""

class SyntaxValidator:
    def validate(self, vendor: str, config: str) -> List[str]:
        """Return list of warning messages. Empty = valid."""
```

## Template Examples

### Cisco IOS — OSPF (`templates/cisco_ios/ospf.j2`)
```jinja2
router ospf {{ process_id }}
 router-id {{ router_id }}
{% for net in networks %}
 network {{ net.network }} {{ net.wildcard }} area {{ net.area }}
{% endfor %}
{% if passive_interfaces is defined %}
{% for intf in passive_interfaces %}
 passive-interface {{ intf }}
{% endfor %}
{% endif %}
```

### Huawei VRP — OSPF (`templates/huawei_vrp/ospf.j2`)
```jinja2
ospf {{ process_id }} router-id {{ router_id }}
{% for net in networks %}
 area {{ net.area }}
  network {{ net.network }} {{ net.wildcard }}
{% endfor %}
{% if passive_interfaces is defined %}
{% for intf in passive_interfaces %}
 silent-interface {{ intf }}
{% endfor %}
{% endif %}
```

### Juniper Junos — OSPF (`templates/juniper_junos/ospf.j2`)
```jinja2
set protocols ospf router-id {{ router_id }}
{% for net in networks %}
set protocols ospf area {{ net.area }} interface {{ net.interface }}
{% endfor %}
{% if passive_interfaces is defined %}
{% for intf in passive_interfaces %}
set protocols ospf area 0 interface {{ intf }} passive
{% endfor %}
{% endif %}
```

## Implementation Order

| Phase | What | Files |
|-------|------|-------|
| 1 | Renderer + schemas | `renderer.py`, `__init__.py`, `schemas/*.json` |
| 2 | Cisco IOS templates (all 10 features) | `templates/cisco_ios/*.j2` |
| 3 | Huawei VRP templates (all 10 features) | `templates/huawei_vrp/*.j2` |
| 4 | Juniper Junos templates (all 10 features) | `templates/juniper_junos/*.j2` |
| 5 | Differ | `differ.py` |
| 6 | Validator | `validator.py` |
| 7 | Verification | Unit tests + manual rendering |

## Verification Plan

### Automated Tests
- Render each template with sample params and verify the output matches expected CLI.
- Test the differ with known config pairs.
- Test the validator catches common errors (missing `end`, unclosed brackets, etc.).

### Manual Verification
- Render an OSPF config for all 3 vendors and visually inspect the output.
- Generate a diff between two configs and verify readability.

## Status
- **Created:** 2026-02-16
- **Branch:** `M3_ConfigurationEngine` (to be created)
- **Depends on:** M1 (Driver Layer — for Vendor enum and device types)
- **Used by:** M4 (Deploy), M5 (LLM Agent), M6 (MCP), M8 (Drift), M13 (Security)
