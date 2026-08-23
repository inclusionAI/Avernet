"""The operator matrix, swept across every engine-runtime operation.

Who may hold an operator channel is one rule (owner, or collaborator at
member level or above — ``core/engine_runtime/gate.py``), and it must hold on
all twenty swept operations identically: a route that refused a session list but
served an engine read would leak through the difference. The sweep asserts
the matrix per route — owner served, collaborator served, anyone else
answered byte-identically to a bot that does not exist.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1 import _ENGINE_RUNTIME_GROUPS
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.engine_runtime.models import (
    ConnectionResult,
    EngineResult,
    SocketInfo,
)

from .conftest import BOT, OWNER, FakeExpertChat, FakeFriendships, FakeRelay

COLLABORATOR = "u-collab"
STRANGER = "u-stranger"

SESSION_ID = "session:abc:user:1"

#: (method, path template, body) for all 20 swept routes — the same shape as the
#: tenant-isolation sweep, kept separately because the two sweeps pin
#: different halves (cross-tenant masking there, the operator matrix here)
#: and must each fail on its own terms.
ROUTES = [
    ("get", "/{bot}/sessions", None),
    ("post", "/{bot}/sessions", {"title": "T"}),
    ("get", f"/{{bot}}/sessions/{SESSION_ID}", None),
    ("patch", f"/{{bot}}/sessions/{SESSION_ID}", {"title": "T"}),
    ("delete", f"/{{bot}}/sessions/{SESSION_ID}", None),
    ("get", f"/{{bot}}/sessions/{SESSION_ID}/messages", None),
    ("delete", f"/{{bot}}/sessions/{SESSION_ID}/messages", None),
    ("get", "/{bot}/sessions/favorites", None),
    ("put", f"/{{bot}}/sessions/{SESSION_ID}/favorite", None),
    ("delete", f"/{{bot}}/sessions/{SESSION_ID}/favorite", None),
    ("get", "/{bot}/engine/status", None),
    ("get", "/{bot}/engine/capabilities", None),
    ("get", "/{bot}/engine/available", None),
    ("get", "/{bot}/models", None),
    ("get", "/{bot}/models/openai/gpt-5.3", None),
    ("get", "/{bot}/nodes", None),
    ("get", "/{bot}/approvals/mode?session_key=k", None),
    ("put", "/{bot}/approvals/mode?session_key=k", {"mode": "never"}),
    ("get", "/{bot}/approvals/modes", None),
    ("get", "/{bot}/connection", None),
]

#: One payload every handler can map: each reads only the keys it needs.
_UNIVERSAL = {
    "supported": ["approval.set"],
    "engines": [],
    "models": [],
    "sessionKey": "k",
    "mode": "approve",
    "id": SESSION_ID,
}


class _Connections:
    """A succeeding connection double that runs the fake adjudication."""

    def __init__(self, relay: FakeRelay) -> None:
        self._relay = relay
        self.builds: list[dict] = []

    def build(self, *, bot_id, owner_id, caller_id, stage) -> ConnectionResult:
        self._relay.resolve_bot(bot_id, owner_id, caller_id)
        self.builds.append(
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "caller_id": caller_id,
                "stage": stage,
            }
        )
        return ConnectionResult(
            engine="openclaw",
            expires_at="2026-08-09T00:00:00+00:00",
            sockets=[SocketInfo(kind="chat", url="wss://gw.example/ws?t=k")],
        )


@pytest.fixture
def relay() -> FakeRelay:
    fake = FakeRelay()
    fake.results = [EngineResult(data=_UNIVERSAL)]
    return fake


@pytest.fixture
def connections(relay) -> _Connections:
    return _Connections(relay)


@pytest.fixture
def make_caller(relay, connections, friendships, expert):
    """A client for ``caller`` across all engine-runtime groups."""

    def _build(caller: str):
        class _M(Module):
            def configure(self, binder):
                binder.bind(EngineRuntimeRelayProtocol, to=relay)
                binder.bind(EngineConnectionServiceProtocol, to=connections)
                binder.bind(HumanBotFriendshipServiceProtocol, to=friendships)
                binder.bind(ExpertChatServiceProtocol, to=expert)

        app = FastAPI()
        for router in _ENGINE_RUNTIME_GROUPS:
            app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: {"user_id": caller}
        attach_injector(app, Injector([_M()]))
        return user_scoped_client(app, caller)

    return _build


def _url(suffix: str, bot: str = BOT) -> str:
    return f"/openapi/v1/bots{suffix.format(bot=bot)}"


def test_all_twenty_routes_are_covered():
    """Guard the guard: a shrinking list would silently narrow this sweep."""
    assert len(ROUTES) == 20


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES, ids=lambda v: str(v))
def test_the_owner_is_served_every_route(make_caller, relay, method, suffix, body):
    """The case the expansion must not have closed: the owner, naming
    nothing extra."""
    client = make_caller(OWNER)
    if suffix.endswith("/nodes"):
        relay.results = [EngineResult(data=[])]
    kwargs = {"json": body} if body is not None else {}
    resp = getattr(client, method)(_url(suffix), **kwargs)
    assert resp.status_code in (200, 201), resp.json()


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES, ids=lambda v: str(v))
def test_a_collaborator_is_served_every_route(
    make_caller, relay, connections, method, suffix, body
):
    """One bar for the whole surface: a member-level collaborator holds the
    console on reads, writes and the socket alike, naming the owner they
    address."""
    relay.add_operator(COLLABORATOR)
    if suffix.endswith("/nodes"):
        relay.results = [EngineResult(data=[])]
    client = make_caller(COLLABORATOR)
    kwargs = {"json": body} if body is not None else {}
    kwargs["params"] = {"owner_id": OWNER}
    resp = getattr(client, method)(_url(suffix), **kwargs)
    assert resp.status_code in (200, 201), resp.json()
    # Every forward and build addresses the NAMED owner's bot — a handler
    # that regressed to passing the caller's id would resolve the wrong
    # owner's device in production while the prepaid-facts fake stayed green.
    for forwarded in relay.calls:
        assert forwarded["owner_id"] == OWNER
    for built in connections.builds:
        assert built["owner_id"] == OWNER and built["caller_id"] == COLLABORATOR


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES, ids=lambda v: str(v))
def test_a_stranger_is_the_masked_404_on_every_route(
    make_caller, relay, connections, method, suffix, body
):
    """A caller who is neither owner nor collaborator learns nothing — and no
    forward or connection build survives the refusal."""
    client = make_caller(STRANGER)
    kwargs = {"json": body} if body is not None else {}
    kwargs["params"] = {"owner_id": OWNER}
    resp = getattr(client, method)(_url(suffix), **kwargs)

    assert resp.status_code == 404, resp.json()
    payload = resp.json()
    assert payload["message"] == "Not found"
    assert payload["data"] is None
    assert set(payload) == {"code", "message", "data", "request_id"}
    assert relay.calls == [], f"{method.upper()} {suffix} reached a device"
    assert connections.builds == []


def test_refused_and_absent_answers_are_byte_identical(make_caller):
    """Two refusals, one body: naming a bot the caller may not operate and
    naming a bot that does not exist must be indistinguishable — anything
    else confirms the bot exists."""
    client = make_caller(STRANGER)
    refused = client.get(_url("/{bot}/sessions"), params={"owner_id": OWNER}).json()
    absent = client.get(_url("/{bot}/sessions", bot="no-such-bot")).json()
    # request_id differs per request by design; every other byte must not.
    refused.pop("request_id")
    absent.pop("request_id")
    assert refused == absent
