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
