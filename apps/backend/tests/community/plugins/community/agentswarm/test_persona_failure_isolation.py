"""A failed persona write fails that persona, and never its siblings.

The spec's scenario: "one failing write leaves the other workers provisioned and the
failure attributed to the affected persona". Two properties, and they are independent:

1. **Attribution** — the failure names the persona and the file it was writing.
2. **Isolation and ordering** — a persona that cannot store its content never gets a
   container, so it cannot boot blank; its siblings are unaffected.

The ordering test drives the adapter's own `_create_worker`. An earlier version
re-stated the order in test code and asserted that, which passed no matter what the
adapter did — the failure mode this file exists to avoid.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest

from agentclaw.community.plugin_api.agentswarm.bot import (
    BotCreateSpec,
    IdempotencyKey,
)
from agentclaw.community.plugins.community.agentswarm.host import AvernetBotPlatform
from agentclaw.community.plugins.community.agentswarm.persona_delivery import (
    store_persona,
)
from agentclaw.community.plugins.community.agentswarm.run_registry import (
    SwarmIdentityRegistry,
)


# ── attribution ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_a_failure_names_the_persona_and_the_file() -> None:
    report = AvernetBotPlatform.persona_delivery_failure(
        worker_id="agent_swarm_abc123",
        file_type="SOUL.md",
        error=RuntimeError("store refused"),
    )

    assert "agent_swarm_abc123" in report
    assert "SOUL.md" in report
    assert "store refused" in report


@pytest.mark.unit
def test_two_personas_report_distinctly() -> None:
    """A run's report must not conflate two personas into one line."""
    a = AvernetBotPlatform.persona_delivery_failure(
        worker_id="worker-a", file_type="SOUL.md", error=RuntimeError("x")
    )
    b = AvernetBotPlatform.persona_delivery_failure(
        worker_id="worker-b", file_type="SOUL.md", error=RuntimeError("x")
    )

    assert a != b


# ── stores ──────────────────────────────────────────────────────────────────


class _Store:
    """A content store that refuses one named persona and accepts the rest.

    ``order`` records what the adapter attempted and in which order, so a test can
    assert the store was attempted *before* a container was requested rather than
    trusting a comment.
    """

    def __init__(self, refuse: str) -> None:
        self._refuse = refuse
        self.accepted: list[str] = []
        self.blobs: dict[str, bytes] = {}
        self.order: list[str] = []

    def store_produced(self, data: bytes, *, scope, origin, modifier="",
                       category=None, entry_identity=None):
        self.order.append("store")
        if self._refuse in (entry_identity or ""):
            raise RuntimeError("store refused")
        self.accepted.append(entry_identity or "")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        self.blobs.setdefault(digest, data)

        class _Rec:
            pass

        rec = _Rec()
        rec.digest = digest  # type: ignore[attr-defined]
        return rec

    def read(self, digest: str) -> bytes:
        return self.blobs[digest]


def _scope() -> Any:
    return type("S", (), {"env": "dev", "entity_id": "o", "bot_id": "swarm"})()


def _store_persona(store: _Store, persona_key: str) -> str:
    return store_persona(
        content=store, scope=_scope(), instructions=persona_key,
        engine_type="openclaw", identity_file=None, modifier="o",
        persona_key=persona_key,
    ).digest


@pytest.mark.unit
def test_one_refused_persona_does_not_stop_the_others() -> None:
    store = _Store(refuse="bad")

    with pytest.raises(RuntimeError, match="store refused"):
        _store_persona(store, "bad")

    _store_persona(store, "good-a")
    _store_persona(store, "good-b")

    assert any("good-a" in e for e in store.accepted)
    assert any("good-b" in e for e in store.accepted)
    assert not any("bad" in e for e in store.accepted)


# ── the adapter's own order ─────────────────────────────────────────────────


class _Passport:
    def apply_agent_passport(self, bot_id, owner, mcp_codes, **kw):
        return {"token": "t", "agent_code": "ac-1", "status": "ISSUED"}

    def query_token(self, bot_id, owner):
        return "t"


class _Bots:
    _device_binding_repo = None

    def create_bot(self, **kw):
        return {"bot_id": kw["bot_id"]}


class _Baas:
    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.containers: list[str] = []

    def create_bot(self, **kw):
        self._order.append("container")
        self.containers.append(kw["bot"]["bot_id"])
        return {"bot_uuid": f"BOT-{kw['bot']['bot_id']}"}


def _platform(store: _Store, baas: _Baas) -> AvernetBotPlatform:
    platform = AvernetBotPlatform.__new__(AvernetBotPlatform)
    platform._passport = _Passport()
    platform._bot_service = _Bots()
    platform._baas_service = baas
    platform._content_store = store
    platform._identity_files = None
    platform._space_context = type(
        "S", (), {"resolve_current": lambda *a, **kw: type("P", (), {"id": None})()}
    )()
    platform._run_registry = SwarmIdentityRegistry(
        passport=platform._passport, bot_service=platform._bot_service
    )
    return platform


@pytest.mark.unit
def test_a_refusal_happens_before_a_container_is_requested() -> None:
    """A persona that cannot store its content must not get a container.

    Asserts the adapter's real order: the store is attempted, and the container is
    never requested, because a worker that boots without its persona is worse than
    one that never boots.
    """
    store = _Store(refuse="failing-persona")
    baas = _Baas(store.order)
    platform = _platform(store, baas)

    with pytest.raises(RuntimeError, match="store refused"):
        platform.create_bot(
            spec=BotCreateSpec(
                name="failing-persona", engine="openclaw", instructions="x"
            ),
            idempotency_key=IdempotencyKey("run-1:agent-a:0"),
            owner_id="owner-1",
        )

    assert baas.containers == [], "a persona that could not store got a container"
    assert store.order == ["store"], (
        f"the store must be attempted before any container; got {store.order}"
    )