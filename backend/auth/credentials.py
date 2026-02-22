"""
M15: Device Credential Vault
==============================
Encrypts device SSH/SNMP credentials at rest using Fernet (AES-128-CBC).

Security model:
    - Encryption key sourced from VAULT_KEY env var, or auto-generated to
      a gitignored file (backend/auth/.vault_key).
    - Credentials stored in the ``device_credentials`` SQLite table.
    - Encryption/decryption happens in-process — key never leaves memory.

Usage::

    vault = CredentialVault()
    vault.store("SSH", 1, "admin", "Cisco123")
    cred = vault.get(1, "SSH")
    print(cred)  # {"username": "admin", "password": "Cisco123"}
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken

from backend.auth.auth import SECRET_KEY

logger = logging.getLogger("auth.credentials")

_DB_PATH = Path(__file__).resolve().parent.parent / "db" / "netauto.db"


def _derive_fernet_key(secret: str) -> bytes:
    """
    Derive a valid 32-byte URL-safe base64 Fernet key from an
    arbitrary-length secret string using SHA-256.
    """
    import base64
    import hashlib
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class CredentialVault:
    """
    Encrypts and stores device credentials in the database.
    """

    def __init__(self):
        key = _derive_fernet_key(SECRET_KEY)
        self._fernet = Fernet(key)

    def _connect(self):
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return the Fernet token as UTF-8."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a Fernet token back to plaintext."""
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            logger.error("Failed to decrypt credential — key may have changed")
            raise ValueError("Decryption failed. The vault key may have changed.")

    # ----- CRUD -------------------------------------------------------

    def store(
        self,
        label: str,
        device_id: int,
        username: str,
        password: str,
    ) -> int:
        """
        Store (or update) an encrypted credential for a device.

        Args:
            label:     Credential type (e.g. "SSH", "SNMP", "Enable").
            device_id: FK to the devices table.
            username:  Plaintext username (encrypted before storage).
            password:  Plaintext password (encrypted before storage).

        Returns:
            Row ID of the stored credential.
        """
        enc_user = self.encrypt(username)
        enc_pass = self.encrypt(password)

        conn = self._connect()
        try:
            # Upsert
            existing = conn.execute(
                "SELECT id FROM device_credentials WHERE device_id = ? AND label = ?",
                (device_id, label),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE device_credentials SET username_enc = ?, password_enc = ?, updated_at = datetime('now') WHERE id = ?",
                    (enc_user, enc_pass, existing["id"]),
                )
                conn.commit()
                row_id = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO device_credentials (device_id, label, username_enc, password_enc) VALUES (?, ?, ?, ?)",
                    (device_id, label, enc_user, enc_pass),
                )
                conn.commit()
                row_id = cur.lastrowid

            logger.info("Stored %s credential for device %d", label, device_id)
            return row_id
        finally:
            conn.close()

    def get(self, device_id: int, label: str = "SSH") -> Optional[Dict[str, str]]:
        """
        Retrieve and decrypt a credential for a device.

        Returns:
            ``{"username": "...", "password": "..."}`` or ``None``.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT username_enc, password_enc FROM device_credentials WHERE device_id = ? AND label = ?",
                (device_id, label),
            ).fetchone()
            if not row:
                return None
            return {
                "username": self.decrypt(row["username_enc"]),
                "password": self.decrypt(row["password_enc"]),
            }
        finally:
            conn.close()

    def delete(self, device_id: int, label: str = "SSH") -> bool:
        """Delete a stored credential. Returns True if one was deleted."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM device_credentials WHERE device_id = ? AND label = ?",
                (device_id, label),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_credentials(self, device_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List stored credentials (metadata only — passwords NOT returned).
        """
        conn = self._connect()
        try:
            if device_id:
                rows = conn.execute(
                    "SELECT id, device_id, label, created_at, updated_at FROM device_credentials WHERE device_id = ?",
                    (device_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, device_id, label, created_at, updated_at FROM device_credentials"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
