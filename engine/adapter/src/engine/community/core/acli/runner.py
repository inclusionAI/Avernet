"""``AcliRunner`` — the only code in the engine that executes ``acli``.

``acli`` is the client for the internal CLI market: it searches the market,
installs a tool by its market name, and uninstalls what it installed. The
platform reaches this through ``/api/cli/acli/*`` so a bot can gain or lose a
tool without a restart.

**Every call is a fixed argv.** ``create_subprocess_exec`` with no shell, so a
name or keyword is one argument and can never become a second command. Inputs
are still validated before a process starts, because an argument that begins
with ``-`` would be read by acli as a flag.

**``HOME`` is moved onto persisted storage.** acli keeps its record of what it
installed in ``$HOME/.acli/tools`` and appends a ``PATH`` line to
``$HOME/.profile``. The container's real ``HOME`` (``/root``) does not survive
a restart, while ``/home/admin`` does, so every install and uninstall runs with
``HOME`` set to ``<parent of install_dir>/acli-home``. The install directory
itself is the engine's choice (``engine.community.config.load_acli_install_dir``);
the platform names only the tool. That keeps acli's record
beside the programs it describes, so an uninstall after a restart still finds
the tool, and keeps the ``.profile`` edit out of the real home.

**Failures raise :class:`AcliError`** with a short code and acli's own error
text, masked and bounded. Raw output is logged (masked, truncated) and never
returned: it is a tool's chatter, not an API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("engine.acli")

#: A market name: ``dataphin``, ``deepinsight-cli``, ``yuntu-cli``. The first
#: character is never ``-``, so a name cannot be read as a flag. Refused:
#: ``name@version`` (acli records installs by bare name, so a pinned name could
#: be installed but never found again to uninstall) and npm package names such
#: as ``@antcli/yuntu-cli`` (not market names, and a ``/`` could not be
#: addressed by the platform's ``DELETE …/clis/{cli_code}``).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_NAME_LENGTH = 256
_MAX_KEYWORD_LENGTH = 100
_MAX_PAGE_SIZE = 100
#: The directory acli is told to install into must be a ``bin`` directory:
#: binary products land in it directly, and npm products treat its parent as
#: their prefix, so both kinds end up in the same ``PATH`` entry.
_INSTALL_DIR_NAME = "bin"
ACLI_HOME_NAME = "acli-home"

INSTALL_TIMEOUT_SECONDS = 300.0
QUERY_TIMEOUT_SECONDS = 30.0
#: How long to wait for a killed process group to be reaped before giving up
#: on it. Bounded so a process that ignores the kill cannot hold the request.
_REAP_TIMEOUT_SECONDS = 5.0

_MAX_LOGGED_OUTPUT = 4096
_MAX_ERROR_MESSAGE = 512
_SECRET_RE = re.compile(
    r"(?i)((?:token|secret|password|passwd|api[_-]?key|access[_-]?key|authorization|"
    r"cookie|credential)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?|bearer\s+)([^\s\"',;&]+)"
)


class AcliError(Exception):
    """acli could not do what was asked.

    ``code`` is one of ``invalid_input``, ``acli_unavailable``, ``timeout``,
    ``bad_output``, ``install_failed``, ``uninstall_failed``,
    ``market_failed``. ``message`` is safe to return to a caller.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AcliTool:
    """One tool as acli describes it, in the market or after an install."""

    name: str
    command: str
    description: str
    version: str


@dataclass(frozen=True, slots=True)
class AcliUninstallResult:
    """``uninstalled`` is ``False`` when acli had no record of the tool.

    That is success, not failure: there is nothing of ours to remove (the
    tool was granted some other way, or already removed).
    """

    name: str
    uninstalled: bool


@dataclass(frozen=True, slots=True)
class AcliMarketPage:
    items: tuple[AcliTool, ...]
    total: int
    has_more: bool


def mask_secrets(text: str) -> str:
    """Replace credential-looking values with ``****``."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}****", text)


def validate_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or len(name) > _MAX_NAME_LENGTH
        or not _NAME_RE.fullmatch(name)
        or ".." in name
    ):
        raise AcliError("invalid_input", f"invalid acli tool name: {name!r}")
    return name


def validate_install_dir(install_dir: str) -> Path:
    path = Path(install_dir) if isinstance(install_dir, str) else None
    if (
        path is None
        or not path.is_absolute()
        or ".." in path.parts
        or path.name != _INSTALL_DIR_NAME
    ):
        raise AcliError(
            "invalid_input",
            f"install_dir must be an absolute path ending in /{_INSTALL_DIR_NAME}: "
            f"{install_dir!r}",
        )
    return path


def acli_home_for(install_dir: Path) -> Path:
    """The ``HOME`` acli runs with: ``<parent of install_dir>/acli-home``.

    For ``/home/admin/.agentclaw/bin`` that is ``/home/admin/.agentclaw/acli-home``.
    acli records what it installed under ``$HOME/.acli/tools``; keeping that on
    persisted storage beside the programs lets an uninstall after a container
    restart still find the tool. Nothing else configures this path.
    """
    return install_dir.parent / ACLI_HOME_NAME


def _validate_keyword(keyword: str) -> str:
    if (
        not isinstance(keyword, str)
        or len(keyword) > _MAX_KEYWORD_LENGTH
        or keyword.startswith("-")
        or any(ord(ch) < 32 for ch in keyword)
    ):
        raise AcliError("invalid_input", "invalid market keyword")
    return keyword


def _validate_page(page_no: int, page_size: int) -> None:
    if not 1 <= page_no <= 10_000 or not 1 <= page_size <= _MAX_PAGE_SIZE:
        raise AcliError("invalid_input", "page_no or page_size out of range")


def _bounded(text: str, limit: int) -> str:
    masked = mask_secrets(text)
    return masked if len(masked) <= limit else masked[:limit] + "…"


def _parse_json(stdout: str) -> dict[str, Any] | None:
    """The JSON object acli printed, or ``None``.

    acli can print a blank line before its JSON, so decoding starts at the
    first ``{`` rather than at byte 0.
    """
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _tool_from(payload: Mapping[str, Any]) -> AcliTool:
    return AcliTool(
        name=str(payload.get("name") or ""),
        command=str(payload.get("command") or ""),
        description=str(payload.get("description") or ""),
        version=str(payload.get("version") or ""),
    )


class AcliRunner:
    """Run acli. Stateless; safe to share."""

    def __init__(
        self, *, environ: Mapping[str, str], install_dir: str, executable: str = "acli"
    ) -> None:
        """``environ`` and ``install_dir`` come from the composition root.

        ``install_dir`` is where every tool is installed: the engine owns that
        placement, and callers never choose it. A value that is not an
        absolute ``…/bin`` path is a deployment error and fails here, at start.
        """
        try:
            self._install_dir = validate_install_dir(install_dir)
        except AcliError as error:
            raise ValueError(error.message) from error
        # The process environment acli inherits, supplied by the composition
        # root so this core module never reads runtime configuration itself.
        self._environ = environ
        self._executable = executable

    async def install(self, name: str) -> AcliTool:
        """Install ``name`` into the engine's install directory and describe it."""
        validate_name(name)
        directory = self._install_dir
        payload = await self._run(
            ["install", name, "--install-dir", str(directory), "--yes"],
            env=self._env_for(directory, failure_code="install_failed"),
            timeout=INSTALL_TIMEOUT_SECONDS,
            failure_code="install_failed",
        )
        cli = payload.get("cli") if isinstance(payload.get("cli"), Mapping) else {}
        installed = AcliTool(
            name=str(payload.get("name") or cli.get("name") or ""),
            command=str(payload.get("command") or cli.get("command") or ""),
            description=str(cli.get("description") or payload.get("description") or ""),
            version=str(payload.get("version") or cli.get("version") or ""),
        )
        if not installed.name or not installed.command:
            raise AcliError("bad_output", "acli install reported no name or command")
        return installed

    async def uninstall(self, name: str) -> AcliUninstallResult:
        """Uninstall ``name`` if acli installed it; a tool acli never installed is success."""
        validate_name(name)
        directory = self._install_dir
        env = self._env_for(directory, failure_code="uninstall_failed")
        listing = await self._run(
            ["tool", "list"],
            env=env,
            timeout=QUERY_TIMEOUT_SECONDS,
            failure_code="uninstall_failed",
        )
        # A listing of the wrong shape must not read as "not installed": the
        # grant is already revoked, and skipping the uninstall would leave
        # the program on PATH. An explicit null is an empty listing.
        if "tools" not in listing or not isinstance(listing["tools"], (list, type(None))):
            raise AcliError("bad_output", "acli tool list returned no tools list")
        installed_names = {
            str(tool.get("name"))
            for tool in listing["tools"] or []
            if isinstance(tool, Mapping)
        }
        if name not in installed_names:
            log.info("[acli] uninstall name=%s result=not_installed", name)
            return AcliUninstallResult(name=name, uninstalled=False)
        await self._run(
            ["tool", "uninstall", name, "--yes"],
            env=env,
            timeout=INSTALL_TIMEOUT_SECONDS,
            failure_code="uninstall_failed",
        )
        return AcliUninstallResult(name=name, uninstalled=True)

    async def market(self, keyword: str, page_no: int, page_size: int) -> AcliMarketPage:
        """One page of the acli market, filtered by ``keyword`` (may be empty)."""
        _validate_keyword(keyword)
        _validate_page(page_no, page_size)
        argv = ["list", "--page-no", str(page_no), "--page-size", str(page_size)]
        if keyword:
            argv += ["--keyword", keyword]
        payload = await self._run(
            argv,
            env=dict(self._environ),
            timeout=QUERY_TIMEOUT_SECONDS,
            failure_code="market_failed",
        )
        rows = payload.get("list")
        items = tuple(
            _tool_from(row) for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, Mapping)
        )
        total = payload.get("total")
        return AcliMarketPage(
            items=items,
            total=total if isinstance(total, int) else len(items),
            has_more=bool(payload.get("hasMore")),
        )

    # ── execution ────────────────────────────────────────────────────────

    def _env_for(self, install_dir: Path, *, failure_code: str) -> dict[str, str]:
        home = acli_home_for(install_dir)
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            log.warning("[acli] cannot prepare HOME=%s: %s", home, error)
            raise AcliError(failure_code, f"cannot prepare acli home under {install_dir.parent}") from error
        return {**self._environ, "HOME": str(home)}

    @staticmethod
    async def _kill_group(proc: asyncio.subprocess.Process) -> None:
        """Kill acli and everything it started, then reap it, bounded.

        acli runs npm and installers as children that inherit its pipes, so
        killing acli alone would leave them holding the pipes open and the
        request waiting on them.
        """
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT_SECONDS)
        except TimeoutError:
            log.warning("[acli] pid=%s not reaped after kill", proc.pid)

    async def _run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float,
        failure_code: str,
    ) -> dict[str, Any]:
        argv = [self._executable, *args, "--json"]
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=dict(env),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Its own process group, so a timeout or a cancelled request
                # can kill acli together with whatever it spawned.
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError) as error:
            log.warning("[acli] unavailable argv=%s: %s", argv[:3], error)
            raise AcliError("acli_unavailable", "acli is not installed in this container") from error
        try:
            raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as error:
            await self._kill_group(proc)
            log.warning("[acli] timeout argv=%s after %.0fs", argv[:3], timeout)
            raise AcliError("timeout", f"acli did not finish within {timeout:.0f}s") from error
        except asyncio.CancelledError:
            await self._kill_group(proc)
            raise
        stdout = raw_out.decode("utf-8", errors="replace")
        stderr = raw_err.decode("utf-8", errors="replace")
        duration_ms = int((time.monotonic() - started) * 1000)
        payload = _parse_json(stdout)
        succeeded = proc.returncode == 0 and payload is not None and payload.get("success") is not False
        log.info(
            "[acli] argv=%s exit=%s success=%s duration_ms=%s",
            argv[:3], proc.returncode, succeeded, duration_ms,
        )
        if succeeded and payload is not None:
            return payload
        log.warning(
            "[acli] failed argv=%s exit=%s stdout=%s stderr=%s",
            argv[:3], proc.returncode,
            _bounded(stdout, _MAX_LOGGED_OUTPUT), _bounded(stderr, _MAX_LOGGED_OUTPUT),
        )
        if payload is None:
            if proc.returncode == 0:
                raise AcliError("bad_output", "acli printed no JSON result")
            reason = stderr.strip() or f"acli exited with status {proc.returncode}"
            raise AcliError(failure_code, _bounded(reason, _MAX_ERROR_MESSAGE))
        reason = str(payload.get("error") or f"acli exited with status {proc.returncode}")
        raise AcliError(failure_code, _bounded(reason, _MAX_ERROR_MESSAGE))


__all__ = [
    "ACLI_HOME_NAME",
    "AcliError",
    "AcliMarketPage",
    "AcliRunner",
    "AcliTool",
    "AcliUninstallResult",
    "acli_home_for",
    "mask_secrets",
    "validate_install_dir",
    "validate_name",
]
