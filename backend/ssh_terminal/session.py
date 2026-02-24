"""
SSH Terminal Session Manager
============================
Wraps paramiko SSH connections for interactive web-based terminal sessions.
Each session maps to one SSH channel (invoke_shell) and is identified by a UUID.

Security:
    - Passwords NEVER logged at any level.
    - Each session records its owner (username from JWT).
    - Idle sessions reaped after MAX_IDLE_SECONDS.

Limits:
    - MAX_SESSIONS_PER_USER  – concurrent tabs per user.
    - MAX_SESSIONS_PER_HOST  – concurrent tabs to same host.
"""

import logging
import threading
import time
import uuid
from typing import Dict, List, Optional

import paramiko

logger = logging.getLogger("ssh_terminal.session")

# ---------------------------------------------------------------------------
# Configurable limits / timeouts
# ---------------------------------------------------------------------------
MAX_IDLE_SECONDS: int = 600        # 10 min of inactivity → auto-close
MAX_SESSIONS_PER_USER: int = 10    # concurrent SSH tabs per user
MAX_SESSIONS_PER_HOST: int = 3     # concurrent SSH tabs to the same host


class SSHTerminalSession:
    """A single interactive SSH shell session backed by paramiko."""

    CONNECT_TIMEOUT = 10  # seconds

    def __init__(
        self,
        session_id: str,
        host: str,
        port: int = 22,
        owner_username: str = "anonymous",
    ):
        self.session_id = session_id
        self.host = host
        self.port = port
        self.owner_username = owner_username
        self.last_activity: float = time.monotonic()
        self._client: Optional[paramiko.SSHClient] = None
        self._channel: Optional[paramiko.Channel] = None
        self._connected = False
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected and self._channel is not None and not self._channel.closed

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    def update_activity(self) -> None:
        """Refresh the last-activity timestamp (called on every I/O event)."""
        self.last_activity = time.monotonic()

    # ------------------------------------------------------------------
    # Connection — password intentionally excluded from all log lines
    # ------------------------------------------------------------------

    def connect(self, username: str, password: str) -> None:
        """
        Establish SSH connection and open interactive shell.
        The 'password' argument is NEVER included in any log message.
        """
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            self._try_connect(username, password)
            logger.info(
                "SSH session %s → %s as %s (modern algorithms)",
                self.session_id, self.host, username,
            )
        except paramiko.SSHException:
            logger.debug("Modern SSH failed for %s; retrying with legacy algorithms", self.host)
            try:
                self._try_connect(username, password, use_legacy=True)
                logger.info(
                    "SSH session %s → %s as %s (legacy algorithms)",
                    self.session_id, self.host, username,
                )
            except Exception as exc:
                self._client = None
                # Password intentionally absent from this exception message
                raise ConnectionError(
                    f"Cannot SSH to {self.host} as '{username}': {exc}"
                ) from exc

        self._channel = self._client.invoke_shell(term="xterm-256color", width=120, height=40)
        self._channel.settimeout(0.0)   # non-blocking reads
        self._connected = True
        self.update_activity()

    def _try_connect(self, username: str, password: str, use_legacy: bool = False) -> None:
        """Internal connect helper. The password is never forwarded to logging."""
        self._client.connect(
            hostname=self.host,
            port=self.port,
            username=username,
            password=password,
            timeout=self.CONNECT_TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )

    # ------------------------------------------------------------------
    # I/O — content never logged
    # ------------------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        if self._channel and not self._channel.closed:
            self._channel.resize_pty(width=cols, height=rows)
            self.update_activity()

    def write(self, data: str) -> None:
        """Send data (keystrokes) over the SSH shell. Content is never logged."""
        if self._channel and not self._channel.closed:
            self._channel.sendall(data.encode("utf-8"))
            self.update_activity()

    def read(self, size: int = 65536) -> Optional[bytes]:
        """Non-blocking read from SSH channel. Returns bytes or None."""
        if not self._channel or self._channel.closed:
            return None
        try:
            if self._channel.recv_ready():
                data = self._channel.recv(size)
                self.update_activity()
                return data
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Cleanly close the SSH session."""
        self._connected = False
        with self._lock:
            if self._channel:
                try:
                    self._channel.close()
                except Exception:
                    pass
                self._channel = None
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None
        logger.info("SSH session %s closed (owner: %s)", self.session_id, self.owner_username)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "host": self.host,
            "port": self.port,
            "owner_username": self.owner_username,
            "connected": self.connected,
            "idle_seconds": round(self.idle_seconds, 1),
        }


# ---------------------------------------------------------------------------
# Global session registry
# ---------------------------------------------------------------------------

_sessions: Dict[str, SSHTerminalSession] = {}
_sessions_lock = threading.Lock()


def create_session(
    host: str,
    port: int = 22,
    owner_username: str = "anonymous",
) -> SSHTerminalSession:
    """Create a new SSH session (not yet connected)."""
    sid = uuid.uuid4().hex[:12]
    s = SSHTerminalSession(session_id=sid, host=host, port=port, owner_username=owner_username)
    with _sessions_lock:
        _sessions[sid] = s
    return s


def get_session(session_id: str) -> Optional[SSHTerminalSession]:
    with _sessions_lock:
        return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    """Remove and close a session."""
    with _sessions_lock:
        s = _sessions.pop(session_id, None)
    if s:
        s.close()


def list_sessions() -> List[dict]:
    with _sessions_lock:
        return [s.to_dict() for s in _sessions.values()]


# ---------------------------------------------------------------------------
# Limit helpers (called by api.py for admission control)
# ---------------------------------------------------------------------------

def count_sessions_for_user(username: str) -> int:
    with _sessions_lock:
        return sum(1 for s in _sessions.values() if s.owner_username == username)


def count_sessions_for_host(host: str) -> int:
    with _sessions_lock:
        return sum(1 for s in _sessions.values() if s.host == host)


# ---------------------------------------------------------------------------
# Idle-timeout cleanup — called periodically by backend.py lifespan task
# ---------------------------------------------------------------------------

def cleanup_idle_sessions(max_idle_seconds: int = MAX_IDLE_SECONDS) -> List[str]:
    """
    Close and remove sessions idle longer than `max_idle_seconds`.
    Returns list of reaped session IDs.
    """
    with _sessions_lock:
        stale = [
            sid for sid, s in _sessions.items()
            if s.idle_seconds > max_idle_seconds
        ]
        for sid in stale:
            _sessions.pop(sid, None)

    for sid in stale:
        logger.info("Reaped idle SSH session %s (idle > %ds)", sid, max_idle_seconds)

    return stale
