"""Shared harness for the engine-runtime endpoint tests.

A minimal FastAPI app hosting one group's router, with the caller principal
overridden and the relay Protocol bound to a fake. The fake records every
forward, so tests can assert *that no device was touched* — the assertion the
isolation invariant actually needs, since a 404 alone does not prove the
forward was skipped.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult

OWNER = "u1"
BOT = "b1"


class FakeRelay:
    """Stands in for ``EngineRuntimeRelay`` at the Protocol boundary."""

    def __init__(self) -> None:
        self.bots: dict[tuple[str, str], BotFacts] = {
            (BOT, OWNER): BotFacts(
                bot_id=BOT, bot_type="personal", active_engine="openclaw"
            )
        }
        #: queued results, consumed in order; a lone entry is reused
        self.results: list[Any] = [EngineResult(data={})]
        self.raises: Exception | None = None
        #: forwards that reached the transport (bot resolution passed)
        self.calls: list[dict[str, Any]] = []
        #: every forward the handler *attempted*, resolved or not
        self.attempts: list[dict[str, Any]] = []

    # -- Protocol ----------------------------------------------------------
    def resolve_bot(self, bot_id: str, owner_id: str) -> BotFacts:
        facts = self.bots.get((bot_id, owner_id))
        if facts is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return facts

    async def resolve_bot_off_loop(self, bot_id: str, owner_id: str) -> BotFacts:
        return self.resolve_bot(bot_id, owner_id)

    async def call(
        self, *, bot_id, owner_id, method, path,
        body=None, params=None, timeout=None, enveloped=True, facts=None,
    ) -> EngineResult:
        # Record the ATTEMPT first, then resolve. Ordering it the other way
        # made "no device was touched" tautological: a foreign bot raises in
        # resolve_bot, so `calls` was empty regardless of what the handler did,
        # and the sweep could only ever have caught a handler bypassing the
        # relay entirely. Recording first means `attempts` reflects the
        # handler's behaviour and `calls` reflects what would really reach a
        # device.
        self.attempts.append({"bot_id": bot_id, "owner_id": owner_id, "path": path})
        self.resolve_bot(bot_id, owner_id)
        self.calls.append(
            {
                "bot_id": bot_id, "owner_id": owner_id, "method": method,
                "path": path, "body": body, "params": params,
                "timeout": timeout, "enveloped": enveloped,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    # -- helpers -----------------------------------------------------------
    def set_bot_type(self, bot_type: str) -> None:
        self.bots[(BOT, OWNER)] = BotFacts(
            bot_id=BOT, bot_type=bot_type, active_engine="openclaw"
        )

    def set_shared(self, is_shared: bool = True) -> None:
        """Make the bot one that more than its owner can reach."""
        current = self.bots[(BOT, OWNER)]
        self.bots[(BOT, OWNER)] = BotFacts(
            bot_id=current.bot_id,
            bot_type=current.bot_type,
            active_engine=current.active_engine,
            bot_pk=current.bot_pk,
            is_shared=is_shared,
        )

    @property
    def paths(self) -> list[str]:
        """Forwards that actually reached the transport."""
        return [c["path"] for c in self.calls]


@pytest.fixture
def relay() -> FakeRelay:
    return FakeRelay()


@pytest.fixture
def make_client(relay):
    """Build a TestClient hosting ``router`` with the fake relay bound."""

    def _build(router, principal: Any = {"user_id": OWNER}) -> TestClient:
        class _M(Module):
            def configure(self, binder):
                binder.bind(EngineRuntimeRelayProtocol, to=relay)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: principal
        attach_injector(app, Injector([_M()]))
        return TestClient(app)

    return _build


def ok(resp, code: int = 200000) -> Any:
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    assert "request_id" in body
    return body["data"]


def fails(resp, status: int) -> dict:
    body = resp.json()
    assert resp.status_code == status, body
    assert set(body) == {"code", "message", "data", "request_id"}, body
    assert body["data"] is None
    return body
