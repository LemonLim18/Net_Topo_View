"""
Generate docs/test_case/intent_catalog_completeness_audit.md from testcase_list.md checklist lines.
Run from repo root: python backend/scripts/generate_intent_completeness_audit.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTCASE = ROOT / "docs" / "test_case" / "testcase_list.md"
OUT = ROOT / "docs" / "test_case" / "intent_catalog_completeness_audit.md"

CHECK = re.compile(r"^- \[[ x]\]\s+(.+?)\s*$")


def infer(prompt: str) -> tuple[str, list[str], str]:
    p = prompt.lower()
    cats: list[str] = []
    if "vlan" in p or "svlan" in p:
        cats.append("vlan")
    if any(
        x in p
        for x in (
            "interface",
            "port",
            "gigabit",
            "ethernet",
            "shutdown",
            "admin-down",
            "trunk",
            "acl",
            "route",
            "ospf",
            "qos",
        )
    ):
        if "gigabit" in p or "ethernet" in p or "interface" in p or "port" in p or "shutdown" in p or "admin" in p:
            if "interface" not in cats:
                cats.append("interface")
    if "acl" in p or "access list" in p or "access-list" in p:
        cats.append("acl")
    if "qos" in p or "quality of service" in p or "dscp" in p or "polic" in p:
        cats.append("qos")
    if "static route" in p or "default route" in p or "route " in p or "next hop" in p:
        cats.append("static_route")
    if "ospf" in p:
        cats.append("ospf")
    cat = ", ".join(dict.fromkeys(cats)) or "general"
    ops: list[str] = []
    if "create vlan" in p or ("vlan" in p and "create" in p):
        ops.append("create_vlan")
    if "access" in p and "vlan" in p and "port" in p:
        ops.append("assign_vlan_to_access_port")
    if "trunk" in p or "allow" in p and "vlan" in p:
        ops.append("assign_vlan_to_trunk_port / set_native_vlan")
    if "native" in p or "pvid" in p:
        ops.append("set_native_vlan")
    if "shutdown" in p or "admin-down" in p or "shut down" in p:
        ops.append("shutdown_interface")
    if "routed" in p or "ip address" in p or "/24" in p:
        ops.append("configure_interface (L3)")
    if "acl" in p:
        if "apply" in p or "inbound" in p or "outbound" in p:
            ops.append("apply_acl_to_interface / configure_acl")
        else:
            ops.append("create_acl / configure_acl")
    if "qos" in p:
        ops.append("configure_qos / create_qos_policy / apply_qos_policy_to_interface")
    if "route" in p:
        ops.append("configure_static_route / add_default_route")
    if "ospf" in p:
        ops.append("configure_ospf + fine-grained OSPF ops")
    if "generic" in p or "ambiguous" in p:
        ops.append("(ambiguous → requires_resolution)")
    op_s = ", ".join(dict.fromkeys(ops)) or "(detect from catalog + LLM)"
    issues = "See **Global fixes**; historical traces in testcase_list.md may pre-date validation_engine + YAML updates."
    return cat, [op_s], issues


def main() -> None:
    lines = TESTCASE.read_text(encoding="utf-8").splitlines()
    prompts: list[str] = []
    for ln in lines:
        m = CHECK.match(ln.strip())
        if m:
            prompts.append(m.group(1).strip())

    parts: list[str] = []
    parts.append("# Intent catalog completeness audit\n")
    parts.append("Generated from `testcase_list.md` checklist prompts. ")
    parts.append("This document tracks **schema + validation + cross-check** coverage (not LLM extraction quality).\n")
    parts.append("\n## Global fixes applied (architecture)\n\n")
    parts.append(
        "- **`required_parameter_groups.any_of`** in `validation_engine.py`: ACL create vs apply, QoS policy vs interface, static route next-hop vs egress-if, OSPF variants.\n"
    )
    parts.append(
        "- **`_canonical_operation`** preserved in `operation_adapter.py` so validation uses fine-grained catalog rows after legacy action rewrite.\n"
    )
    parts.append(
        "- **`resolve_operation_spec_for_intent`**: prefers `parameters._canonical_operation` for spec lookup.\n"
    )
    parts.append(
        "- **Field satisfiers**: `rules` non-empty, `interfaces` / `interfaces_to_shutdown` (incl. all/*), `destination`/`default_route`, `egress_interface` aliases, OSPF `ospf_networks`/`passive_interfaces`/`redistribute`/`router_id`, ACL/QoS aliases.\n"
    )
    parts.append(
        "- **New / split catalog operations**: `create_acl`, `apply_acl_to_interface`, `create_qos_policy`, `apply_qos_policy_to_interface`, `add_default_route`, OSPF fine-grained ops + `configure_ospf_redistribute` adapter mapping.\n"
    )
    parts.append(
        "- **`cross_check.py`**: inventory candidates include `device_deduction.deduced`; wildcard shutdown lists skip bogus interface validation.\n"
    )
    parts.append(
        "- **`keyword_map.yaml`**: hints for apply ACL, OSPF passive/redistribute, routed L3, route-via-interface, QoS uplink/DSCP.\n"
    )
    parts.append(
        "- **`interface.yaml`**: `implicit_requires_targets: true` on all interface operations.\n"
    )
    parts.append("\n## Per checklist prompt\n\n")
    for i, pr in enumerate(prompts, 1):
        cat, ops, iss = infer(pr)
        parts.append(f"### {i}. Prompt:\n{pr}\n\n")
        parts.append("### Detected (expected):\n")
        parts.append(f"- category: **{cat}**\n")
        parts.append(f"- operation(s): **{ops[0]}**\n\n")
        parts.append("### Issues Found (historical vs current codebase):\n")
        parts.append(f"- {iss}\n\n")
        parts.append("### Fix Applied:\n")
        parts.append(
            "- Schema/validation/cross-check/keyword updates documented in **Global fixes**; operation YAML under `backend/ansible/intent_catalog/categories/`.\n\n"
        )
        parts.append("---\n\n")

    parts.append("## Summary: operations supported (catalog keys)\n\n")
    parts.append(
        "Run: `python -c \"from ansible.intent_pipeline.schema_loader import reload_catalog; c=reload_catalog(); "
        "print(sorted({k for cat in c.categories.values() for k in (cat.get('operations') or {})}))\"` "
        "from `backend/`.\n\n"
    )
    parts.append("### VLAN (`vlan.yaml`)\n")
    parts.append("- `create_vlan`, `configure_interface` (legacy in category)\n\n")
    parts.append("### Interface (`interface.yaml`)\n")
    parts.append(
        "- `configure_interface`, `assign_vlan_to_access_port`, `assign_vlan_to_trunk_port`, "
        "`set_native_vlan`, `shutdown_interface`\n\n"
    )
    parts.append("### ACL (`acl.yaml`)\n")
    parts.append("- `create_acl`, `apply_acl_to_interface`, `configure_acl` (any-of groups)\n\n")
    parts.append("### QoS (`qos.yaml`)\n")
    parts.append("- `create_qos_policy`, `apply_qos_policy_to_interface`, `configure_qos` (any-of groups)\n\n")
    parts.append("### Static route (`static_route.yaml`)\n")
    parts.append("- `add_default_route`, `configure_static_route` (any-of groups)\n\n")
    parts.append("### OSPF (`ospf.yaml`)\n")
    parts.append(
        "- `enable_ospf_process`, `advertise_network`, `configure_passive_interface`, "
        "`configure_ospf_redistribute`, `configure_ospf` (any-of groups)\n\n"
    )
    parts.append("## Summary: category / shared fields\n\n")
    parts.append("- Per-category: `category_key`, `display_name`, `operations` (each with `action`, `feature`, ")
    parts.append("`required_parameters` OR `required_parameter_groups.any_of`, `implicit_requires_targets`, ")
    parts.append("`optional_parameters`, `keyword_triggers`, `rules`, `dependencies`, `forbidden_fields`).\n")
    parts.append("- Global: `global_fields.yaml` (`target_devices`, `category`, `operation`, …).\n\n")
    parts.append("## Summary: validation rules (engine)\n\n")
    parts.append("- Required fields / **any-of groups** per operation spec.\n")
    parts.append("- `implicit_requires_targets`: scope / target_devices / resolved_devices / device_deduction (unless suppressed).\n")
    parts.append("- `forbidden_fields`: non-empty presence → violation + interactive ask.\n")
    parts.append("- `dependencies`: surfaced as `schema_dependency_note` asks.\n")
    parts.append("- VLAN `vlans` virtual expansion → `vlan_id` / `vlan_name` missing flags.\n")
    parts.append("- Cross-check: devices/interfaces vs DB; wildcard shutdown skips iface checks; deduced devices included.\n")
    parts.append("- Unknown ops: `schema_recovery` (adapter → catalog, optional synthetic with `INTENT_SCHEMA_AUTO_RECOVER`).\n")

    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"Wrote {OUT} ({len(prompts)} prompts)")


if __name__ == "__main__":
    main()
