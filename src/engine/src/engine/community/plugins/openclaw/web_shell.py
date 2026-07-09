"""OpenClaw web-shell PTY session — leaf-side implementation.

Contains the PTY session class (``OpenClawWebShellSession``) and the helper
that forks the shell child (``_open_shell``), copied from
``engines/openclaw/web_shell.py``.  The legacy module stays live; this copy
lives in the plugins leaf so the ``OpenClawPluginImpl.open_session`` method can
construct sessions without importing from ``core``.

``OpenClawWebShellSession`` carries no core types — it is pure os/pty/fcntl
and structurally satisfies the ``core/web_shell/protocol.WebShellSession``
Protocol (read / write / resize / close).  The adapter passes it through
unchanged.
"""
from __future__ import annotations

import asyncio
import fcntl
import hmac
import os
import pty
import pwd
import struct
import termios
from typing import Optional


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


def _open_shell(shell_user: str) -> tuple[int, int]:
    """Fork a PTY shell running as ``shell_user``.  Returns (master_fd, pid).

    Copied intact from ``engines/openclaw/web_shell.py:_open_shell``.
    """
    master_fd, slave_fd = pty.openpty()
    pid = os.fork()

    if pid == 0:
        # ── child ──
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        for fd in (0, 1, 2):
            os.dup2(slave_fd, fd)
        os.close(slave_fd)
        try:
            pw = pwd.getpwnam(shell_user)
            os.setgid(pw.pw_gid)
            os.setuid(pw.pw_uid)
            env = {
                "HOME": pw.pw_dir,
                "USER": pw.pw_name,
                "LOGNAME": pw.pw_name,
                "SHELL": pw.pw_shell or "/bin/bash",
                "TERM": "xterm-256color",
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "PATH": os.environ.get(
                    "PATH", "/usr/local/bin:/usr/bin:/bin",
                ),
            }
            for k, v in os.environ.items():
                if k.startswith(
                    ("KUBERNETES_", "POD_", "NODE_", "CONDA_", "OPENCLAW_")
                ):
                    env[k] = v
            os.chdir(pw.pw_dir)
            os.execve(env["SHELL"], [env["SHELL"], "--login"], env)
        except Exception as exc:
            os.write(2, f"open_shell error: {exc}\n".encode())
        finally:
            os._exit(1)

    # ── parent ──
    os.close(slave_fd)
    return master_fd, pid


class OpenClawWebShellSession:
    """PTY session backed by a forked shell child.

    Copied intact from ``engines/openclaw/web_shell.py:OpenClawWebShellSession``.
    Structurally satisfies ``core/web_shell/protocol.WebShellSession``
    (read / write / resize / close) without importing any core type.
    """

    def __init__(self, master_fd: int, child_pid: int) -> None:
        self._master_fd: Optional[int] = master_fd
        self._child_pid: Optional[int] = child_pid
        self._closed = False

    async def read(self) -> bytes:
        if self._master_fd is None:
            return b""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: os.read(self._master_fd, 4096)  # type: ignore[arg-type]
            )
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        if self._master_fd is None:
            return
        try:
            os.write(self._master_fd, data)
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            return
        try:
            _set_winsize(self._master_fd, cols, rows)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._child_pid is not None:
            try:
                os.kill(self._child_pid, 9)
                os.waitpid(self._child_pid, os.WNOHANG)
            except OSError:
                pass
            self._child_pid = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None


class _WebShellPortMixin:
    """Domain mixin: web-shell token check + session open (local-infra)."""

    # Env-var names and default user relocated from engines/openclaw/web_shell.py
    _DEBUG_TOKEN_ENV = "DEBUG_TOKEN"
    _SHELL_USER_ENV = "SHELL_USER"
    _DEFAULT_SHELL_USER = "admin"

    def _debug_token(self) -> str:
        return os.environ.get(self._DEBUG_TOKEN_ENV, "")

    def _shell_user(self) -> str:
        return os.environ.get(self._SHELL_USER_ENV, self._DEFAULT_SHELL_USER)

    def check_token(self, token_str: str) -> bool:
        """Validate the debug-terminal access token.

        Returns ``True`` when the token matches (HMAC compare) or when no
        token is configured (in-cluster, always allow).
        Copied from ``engines/openclaw/web_shell.py:OpenClawWebShellService.check_token``.
        """
        configured = self._debug_token()
        if not configured:
            return True  # no token configured → in-cluster, always allow
        return hmac.compare_digest(token_str, configured)

    async def open_session(self) -> "OpenClawWebShellSession":  # noqa: F821
        """Fork a new PTY shell session; return the native session object.

        Delegates to ``_open_shell`` from ``plugins/community/openclaw/web_shell.py``.
        Copied from ``engines/openclaw/web_shell.py:OpenClawWebShellService.open_session``
        (minus the ``auth`` parameter — auth-bridging lives in the adapter).
        """
        master_fd, child_pid = _open_shell(self._shell_user())
        return OpenClawWebShellSession(master_fd, child_pid)


__all__ = ["OpenClawWebShellSession", "_WebShellPortMixin", "_open_shell"]
