import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from auth.auth import get_current_user as _get_user
except ImportError:
    async def _get_user():  # type: ignore
        return {"username": "anonymous", "role": "admin"}


router = APIRouter(prefix="/template-git", tags=["Template Studio Git"])

# repo_root = <workspace root>, i.e. parent of backend/
_REPO_ROOT = Path(__file__).resolve().parents[2]


class GitExecRequest(BaseModel):
    command: str


class GitHubLoginRequest(BaseModel):
    username: str
    token: str
    git_name: Optional[str] = None
    git_email: Optional[str] = None


_ALLOWED_GIT_SUBCOMMANDS = {
    "init",
    "status",
    "add",
    "commit",
    "push",
    "pull",
    "fetch",
    "log",
    "show",
    "diff",
    "checkout",
    "switch",
    "branch",
    "restore",
    "reset",
    "rm",
    "mv",
    "tag",
    "remote",
    "stash",
    "rebase",
    "merge",
    "rev-parse",
}


def _run_cmd(
    argv: List[str],
    *,
    timeout: int = 30,
    input_text: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(_REPO_ROOT),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Command not found: {argv[0]}",
        }


def _git(argv: List[str], *, timeout: int = 30) -> Dict[str, Any]:
    return _run_cmd(["git", *argv], timeout=timeout)


def _current_branch() -> str:
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if not r["ok"]:
        return "unknown"
    return (r["stdout"] or "unknown").strip() or "unknown"


def _origin_url() -> str:
    r = _git(["remote", "get-url", "origin"])
    if not r["ok"]:
        return ""
    return (r["stdout"] or "").strip()


def _is_dirty() -> bool:
    r = _git(["status", "--porcelain"])
    if not r["ok"]:
        return False
    return bool((r["stdout"] or "").strip())


def _github_cli_auth_status() -> Dict[str, Any]:
    gh_path = shutil.which("gh")
    if not gh_path:
        return {"available": False, "authenticated": False, "username": None}

    r = _run_cmd([gh_path, "auth", "status", "-h", "github.com"], timeout=20)
    text = ((r.get("stdout") or "") + "\n" + (r.get("stderr") or "")).strip()
    user = None
    m = re.search(r"logged in to github\.com as\s+([^\s]+)", text, flags=re.I)
    if m:
        user = m.group(1).strip()
    return {
        "available": True,
        "authenticated": bool(r.get("ok")),
        "username": user,
        "raw": text,
    }


@router.get("/status")
async def git_status(_user: dict = Depends(_get_user)):
    origin = _origin_url()
    cli = _github_cli_auth_status()
    return {
        "repo_root": str(_REPO_ROOT),
        "branch": _current_branch(),
        "is_dirty": _is_dirty(),
        "origin": origin,
        "github_remote": "github.com" in origin.lower(),
        "github_cli_available": bool(cli.get("available")),
        "github_authenticated": bool(cli.get("authenticated")),
        "github_username": cli.get("username"),
    }


@router.post("/exec")
async def git_exec(body: GitExecRequest, _user: dict = Depends(_get_user)):
    cmd = (body.command or "").strip()
    if not cmd:
        raise HTTPException(400, "Command is required")

    try:
        tokens = shlex.split(cmd, posix=False)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid command: {exc}")

    if not tokens:
        raise HTTPException(400, "Command is required")

    if tokens[0].lower() != "git":
        raise HTTPException(400, "Only git commands are allowed in Template Studio terminal")

    if len(tokens) < 2:
        raise HTTPException(400, "Please provide a git subcommand")

    subcommand = tokens[1].lower()
    if subcommand not in _ALLOWED_GIT_SUBCOMMANDS:
        raise HTTPException(400, f"git {subcommand} is not allowed in Template Studio terminal")

    res = _run_cmd(tokens, timeout=90)
    return {
        "ok": bool(res["ok"]),
        "exit_code": int(res["exit_code"]),
        "stdout": res["stdout"],
        "stderr": res["stderr"],
        "branch": _current_branch(),
        "is_dirty": _is_dirty(),
    }


@router.post("/github/login")
async def github_login(body: GitHubLoginRequest, _user: dict = Depends(_get_user)):
    username = (body.username or "").strip()
    token = (body.token or "").strip()
    if not username or not token:
        raise HTTPException(400, "username and token are required")

    origin = _origin_url()
    if "github.com" not in origin.lower():
        raise HTTPException(400, "Current git origin is not a GitHub remote")

    if body.git_name and body.git_name.strip():
        _git(["config", "user.name", body.git_name.strip()])
    if body.git_email and body.git_email.strip():
        _git(["config", "user.email", body.git_email.strip()])

    cred_input = (
        "protocol=https\n"
        "host=github.com\n"
        f"username={username}\n"
        f"password={token}\n\n"
    )
    _approve = _run_cmd(["git", "credential", "approve"], input_text=cred_input, timeout=20)
    if not _approve["ok"]:
        raise HTTPException(500, _approve["stderr"] or "Failed to save GitHub credentials")

    return {
        "status": "connected",
        "username": username,
        "branch": _current_branch(),
        "origin": origin,
    }


@router.post("/github/logout")
async def github_logout(_user: dict = Depends(_get_user)):
    reject_input = "protocol=https\nhost=github.com\n\n"
    _run_cmd(["git", "credential", "reject"], input_text=reject_input, timeout=20)

    gh_path = shutil.which("gh")
    if gh_path:
        # best-effort: ignore errors if gh was not logged in
        _run_cmd([gh_path, "auth", "logout", "-h", "github.com", "-y"], timeout=20)

    return {"status": "disconnected"}
