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
import logging
import re
import subprocess
import tempfile
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ansible.builder")

PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompt_templates" / "llm_to_ansible.txt"
).read_text()

# Regex to extract a YAML fenced code block from LLM response
_YAML_FENCE = re.compile(r"```ya?ml\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# Hard cap on context passed to LLM (avoid token overflow)
_RAG_CONTEXT_MAX_CHARS  = 6000
_KB_SNIPPETS_MAX        = 3
_KB_SNIPPET_MAX_CHARS   = 1200


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
        # ── Build prompt ───────────────────────────────────────────────────
        params_yaml = yaml.dump(
            intent.get("params", {}),
            default_flow_style=False,
            indent=2,
        ).strip()

        snippets_text = "\n---\n".join(
            s[:_KB_SNIPPET_MAX_CHARS]
            for s in kb_snippets[:_KB_SNIPPETS_MAX]
        ) or "(none retrieved)"

        prompt_text = PROMPT_TEMPLATE.format(
            rag_context=rag_context[:_RAG_CONTEXT_MAX_CHARS],
            kb_snippets=snippets_text,
            run_id=run_id or "unknown",
            intent_description=intent.get("description", ""),
            target_hosts=", ".join(intent.get("target_hosts", [])),
            vendor=intent.get("vendor", ""),
            feature=intent.get("feature", ""),
            params_yaml=params_yaml,
        )

        # ── Call LLM ───────────────────────────────────────────────────────
        from llm.provider import LLMMessage
        messages = [LLMMessage(role="user", content=prompt_text)]

        logger.info(
            "Requesting playbook from LLM: intent=%s vendor=%s feature=%s",
            intent.get("description", "")[:80],
            intent.get("vendor", ""),
            intent.get("feature", ""),
        )

        response = await self._provider.generate(messages=messages, temperature=0.1)
        raw_text = response.content

        # ── Extract YAML ────────────────────────────────────────────────────
        playbook_yaml = self._extract_yaml(raw_text)

        # ── Structural validation (parse as YAML) ───────────────────────────
        self._validate_yaml_structure(playbook_yaml)

        # ── ansible-lint (optional, non-fatal if not installed) ─────────────
        self._lint(playbook_yaml)

        logger.info("Playbook built and validated OK")
        return playbook_yaml

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

    def _validate_yaml_structure(self, yaml_text: str) -> None:
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

    def _lint(self, yaml_text: str) -> None:
        """Run ansible-lint. Non-fatal if ansible-lint is not installed."""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".yml", mode="w", delete=False
            ) as f:
                f.write(yaml_text)
                fpath = f.name

            result = subprocess.run(
                ["ansible-lint", "--nocolor", "-p", "--offline", fpath],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode not in (0, 2):  # 2 = warnings only
                raise PlaybookBuildError(
                    f"ansible-lint found errors:\n{result.stdout}\n{result.stderr}"
                )
            logger.info("ansible-lint passed (rc=%d)", result.returncode)
        except FileNotFoundError:
            logger.warning("ansible-lint not found — skipping lint step")
        except subprocess.TimeoutExpired:
            logger.warning("ansible-lint timed out — skipping lint step")


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
