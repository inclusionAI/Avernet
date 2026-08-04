"""Isolation across every engine-runtime route (Track C, Task 12).

Two layers, because a 404 alone proves neither:

1. **Endpoint layer** — parametrised over all 16 routes: a bot that is not the
   caller's answers a masked 404 **and the transport is never invoked**. The
   Track A guard constrains SQL statements, not device calls, so a handler that
   forwarded first and filtered afterwards would still have reached someone
   else's device while returning a perfectly correct-looking 404.
2. **Guard layer** — the real ``BotRepository`` over SQLite with the real
   tenant guard, proving the mechanism the endpoint layer relies on: a bot in
   another tenant is simply not there to resolve.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.adapters.http.openapi_v1 import _ENGINE_RUNTIME_GROUPS
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.bot_repository import BotRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from .conftest import BOT, OWNER, FakeRelay

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

SESSION_ID = "session:abc:user:1"

#: (method, suffix under /openapi/v1/bots/{bot_id}, body) for all 16 routes.
#:
#: Bodies are per-route and valid: every request model sets ``extra="forbid"``,
#: so a generic body would 422 in validation — before the handler runs — and the
#: sweep would prove nothing about ownership.
ROUTES = [
    ("get", "/sessions", None),
    ("post", "/sessions", {"title": "T"}),
    ("get", f"/sessions/{SESSION_ID}", None),
    ("patch", f"/sessions/{SESSION_ID}", {"title": "T"}),
    ("delete", f"/sessions/{SESSION_ID}", None),
    ("get", f"/sessions/{SESSION_ID}/messages", None),
    ("delete", f"/sessions/{SESSION_ID}/messages", None),
    ("get", "/engine/status", None),
    ("get", "/engine/capabilities", None),
    ("get", "/engine/available", None),
    ("get", "/models", None),
    ("get", "/models/openai/gpt-5.3", None),
    ("get", "/approvals/mode?session_key=k", None),
    ("put", "/approvals/mode", {"session_key": "k", "mode": "never"}),
    ("get", "/approvals/modes", None),
    ("get", "/connection", None),
]


class _FakeConnections:
    """Models ``EngineConnectionService``: resolve the bot, then build.

    Faithful rather than fail-loud. The real service resolves the caller's bot
    before it touches ``DeviceService``, so the connection handler delegating to
    it — with no pre-check of its own — is correct. A double that refused to be
    called at all would fail that correct handler while proving nothing about
    the device.
    """

    def __init__(self, relay: FakeRelay) -> None:
        self._relay = relay
        #: builds that got past bot resolution — i.e. would reach a device
        self.built: list[str] = []

    def build(self, *, bot_id, owner_id):
        self._relay.resolve_bot(bot_id, owner_id)  # raises for a foreign bot
        self.built.append(bot_id)
        raise AssertionError("unreachable in these tests — resolution must fail")


@pytest.fixture
def connections(relay: FakeRelay):
    return _FakeConnections(relay)


@pytest.fixture
def client(relay: FakeRelay, connections: _FakeConnections):
    class _M(Module):
        def configure(self, binder):
            binder.bind(EngineRuntimeRelayProtocol, to=relay)
            binder.bind(EngineConnectionServiceProtocol, to=connections)

    app = FastAPI()
    for group in _ENGINE_RUNTIME_GROUPS:
        app.include_router(group)
    app.dependency_overrides[require_principal] = lambda: {"user_id": OWNER}
    attach_injector(app, Injector([_M()]))
    return TestClient(app)


def test_all_sixteen_routes_are_covered():
    """Guard the guard: a shrinking list would silently narrow this sweep."""
    assert len(ROUTES) == 16


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES, ids=lambda v: str(v))
def test_a_bot_that_is_not_the_callers_is_a_masked_404(
    client, relay, method, suffix, body
):
    kwargs = {"json": body} if body is not None else {}
    resp = getattr(client, method)(f"/openapi/v1/bots/not-my-bot{suffix}", **kwargs)

    assert resp.status_code == 404, resp.json()
    body = resp.json()
    assert body["message"] == "Not found"
    assert body["data"] is None
    # Byte-identical to a bot that does not exist at all — a caller must not be
    # able to probe for the existence of other tenants' bots.
    assert set(body) == {"code", "message", "data", "request_id"}


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES, ids=lambda v: str(v))
def test_no_device_is_reached_for_a_foreign_bot(
    client, relay, connections, method, suffix, body
):
    """Nothing reaches a device.

    **This assertion is one half of the guarantee, not all of it.** The fake
    models the real relay, which resolves the bot before forwarding — so
    ``calls`` staying empty here partly reflects that modelling. The other half
    is ``test_relay.py::test_foreign_bot_raises_before_touching_the_device``,
    which proves the same ordering in the *real* relay against a transport
    double. Together: handlers reach a device only through the relay (asserted
    below and by ``_NeverCalled``), and the relay reaches a device only after
    ownership is established.

    A handler may legitimately *attempt* ``relay.call`` without pre-checking —
    the relay is the ownership seam. Only the sessions group pre-checks, because
    it must branch on ``bot_type`` before deciding to forward at all.
    """
    kwargs = {"json": body} if body is not None else {}
    getattr(client, method)(f"/openapi/v1/bots/not-my-bot{suffix}", **kwargs)
    assert relay.calls == [], f"{method.upper()} {suffix} reached a device"
    assert connections.built == [], (
        f"{method.upper()} {suffix} built a connection for a foreign bot"
    )


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [r for r in ROUTES if r[1].startswith("/sessions")],
    ids=lambda v: str(v),
)
def test_sessions_does_not_even_attempt_a_forward_for_a_foreign_bot(
    client, relay, method, suffix, body
):
    """The sessions group resolves the bot itself, before any relay call.

    It has to: the personal-bots-only rule is decided from ``bot_type``, and
    deciding it after forwarding would mean the device had already returned
    every caller's sessions. ``attempts`` records intent, so this fails if the
    gate is ever moved after the forward.
    """
    kwargs = {"json": body} if body is not None else {}
    getattr(client, method)(f"/openapi/v1/bots/not-my-bot{suffix}", **kwargs)
    assert relay.attempts == [], (
        f"{method.upper()} {suffix} tried to forward before checking the bot"
    )


# ── the guard the endpoint layer relies on ───────────────────────────────────


class _DB:
    def __init__(self, engine):
        self._Session = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def _seed_bot() -> dict:
    return dict(
        bot_id=BOT, bot_name="N", bot_desc="d", entity_id=OWNER, entity_type="staff",
        creator_id=OWNER, owner_id=OWNER, status="ACTIVE", owner_name="O",
        active_engine="openclaw",
    )


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bots.db'}",
        connect_args={"check_same_thread": False},
    )
    for m in (BotModel, BotPublishModel, EntityDeviceBinding, BotCollaboratorModel):
        m.__table__.create(engine)
    return BotRepository(_DB(engine))


def test_a_bot_in_another_tenant_cannot_be_resolved(repo):
    """The mechanism behind every masked 404 above.

    The relay resolves the bot before it will touch a device; under another
    tenant that resolution finds nothing, so the forward never happens.
    """
    with avernet_tenant_scope(TENANT_A):
        repo.insert(_seed_bot())
        assert repo.get_by_id_and_owner(BOT, OWNER) is not None

    with avernet_tenant_scope(TENANT_B):
        assert repo.get_by_id_and_owner(BOT, OWNER) is None


def test_a_bot_owned_by_someone_else_cannot_be_resolved(repo):
    """Owner scoping, the second half — tenant isolation alone is not enough."""
    with avernet_tenant_scope(TENANT_A):
        repo.insert(_seed_bot())
        assert repo.get_by_id_and_owner(BOT, "someone-else") is None
