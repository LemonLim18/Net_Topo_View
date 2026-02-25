"""
Ansible-runner wrapper.

Executes Ansible playbooks via the ansible-runner Python API inside a
temporary private_data_dir so the main repo is never polluted with run artefacts.

All credentials are resolved from Ansible Vault files — no plaintext ever
appears in the inventory or extra_vars written to disk.
"""

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from ansible.vault import vault_file_exists, vault_password_file_path

logger = logging.getLogger("ansible.runner")

ANSIBLE_BASE = Path(__file__).parent
VAULT_FILE    = ANSIBLE_BASE / "vault" / "net_creds.yml"
ANSIBLE_CFG   = ANSIBLE_BASE / "ansible.cfg"
ROLES_PATH    = ANSIBLE_BASE / "roles"


@dataclass
class AnsibleRunResult:
    success: bool
    rc:      int
    stdout:  str
    stderr:  str
    stats:   dict  = field(default_factory=dict)   # per-host ok/changed/failed
    changed: bool  = False

    @property
    def summary(self) -> str:
        lines = []
        for host, counts in self.stats.get("custom", {}).items():
            lines.append(
                f"{host}: ok={counts.get('ok',0)} "
                f"changed={counts.get('changed',0)} "
                f"failed={counts.get('failures',0)}"
            )
        return "\n".join(lines) if lines else ("OK" if self.success else "FAILED")


def run_playbook(
    playbook_content: str,
    inventory:        dict,
    extra_vars:       dict,
    check_mode:       bool = False,
    job_id:           Optional[int] = None,
    timeout:          int  = 300,
    tags:             Optional[list[str]] = None,
    limit:            Optional[str] = None,
) -> AnsibleRunResult:
    """
    Run a playbook string via ansible-runner.

    All I/O is isolated to a temporary directory that is cleaned up on exit.
    The vault file (if it exists) is copied into the private_data_dir so
    playbooks can resolve {{ vault_net_user }} etc.

    Args:
        playbook_content:  Raw YAML string of the playbook.
        inventory:         Inventory dict (from ansible/inventory.py).
        extra_vars:        Extra variables passed to the playbook.
        check_mode:        If True, adds --check (dry-run, no changes).
        job_id:            Injected as `run_id` extra var for audit tracking.
        timeout:           Per-task timeout in seconds.
        tags:              Optional list of Ansible tags to run.
        limit:             Optional host/group limit pattern.

    Returns:
        AnsibleRunResult with success flag, stdout, stats.
    """
    try:
        import ansible_runner  # type: ignore
    except ImportError:
        return AnsibleRunResult(
            success=False, rc=1, stdout="",
            stderr="ansible-runner not installed — run: pip install ansible-runner",
        )

    with tempfile.TemporaryDirectory(prefix="nettopo_ansible_") as tmpdir:
        priv = Path(tmpdir)

        # ── Project directory ──────────────────────────────────────────────
        project = priv / "project"
        project.mkdir(parents=True)

        pb_path = project / "site.yml"
        pb_path.write_text(playbook_content)

        # Copy ansible.cfg
        if ANSIBLE_CFG.exists():
            shutil.copy(ANSIBLE_CFG, project / "ansible.cfg")

        # Copy roles dir (symlink not reliable cross-OS)
        if ROLES_PATH.exists():
            shutil.copytree(ROLES_PATH, project / "roles")

        # Copy vault file if present
        if VAULT_FILE.exists():
            vault_dest = project / "vault"
            vault_dest.mkdir(exist_ok=True)
            shutil.copy(VAULT_FILE, vault_dest / "net_creds.yml")

        # ── Inventory ──────────────────────────────────────────────────────
        inv_dir = priv / "inventory"
        inv_dir.mkdir(parents=True)
        (inv_dir / "hosts.yml").write_text(yaml.dump(inventory, default_flow_style=False))

        # ── Environment / Extra vars ───────────────────────────────────────
        env_dir = priv / "env"
        env_dir.mkdir(exist_ok=True)

        # Extra vars as JSON file (avoids shell escaping issues)
        ev = dict(extra_vars)
        if job_id is not None:
            ev["run_id"] = str(job_id)
        (env_dir / "extravars").write_text(
            "\n".join(f"{k}={json.dumps(v)}" for k, v in ev.items())
        )

        # ── Build cmdline args ─────────────────────────────────────────────
        cmdline_parts: list[str] = []
        if check_mode:
            cmdline_parts.append("--check")
            cmdline_parts.append("--diff")
        if tags:
            cmdline_parts += ["--tags", ",".join(tags)]
        if limit:
            cmdline_parts += ["--limit", limit]

        # Vault password
        vault_pw_file = vault_password_file_path()
        if vault_pw_file:
            cmdline_parts += ["--vault-password-file", vault_pw_file]

        # ── Run ────────────────────────────────────────────────────────────
        logger.info(
            "ansible-runner start: job_id=%s check=%s tags=%s limit=%s",
            job_id, check_mode, tags, limit,
        )

        runner = ansible_runner.run(
            private_data_dir=str(priv),
            playbook="site.yml",
            cmdline=" ".join(cmdline_parts) if cmdline_parts else None,
            quiet=True,
            timeout=timeout,
        )

        # ── Collect output ─────────────────────────────────────────────────
        stdout_lines: list[str] = []
        for event in runner.events:
            stdout_val = event.get("stdout", "")
            if stdout_val:
                stdout_lines.append(str(stdout_val))
        stdout = "\n".join(stdout_lines)

        stderr = ""
        if hasattr(runner.stderr, "read"):
            stderr = runner.stderr.read()
        elif isinstance(runner.stderr, str):
            stderr = runner.stderr

        stats = runner.stats or {}
        changed = any(
            v.get("changed", 0) > 0
            for v in stats.values()
            if isinstance(v, dict)
        )

        result = AnsibleRunResult(
            success=runner.rc == 0,
            rc=runner.rc,
            stdout=stdout[-8000:],  # cap at 8 KB to keep DB sane
            stderr=stderr[-2000:],
            stats=stats,
            changed=changed,
        )

        logger.info(
            "ansible-runner complete: job_id=%s rc=%d changed=%s",
            job_id, runner.rc, changed,
        )
        return result
