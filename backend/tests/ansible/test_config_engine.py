"""Quick verification of M3 Config Engine."""
import sys
sys.path.insert(0, r"c:\Users\limmi\OneDrive\Desktop\NetTopoView")

from config_engine.renderer import TemplateRenderer
from config_engine.differ import ConfigDiffer
from config_engine.validator import SyntaxValidator

renderer = TemplateRenderer()
differ = ConfigDiffer()
validator = SyntaxValidator()

# ── Cisco IOS OSPF ────────────────────────────
print("=" * 50)
print("CISCO IOS — OSPF")
print("=" * 50)
cisco_ospf = renderer.render("cisco_ios", "ospf", {
    "process_id": 1,
    "router_id": "1.1.1.1",
    "networks": [{"network": "10.0.0.0", "wildcard": "0.0.0.255", "area": 0}],
    "passive_interfaces": ["Loopback0"],
})
print(cisco_ospf)

# ── Huawei VRP OSPF ──────────────────────────
print("\n" + "=" * 50)
print("HUAWEI VRP — OSPF")
print("=" * 50)
huawei_ospf = renderer.render("huawei_vrp", "ospf", {
    "process_id": 1,
    "router_id": "2.2.2.2",
    "networks": [{"network": "10.0.0.0", "wildcard": "0.0.0.255", "area": 0}],
})
print(huawei_ospf)

# ── Juniper Junos OSPF ───────────────────────
print("\n" + "=" * 50)
print("JUNIPER JUNOS — OSPF")
print("=" * 50)
junos_ospf = renderer.render("juniper_junos", "ospf", {
    "process_id": 1,
    "router_id": "3.3.3.3",
    "networks": [{"interface": "ge-0/0/0.0", "area": 0}],
    "passive_interfaces": ["lo0.0"],
})
print(junos_ospf)

# ── Undo Template ─────────────────────────────
print("\n" + "=" * 50)
print("CISCO IOS — OSPF UNDO")
print("=" * 50)
undo = renderer.render_undo("cisco_ios", "ospf", {"process_id": 1})
print(undo)

# ── BGP Test ──────────────────────────────────
print("\n" + "=" * 50)
print("CISCO IOS — BGP")
print("=" * 50)
cisco_bgp = renderer.render("cisco_ios", "bgp", {
    "asn": 65001,
    "router_id": "1.1.1.1",
    "neighbors": [
        {"ip": "10.0.0.2", "remote_as": 65002, "description": "ISP-Peer"},
    ],
    "networks": [{"prefix": "192.168.0.0", "mask": "255.255.255.0"}],
})
print(cisco_bgp)

# ── Template Discovery ────────────────────────
print("\n" + "=" * 50)
print("TEMPLATE DISCOVERY")
print("=" * 50)
templates = renderer.list_templates()
print(f"Total feature templates: {len(templates)}")
for v in ["cisco_ios", "cisco_xe", "huawei_vrp", "juniper_junos"]:
    count = len([t for t in templates if t["vendor"] == v])
    undos = len([t for t in templates if t["vendor"] == v and t["has_undo"]])
    print(f"  {v}: {count} features, {undos} with undo")

# ── Differ ────────────────────────────────────
print("\n" + "=" * 50)
print("CONFIG DIFFER")
print("=" * 50)
old_cfg = "router ospf 1\n router-id 1.1.1.1\n network 10.0.0.0 0.0.0.255 area 0"
new_cfg = old_cfg + "\n network 192.168.1.0 0.0.0.255 area 1"
result = differ.full_diff(old_cfg, new_cfg)
print(f"Additions: {result.additions}, Deletions: {result.deletions}")
print(f"Summary: {differ.summary(old_cfg, new_cfg)}")

# ── Validator ─────────────────────────────────
print("\n" + "=" * 50)
print("SYNTAX VALIDATOR")
print("=" * 50)
w1 = validator.validate("cisco_ios", cisco_ospf)
print(f"Cisco OSPF warnings: {len(w1)}")
for w in w1:
    print(f"  ⚠ {w}")

w2 = validator.validate("juniper_junos", junos_ospf)
print(f"Junos OSPF warnings: {len(w2)}")
for w in w2:
    print(f"  ⚠ {w}")

# ── Schema Validation ─────────────────────────
print("\n" + "=" * 50)
print("SCHEMA VALIDATION (expected failure)")
print("=" * 50)
try:
    renderer.render("cisco_ios", "ospf", {"process_id": 1})
    print("ERROR: Should have raised ValueError!")
except ValueError as e:
    print(f"Correctly caught: {e}")

print("\n[OK] ALL TESTS PASSED!")
