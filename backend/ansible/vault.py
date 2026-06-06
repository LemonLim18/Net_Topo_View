"""
Ansible Vault credential helper.

Reads the encrypted vault file at runtime using the Vault password supplied
via the environment variable ANSIBLE_VAULT_PASSWORD or from the file path
in ANSIBLE_VAULT_PASSWORD_FILE.

The vault file itself (vault/net_creds.yml) is GITIGNORED and must be
created/encrypted by the operator:

    ansible-vault create backend/ansible/vault/net_creds.yml

Plaintext template (DO NOT COMMIT UNENCRYPTED):
    vault_net_user:   "netautomation"
    vault_net_pass:   "ChangeMe123!"
    vault_enable_pass: "EnableMe!"

Optional per-vendor overrides (different SSH credentials per vendor):
    vault_cisco_ios_user:   "cisco_admin"
    vault_cisco_ios_pass:   "CiscoPass!"
    vault_cisco_ios_enable_pass: "CiscoEnable!"
    vault_juniper_junos_user: "junos_ops"
    vault_juniper_junos_pass: "JunosPass!"
    vault_huawei_vrp_user:  "vrp_admin"
    vault_huawei_vrp_pass:  "HuaweiPass!"

If per-vendor vars are set, inventory uses them; otherwise vault_net_* apply.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("ansible.vault")

VAULT_DIR  = Path(__file__).parent / "vault"
VAULT_FILE = VAULT_DIR / "net_creds.yml"
VAULT_PASS_FILE = VAULT_DIR / ".vault_pass"   # plain-text password file (gitignored)


def ensure_vault_dir() -> None:
    """Create vault directory if it doesn't exist."""
    VAULT_DIR.mkdir(parents=True, exist_ok=True)


def get_vault_password() -> str | None:
    """
    Retrieve vault password from (in priority order):
      1. ANSIBLE_VAULT_PASSWORD env var
      2. File path in ANSIBLE_VAULT_PASSWORD_FILE env var
      3. Default VAULT_PASS_FILE path if it exists
    Returns None if not configured (dev/test only — vault features disabled).
    """
    pw = os.getenv("ANSIBLE_VAULT_PASSWORD")
    if pw:
        return pw

    pw_file = os.getenv("ANSIBLE_VAULT_PASSWORD_FILE") or str(VAULT_PASS_FILE)
    path = Path(pw_file)
    if path.exists():
        return path.read_text().strip()

    logger.warning(
        "No Ansible Vault password configured. "
        "Set ANSIBLE_VAULT_PASSWORD or ANSIBLE_VAULT_PASSWORD_FILE. "
        "Credentials will not be available to playbooks."
    )
    return None


def vault_password_file_path() -> str | None:
    """
    Return the path to a plain-text vault password file that ansible-runner
    can reference via --vault-password-file.
    Writes a temp file from the env var if needed.
    """
    import tempfile

    # If explicit file env var, use it directly
    explicit = os.getenv("ANSIBLE_VAULT_PASSWORD_FILE")
    if explicit and Path(explicit).exists():
        return explicit

    # If inlined password env var, write to a temp file
    pw = os.getenv("ANSIBLE_VAULT_PASSWORD")
    if pw:
        tmp = Path(tempfile.mktemp(suffix=".vault_pass"))
        tmp.write_text(pw)
        tmp.chmod(0o600)
        return str(tmp)

    # Use the local default
    if VAULT_PASS_FILE.exists():
        return str(VAULT_PASS_FILE)

    return None


def vault_file_exists() -> bool:
    return VAULT_FILE.exists()


def get_decrypted_vault_vars() -> dict:
    """
    Load vault/net_creds.yml and return a dict of variable names to values.
    - If the file is plain YAML (does not start with $ANSIBLE_VAULT), load it directly.
      Use this on Windows where ansible-vault CLI may not run (os.get_blocking etc.).
    - If the file is vault-encrypted, decrypt via ansible-vault view.
    Returns {} if file is missing or decryption fails.
    """
    import subprocess

    try:
        import yaml
    except ImportError:
        return {}

    if not VAULT_FILE.exists():
        return {}

    raw = VAULT_FILE.read_text(encoding="utf-8", errors="replace").strip()
    # Plain YAML (no encryption) — e.g. Windows where ansible-vault encrypt fails
    if not raw.startswith("$ANSIBLE_VAULT"):
        try:
            data = yaml.safe_load(raw) or {}
            out = dict(data) if isinstance(data, dict) else {}
            if out:
                logger.debug("Loaded plain net_creds.yml (not encrypted). Keep file gitignored.")
            return out
        except Exception:
            return {}

    pw_file = vault_password_file_path()
    if not pw_file:
        return {}
    try:
        result = subprocess.run(
            [
                "ansible-vault",
                "view",
                "--vault-password-file",
                pw_file,
                str(VAULT_FILE),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {}
        data = yaml.safe_load(result.stdout) or {}
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return {}
