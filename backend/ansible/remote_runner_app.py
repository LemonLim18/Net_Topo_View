"""
NetTopoView Remote Ansible Runner — run on your Linux VM (e.g. 192.168.1.251).

This script receives playbook + inventory from the NetTopoView backend and runs
ansible-playbook locally, then returns rc/stdout/stderr. Deploy on a machine
that has Ansible (or ansible-runner) installed so the Windows backend can
delegate execution here.

  pip install fastapi uvicorn pyyaml
  python -m ansible.remote_runner_app

  Listens on 0.0.0.0:8765 by default. Set HOST=127.0.0.1 for local-only.
  Optional: set AUTH_HEADER=Bearer <secret> and the backend must send it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Header, Request
    from fastapi.responses import JSONResponse
    import uvicorn
    import yaml
except ImportError as e:
    print("Install: pip install fastapi uvicorn pyyaml", file=sys.stderr)
    raise SystemExit(1) from e

app = FastAPI(title="NetTopoView Remote Ansible Runner", version="1.0")

# Optional: require Authorization header (set AUTH_HEADER env on server and ANSIBLE_REMOTE_AUTH_HEADER on backend)
EXPECTED_AUTH = os.environ.get("AUTH_HEADER")  # e.g. "Bearer my-secret-token"


def _run_playbook(
    playbook_yaml: str,
    inventory: dict,
    check_mode: bool,
    extra_vars: dict,
    vault_password: str | None,
    timeout: int,
    playbook_path: str | None,
    project_files: list[dict] | None,
    tags: list[str] | None,
    limit: str | None,
) -> dict:
    """Run ansible-playbook in a temp dir; return rc, stdout, stderr, changed, stats."""
    runtime_playbook = playbook_path or "site.yml"
    with tempfile.TemporaryDirectory(prefix="nettopo_remote_") as tmpdir:
        root = Path(tmpdir)
        project = root / "project"
        project.mkdir()
        inv_dir = root / "inventory"
        inv_dir.mkdir()

        if project_files:
            for art in project_files:
                path = (art.get("path") or "").replace("\\", "/")
                if path.startswith("/") or ".." in path:
                    continue
                target = project / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(art.get("content") or "", encoding="utf-8")
        else:
            (project / runtime_playbook).write_text(playbook_yaml, encoding="utf-8")

        (inv_dir / "hosts.yml").write_text(yaml.dump(inventory, default_flow_style=False), encoding="utf-8")

        # Host key checking off + legacy SSH algorithms so old network devices can negotiate
        ansible_cfg = project / "ansible.cfg"
        ansible_cfg.write_text(
            "[defaults]\n"
            "host_key_checking = False\n"
            "gathering = explicit\n"
            "[ssh_connection]\n"
            "ssh_args = -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            "-o KexAlgorithms=+diffie-hellman-group-exchange-sha1,diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 "
            "-o HostKeyAlgorithms=+ssh-rsa "
            "-o Ciphers=+aes128-cbc,3des-cbc,aes192-cbc,aes256-cbc "
            "-o MACs=+hmac-sha1,hmac-sha1-96,hmac-md5,hmac-md5-96\n",
            encoding="utf-8",
        )

        cmd = ["ansible-playbook", runtime_playbook, "-i", str(inv_dir / "hosts.yml")]
        if check_mode:
            cmd += ["--check", "--diff"]
        if tags:
            cmd += ["--tags", ",".join(tags)]
        if limit:
            cmd += ["--limit", limit]
        if extra_vars:
            cmd += ["-e", json.dumps(extra_vars)]

        vault_file = None
        if vault_password:
            vault_file = root / ".vault_pass"
            vault_file.write_text(vault_password, encoding="utf-8")
            vault_file.chmod(0o600)
            cmd += ["--vault-password-file", str(vault_file)]

        env = os.environ.copy()
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        # NETCONF/SSH persistent connection defaults are 30s; Huawei is often slower
        env.setdefault("ANSIBLE_PERSISTENT_CONNECT_TIMEOUT", "60")
        env.setdefault("ANSIBLE_PERSISTENT_COMMAND_TIMEOUT", "90")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(project),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            rc = result.returncode
            stdout = result.stdout or ""
            stderr = result.stderr or ""
        except subprocess.TimeoutExpired as e:
            rc = 1
            stdout = (e.stdout or "")[-8000:]
            stderr = ((e.stderr or "") + "\nTimeout.")[-2000:]
        except FileNotFoundError:
            rc = 1
            stdout = ""
            stderr = "ansible-playbook not found. Install ansible-core (or ansible) on this host."

        changed = "changed" in (stdout + stderr).lower() and rc == 0
        return {
            "rc": rc,
            "stdout": stdout[-8000:],
            "stderr": stderr[-2000:],
            "changed": changed,
            "stats": {},
        }


@app.post("/run")
async def run(request: Request, authorization: str | None = Header(None)):
    if EXPECTED_AUTH and authorization != EXPECTED_AUTH:
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization")
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    playbook_yaml = body.get("playbook_yaml") or ""
    inventory = body.get("inventory") or {}
    check_mode = bool(body.get("check_mode"))
    extra_vars = body.get("extra_vars") or {}
    vault_password = body.get("vault_password")
    timeout = int(body.get("timeout", 300))
    playbook_path = body.get("playbook_path")
    project_files = body.get("project_files") or []
    tags = body.get("tags")
    limit = body.get("limit")

    if not playbook_yaml and not project_files:
        raise HTTPException(status_code=400, detail="playbook_yaml or project_files required")

    result = _run_playbook(
        playbook_yaml=playbook_yaml,
        inventory=inventory,
        check_mode=check_mode,
        extra_vars=extra_vars,
        vault_password=vault_password,
        timeout=timeout,
        playbook_path=playbook_path,
        project_files=project_files,
        tags=tags,
        limit=limit,
    )
    return JSONResponse(content=result)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nettopo-remote-ansible-runner"}


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))
    print(f"Remote Ansible Runner listening on http://{host}:{port}", file=sys.stderr)
    print("  POST /run  — run playbook (see NetTopoView backend)", file=sys.stderr)
    print("  GET  /health — health check", file=sys.stderr)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
