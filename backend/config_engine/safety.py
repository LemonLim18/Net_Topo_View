from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from ansible.router import ExecutionAction, route_execution
from config_engine.renderer import TemplateRenderer
from config_engine.validator import SyntaxValidator


logger = logging.getLogger("config_engine.safety")


SAFETY_VALIDATION_VERSION = "phase3_step10_v1"

# Placeholder used when parsing YAML that contains Ansible/Jinja2 {{ ... }} (so PyYAML doesn't choke)
_J2_PLACEHOLDER = "__J2__"
# Valid YAML scalar used only when the LLM emitted literal __J2__ so we can still parse structure
_LITERAL_J2_REPLACEMENT_FOR_PARSE = "JINJA_VAR"


def _mask_jinja2_for_yaml_parse(text: str) -> str:
    """Replace {{ ... }} with a quoted placeholder so PyYAML can parse playbook YAML that uses Ansible vars."""
    return re.sub(r"\{\{.*?\}\}", _J2_PLACEHOLDER, text, flags=re.DOTALL)


def _normalize_literal_j2_for_parse(text: str) -> str:
    """Replace literal __J2__ (wrong LLM output) with a valid YAML scalar so structure parsing can succeed."""
    return text.replace(_J2_PLACEHOLDER, _LITERAL_J2_REPLACEMENT_FOR_PARSE)

_UNSAFE_TEMPLATE_PATTERNS: Tuple[Tuple[str, re.Pattern[str], str], ...] = (
    (
        "template_includes_or_imports",
        re.compile(r"\{%-?\s*(include|import|from|extends)\b", re.IGNORECASE),
        "Template uses Jinja include/import/extends constructs that are outside the approved template subset.",
    ),
    (
        "template_dunder_access",
        re.compile(r"__[A-Za-z0-9_]+__"),
        "Template references dunder attributes, which are disallowed in controller-managed templates.",
    ),
    (
        "template_runtime_helpers",
        re.compile(r"\b(namespace|cycler|joiner|lipsum)\s*\(", re.IGNORECASE),
        "Template uses advanced Jinja runtime helpers that are outside the approved deterministic subset.",
    ),
)

_DANGEROUS_CONFIG_PATTERNS: Tuple[Tuple[str, re.Pattern[str], str], ...] = (
    (
        "write_erase",
        re.compile(r"(^|\n)\s*write\s+erase\b", re.IGNORECASE),
        "Configuration contains 'write erase', which is destructive and unsupported in Milestone 3 proposals.",
    ),
    (
        "reload",
        re.compile(r"(^|\n)\s*reload\b", re.IGNORECASE),
        "Configuration contains 'reload', which is destructive and unsupported in Milestone 3 proposals.",
    ),
    (
        "format_flash",
        re.compile(r"(^|\n)\s*format\s+flash\b", re.IGNORECASE),
        "Configuration contains 'format flash', which is destructive and unsupported in Milestone 3 proposals.",
    ),
    (
        "delete_flash",
        re.compile(r"(^|\n)\s*delete\s+flash", re.IGNORECASE),
        "Configuration contains flash deletion commands, which are unsupported in Milestone 3 proposals.",
    ),
    (
        "erase_startup",
        re.compile(r"(^|\n)\s*erase\s+startup-config\b", re.IGNORECASE),
        "Configuration contains 'erase startup-config', which is destructive and unsupported in Milestone 3 proposals.",
    ),
    (
        "clear_ip_route",
        re.compile(r"(^|\n)\s*clear\s+ip\s+route\b", re.IGNORECASE),
        "Configuration contains 'clear ip route', which is destructive and unsupported in Milestone 3 proposals.",
    ),
)

_DANGEROUS_PLAYBOOK_PATTERNS: Tuple[Tuple[str, re.Pattern[str], str], ...] = (
    (
        "destructive_cli_command",
        re.compile(r"\b(write\s+erase|reload|format\s+flash|delete\s+flash|erase\s+startup-config|clear\s+ip\s+route)\b", re.IGNORECASE),
        "Playbook contains destructive network CLI that requires a higher-trust path than Milestone 3 allows.",
    ),
    (
        "raw_module",
        re.compile(r"(^|\n)\s*(ansible\.)?builtin\.raw\s*:", re.IGNORECASE),
        "Playbook uses the raw module, which bypasses structured execution safeguards.",
    ),
    (
        "shell_module",
        re.compile(r"(^|\n)\s*(ansible\.)?builtin\.shell\s*:", re.IGNORECASE),
        "Playbook uses the shell module, which bypasses structured network safeguards.",
    ),
)

_NATIVE_PLAYBOOK_COLLECTIONS: Dict[str, Sequence[str]] = {
    "cisco_ios": ("cisco.ios.",),
    "juniper_junos": ("juniper.device.", "junipernetworks.junos."),
}

# Short names allowed when play declares collections: [cisco.ios] (task key is "ios_vlans" not "cisco.ios.ios_vlans")
_CISCO_IOS_COLLECTION_SHORT_NAMES: frozenset = frozenset({
    "ios_vlans", "ios_config", "ios_command", "ios_facts", "ios_interfaces",
    "ios_l2_interfaces", "ios_l3_interfaces", "ios_static_route", "ios_bgp",
    "ios_ospf", "ios_acl", "ios_banner", "ios_lag_interfaces", "ios_lldp_global",
    "ios_logging", "ios_ntp", "ios_snmp_server", "ios_system", "ios_user",
    "ios_vrf", "ios_ntp_access_group", "ios_ospfv2", "ios_ospfv3",
})

_JUNIPER_JUNOS_COLLECTION_SHORT_NAMES: frozenset = frozenset({
    "junos_interfaces",
    "junos_vlans",
    "junos_l2_interfaces",
    "junos_l3_interfaces",
    "junos_config",
    "junos_command",
    "junos_facts",
    "junos_acl_interfaces",
    "junos_acl_entries",
    "junos_static_routes",
    "junos_prefix_lists",
    "junos_bgp_global",
    "junos_bgp_address_family",
    "junos_bgp_neighbor",
    "junos_ospf_interfaces",
    "junos_ospfv2",
    "junos_ospfv3",
})

_CLI_PLAYBOOK_COLLECTIONS: Dict[str, Sequence[str]] = {
    "huawei_vrp": ("community.network.", "ansible.netcommon."),
}

# Reference playbooks can be multi-vendor (e.g. reference/vlan/multi_vendor); allow all known vendor modules
_MULTI_VENDOR_PREFIXES: Sequence[str] = (
    "cisco.ios.",
    "junipernetworks.junos.",
    "juniper.device.",
    "community.network.",
    "ansible.netcommon.",
)

_VENDOR_TASK_FILENAME_MAP: Dict[str, str] = {
    "cisco_ios": "cisco.yml",
    "cisco_nxos": "cisco.yml",
    "cisco_xr": "cisco.yml",
    "huawei_vrp": "huawei.yml",
    "juniper_junos": "juniper.yml",
}

_HARD_CODED_SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern[str], str], ...] = (
    (
        "hard_coded_ip",
        re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
        "Generated bundle contains a hard-coded IPv4 address; inventory should own device addressing.",
    ),
    (
        "hard_coded_password",
        re.compile(r"\b(password|passwd|secret)\s*:\s*['\"][^'\"]+['\"]", re.IGNORECASE),
        "Generated bundle appears to contain a hard-coded password/secret value.",
    ),
)


def _normalize_block(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").strip().splitlines()]
    return "\n".join(lines).strip()


def _scan_patterns(
    text: str,
    patterns: Sequence[Tuple[str, re.Pattern[str], str]],
) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    content = text or ""
    for code, pattern, message in patterns:
        if pattern.search(content):
            findings.append({"code": code, "message": message})
    return findings


def _template_path(renderer: TemplateRenderer, vendor: str, feature: str, *, undo: bool) -> Path:
    name = f"undo_{feature}.j2" if undo else f"{feature}.j2"
    return Path(renderer.templates_dir) / vendor / name


def _read_template_source(renderer: TemplateRenderer, vendor: str, feature: str, *, undo: bool) -> str:
    path = _template_path(renderer, vendor, feature, undo=undo)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def _compatible_execution_engines(vendor: str, feature: str, rendered_config: str) -> Sequence[str]:
    recommended = route_execution(
        intent={
            "vendor": vendor,
            "feature": feature,
            "rendered_commands": [line for line in rendered_config.splitlines() if line.strip()],
        },
        device_vars={"nettopo_vendor": vendor},
        rag_state_matches=False,
    )
    compatible = [recommended.action.value]
    if recommended.action != ExecutionAction.SSH_FALLBACK:
        compatible.append(ExecutionAction.SSH_FALLBACK.value)
    return compatible


def validate_cli_artifact(
    *,
    vendor: str,
    feature: str,
    params: Dict[str, Any],
    generated_config: str,
    rollback_config: str,
    execution_engine: str,
    rollback_plan: Optional[Dict[str, Any]] = None,
    require_render_match: bool = True,
    require_rollback: bool = True,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "version": SAFETY_VALIDATION_VERSION,
        "artifact_type": "cli_config",
        "vendor": vendor,
        "feature": feature,
        "passed": True,
        "blocked": False,
        "errors": [],
        "warnings": [],
        "checks": [],
    }

    def add_check(name: str, passed: bool, details: Optional[str] = None) -> None:
        report["checks"].append({"name": name, "passed": passed, "details": details or ""})
        if not passed:
            report["passed"] = False
            report["blocked"] = True

    def add_error(message: str) -> None:
        report["errors"].append(message)
        report["passed"] = False
        report["blocked"] = True

    renderer = TemplateRenderer()
    validator = SyntaxValidator()

    try:
        renderer._validate_vendor(vendor)
        add_check("vendor_supported", True, vendor)
    except Exception as exc:
        add_check("vendor_supported", False, str(exc))
        add_error(str(exc))
        return report

    if not (generated_config or "").strip():
        add_check("generated_config_present", False, "Generated configuration is empty.")
        add_error("Generated configuration is empty.")
        return report
    add_check("generated_config_present", True, f"{len(generated_config.splitlines())} rendered line(s)")

    try:
        template_source = _read_template_source(renderer, vendor, feature, undo=False)
        template_findings = _scan_patterns(template_source, _UNSAFE_TEMPLATE_PATTERNS)
        add_check("template_source_safe", len(template_findings) == 0, "; ".join(item["message"] for item in template_findings))
        for finding in template_findings:
            add_error(finding["message"])
    except Exception as exc:
        add_check("template_source_safe", False, str(exc))
        add_error(str(exc))

    try:
        expected_config = renderer.render(vendor, feature, copy.deepcopy(params))
        matches_render = _normalize_block(expected_config) == _normalize_block(generated_config)
        add_check(
            "deterministic_render_match",
            matches_render or not require_render_match,
            "Generated config matches renderer output." if matches_render else "Generated config diverges from deterministic template rendering.",
        )
        if require_render_match and not matches_render:
            add_error("Generated configuration diverges from the deterministic template render for the same vendor, feature, and parameters.")
    except Exception as exc:
        add_check("deterministic_render_match", False, str(exc))
        add_error(f"Template render validation failed: {exc}")

    syntax_issues = validator.validate(vendor, generated_config)
    add_check("syntax_validation", len(syntax_issues) == 0, "; ".join(syntax_issues))
    for issue in syntax_issues:
        add_error(issue)

    dangerous_config = _scan_patterns(generated_config, _DANGEROUS_CONFIG_PATTERNS)
    add_check("dangerous_command_check", len(dangerous_config) == 0, "; ".join(item["message"] for item in dangerous_config))
    for finding in dangerous_config:
        add_error(finding["message"])

    runtime_restore_available = bool((rollback_plan or {}).get("runtime_restore_available"))

    if require_rollback:
        if not (rollback_config or "").strip() and not runtime_restore_available:
            add_check("rollback_present", False, "Rollback config or runtime snapshot restore is required for approval readiness.")
            add_error("Rollback config is missing; rollback viability is incomplete.")
        else:
            details = []
            if (rollback_config or "").strip():
                details.append(f"{len(rollback_config.splitlines())} rollback line(s)")
            if runtime_restore_available:
                details.append("runtime pre-change snapshot restore available")
            add_check("rollback_present", True, ", ".join(details))
            if (rollback_config or "").strip():
                try:
                    undo_source = _read_template_source(renderer, vendor, feature, undo=True)
                    undo_findings = _scan_patterns(undo_source, _UNSAFE_TEMPLATE_PATTERNS)
                    add_check("undo_template_source_safe", len(undo_findings) == 0, "; ".join(item["message"] for item in undo_findings))
                    for finding in undo_findings:
                        add_error(finding["message"])
                except Exception as exc:
                    add_check("undo_template_source_safe", False, str(exc))
                    add_error(f"Rollback template validation failed: {exc}")

                try:
                    expected_rollback = renderer.render_undo(vendor, feature, copy.deepcopy(params))
                    rollback_matches = _normalize_block(expected_rollback) == _normalize_block(rollback_config)
                    add_check(
                        "rollback_render_match",
                        rollback_matches or not require_render_match,
                        "Rollback matches renderer output." if rollback_matches else "Rollback diverges from deterministic undo rendering.",
                    )
                    if require_render_match and not rollback_matches:
                        add_error("Rollback configuration diverges from the deterministic undo template for the same vendor, feature, and parameters.")
                except Exception as exc:
                    add_check("rollback_render_match", False, str(exc))
                    add_error(f"Rollback validation failed: {exc}")

    try:
        compatible_engines = list(_compatible_execution_engines(vendor, feature, generated_config))
        engine_ok = execution_engine in compatible_engines
        add_check(
            "execution_engine_compatible",
            engine_ok,
            f"Engine '{execution_engine}' compatible set: {', '.join(compatible_engines)}",
        )
        if not engine_ok:
            add_error(
                f"Execution engine '{execution_engine}' is incompatible with vendor '{vendor}' and feature '{feature}'."
            )
        report["compatible_execution_engines"] = compatible_engines
    except Exception as exc:
        add_check("execution_engine_compatible", False, str(exc))
        add_error(f"Execution engine validation failed: {exc}")

    return report


def validate_playbook_yaml(
    *,
    playbook_yaml: str,
    vendor: str,
    feature: str,
    target_hosts: Sequence[str],
    execution_engine: str,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "version": SAFETY_VALIDATION_VERSION,
        "artifact_type": "ansible_playbook",
        "vendor": vendor,
        "feature": feature,
        "passed": True,
        "blocked": False,
        "errors": [],
        "warnings": [],
        "checks": [],
    }

    def add_check(name: str, passed: bool, details: Optional[str] = None) -> None:
        report["checks"].append({"name": name, "passed": passed, "details": details or ""})
        if not passed:
            report["passed"] = False
            report["blocked"] = True

    def add_error(message: str) -> None:
        report["errors"].append(message)
        report["passed"] = False
        report["blocked"] = True

    try:
        plays = yaml.safe_load(playbook_yaml)
        plays_ok = isinstance(plays, list) and len(plays) > 0
        add_check("yaml_structure", plays_ok, "Parsed playbook as a non-empty list of plays." if plays_ok else "Playbook must be a non-empty YAML list.")
        if not plays_ok:
            add_error("Playbook must be a non-empty YAML list of plays.")
            return report
    except yaml.YAMLError as exc:
        add_check("yaml_structure", False, str(exc))
        add_error(f"YAML parse error: {exc}")
        return report

    dangerous = _scan_patterns(playbook_yaml, _DANGEROUS_PLAYBOOK_PATTERNS)
    add_check("dangerous_playbook_patterns", len(dangerous) == 0, "; ".join(item["message"] for item in dangerous))
    for finding in dangerous:
        add_error(finding["message"])

    if not target_hosts:
        add_check("target_hosts_present", False, "Target hosts are required.")
        add_error("Playbook target_hosts must not be empty.")
    else:
        add_check("target_hosts_present", True, ", ".join(target_hosts))

    if vendor == "multi_vendor":
        compatible_engines = [
            ExecutionAction.ANSIBLE_NATIVE.value,
            ExecutionAction.ANSIBLE_CLI.value,
            ExecutionAction.SSH_FALLBACK.value,
        ]
    else:
        compatible_engines = list(_compatible_execution_engines(vendor, feature, ""))
    engine_ok = execution_engine in compatible_engines
    add_check(
        "execution_engine_compatible",
        engine_ok,
        f"Engine '{execution_engine}' compatible set: {', '.join(compatible_engines)}",
    )
    if not engine_ok:
        add_error(f"Execution engine '{execution_engine}' is incompatible with vendor '{vendor}'.")

    def collect_task_keys(task_list: list) -> None:
        for task in task_list or []:
            if not isinstance(task, dict):
                continue
            if "block" in task:
                collect_task_keys(task.get("block") or [])
                collect_task_keys(task.get("rescue") or [])
                continue
            for key in task.keys():
                if key == "name":
                    continue
                module_keys.append(str(key))

    module_keys: List[str] = []
    for play in plays:
        if not isinstance(play, dict):
            continue
        collect_task_keys(play.get("tasks") or [])
        collect_task_keys(play.get("pre_tasks") or [])
        collect_task_keys(play.get("post_tasks") or [])

    expected_prefixes: Sequence[str] = ()
    if vendor == "multi_vendor":
        expected_prefixes = _MULTI_VENDOR_PREFIXES
    elif execution_engine == ExecutionAction.ANSIBLE_NATIVE.value:
        expected_prefixes = _NATIVE_PLAYBOOK_COLLECTIONS.get(vendor, ())
    elif execution_engine == ExecutionAction.ANSIBLE_CLI.value:
        expected_prefixes = _CLI_PLAYBOOK_COLLECTIONS.get(vendor, ("ansible.netcommon.",))

    def module_matches(m: str) -> bool:
        if any(m.startswith(prefix) for prefix in expected_prefixes):
            return True
        if (vendor == "cisco_ios" or vendor == "multi_vendor") and execution_engine == ExecutionAction.ANSIBLE_NATIVE.value:
            if m in _CISCO_IOS_COLLECTION_SHORT_NAMES:
                return True
        if (vendor == "juniper_junos" or vendor == "multi_vendor") and execution_engine == ExecutionAction.ANSIBLE_NATIVE.value:
            if m in _JUNIPER_JUNOS_COLLECTION_SHORT_NAMES:
                return True
        return False

    if expected_prefixes:
        module_ok = any(module_matches(m) for m in module_keys) if module_keys else False
        add_check(
            "module_family_match",
            module_ok,
            f"Observed modules: {', '.join(module_keys) or '(none)'}",
        )
        if not module_ok:
            add_error(
                f"Playbook modules do not match the expected module family for vendor '{vendor}' and execution engine '{execution_engine}'."
            )

    return report


def validate_playbook_bundle(
    *,
    bundle: Dict[str, Any],
    vendor: str,
    feature: str,
    target_hosts: Sequence[str],
    execution_engine: str,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "version": SAFETY_VALIDATION_VERSION,
        "artifact_type": "ansible_bundle",
        "vendor": vendor,
        "feature": feature,
        "passed": True,
        "blocked": False,
        "errors": [],
        "warnings": [],
        "checks": [],
    }

    def add_check(name: str, passed: bool, details: Optional[str] = None) -> None:
        report["checks"].append({"name": name, "passed": passed, "details": details or ""})
        if not passed:
            report["passed"] = False
            report["blocked"] = True

    def add_error(message: str) -> None:
        report["errors"].append(message)
        report["passed"] = False
        report["blocked"] = True

    entrypoint_path = str(bundle.get("entrypoint_path") or "").replace("\\", "/").strip("/")
    files = bundle.get("files") or []
    add_check("entrypoint_present", bool(entrypoint_path), entrypoint_path or "missing")
    add_check("files_present", isinstance(files, list) and len(files) > 0, f"{len(files) if isinstance(files, list) else 0} file(s)")
    if not entrypoint_path or not isinstance(files, list) or not files:
        if not entrypoint_path:
            add_error("Bundle entrypoint_path is missing.")
        if not isinstance(files, list) or not files:
            add_error("Bundle must contain a non-empty files list.")
        return report

    file_map: Dict[str, Dict[str, Any]] = {}
    allow_ipv4_literals = str(feature or "").strip().lower() in {"acl", "static_route", "ospf", "routing"}
    for item in files:
        if not isinstance(item, dict):
            add_error("Bundle contains a non-object file entry.")
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip("/")
        if not path:
            add_error("Bundle file entry is missing path.")
            continue
        if path in file_map:
            add_error(f"Bundle contains duplicate path '{path}'.")
            continue
        file_map[path] = item

        if ".." in Path(path).parts:
            add_error(f"Bundle file path '{path}' contains parent traversal.")
        if not (path.startswith("playbooks/generated/") or path.startswith("roles/generated/")):
            add_error(f"Bundle file path '{path}' is outside the approved generated subtrees.")

        content = str(item.get("content") or "")
        for finding in _scan_patterns(content, _HARD_CODED_SECRET_PATTERNS):
            # ACL/routing artifacts legitimately contain IPv4 prefixes and next-hops.
            # The "hard-coded IP" rule is meant to prevent embedding device addressing (mgmt IPs)
            # into generated bundles, not to block policy objects like ACL source prefixes.
            if finding.get("code") == "hard_coded_ip" and allow_ipv4_literals:
                continue
            add_error(f"{finding['message']} File: {path}")

    add_check("entrypoint_in_files", entrypoint_path in file_map, entrypoint_path)
    if entrypoint_path not in file_map:
        add_error(f"Bundle entrypoint '{entrypoint_path}' is not present in files.")
        return report

    entrypoint_yaml = str(file_map[entrypoint_path].get("content") or "")
    if _J2_PLACEHOLDER in entrypoint_yaml:
        add_error(
            "Playbook must use real Ansible/Jinja2 variables (e.g. {{ interfaces_to_shutdown | default([]) }}), "
            "not the literal __J2__. Fix the generator to output proper variable syntax."
        )
    # Normalize literal __J2__ so structure can still be parsed; then mask real Jinja2 for PyYAML
    yaml_for_parse = _mask_jinja2_for_yaml_parse(_normalize_literal_j2_for_parse(entrypoint_yaml))
    try:
        plays = yaml.safe_load(yaml_for_parse)
        plays_ok = isinstance(plays, list) and len(plays) > 0
        add_check(
            "entrypoint_yaml_structure",
            plays_ok,
            "Parsed entrypoint as a non-empty YAML list of plays." if plays_ok else "Entrypoint must be a non-empty YAML list.",
        )
        if not plays_ok:
            add_error("Entrypoint playbook must be a non-empty YAML list of plays.")
            return report
    except yaml.YAMLError as exc:
        add_check("entrypoint_yaml_structure", False, str(exc))
        add_error(f"Entrypoint YAML parse error: {exc}")
        return report

    dangerous = _scan_patterns(entrypoint_yaml, _DANGEROUS_PLAYBOOK_PATTERNS)
    add_check("entrypoint_dangerous_patterns", len(dangerous) == 0, "; ".join(item["message"] for item in dangerous))
    for finding in dangerous:
        add_error(finding["message"])

    if not target_hosts:
        add_check("target_hosts_present", False, "Target hosts are required.")
        add_error("Bundle target_hosts must not be empty.")
    else:
        add_check("target_hosts_present", True, ", ".join(target_hosts))

    try:
        compatible_engines = list(_compatible_execution_engines(vendor, feature, ""))
    except Exception as exc:
        compatible_engines = []
        add_error(f"Execution engine validation failed: {exc}")
    engine_ok = execution_engine in compatible_engines if compatible_engines else False
    add_check(
        "execution_engine_compatible",
        engine_ok,
        f"Engine '{execution_engine}' compatible set: {', '.join(compatible_engines)}",
    )
    if compatible_engines and not engine_ok:
        add_error(f"Execution engine '{execution_engine}' is incompatible with vendor '{vendor}'.")

    task_files = [path for path in file_map if "/tasks/" in path]
    task_filenames = {Path(path).name for path in task_files}
    required_task_filenames: set[str] = {"main.yml"}
    if str(vendor or "").strip().lower() == "multi_vendor":
        required_task_filenames.update({"cisco.yml", "huawei.yml", "juniper.yml"})
    else:
        required_task_filenames.add(
            _VENDOR_TASK_FILENAME_MAP.get(str(vendor or "").strip().lower(), "juniper.yml")
        )
    missing_task_files = sorted(required_task_filenames - task_filenames)
    add_check(
        "role_task_files_present",
        len(missing_task_files) == 0,
        ", ".join(sorted(task_files)),
    )
    if missing_task_files:
        add_error(
            "Bundle is missing required role task file(s): "
            + ", ".join(missing_task_files)
            + "."
        )

    module_keys: List[str] = []

    def collect_task_keys(task_list: list) -> None:
        for task in task_list or []:
            if not isinstance(task, dict):
                continue
            if "block" in task:
                collect_task_keys(task.get("block") or [])
                collect_task_keys(task.get("rescue") or [])
                continue
            action_val = task.get("action")
            if isinstance(action_val, str):
                action_mod = action_val.strip().split()[0]
                if action_mod:
                    module_keys.append(str(action_mod))
            elif isinstance(action_val, dict):
                action_mod = str(action_val.get("module") or action_val.get("__ansible_module__") or "").strip()
                if action_mod:
                    module_keys.append(action_mod)
            for key in task.keys():
                if key in {"name", "when", "vars", "register", "delegate_to", "tags"}:
                    continue
                module_keys.append(str(key))

    # Collect modules from role task files.
    for path in task_files:
        try:
            content = str(file_map[path].get("content") or "")
            content_for_parse = _mask_jinja2_for_yaml_parse(_normalize_literal_j2_for_parse(content))
            parsed = yaml.safe_load(content_for_parse)
        except yaml.YAMLError as exc:
            add_error(f"Role task file '{path}' has invalid YAML: {exc}")
            continue
        if isinstance(parsed, list):
            collect_task_keys(parsed)

    # Also collect modules declared directly in entrypoint tasks/pre/post tasks.
    try:
        if isinstance(plays, list):
            for play in plays:
                if not isinstance(play, dict):
                    continue
                collect_task_keys(play.get("tasks") or [])
                collect_task_keys(play.get("pre_tasks") or [])
                collect_task_keys(play.get("post_tasks") or [])
    except Exception:
        pass

    expected_prefixes = ()
    if execution_engine == ExecutionAction.ANSIBLE_NATIVE.value:
        expected_prefixes = _NATIVE_PLAYBOOK_COLLECTIONS.get(vendor, ())
    elif execution_engine == ExecutionAction.ANSIBLE_CLI.value:
        expected_prefixes = _CLI_PLAYBOOK_COLLECTIONS.get(vendor, ("ansible.netcommon.",))

    def module_matches(m: str) -> bool:
        if any(m.startswith(prefix) for prefix in expected_prefixes):
            return True
        if vendor == "cisco_ios" and execution_engine == ExecutionAction.ANSIBLE_NATIVE.value:
            return m in _CISCO_IOS_COLLECTION_SHORT_NAMES
        if vendor == "juniper_junos" and execution_engine == ExecutionAction.ANSIBLE_NATIVE.value:
            return m in _JUNIPER_JUNOS_COLLECTION_SHORT_NAMES
        return False

    if expected_prefixes:
        module_ok = any(module_matches(m) for m in module_keys) if module_keys else False
        add_check(
            "module_family_match",
            module_ok,
            f"Observed bundle modules: {', '.join(module_keys) or '(none)'}",
        )
        if not module_ok:
            add_error(
                f"Bundle modules do not match the expected module family for vendor '{vendor}' and execution engine '{execution_engine}'."
            )

    return report


def validate_action_artifact(
    action_artifact: Optional[Dict[str, Any]],
    *,
    vendor: str,
    feature: str,
    params: Dict[str, Any],
    proposal_generated_config: str = "",
    require_render_match: bool = True,
) -> Dict[str, Any]:
    artifact = action_artifact or {}
    device_artifacts = list(artifact.get("device_artifacts") or [])
    report: Dict[str, Any] = {
        "version": SAFETY_VALIDATION_VERSION,
        "artifact_type": artifact.get("action_type") or "proposal_artifact",
        "passed": True,
        "blocked": False,
        "errors": [],
        "warnings": [],
        "device_reports": [],
    }

    for item in device_artifacts:
        device_vendor = str(item.get("vendor") or vendor)
        device_feature = str(item.get("feature") or feature)
        device_report = validate_cli_artifact(
            vendor=device_vendor,
            feature=device_feature,
            params=params,
            generated_config=str(item.get("generated_config") or ""),
            rollback_config=str(item.get("rollback_config") or ""),
            execution_engine=str(item.get("execution_engine") or ExecutionAction.SSH_FALLBACK.value),
            rollback_plan=item.get("rollback_plan") if isinstance(item.get("rollback_plan"), dict) else None,
            require_render_match=require_render_match,
        )
        item["safety_validation"] = device_report
        item["approval_ready"] = bool(device_report.get("passed"))
        report["device_reports"].append(
            {
                "hostname": item.get("hostname"),
                "passed": device_report.get("passed", False),
                "errors": list(device_report.get("errors") or []),
                "warnings": list(device_report.get("warnings") or []),
            }
        )
        if not device_report.get("passed", False):
            report["passed"] = False
            report["blocked"] = True
            report["errors"].extend(device_report.get("errors") or [])
        report["warnings"].extend(device_report.get("warnings") or [])

    if proposal_generated_config and len(device_artifacts) == 1:
        stored = _normalize_block(str(device_artifacts[0].get("generated_config") or ""))
        current = _normalize_block(proposal_generated_config)
        if stored != current:
            report["passed"] = False
            report["blocked"] = True
            report["errors"].append(
                "Proposal generated_config no longer matches the validated device artifact; regenerate the proposal before approval."
            )

    playbook_spec = artifact.get("playbook_spec") if isinstance(artifact.get("playbook_spec"), dict) else None
    if playbook_spec and playbook_spec.get("generated_yaml") and playbook_spec.get("playbook_yaml"):
        playbook_report = validate_playbook_yaml(
            playbook_yaml=str(playbook_spec.get("playbook_yaml") or ""),
            vendor=vendor,
            feature=feature,
            target_hosts=list(playbook_spec.get("target_hosts") or []),
            execution_engine=str((artifact.get("execution") or {}).get("primary_engine") or ExecutionAction.ANSIBLE_CLI.value),
        )
        report["playbook_report"] = playbook_report
        if not playbook_report.get("passed", False):
            report["passed"] = False
            report["blocked"] = True
            report["errors"].extend(playbook_report.get("errors") or [])

    return report
