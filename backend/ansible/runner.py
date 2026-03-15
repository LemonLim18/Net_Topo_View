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
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from ansible.vault import vault_file_exists, vault_password_file_path, get_vault_password, get_decrypted_vault_vars

logger = logging.getLogger("ansible.runner")

# Handle fcntl and pwd missing on Windows (ansible-runner dependencies)
if sys.platform == "win32":
    import types
    for module_name in ["fcntl", "pwd"]:
        if module_name not in sys.modules:
            try:
                __import__(module_name)
            except ImportError:
                mock_mod = types.ModuleType(module_name)
                sys.modules[module_name] = mock_mod
                logger.debug(f"Mocked '{module_name}' module for Windows to allow ansible-runner import.")

try:
    import ansible_runner  # noqa: F401
    _RUNNER_AVAILABLE = True
except ImportError as e:
    _RUNNER_AVAILABLE = False
    python_path = getattr(sys, "executable", "?")
    if sys.platform == "win32":
        logger.warning(
            "ansible-runner not available on Windows (Error: %s). "
            "Native execution is limited on Windows; consider using WSL for full Ansible support. "
            "Backend Python: %s",
            e, python_path
        )
    else:
        logger.warning(
            "ansible-runner not importable (Python: %s). Check/Deploy will fail until the server is started with the project venv.",
            python_path,
        )

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


def _run_playbook_remote(
    remote_url: str,
    playbook_content: str,
    inventory: Optional[dict],
    extra_vars: Optional[dict],
    check_mode: bool,
    job_id: Optional[int],
    timeout: int,
    tags: Optional[list[str]],
    limit: Optional[str],
    playbook_path: Optional[str],
    project_files: Optional[list[dict]],
) -> AnsibleRunResult:
    """Delegate execution to a remote Ansible runner (Linux VM)."""
    import urllib.request
    import urllib.error

    ev = dict(extra_vars or {})
    if job_id is not None:
        ev["run_id"] = str(job_id)
    # Remote runner has no access to vault file; inject decrypted vault vars so inventory can resolve vault_net_user etc.
    vault_vars = get_decrypted_vault_vars()
    if vault_vars:
        ev.update(vault_vars)

    payload = {
        "playbook_yaml": playbook_content,
        "inventory": inventory or {},
        "check_mode": check_mode,
        "extra_vars": ev,
        "timeout": timeout,
        "playbook_path": playbook_path,
        "project_files": project_files or [],
        "tags": tags,
        "limit": limit,
    }
    vault_pw = get_vault_password()
    if vault_pw:
        payload["vault_password"] = vault_pw

    url = remote_url.rstrip("/") + "/run"
    headers = {"Content-Type": "application/json"}
    auth_header = os.environ.get("ANSIBLE_REMOTE_AUTH_HEADER")
    if auth_header:
        headers["Authorization"] = auth_header

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return AnsibleRunResult(
            success=False,
            rc=1,
            stdout="",
            stderr=f"Remote runner HTTP {e.code}: {body[:2000]}",
            stats={},
            changed=False,
        )
    except OSError as e:
        return AnsibleRunResult(
            success=False,
            rc=1,
            stdout="",
            stderr=f"Remote runner request failed: {e}",
            stats={},
            changed=False,
        )

    return AnsibleRunResult(
        success=data.get("rc", 1) == 0,
        rc=data.get("rc", 1),
        stdout=data.get("stdout", "")[-8000:],
        stderr=data.get("stderr", "")[-2000:],
        stats=data.get("stats", {}),
        changed=bool(data.get("changed")),
    )


def run_playbook(
    playbook_content: str = "",
    inventory:        Optional[dict] = None,
    extra_vars:       Optional[dict] = None,
    check_mode:       bool = False,
    job_id:           Optional[int] = None,
    timeout:          int  = 300,
    tags:             Optional[list[str]] = None,
    limit:            Optional[str] = None,
    playbook_path:    Optional[str] = None,
    project_files:    Optional[list[dict]] = None,
) -> AnsibleRunResult:
    """
    Run a playbook string via ansible-runner (or remote runner if configured).

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
        playbook_path:     Relative path to the entry playbook inside the project.
        project_files:     Optional list of {"path", "content"} files to materialize.

    Returns:
        AnsibleRunResult with success flag, stdout, stats.
    """
    remote_url = os.environ.get("ANSIBLE_REMOTE_RUNNER_URL", "").strip()
    if remote_url:
        logger.info("Using remote Ansible runner: %s", remote_url.split("/")[0])
        return _run_playbook_remote(
            remote_url=remote_url,
            playbook_content=playbook_content,
            inventory=inventory,
            extra_vars=extra_vars,
            check_mode=check_mode,
            job_id=job_id,
            timeout=timeout,
            tags=tags,
            limit=limit,
            playbook_path=playbook_path,
            project_files=project_files,
        )

    if not _RUNNER_AVAILABLE:
        python_used = getattr(sys, "executable", "unknown")
        return AnsibleRunResult(
            success=False, rc=1, stdout="",
            stderr=(
                "ansible-runner not available in the Python running this backend.\n"
                f"Backend Python: {python_used}\n"
                "Start the server with the project venv:\n"
                "  cd backend\n"
                "  .\\venv\\Scripts\\python.exe -m uvicorn backend:app --reload"
            ),
        )

    # Windows: ansible-runner uses fcntl.lockf and fcntl.LOCK_* (Unix-only). Stub so the run can proceed.
    if sys.platform == "win32":
        try:
            import fcntl  # noqa: F401
            if not hasattr(fcntl, "lockf"):
                fcntl.lockf = lambda fd, op, length=0, whence=0, start=0: None
            if not hasattr(fcntl, "LOCK_EX"):
                fcntl.LOCK_EX = 2
            if not hasattr(fcntl, "LOCK_UN"):
                fcntl.LOCK_UN = 8
        except ImportError:
            import types
            _fcntl = types.ModuleType("fcntl")
            _fcntl.LOCK_EX = 2
            _fcntl.LOCK_UN = 8
            _fcntl.lockf = lambda fd, op, length=0, whence=0, start=0: None
            sys.modules["fcntl"] = _fcntl

    tmpdir = tempfile.mkdtemp(prefix="nettopo_ansible_")
    try:
        priv = Path(tmpdir)

        # ── Project directory ──────────────────────────────────────────────
        project = priv / "project"
        project.mkdir(parents=True)

        runtime_playbook = playbook_path or "site.yml"
        if project_files:
            for artifact in project_files:
                rel_path = Path(str(artifact.get("path") or "").replace("\\", "/"))
                if rel_path.is_absolute() or ".." in rel_path.parts:
                    return AnsibleRunResult(
                        success=False,
                        rc=1,
                        stdout="",
                        stderr=f"Invalid generated artifact path: {rel_path}",
                    )
                target = project / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(artifact.get("content") or ""), encoding="utf-8")
        else:
            pb_path = project / runtime_playbook
            pb_path.parent.mkdir(parents=True, exist_ok=True)
            pb_path.write_text(playbook_content, encoding="utf-8")

        # Copy ansible.cfg
        if ANSIBLE_CFG.exists():
            shutil.copy(ANSIBLE_CFG, project / "ansible.cfg")

        # Copy roles dir (symlink not reliable cross-OS)
        if ROLES_PATH.exists():
            shutil.copytree(ROLES_PATH, project / "roles", dirs_exist_ok=True)

        # Copy vault file if present
        if VAULT_FILE.exists():
            vault_dest = project / "vault"
            vault_dest.mkdir(exist_ok=True)
            shutil.copy(VAULT_FILE, vault_dest / "net_creds.yml")

        # ── Inventory ──────────────────────────────────────────────────────
        inv_dir = priv / "inventory"
        inv_dir.mkdir(parents=True)
        (inv_dir / "hosts.yml").write_text(yaml.dump(inventory or {}, default_flow_style=False))

        # ── Environment / Extra vars ───────────────────────────────────────
        env_dir = priv / "env"
        env_dir.mkdir(exist_ok=True)

        # Extra vars as JSON file (avoids shell escaping issues)
        ev = dict(extra_vars or {})
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

        if sys.platform == "win32":
            # ansible-runner uses pexpect.spawn (Unix-only). Ansible CLI uses os.get_blocking() (Unix-only).
            # Run via a bootstrap script that patches os.get_blocking then invokes ansible.cli.playbook.
            inv_path = (priv / "inventory" / "hosts.yml").resolve()
            bootstrap = priv / "_nettopo_ansible_playbook_bootstrap.py"
            bootstrap.write_text(
                r"""
import locale
import os
import sys
import types

# Windows: provide stubs for Unix-only modules that Ansible imports (fcntl, pwd, termios).
if sys.platform == "win32":
    def _stub_get_blocking(fd):
        return True
    os.get_blocking = _stub_get_blocking
    if "fcntl" not in sys.modules:
        _fcntl = types.ModuleType("fcntl")
        _fcntl.LOCK_EX = 2
        _fcntl.LOCK_UN = 8
        _fcntl.lockf = lambda fd, op, length=0, whence=0, start=0: None
        sys.modules["fcntl"] = _fcntl
    if "pwd" not in sys.modules:
        _pwd = types.ModuleType("pwd")
        sys.modules["pwd"] = _pwd
    if "termios" not in sys.modules:
        _termios = types.ModuleType("termios")
        _termios.TCSANOW = 0
        _termios.TCSADRAIN = 1
        _termios.TCSAFLUSH = 2
        _termios.TCIFLUSH = 0
        _termios.TCOFLUSH = 1
        _termios.TCIOFLUSH = 2
        _termios.tcgetattr = lambda fd: [0] * 7
        _termios.tcsetattr = lambda fd, when, attr: None
        _termios.tcdrain = lambda fd: None
        _termios.tcflush = lambda fd, queue: None
        _termios.tcflow = lambda fd, action: None
        sys.modules["termios"] = _termios

# Ansible initialize_locale() uses locale.getlocale() and sys.getfilesystemencoding(); Windows uses cp1252.
_orig_getlocale = locale.getlocale
def _getlocale_utf8(category=None):
    if category is not None:
        lang, enc = _orig_getlocale(category)
    else:
        lang, enc = _orig_getlocale()
    return (lang or "C", "UTF-8")
locale.getlocale = _getlocale_utf8
_orig_getfilesystemencoding = sys.getfilesystemencoding
def _fs_encoding_utf8():
    return "utf-8"
sys.getfilesystemencoding = _fs_encoding_utf8

# Windows: multiprocessing.get_context('fork') is not available; use 'spawn' instead.
if sys.platform == "win32":
    import multiprocessing
    _orig_get_context = multiprocessing.get_context
    def _get_context(method="fork"):
        if method == "fork":
            return _orig_get_context("spawn")
        return _orig_get_context(method)
    multiprocessing.get_context = _get_context

# argv: [this_script, playbook, -i, inv, ...] -> ansible-playbook expects [ansible-playbook, playbook, ...]
sys.argv = ["ansible-playbook"] + sys.argv[1:]
import runpy
runpy.run_module("ansible.cli.playbook", run_name="__main__")
""",
                encoding="utf-8",
            )
            cmd = [
                sys.executable,
                str(bootstrap),
                runtime_playbook,
                "-i", str(inv_path),
            ] + cmdline_parts
            try:
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"  # Ansible requires UTF-8 locale; Windows default is CP1252
                proc = subprocess.run(
                    cmd,
                    cwd=str(project),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                rc = proc.returncode
                stdout = (proc.stdout or "")[-8000:]
                stderr = (proc.stderr or "")[-2000:]
            except subprocess.TimeoutExpired as e:
                rc = 1
                stdout = (e.stdout or "")[-8000:] if e.stdout else ""
                stderr = ((e.stderr or "") + "\nTimeout running ansible-playbook.")[-2000:]
            except FileNotFoundError as e:
                rc = 1
                stdout = ""
                stderr = (
                    "ansible-playbook not found. Install ansible in the same venv: "
                    "pip install ansible-core (or ansible). "
                    f"Detail: {e}"
                )
            result = AnsibleRunResult(
                success=(rc == 0),
                rc=rc,
                stdout=stdout,
                stderr=stderr,
                stats={},
                changed="changed" in (stdout + stderr).lower(),
            )
            logger.info(
                "ansible-playbook (Windows) complete: job_id=%s rc=%d",
                job_id, rc,
            )
            return result

        runner = ansible_runner.run(
            private_data_dir=str(priv),
            playbook=runtime_playbook,
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
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=False)
        except (PermissionError, OSError) as e:
            logger.debug("Cleanup temp dir %s: %s (ignored on Windows)", tmpdir, e)
            if sys.platform != "win32":
                raise
            shutil.rmtree(tmpdir, ignore_errors=True)
