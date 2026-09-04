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

from tests.community.adapters.http.openapi_v1.conftest import (
    SeamLocks,
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult

OWNER = "u1"
BOT = "b1"


class FakeRelay:
    """Stands in for ``EngineRuntimeRelay`` at the Protocol boundary.

    Mirrors the real resolve's two-step isolation: existence under the named
    owner, then the operator adjudication — the owner passes implicitly,
    anyone else needs an entry in :attr:`operators`, and a refusal is the
    same masked ``BotNotFoundError`` an absent bot raises. The *level*
    policy (MEMBER vs ADMIN) is core's business, exercised in
    ``tests/community/core/engine_runtime``; at this boundary a caller is
    either an operator or not.

    The relay keeps that second step even though most of this package's rows
    are ``Check(MEMBER)`` now, so the delivery and core gates remain aligned.
    :attr:`operators` therefore feeds two things
    at once — this method, and the seam doubles :func:`bind_seam_from_relay`
    wires — which is what keeps one ``add_operator`` call describing one world.
    """

    def __init__(self) -> None:
        self.bots: dict[tuple[str, str], BotFacts] = {
            (BOT, OWNER): BotFacts(
                bot_id=BOT,
                bot_type="personal",
                active_engine="openclaw",
                owner_id=OWNER,
            )
        }
        #: non-owner callers admitted as operators, keyed like the real
        #: adjudication: (bot_id, owner_id, caller_id)
        self.operators: set[tuple[str, str, str]] = set()
        #: queued results, consumed in order; a lone entry is reused
        self.results: list[Any] = [EngineResult(data={})]
        self.raises: Exception | None = None
        #: forwards that reached the transport (bot resolution passed)
        self.calls: list[dict[str, Any]] = []
        #: every forward the handler *attempted*, resolved or not
        self.attempts: list[dict[str, Any]] = []

    # -- Protocol ----------------------------------------------------------
    def resolve_bot(self, bot_id: str, owner_id: str, caller_id: str) -> BotFacts:
        facts = self.bots.get((bot_id, owner_id))
        if facts is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        if caller_id != facts.owner_id and (
            (bot_id, owner_id, caller_id) not in self.operators
        ):
            # Byte-identical to the absent-bot refusal, like the real thing.
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return facts

    async def resolve_bot_off_loop(
        self, bot_id: str, owner_id: str, caller_id: str
    ) -> BotFacts:
        return self.resolve_bot(bot_id, owner_id, caller_id)

    async def call(
        self, *, bot_id, owner_id, method, path,
        body=None, params=None, timeout=None, enveloped=True, facts=None,
        stage,
    ) -> EngineResult:
        # Record the ATTEMPT first, then resolve. Ordering it the other way
        # made "no device was touched" tautological: a foreign bot raises in
        # resolve_bot, so `calls` was empty regardless of what the handler did,
        # and the sweep could only ever have caught a handler bypassing the
        # relay entirely. Recording first means `attempts` reflects the
        # handler's behaviour and `calls` reflects what would really reach a
        # device.
        self.attempts.append({"bot_id": bot_id, "owner_id": owner_id, "path": path})
        if facts is None:
            # The real relay's facts=None path resolves with the owner as the
            # caller — an owner-scoped internal call.
            self.resolve_bot(bot_id, owner_id, owner_id)
        self.calls.append(
            {
                "bot_id": bot_id, "owner_id": owner_id, "method": method,
                "path": path, "body": body, "params": params,
                "timeout": timeout, "enveloped": enveloped,
                "stage": stage,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    # -- helpers -----------------------------------------------------------
    def set_bot_type(self, bot_type: str) -> None:
        self.bots[(BOT, OWNER)] = BotFacts(
            bot_id=BOT, bot_type=bot_type, active_engine="openclaw", owner_id=OWNER
        )

    def add_operator(
        self, caller_id: str, *, bot_id: str = BOT, owner_id: str = OWNER
    ) -> None:
        """Admit ``caller_id`` as a member-level collaborator of the bot.

        Read by both gates — this fake's own adjudication and the seam doubles
        :func:`bind_seam_from_relay` wires — so one call still describes one
        caller's relation to one bot.
        """
        self.operators.add((bot_id, owner_id, caller_id))

    @property
    def paths(self) -> list[str]:
        """Forwards that actually reached the transport."""
        return [c["path"] for c in self.calls]


def bind_seam_from_relay(binder, relay: FakeRelay) -> None:
    """Wire ``bot_access`` so it adjudicates from the fake relay's own state.

    These operations declare ``Check(MEMBER)``, so the gate runs before every
    handler in this package and fails closed against an app that binds neither
    a bot repository nor a collaborator service. It could be given generic
    doubles, but then ``relay.add_operator(...)`` and ``relay.bots`` would
    describe one world and the gate another, and every operator test in this
    package would be asserting against a fixture rather than the surface.

    So both doubles read the relay: a bot exists to the gate exactly when it
    exists to the relay, and a caller is MEMBER exactly when
    :meth:`FakeRelay.add_operator` said so. The owner never reaches the
    collaborator double at all — ``resolve_operable_permission_level``
    short-circuits ``user_id == owner_id`` to OWNER.

    The relay still adjudicates too, deliberately. Two gates at one bar read
    one set.
    """
    from injector import InstanceProvider

    from agentclaw.community.core.bot_collaborator.models import PermissionLevel
    from agentclaw.community.core.bot_collaborator.protocols import (
        CollaboratorServiceProtocol,
    )
    from agentclaw.community.core.repository.protocols.bot import (
        BotCollabLogRepositoryProtocol,
        BotRepository,
    )

    class _Bots:
        def get_by_id_and_owner(self, bot_id: str, owner_id: str):
            if (bot_id, owner_id) not in relay.bots:
                return None
            return {"id": 1, "bot_id": bot_id, "owner_id": owner_id, "env": "dev"}

    class _Collaborators:
        def get_operable_permission_level(self, *, bot, user_id, env=None):
            key = (str(bot["bot_id"]), str(bot["owner_id"]), user_id)
            return (
                PermissionLevel.MEMBER
                if key in relay.operators
                else PermissionLevel.NONE
            )

    class _Audit:
        def insert(self, data):
            return data

    binder.bind(BotRepository, to=InstanceProvider(_Bots()))
    binder.bind(CollaboratorServiceProtocol, to=InstanceProvider(_Collaborators()))
    binder.bind(BotCollabLogRepositoryProtocol, to=InstanceProvider(_Audit()))
    binder.bind(CollaboratorLockServiceProtocol, to=InstanceProvider(SeamLocks()))


@pytest.fixture
def relay() -> FakeRelay:
    return FakeRelay()


class FakeFriendships:
    def __init__(self) -> None:
        self.allowed = False
        self.calls: list[dict[str, Any]] = []

    def is_friend(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return self.allowed


class FakeExpertChat:
    """Small recording fake for the friend-backed OpenAPI branch."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.sessions: dict[str, Any] = {"items": [], "total": 0}
        self.session: dict[str, Any] = {"id": "friend-session", "title": "Friend"}

    def add_chat_bot(self, *args, **kwargs):
        self.calls.append(("add_chat_bot", args, kwargs))
        return {}

    async def list_chat_sessions(self, *args, **kwargs):
        self.calls.append(("list_chat_sessions", args, kwargs))
        return self.sessions

    async def create_chat_session(self, *args, **kwargs):
        self.calls.append(("create_chat_session", args, kwargs))
        return {"session_key": self.session["id"]}

    async def connect_chat_session(self, *args, **kwargs):
        self.calls.append(("connect_chat_session", args, kwargs))
        return {
            "session_key": self.session["id"],
            "connection": {"ws_url": "wss://example.invalid/chat", "token": "opaque"},
        }

    async def get_owned_chat_session(self, *args, **kwargs):
        self.calls.append(("get_owned_chat_session", args, kwargs))
        return self.session

    async def update_owned_chat_session(self, *args, **kwargs):
        self.calls.append(("update_owned_chat_session", args, kwargs))
        fields = args[4] if len(args) > 4 else kwargs.get("fields", {})
        return {**self.session, **fields}

    async def delete_owned_chat_session(self, *args, **kwargs):
        self.calls.append(("delete_owned_chat_session", args, kwargs))
        return True

    async def list_owned_chat_session_messages(self, *args, **kwargs):
        self.calls.append(("list_owned_chat_session_messages", args, kwargs))
        return {
            "items": [{"id": "m1", "role": "user", "content": "hello"}],
            "total": 1,
        }

    async def clear_owned_chat_session_messages(self, *args, **kwargs):
        self.calls.append(("clear_owned_chat_session_messages", args, kwargs))
        return True

    async def set_owned_chat_session_favorite(self, *args, **kwargs):
        self.calls.append(("set_owned_chat_session_favorite", args, kwargs))
        return True


@pytest.fixture
def friendships() -> FakeFriendships:
    return FakeFriendships()


@pytest.fixture
def expert() -> FakeExpertChat:
    return FakeExpertChat()


@pytest.fixture
def make_client(relay, friendships, expert):
    """Build a TestClient hosting ``router`` with the fake relay bound.

    ``caller`` is who the request authenticates and names as its ``user_id``
    — ``OWNER`` by default. Operator tests build a second client for a
    collaborator or a stranger and address the owner's bot with
    ``params={"owner_id": OWNER}``.
    """

    def _build(
        router, principal: Any = None, caller: str = OWNER
    ) -> TestClient:
        resolved = {"user_id": caller} if principal is None else principal

        class _M(Module):
            def configure(self, binder):
                binder.bind(EngineRuntimeRelayProtocol, to=relay)
                binder.bind(HumanBotFriendshipServiceProtocol, to=friendships)
                binder.bind(ExpertChatServiceProtocol, to=expert)
                bind_seam_from_relay(binder, relay)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: resolved
        # The seam refuses before the handler, so ``@envelope_errors`` never
        # sees it and the refusal needs the application's own handler to
        # become the 404 body these tests read.
        mount_public_error_handlers(app)
        attach_injector(app, Injector([_M()]))
        return user_scoped_client(app, caller)

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
