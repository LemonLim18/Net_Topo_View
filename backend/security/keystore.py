"""
M15: Authentication & Credentials — Fernet Keystore
=====================================================
Central key management for the credential vault (CredentialVault) and
any other module that needs symmetric encryption at rest.

Key resolution order
--------------------
1. Environment variable ``FERNET_KEY``  (base64-encoded 32-byte key)
2. Environment variable ``VAULT_KEY``   (arbitrary string → SHA-256 → Fernet)
3. File path in ``FERNET_KEY_PATH`` env var (path to a file holding key)
4. Auto-generated key persisted to ``backend/auth/.vault_key``
   (gitignored — never committed)

All callers should use the module-level helpers rather than constructing
``Fernet`` objects directly, so the key source is configured in one place.

Usage::

    from security.keystore import get_fernet, encrypt, decrypt

    token = encrypt(b"s3cr3t")
    plain = decrypt(token)         # b"s3cr3t"
"""

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("security.keystore")

# Where to write / read the auto-generated key (relative to this file → backend/auth/)
_DEFAULT_KEY_FILE = Path(__file__).resolve().parent.parent / "auth" / ".vault_key"


# ---------------------------------------------------------------------------
# Internal key loading
# ---------------------------------------------------------------------------

def _load_or_generate_key() -> bytes:
    """
    Resolve the Fernet key according to the resolution order documented
    in the module docstring.
    """

    # 1. FERNET_KEY env var — direct base64 key
    raw_env = os.environ.get("FERNET_KEY", "").strip()
    if raw_env:
        try:
            # Validate it is a proper Fernet key (32 bytes base64url)
            key = raw_env.encode() if isinstance(raw_env, str) else raw_env
            Fernet(key)   # raises if invalid
            logger.debug("Keystore: using FERNET_KEY env var")
            return key
        except Exception:
            logger.warning("FERNET_KEY env var is not a valid Fernet key — falling through")

    # 2. VAULT_KEY env var — arbitrary string, SHA-256 stretched
    vault_env = os.environ.get("VAULT_KEY", "").strip()
    if vault_env:
        key = _stretch(vault_env)
        logger.debug("Keystore: derived key from VAULT_KEY env var")
        return key

    # 3. FERNET_KEY_PATH env var — path to a file containing the key
    key_path_env = os.environ.get("FERNET_KEY_PATH", "").strip()
    if key_path_env:
        key_file = Path(key_path_env)
        if key_file.exists():
            key = key_file.read_bytes().strip()
            logger.debug("Keystore: loaded key from FERNET_KEY_PATH=%s", key_path_env)
            return key
        logger.warning("FERNET_KEY_PATH points to non-existent file: %s — falling through", key_path_env)

    # 4. Auto-generate and persist to .vault_key
    if _DEFAULT_KEY_FILE.exists():
        key = _DEFAULT_KEY_FILE.read_bytes().strip()
        logger.debug("Keystore: loaded existing auto-generated key from %s", _DEFAULT_KEY_FILE)
        return key

    key = Fernet.generate_key()
    _DEFAULT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DEFAULT_KEY_FILE.write_bytes(key)
    # Restrict to owner read/write only on POSIX
    try:
        _DEFAULT_KEY_FILE.chmod(0o600)
    except Exception:
        pass
    logger.info("Keystore: generated new Fernet key → %s", _DEFAULT_KEY_FILE)
    return key


def _stretch(secret: str) -> bytes:
    """Derive a Fernet-compatible 32-byte key from an arbitrary string."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


# ---------------------------------------------------------------------------
# Module-level singleton & helpers
# ---------------------------------------------------------------------------

_fernet: Optional[Fernet] = None


def get_fernet() -> Fernet:
    """Return the module-level Fernet singleton (lazy-initialised)."""
    global _fernet
    if _fernet is None:
        key = _load_or_generate_key()
        _fernet = Fernet(key)
    return _fernet


def encrypt(data: bytes) -> bytes:
    """Encrypt *data* and return a Fernet token (bytes)."""
    return get_fernet().encrypt(data)


def decrypt(token: bytes) -> bytes:
    """
    Decrypt a Fernet *token* and return the plaintext bytes.
    Raises ``cryptography.fernet.InvalidToken`` on failure.
    """
    return get_fernet().decrypt(token)


def encrypt_str(plaintext: str, encoding: str = "utf-8") -> str:
    """Convenience wrapper — encrypts a string and returns a base64 token string."""
    return encrypt(plaintext.encode(encoding)).decode("ascii")


def decrypt_str(token: str, encoding: str = "utf-8") -> str:
    """Convenience wrapper — decrypts a base64 token string and returns plaintext."""
    return decrypt(token.encode("ascii")).decode(encoding)


def rotate_key(new_key: Optional[bytes] = None) -> bytes:
    """
    Replace the module-level Fernet key with *new_key* (or generate one).
    Returns the new key bytes.

    NOTE: This does NOT re-encrypt existing stored data.  The caller must
    handle re-encryption of any data stored under the old key.
    """
    global _fernet
    if new_key is None:
        new_key = Fernet.generate_key()
    _fernet = Fernet(new_key)
    _DEFAULT_KEY_FILE.write_bytes(new_key)
    try:
        _DEFAULT_KEY_FILE.chmod(0o600)
    except Exception:
        pass
    logger.info("Keystore: key rotated and saved to %s", _DEFAULT_KEY_FILE)
    return new_key
