"""DWS v1.0.62 CLI connection. This module has no send/reply command."""

from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from engine.community.kernel.dws_connection import (
    AuthorizationChallenge,
    AuthorizationStatus,
    ConnectionHealth,
    ConnectionIdentity,
    DwsError,
    HistoryResult,
    ListenerNotice,
)
from engine.community.plugins.dws_hosting import wire
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.process import DwsProcess


class DwsCliConnection:
    def __init__(
        self,
        process: DwsProcess,
        identity: ConnectionIdentity,
        *,
        authorization_hosts: frozenset[str],
        history_timezone: str,
        authorization_seconds: float = 300,
        listener_seconds: int = 3600,
    ) -> None:
        if (
            not authorization_hosts
            or authorization_seconds <= 0
            or listener_seconds <= 0
        ):
            raise ValueError("invalid connection bounds")
        for value in (identity.organization_id, identity.user_id):
            if any(char in value for char in (",", ":", "\x00", "\n")):
                raise ValueError(
                    "profile must identify exactly one organization and user"
                )
        ZoneInfo(history_timezone)
        self.process, self.identity = process, identity
        self.authorization_hosts, self.history_timezone = (
            authorization_hosts,
            history_timezone,
        )
        self.authorization_seconds, self.listener_seconds = (
            authorization_seconds,
            listener_seconds,
        )
        self._consumer_pid = 0
        self._profile = ("--profile", f"{identity.organization_id}:{identity.user_id}")

    @asynccontextmanager
    async def exclusive(self):
        path = self.process.config_dir / "hosting.lock"
        # COSEC: one local hosting owner; do not follow an attacker-replaced lock path.
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            HostingDatabase._check_private(path, directory=False)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DwsError("connection_busy") from None
            yield
        finally:
            os.close(fd)

    async def authorization_status(self) -> AuthorizationStatus:
        raw = await self.process.capture(("auth", "status", "-f", "json"))
        return wire.auth_status(raw, self.identity)

    async def authorize(
        self,
        on_challenge: Callable[[AuthorizationChallenge], Awaitable[None]],
    ) -> AuthorizationStatus:
        # A fresh credential directory has no selectable profile until login completes.
        args = ("auth", "login", "--device", "--no-browser", "-y", "-f", "json")
        try:
            async with (
                asyncio.timeout(self.authorization_seconds),
                self.process.open(args) as child,
                aclosing(self.process.lines(child)) as lines,
            ):
                output, size = [], 0
                async for channel, line in lines:
                    size += len(line.encode("utf-8"))
                    if size > self.process.output_limit:
                        raise DwsError("output_limit")
                    if channel == "stdout":
                        output.append(line)
                    else:
                        challenge = self._challenge(line.strip())
                        if challenge is not None:
                            await on_challenge(challenge)
                if await child.wait() != 0:
                    raise DwsError("authorization_failed")
                result = wire.payload("\n".join(output))
                if result.get("success") is not True:
                    raise DwsError("authorization_failed")
                if (result.get("corp_id"), result.get("user_id")) != (
                    self.identity.organization_id,
                    self.identity.user_id,
                ):
                    raise DwsError("identity_mismatch")
                status = await self.authorization_status()
                if not status.authenticated:
                    raise DwsError("authorization_required")
                return status
        except TimeoutError:
            raise DwsError("authorization_timeout") from None

    def _challenge(self, line: str) -> AuthorizationChallenge | None:
        if not line.startswith("https://"):
            return None
        try:
            parsed = urlsplit(line)
            port = parsed.port
        except ValueError:
            raise DwsError("invalid_authorization_url") from None
        query = parse_qs(parsed.query)
        code = query.get("user_code", query.get("userCode", []))
        if not code:
            return None
        # COSEC: only return a configured authorization origin, not arbitrary CLI URLs.
        if (
            parsed.hostname not in self.authorization_hosts
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or len(code) != 1
            or len(line) > 8192
            or len(code[0]) > 512
        ):
            raise DwsError("invalid_authorization_url")
        return AuthorizationChallenge(verification_url=line, user_code=code[0])

    async def listen(self) -> AsyncIterator[ListenerNotice]:
        args = (
            "event",
            "consume",
            wire.DM_EVENT,
            "--flatten",
            "-f",
            "ndjson",
            "--duration",
            f"{self.listener_seconds}s",
            "--ttl",
            f"{self.listener_seconds + 60}s",
            "--name",
            "avernet-personal-hosting",
            *self._profile,
        )
        async with (
            self.process.open(args) as child,
            aclosing(self.process.lines(child)) as lines,
        ):
            self._consumer_pid = child.pid
            try:
                async for channel, line in lines:
                    if channel == "stderr":
                        if line.strip().startswith("[event] ready"):
                            yield ListenerNotice(kind="ready")
                    elif line.strip():
                        event = wire.object_json(line)
                        if event.get("type") != wire.DM_EVENT:
                            raise DwsError("unexpected_event")
                        yield ListenerNotice(
                            kind="conversation",
                            conversation_id=wire.identifier(
                                event.get("conversation_id")
                            ),
                            message_id=wire.identifier(event.get("message_id")),
                            created_at_ms=wire.timestamp(
                                event.get("create_time"), self.history_timezone
                            ),
                        )
                # Even a planned TTL rotation must pass a new readiness/reconciliation barrier.
                raise DwsError("listener_ended")
            finally:
                self._consumer_pid = 0

    async def health(self) -> ConnectionHealth:
        raw = await self.process.capture(
            ("event", "status", "-f", "json", *self._profile)
        )
        return wire.health(raw, self._consumer_pid)

    async def discover(self, start_ms: int, end_ms: int) -> tuple[str, ...]:
        raw = await self.process.capture(
            (
                "chat",
                "+recent-conversations",
                "--start",
                wire.rfc3339(start_ms),
                "--end",
                wire.rfc3339(end_ms),
                "--page-limit",
                "50",
                "--total-timeout",
                "45",
                "-f",
                "json",
                *self._profile,
            )
        )
        return wire.discovery(raw)

    async def history(
        self, conversation_id: str, start_ms: int, end_ms: int
    ) -> HistoryResult:
        wire.identifier(conversation_id)
        raw = await self.process.capture(
            (
                "chat",
                "+chat-messages",
                "--conversation-id",
                conversation_id,
                "--start",
                wire.rfc3339(start_ms),
                "--end",
                wire.rfc3339(end_ms),
                "--order",
                "asc",
                "--page-all",
                "--page-limit",
                "50",
                "--max-items",
                "5000",
                "--no-reactions",
                "-f",
                "json",
                *self._profile,
            )
        )
        return wire.history(
            raw, conversation_id, start_ms, end_ms, self.identity, self.history_timezone
        )
