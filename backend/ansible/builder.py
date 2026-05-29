"""
Playbook builder — LLM → validated Ansible YAML.

Flow:
  1. Render prompt template with live RAG context + KB snippets + intent.
  2. Call Gemini (or any LLMProvider) to generate the playbook.
  3. Extract the YAML code block from the response.
  4. Run ansible-lint (if installed) to catch structural problems.
  5. Return the validated YAML string.

Only the builder emits new playbooks.  The runner.py executes them.
"""

import asyncio
import json
import logging
import re
import subprocess
import tempfile
import yaml
from pathlib import Path
from typing import Any, Optional, Sequence

from ansible.bundle_artifacts import AnsibleArtifactBundle, AnsibleArtifactFile, parse_bundle_response
from config_engine.safety import validate_playbook_yaml
from config_engine.safety import validate_playbook_bundle

logger = logging.getLogger("ansible.builder")

# Regex for LLM-generated "that: \"...\" in var.stdout[...]" which breaks YAML (value must be one quoted string)
_RE_THAT_UNQUOTED = re.compile(
    r"^(\s*that:\s*)\"([^\"]+)\"\s+in\s+(.+)$",
    re.MULTILINE,
)


def repair_playbook_yaml(yaml_text: str) -> str:
    """
    Fix common YAML mistakes from LLM output so parsing succeeds.
    E.g. that: "vlan 200" in running_config_post.stdout[0] -> that: '"vlan 200" in running_config_post.stdout[0]'
    """
    if not yaml_text or not yaml_text.strip():
        return yaml_text

    def _repl(m: re.Match) -> str:
        prefix, quoted, rest = m.group(1), m.group(2), m.group(3).strip()
        return f'{prefix}\'"{quoted}" in {rest}\''

    repaired = _RE_THAT_UNQUOTED.sub(_repl, yaml_text)
    if repaired != yaml_text:
        logger.debug("Applied YAML repair for unquoted 'that: \"...\" in ...' pattern")
    return repaired


def _mask_jinja2_for_yaml_parse(text: str) -> str:
    return re.sub(r"\{\{.*?\}\}", _J2_PLACEHOLDER, text, flags=re.DOTALL)


def _normalize_literal_j2_for_parse(text: str) -> str:
    return text.replace(_J2_PLACEHOLDER, _LITERAL_J2_REPLACEMENT_FOR_PARSE)


PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompt_templates" / "llm_to_ansible.txt"
).read_text()
BUNDLE_PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompt_templates" / "llm_to_ansible_bundle.txt"
).read_text()

# Regex to extract a YAML fenced code block from LLM response
_YAML_FENCE = re.compile(r"```ya?ml\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# Hard cap on context passed to LLM (avoid token overflow)
_RAG_CONTEXT_MAX_CHARS  = 6000
_KB_SNIPPETS_MAX        = 3
_KB_SNIPPET_MAX_CHARS   = 1200
SUPPORTED_ROLE_BUNDLE_FEATURES = frozenset({"vlan", "interface", "acl", "routing"})
_J2_PLACEHOLDER = "__J2__"
_LITERAL_J2_REPLACEMENT_FOR_PARSE = "JINJA_VAR"
_ENTRYPOINT_RESERVED_KEYS = {"name", "hosts", "gather_facts", "vars", "serial", "collections", "connection"}
_VENDOR_TASK_FILE_NAMES = {
    "cisco_ios": "cisco.yml",
    "cisco_nxos": "cisco.yml",
    "cisco_xr": "cisco.yml",
    "huawei_vrp": "huawei.yml",
    "juniper_junos": "juniper.yml",
}


class PlaybookBuildError(RuntimeError):
    """Raised when the LLM response cannot be turned into a valid playbook."""


class PlaybookBuilder:
    """
    Uses an LLMProvider to generate, extract, and validate Ansible playbooks.

    Args:
        provider:  Any LLMProvider instance (GeminiProvider, etc.)
    """

    def __init__(self, provider) -> None:
        self._provider = provider

    @staticmethod
    def supports_role_bundle(feature: str) -> bool:
        return str(feature or "").strip().lower() in SUPPORTED_ROLE_BUNDLE_FEATURES

    async def build(
        self,
        intent:      dict,
        rag_context: str,
        kb_snippets: list[str],
        run_id:      Optional[str] = None,
    ) -> str:
        """
        Generate a validated Ansible playbook for the given intent.

        Args:
            intent:       {description, target_hosts, vendor, feature, params}
            rag_context:  Live state text from Context-Injection RAG.
            kb_snippets:  Relevant templates/SOPs from Vector-RAG (M21).
            run_id:       Job/run identifier for audit trail.

        Returns:
            Validated YAML string ready to be handed to runner.run_playbook().

        Raises:
            PlaybookBuildError on lint failure or missing YAML block.
        """
        playbook_yaml = await self.build_draft(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            run_id=run_id,
        )

        validation_report = self.validate_playbook(intent=intent, playbook_yaml=playbook_yaml)
        if not validation_report.get("passed", False):
            raise PlaybookBuildError(
                "Playbook failed validation: "
                + "; ".join(validation_report.get("errors") or ["unknown validation error"])
            )

        logger.info("Playbook built and validated OK")
        return playbook_yaml

    async def build_draft(
        self,
        intent:      dict,
        rag_context: str,
        kb_snippets: list[str],
        run_id:      Optional[str] = None,
        temperature: float = 0.1,
        extra_guidance: str = "",
    ) -> str:
        """Generate a YAML playbook draft without rejecting it on validation errors."""
        # ── Build prompt ───────────────────────────────────────────────────
        prompt_text = self._render_prompt(
            template=PROMPT_TEMPLATE,
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            run_id=run_id,
        )
        if extra_guidance:
            prompt_text += f"\n\n{extra_guidance.strip()}"

        # ── Call LLM ───────────────────────────────────────────────────────
        from llm.provider import LLMMessage
        messages = [LLMMessage(role="user", content=prompt_text)]

        logger.info(
            "Requesting playbook from LLM: intent=%s vendor=%s feature=%s",
            intent.get("description", "")[:80],
            intent.get("vendor", ""),
            intent.get("feature", ""),
        )

        response = await self._provider.generate(messages=messages, temperature=temperature)
        raw_text = response.content

        # ── Extract YAML ────────────────────────────────────────────────────
        return self._extract_yaml(raw_text)

    async def build_bundle_draft(
        self,
        intent: dict,
        rag_context: str,
        kb_snippets: list[str],
        run_id: Optional[str] = None,
        temperature: float = 0.1,
        extra_guidance: str = "",
    ) -> AnsibleArtifactBundle:
        prompt_text = self._render_prompt(
            template=BUNDLE_PROMPT_TEMPLATE,
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets,
            run_id=run_id,
        )
        if extra_guidance:
            prompt_text += f"\n\n{extra_guidance.strip()}"

        from llm.provider import LLMMessage
        response = await self._provider.generate(
            messages=[LLMMessage(role="user", content=prompt_text)],
            temperature=temperature,
        )
        if response is None:
            raise PlaybookBuildError("LLM provider returned no response object for bundle generation")
        raw = response.content or ""
        if not raw.strip():
            raise PlaybookBuildError(
                f"LLM returned empty content for bundle generation "
                f"(finish_reason={getattr(response, 'finish_reason', 'unknown')})"
            )

        try:
            return parse_bundle_response(raw)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(
                "Bundle JSON parse failed (%s), falling back to single-playbook extraction. "
                "finish_reason=%s preview=%r",
                e,
                getattr(response, "finish_reason", "unknown"),
                raw[:500],
            )
            try:
                playbook_yaml = self._extract_yaml(raw)
            except PlaybookBuildError:
                raise PlaybookBuildError(
                    "LLM did not return valid bundle JSON or a single YAML playbook. "
                    "Expected a ```json ... ``` block with entrypoint_path and files, or a ```yaml ... ``` playbook."
                ) from e
            return self._synthesize_role_bundle_from_playbook(
                intent=intent,
                playbook_yaml=playbook_yaml,
                run_id=run_id,
            )

    def validate_playbook(self, intent: dict, playbook_yaml: str) -> dict[str, Any]:
        """Return a structured validation report for a playbook draft."""
        issues: list[dict[str, Any]] = []

        try:
            plays = self._validate_yaml_structure(playbook_yaml)
        except PlaybookBuildError as exc:
            issues.append(self._issue("yaml", str(exc), rule="yaml_parse"))
            return {
                "passed": False,
                "issues": issues,
                "errors": [self._format_issue(issue) for issue in issues],
            }

        issues.extend(self._lint_issues(playbook_yaml))
        issues.extend(self._validate_hosts_alignment(plays, intent.get("target_hosts") or []))

        playbook_validation = validate_playbook_yaml(
            playbook_yaml=playbook_yaml,
            vendor=str(intent.get("vendor", "")),
            feature=str(intent.get("feature", "")),
            target_hosts=list(intent.get("target_hosts", [])),
            execution_engine=self._expected_execution_engine(str(intent.get("vendor", ""))),
        )
        for error in playbook_validation.get("errors") or []:
            issues.append(self._issue("safety", str(error), rule="step10_safety"))

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "errors": [self._format_issue(issue) for issue in issues],
        }

    def validate_bundle(self, intent: dict, bundle: AnsibleArtifactBundle) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        bundle_validation = validate_playbook_bundle(
            bundle=bundle.to_dict(),
            vendor=str(intent.get("vendor", "")),
            feature=str(intent.get("feature", "")),
            target_hosts=list(intent.get("target_hosts", [])),
            execution_engine=self._expected_execution_engine(str(intent.get("vendor", ""))),
        )
        for error in bundle_validation.get("errors") or []:
            issues.append(self._issue("bundle", str(error), rule="bundle_validation"))
        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "errors": [self._format_issue(issue) for issue in issues],
        }

    async def build_candidates(
        self,
        intent:      dict,
        rag_context: str,
        kb_snippets: list[str],
        n:           int = 3,
        run_id:      Optional[str] = None,
    ) -> list[dict]:
        """
        Generate *n* candidate playbooks with different approaches/temperatures.

        Returns a list of dicts:
            [
                {"index": 0, "yaml": "...", "approach": "conservative", "valid": True, "errors": []},
                {"index": 1, "yaml": "...", "approach": "balanced",     "valid": True, "errors": []},
                {"index": 2, "yaml": "...", "approach": "aggressive",   "valid": False, "errors": ["lint error ..."]},
            ]
        """
        approaches = [
            {"label": "conservative", "temp": 0.05, "extra": "Use the simplest, safest commands. Prefer single-step changes."},
            {"label": "balanced",     "temp": 0.2,  "extra": "Balance robustness with brevity. Use best practices."},
            {"label": "aggressive",   "temp": 0.4,  "extra": "Optimise for completeness. Include pre/post validation tasks."},
        ][:n]

        candidates = []
        for i, approach in enumerate(approaches):
            try:
                prompt_text = self._render_prompt(
                    template=PROMPT_TEMPLATE,
                    intent=intent,
                    rag_context=rag_context,
                    kb_snippets=kb_snippets,
                    run_id=run_id,
                )
                # Append approach guidance
                prompt_text += f"\n\n**Approach: {approach['label']}** — {approach['extra']}"

                from llm.provider import LLMMessage
                messages = [LLMMessage(role="user", content=prompt_text)]

                response = await self._provider.generate(
                    messages=messages, temperature=approach["temp"]
                )
                raw_text = response.content
                playbook_yaml = self._extract_yaml(raw_text)

                validation_report = self.validate_playbook(intent=intent, playbook_yaml=playbook_yaml)
                errors = list(validation_report.get("errors") or [])

                candidates.append({
                    "index": i,
                    "yaml": playbook_yaml,
                    "approach": approach["label"],
                    "valid": validation_report.get("passed", False),
                    "errors": errors,
                })
            except Exception as e:
                candidates.append({
                    "index": i,
                    "yaml": "",
                    "approach": approach["label"],
                    "valid": False,
                    "errors": [str(e)],
                })

        return candidates

    # ── helpers ────────────────────────────────────────────────────────────

    def _extract_yaml(self, raw: str) -> str:
        m = _YAML_FENCE.search(raw)
        if not m:
            raise PlaybookBuildError(
                "LLM response did not contain a ```yaml ... ``` code block.\n"
                f"Response preview: {raw[:500]}"
            )
        extracted = m.group(1).strip()
        if not extracted:
            raise PlaybookBuildError("LLM returned an empty YAML code block")
        return extracted

    def build_bundle_from_playbook(
        self,
        *,
        intent: dict,
        playbook_yaml: str,
        run_id: Optional[str],
    ) -> AnsibleArtifactBundle:
        """Convert a filled single-play reference into the generated role-bundle shape."""
        return self._synthesize_role_bundle_from_playbook(
            intent=intent,
            playbook_yaml=playbook_yaml,
            run_id=run_id,
        )

    def _synthesize_role_bundle_from_playbook(
        self,
        *,
        intent: dict,
        playbook_yaml: str,
        run_id: Optional[str],
    ) -> AnsibleArtifactBundle:
        run_suffix = str(run_id or "unknown")
        feature = str(intent.get("feature") or "generated").strip().lower() or "generated"
        role_root = f"roles/generated/job_{run_suffix}/{feature}"
        entrypoint_path = f"playbooks/generated/job_{run_suffix}/playbook.yml"
        vendor = str(intent.get("vendor") or "").strip().lower()
        vendor_task_file = _VENDOR_TASK_FILE_NAMES.get(vendor, "juniper.yml")

        parsed_playbook = self._parse_playbook_loose(playbook_yaml)
        first_play = parsed_playbook[0] if parsed_playbook and isinstance(parsed_playbook[0], dict) else {}
        vendor_tasks = list(first_play.get("tasks") or [])
        if not vendor_tasks:
            raise PlaybookBuildError(
                "Fallback YAML playbook did not contain tasks that can be converted into a generated role bundle."
            )

        entrypoint_yaml = self._render_entrypoint_playbook(
            first_play=first_play,
            entrypoint_role=f"generated/job_{run_suffix}/{feature}",
            target_hosts=list(intent.get("target_hosts") or []),
            feature=feature,
        )
        main_tasks_yaml = self._render_dispatch_tasks(vendor_task_file)

        placeholder_tasks = {
            "cisco.yml": self._render_placeholder_vendor_task("Cisco IOS"),
            "huawei.yml": self._render_placeholder_vendor_task("Huawei VRP"),
            "juniper.yml": self._render_placeholder_vendor_task("Juniper Junos"),
        }
        placeholder_tasks[vendor_task_file] = yaml.safe_dump(
            vendor_tasks,
            sort_keys=False,
            allow_unicode=False,
        ).strip() + "\n"

        files = [
            AnsibleArtifactFile(path=entrypoint_path, content=entrypoint_yaml, kind="playbook"),
            AnsibleArtifactFile(path=f"{role_root}/tasks/main.yml", content=main_tasks_yaml, kind="task"),
        ]
        for name in ("cisco.yml", "huawei.yml", "juniper.yml"):
            files.append(
                AnsibleArtifactFile(
                    path=f"{role_root}/tasks/{name}",
                    content=placeholder_tasks[name],
                    kind="task",
                )
            )

        logger.info(
            "Synthesized fallback role bundle from single playbook: vendor=%s feature=%s run_id=%s",
            vendor or "unknown",
            feature,
            run_suffix,
        )
        return AnsibleArtifactBundle(
            entrypoint_path=entrypoint_path,
            files=files,
            manifest={
                "generator": "fallback_role_bundle",
                "run_id": run_suffix,
                "vendor_hint": vendor,
                "feature": feature,
            },
        )

    def _parse_playbook_loose(self, playbook_yaml: str) -> list[dict[str, Any]]:
        try:
            parsed = yaml.safe_load(playbook_yaml)
        except yaml.YAMLError:
            yaml_for_parse = _mask_jinja2_for_yaml_parse(_normalize_literal_j2_for_parse(playbook_yaml))
            try:
                parsed = yaml.safe_load(yaml_for_parse)
            except yaml.YAMLError as exc:
                raise PlaybookBuildError(f"Fallback bundle conversion could not parse YAML: {exc}") from exc
        if not isinstance(parsed, list) or not parsed:
            raise PlaybookBuildError("Fallback bundle conversion requires a non-empty YAML play list.")
        normalized: list[dict[str, Any]] = []
        for play in parsed:
            if isinstance(play, dict):
                normalized.append(play)
        if not normalized:
            raise PlaybookBuildError("Fallback bundle conversion requires at least one mapping play.")
        return normalized

    def _render_entrypoint_playbook(
        self,
        *,
        first_play: dict[str, Any],
        entrypoint_role: str,
        target_hosts: Sequence[str],
        feature: str,
    ) -> str:
        hosts_value = str(first_play.get("hosts") or "").strip()
        if not hosts_value:
            hosts_value = ", ".join(str(item).strip() for item in target_hosts if str(item).strip()) or "all"
        play: dict[str, Any] = {
            "name": str(first_play.get("name") or f"Generated {feature} orchestration"),
            "hosts": hosts_value,
            "gather_facts": bool(first_play.get("gather_facts", False)),
        }
        for key in ("serial", "connection", "collections", "vars"):
            if key in first_play:
                play[key] = first_play.get(key)
        extra_keys = {
            key: value
            for key, value in first_play.items()
            if key not in _ENTRYPOINT_RESERVED_KEYS and key not in {"tasks", "pre_tasks", "post_tasks"}
        }
        play.update(extra_keys)
        play["roles"] = [entrypoint_role]
        return yaml.safe_dump([play], sort_keys=False, allow_unicode=False).strip() + "\n"

    @staticmethod
    def _render_dispatch_tasks(selected_vendor_task_file: str) -> str:
        dispatch = [
            {
                "name": "Dispatch Cisco task set",
                "ansible.builtin.include_tasks": "cisco.yml",
                "when": [
                    "inventory_hostname in groups.get('vendor_cisco_ios', [])",
                ],
            },
            {
                "name": "Dispatch Huawei task set",
                "ansible.builtin.include_tasks": "huawei.yml",
                "when": [
                    "inventory_hostname in groups.get('vendor_huawei_vrp', [])",
                ],
            },
            {
                "name": "Dispatch Juniper task set",
                "ansible.builtin.include_tasks": "juniper.yml",
                "when": [
                    "inventory_hostname in groups.get('vendor_juniper_junos', [])",
                ],
            },
        ]
        if selected_vendor_task_file == "cisco.yml":
            dispatch[0]["when"].append("(ansible_network_os | default('')) in ['ios', 'cisco.ios.ios']")
        elif selected_vendor_task_file == "huawei.yml":
            dispatch[1]["when"].append("(ansible_network_os | default('')) in ['ce', 'vrp', 'huawei.vrp.vrp']")
        elif selected_vendor_task_file == "juniper.yml":
            dispatch[2]["when"].append("(ansible_network_os | default('')) in ['junos', 'juniper.device.junos']")
        return yaml.safe_dump(dispatch, sort_keys=False, allow_unicode=False).strip() + "\n"

    @staticmethod
    def _render_placeholder_vendor_task(label: str) -> str:
        return (
            "---\n"
            f"- name: {label} not targeted by this request\n"
            "  ansible.builtin.debug:\n"
            "    msg: This generated bundle contains a placeholder task file for a non-target vendor.\n"
        )

    def _render_prompt(
        self,
        *,
        template: str,
        intent: dict,
        rag_context: str,
        kb_snippets: list[str],
        run_id: Optional[str],
    ) -> str:
        params_yaml = yaml.dump(
            intent.get("params", {}),
            default_flow_style=False,
            indent=2,
        ).strip()

        snippets_text = "\n---\n".join(
            s[:_KB_SNIPPET_MAX_CHARS]
            for s in kb_snippets[:_KB_SNIPPETS_MAX]
        ) or "(none retrieved)"

        return template.format(
            rag_context=rag_context[:_RAG_CONTEXT_MAX_CHARS],
            kb_snippets=snippets_text,
            run_id=run_id or "unknown",
            intent_description=intent.get("description", ""),
            target_hosts=", ".join(intent.get("target_hosts", [])),
            vendor=intent.get("vendor", ""),
            feature=intent.get("feature", ""),
            params_yaml=params_yaml,
            device_inventory_context=intent.get("device_inventory_context", "(no scoped device inventory provided)"),
        )

    def _validate_yaml_structure(self, yaml_text: str):
        """Parse YAML and check it is a non-empty list of plays."""
        try:
            plays = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise PlaybookBuildError(f"YAML parse error: {exc}") from exc

        if not isinstance(plays, list) or len(plays) == 0:
            raise PlaybookBuildError(
                "Playbook must be a YAML list of plays (starts with '- name: ...')"
            )

        # Lightweight safety guards
        forbidden = [
            "write erase", "reload", "no router ospf",
            "clear ip route", "format flash",
        ]
        lowered = yaml_text.lower()
        for banned in forbidden:
            if banned in lowered:
                # Only hard-fail if no operator_approved_destructive guard
                if "operator_approved_destructive" not in yaml_text:
                    raise PlaybookBuildError(
                        f"Playbook contains potentially destructive command '{banned}' "
                        "without an operator_approved_destructive guard. Rejected."
                    )

        logger.debug("YAML structure validation passed")
        return plays

    def _lint_issues(self, yaml_text: str) -> list[dict[str, Any]]:
        """Run ansible-lint and return structured issues."""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".yml", mode="w", delete=False, encoding="utf-8"
            ) as f:
                f.write(yaml_text)
                fpath = f.name

            result = subprocess.run(
                ["ansible-lint", "--nocolor", "-p", "--offline", fpath],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode not in (0, 2):  # 2 = warnings only
                issues: list[dict[str, Any]] = []
                combined = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
                for line in combined.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    match = re.match(r".*?:(\d+):\d+:\s*([^:]+):\s*(.*)", stripped)
                    if match:
                        issues.append(
                            self._issue(
                                "lint",
                                match.group(3).strip(),
                                rule=match.group(2).strip(),
                                line=int(match.group(1)),
                            )
                        )
                    else:
                        issues.append(self._issue("lint", stripped, rule="ansible_lint"))
                return issues or [self._issue("lint", "ansible-lint found errors", rule="ansible_lint")]
            logger.info("ansible-lint passed (rc=%d)", result.returncode)
            return []
        except FileNotFoundError:
            logger.warning("ansible-lint not found — skipping lint step")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("ansible-lint timed out — skipping lint step")
            return []

    def _validate_hosts_alignment(self, plays, target_hosts: list[str]) -> list[dict[str, Any]]:
        if not plays or not target_hosts:
            return []
        first_play = plays[0] if isinstance(plays[0], dict) else {}
        hosts_value = first_play.get("hosts")
        if not isinstance(hosts_value, str) or not hosts_value.strip():
            return [self._issue("hosts", "First play is missing a concrete hosts field", rule="missing_hosts")]

        declared_hosts = {item.strip() for item in hosts_value.split(",") if item.strip()}
        expected_hosts = {str(item).strip() for item in target_hosts if str(item).strip()}
        if hosts_value.strip() == "all" or declared_hosts != expected_hosts:
            expected_render = ", ".join(sorted(expected_hosts)) or "(none)"
            actual_render = hosts_value.strip() or "(none)"
            return [
                self._issue(
                    "hosts",
                    f"Playbook hosts '{actual_render}' do not match requested targets '{expected_render}'",
                    rule="target_hosts_mismatch",
                )
            ]
        return []

    @staticmethod
    def _issue(phase: str, message: str, rule: str, line: Optional[int] = None) -> dict[str, Any]:
        issue: dict[str, Any] = {
            "phase": phase,
            "message": message,
            "rule": rule,
        }
        if line is not None:
            issue["line"] = line
        return issue

    @staticmethod
    def _format_issue(issue: dict[str, Any]) -> str:
        prefix = f"[{issue.get('phase', 'validation')}/{issue.get('rule', 'unknown')}]"
        line = issue.get("line")
        if line is not None:
            prefix += f" line {line}"
        return f"{prefix} {issue.get('message', '')}".strip()

    @staticmethod
    def _expected_execution_engine(vendor: str) -> str:
        vendor_key = (vendor or "").lower()
        if vendor_key in {"cisco_ios", "cisco_nxos", "cisco_xr", "juniper_junos"}:
            return "ansible_native"
        if vendor_key in {"huawei_vrp", "aruba", "multi_vendor"}:
            return "ansible_cli"
        return "ssh_fallback"


def build_sync(
    provider,
    intent: dict,
    rag_context: str = "",
    kb_snippets: list[str] | None = None,
    run_id: str | None = None,
) -> str:
    """
    Synchronous convenience wrapper around PlaybookBuilder.build().
    Useful when called from sync (non-async) pipeline code.
    """
    builder = PlaybookBuilder(provider)
    return asyncio.run(
        builder.build(
            intent=intent,
            rag_context=rag_context,
            kb_snippets=kb_snippets or [],
            run_id=run_id,
        )
    )
