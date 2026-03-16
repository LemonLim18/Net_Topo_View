"""
Resolve reference playbook templates by feature/vendor and fill them from intent.

Flow:
  1. Resolve which reference playbook to use (RAG-informed or deterministic from feature + vendors).
  2. Load the template (playbook.yml or playbook_template.yml).
  3. Fill placeholders (e.g. {{ vlan_id }}, {{ vlan_name }}) from intent.params only — no LLM rewrite.
  4. Return the filled YAML string.

This keeps the reference template structure intact; only variable values are substituted.
"""

import logging
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ansible.reference_resolver")

_REFERENCE_DIR = Path(__file__).parent / "reference"

# Known features that have reference playbooks (subdirs under reference/)
_KNOWN_FEATURES = frozenset({"vlan"})

# Vendors that have single-vendor playbooks under reference/{feature}/{vendor}/
_KNOWN_VENDORS = frozenset({"cisco_ios", "huawei_vrp", "juniper_junos"})

# Multi-vendor template name (under reference/{feature}/multi_vendor/)
_MULTI_VENDOR_TEMPLATE = "playbook_template.yml"
_SINGLE_PLAYBOOK = "playbook.yml"


def resolve_playbook_template_path(
    feature: str,
    vendors: List[str],
) -> Optional[Path]:
    """
    Resolve the path to the reference playbook template for this feature and vendor set.

    - If multiple vendors (e.g. cisco_ios + huawei_vrp): use reference/{feature}/multi_vendor/playbook_template.yml.
    - If a single known vendor: use reference/{feature}/{vendor}/playbook.yml.
    - Otherwise return None (caller should fall back to LLM generation).

    Args:
        feature: e.g. "vlan"
        vendors: list of normalized vendor strings, e.g. ["cisco_ios", "huawei_vrp"]

    Returns:
        Path to the template file, or None if no reference exists.
    """
    feature = (feature or "").strip().lower()
    if not feature or feature not in _KNOWN_FEATURES:
        return None

    base = _REFERENCE_DIR / feature
    if not base.exists():
        return None

    # Normalize and filter to known vendors
    seen = {normalize_vendor(v) for v in vendors if v}
    seen = seen & _KNOWN_VENDORS

    if len(seen) > 1:
        multi_path = base / "multi_vendor" / _MULTI_VENDOR_TEMPLATE
        if multi_path.exists():
            return multi_path
        return None
    if len(seen) == 1:
        vendor = next(iter(seen))
        single_path = base / vendor / _SINGLE_PLAYBOOK
        if single_path.exists():
            return single_path
    # For vlan with no vendor info (e.g. planning_context empty), default to multi_vendor
    # so we always get the correct reference format instead of LLM/bundle wrong format
    if feature == "vlan":
        multi_path = base / "multi_vendor" / _MULTI_VENDOR_TEMPLATE
        if multi_path.exists():
            return multi_path
    return None


def normalize_vendor(value: Any) -> str:
    v = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v else ""


def vendors_from_planning_context(planning_context: Optional[Dict[str, Any]]) -> List[str]:
    """Extract unique normalized vendors from planning_context['devices']."""
    if not planning_context:
        return []
    devices = planning_context.get("devices") or []
    out = []
    seen = set()
    for d in devices:
        v = normalize_vendor(d.get("vendor"))
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def vendors_from_db(hostnames: List[str], db_manager: Any) -> List[str]:
    """
    Get unique normalized vendors for the given hostnames from the device DB.
    Use when planning_context has no vendor info (e.g. bundle inventory empty or vendor not set).
    """
    if not hostnames or db_manager is None:
        return []
    out = []
    seen = set()
    for host in hostnames:
        try:
            dev = db_manager.get_device_by_hostname(host)
            if dev is None:
                continue
            v = normalize_vendor(getattr(dev, "vendor", None))
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        except Exception:
            continue
    return out


def load_template(path: Path) -> str:
    """Load template file content as string."""
    return path.read_text(encoding="utf-8")


def get_template_vars(intent: Dict[str, Any], _template_content: str) -> Dict[str, Any]:
    """
    Build the variable dict for template rendering from intent.

    Uses intent.params (e.g. vlan_id, vlan_name, interfaces, mode) and provides
    defaults so the template renders without missing key errors.
    """
    params = dict(intent.get("params") or {})
    # vlan_id / vlan_name: from flat params or from vlans[0] (schema shape)
    vlans_list = params.get("vlans") or []
    if isinstance(vlans_list, list) and vlans_list and isinstance(vlans_list[0], dict):
        first = vlans_list[0]
        params.setdefault("vlan_id", str((first.get("id") or first.get("vlan_id") or "200")).strip())
        params.setdefault("vlan_name", str((first.get("name") or first.get("vlan_name") or "TEST")).strip())
    vlan_id = params.get("vlan_id")
    if vlan_id is not None:
        params["vlan_id"] = str(vlan_id).strip()
    else:
        params.setdefault("vlan_id", "200")
    vlan_name = params.get("vlan_name")
    if vlan_name is not None:
        params["vlan_name"] = str(vlan_name).strip()
    else:
        params.setdefault("vlan_name", "TEST")

    # Scenario-based interfaces: list of { name, mode [, trunk_vlans ] } for ios_l2_interfaces
    raw_interfaces = params.get("interfaces")
    if isinstance(raw_interfaces, list) and raw_interfaces:
        interface_names = [str(x).strip() for x in raw_interfaces if str(x).strip()]
    else:
        interface_names = ["GigabitEthernet0/1", "GigabitEthernet0/2"]

    mode = str(params.get("mode") or "access").strip().lower()
    if mode not in ("access", "trunk"):
        mode = "access"

    vlan_id_str = str(params.get("vlan_id", "200")).strip()
    interfaces_config: List[Dict[str, Any]] = []
    interfaces_config_with_vlan: List[Dict[str, Any]] = []

    for name in interface_names:
        interfaces_config.append({"name": name, "mode": mode})
        if mode == "access":
            interfaces_config_with_vlan.append({
                "name": name,
                "mode": "access",
                "access": {"vlan": vlan_id_str},
            })
        else:
            trunk_vlans = str(params.get("trunk_vlans") or "all").strip()
            interfaces_config_with_vlan.append({
                "name": name,
                "mode": "trunk",
                "trunk": {"trunk_vlans": trunk_vlans},
            })

    params["interfaces_config"] = interfaces_config
    params["interfaces_config_with_vlan"] = interfaces_config_with_vlan
    # Serialize for injection into playbook YAML (indent 10 spaces to align under config:)
    yaml_str = yaml.dump(interfaces_config_with_vlan, default_flow_style=False, sort_keys=False)
    params["interfaces_config_yaml"] = "\n".join("          " + line for line in yaml_str.strip().splitlines())
    # Concrete host list so the filled playbook passes hosts alignment (reference format unchanged, only value)
    target_hosts = intent.get("target_hosts") or []
    params["target_hosts"] = ",".join(str(h).strip() for h in target_hosts if str(h).strip())
    return params


def render_template(template_content: str, variables: Dict[str, Any]) -> str:
    """
    Render the template with the given variables.

    Uses Jinja2-style substitution: {{ vlan_id }}, {{ vlan_name }} in the
    template are replaced by the values in variables. No other logic;
    the reference template is not modified structurally.
    """
    try:
        from jinja2 import Environment, BaseLoader
    except ImportError:
        # Fallback: simple replace for {{ key }} patterns
        out = template_content
        for key, val in variables.items():
            out = out.replace("{{ " + key + " }}", str(val))
            out = out.replace("{{ " + key + "}}", str(val))
            out = out.replace("{{" + key + " }}", str(val))
            out = out.replace("{{" + key + "}}", str(val))
        return out

    env = Environment(
        loader=BaseLoader(),
        variable_start_string="{{ ",
        variable_end_string=" }}",
        # Avoid stripping newlines
        trim_blocks=False,
        lstrip_blocks=False,
    )
    tpl = env.from_string(template_content)
    return tpl.render(**variables)


def fill_playbook_from_reference(
    feature: str,
    vendors: List[str],
    intent: Dict[str, Any],
    planning_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    If a reference template exists for this feature/vendor set, load it,
    fill variables from intent, and return the playbook YAML. Otherwise return None.

    Caller should fall back to LLM generation when this returns None.
    """
    path = resolve_playbook_template_path(feature, vendors)
    if path is None:
        return None
    try:
        content = load_template(path)
        variables = get_template_vars(intent, content)
        return render_template(content, variables)
    except Exception as exc:
        logger.warning("Template fill failed for %s: %s", path, exc)
        return None
