"""The host provisions a run's personas as workers under one identity.

This exercises the wired path — ``create_bot`` with shared identity enabled — rather
than the registry in isolation, because the interesting failure mode is at the seam:
the idempotency key is the only thing that says which run a spawn belongs to, and a
wrong reading of it would silently mint one identity per persona.

The old per-attempt path stays reachable when the flag is off (group 6 removes it),
and that is asserted too: a flag that did nothing would make this whole change a
no-op.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from agentclaw.community.plugins.community.agentswarm.host import (
    AvernetBotPlatform,
    _mcp_codes_from_document,
    _swarm_bot_name,
    bot_id_for,
    run_id_from_idempotency_key,
)
from agentclaw.community.plugins.community.agentswarm.run_registry import (
    SwarmIdentityRegistry,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotCreateSpec, IdempotencyKey


class _Passport:
    """Answers both legs the adapter uses: apply, and the token read-back.

    The token is what a worker is provisioned with, so a stand-in that only answered
    the apply would let a worker get an empty credential — the bug this pins.
    """

    def __init__(self, token: str | None = "tok") -> None:
        self.calls: list[dict[str, Any]] = []
        self.token_calls: list[tuple[str, str]] = []
        self._token = token

    def query_token(self, bot_id: str, owner_workno: str) -> str | None:
        self.token_calls.append((bot_id, owner_workno))
        return self._token

    def apply_agent_passport(
        self, bot_id: str, owner: str, mcp_codes: list[str], **kw: Any
    ) -> Mapping[str, Any]:
        self.calls.append({"bot_id": bot_id, "mcp": list(mcp_codes)})
        return {"token": "tok", "agent_code": f"ac-{bot_id}", "status": "ISSUED"}


class _Bots:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._device_binding_repo = None

    def create_bot(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return {"bot_id": kwargs["bot_id"], "status": "PENDING"}


class _Devices:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert_binding(self, **kwargs: Any) -> int:
        self.rows.append(kwargs)
        return len(self.rows)


class _Baas:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create_bot(self, **kwargs: Any) -> Mapping[str, Any]:
        self.created.append(kwargs)
        return {"bot_uuid": f"BOT-{len(self.created)}"}


def _platform() -> tuple[AvernetBotPlatform, _Passport, _Bots, _Baas, _Devices]:
    passport, bots, baas, devices = _Passport(), _Bots(), _Baas(), _Devices()
    bots._device_binding_repo = devices
    platform = AvernetBotPlatform.__new__(AvernetBotPlatform)
    platform._passport = passport
    platform._bot_service = bots
    platform._baas_service = baas
    platform._space_context = SimpleNamespace(
        resolve_current=lambda **kw: SimpleNamespace(id=None)
    )
    platform._run_registry = SwarmIdentityRegistry(passport=passport, bot_service=bots)
    return platform, passport, bots, baas, devices


def _spec(name: str = "analyst", *, manifest: str = "") -> BotCreateSpec:
    return BotCreateSpec(name=name, description="d", engine="openclaw", config_manifest=manifest)


# ── the key carries the run ─────────────────────────────────────────────────


@pytest.mark.unit
def test_run_id_is_read_from_the_three_segment_key() -> None:
    assert run_id_from_idempotency_key("run-1:agent-1:0") == "run-1"


@pytest.mark.unit
def test_run_id_may_itself_contain_colons() -> None:
    """A naive first-segment split would mint an identity per agent."""
    assert run_id_from_idempotency_key("a:b:c:agent:2") == "a:b:c"


@pytest.mark.unit
def test_an_unexpected_key_shape_is_treated_as_its_own_run() -> None:
    assert run_id_from_idempotency_key("lonely") == "lonely"


# ── one identity, N workers ─────────────────────────────────────────────────


@pytest.mark.unit
def test_n_personas_in_one_run_apply_once_and_provision_n_containers() -> None:
    platform, passport, _bots, baas, devices = _platform()

    for attempt in range(3):
        platform.create_bot(
            spec=_spec(f"persona-{attempt}"),
            idempotency_key=IdempotencyKey(f"run-1:agent-{attempt}:0"),
            owner_id="owner-1",
        )

    assert len(passport.calls) == 1, "one identity per run"
    assert len(baas.created) == 3, "one container per persona"
    assert len(devices.rows) == 3, "one binding per worker"


@pytest.mark.unit
def test_every_worker_binding_names_the_swarm_bot() -> None:
    platform, passport, _bots, _baas, devices = _platform()

    platform.create_bot(
        spec=_spec("a"),
        idempotency_key=IdempotencyKey("run-1:agent-a:0"),
        owner_id="owner-1",
    )
    swarm_bot_id = passport.calls[0]["bot_id"]

    assert devices.rows[0]["apply_reason"] == f"agentswarm_worker:{swarm_bot_id}"


@pytest.mark.unit
def test_workers_in_different_runs_get_different_identities() -> None:
    platform, passport, _bots, _baas, _devices = _platform()

    platform.create_bot(
        spec=_spec("a"),
        idempotency_key=IdempotencyKey("run-1:agent-a:0"),
        owner_id="owner-1",
    )
    platform.create_bot(
        spec=_spec("b"),
        idempotency_key=IdempotencyKey("run-2:agent-b:0"),
        owner_id="owner-1",
    )

    assert len(passport.calls) == 2
    assert passport.calls[0]["bot_id"] != passport.calls[1]["bot_id"]


@pytest.mark.unit
def test_the_swarm_bot_itself_is_never_provisioned() -> None:
    """N personas, N containers — the identity must not add an N+1st."""
    platform, _passport, bots, baas, _devices = _platform()

    platform.create_bot(
        spec=_spec("a"),
        idempotency_key=IdempotencyKey("run-1:agent-a:0"),
        owner_id="owner-1",
    )

    assert len(baas.created) == 1, "exactly one container for one persona"
    assert bots.calls[0]["provision"] is False


@pytest.mark.unit
def test_a_persona_without_an_owner_is_refused() -> None:
    platform, passport, _bots, _baas, _devices = _platform()

    with pytest.raises(RuntimeError, match="authenticated owner"):
        platform.create_bot(
            spec=_spec("a"),
            idempotency_key=IdempotencyKey("run-1:agent-a:0"),
            owner_id="",
        )

    assert passport.calls == []


# ── persona-declared resources ──────────────────────────────────────────────


@pytest.mark.unit
def test_mcp_codes_come_from_the_personas_manifest() -> None:
    document = """
schema_version: 1
manifest:
  mcp:
    - server_code: calendar
    - server_code: code-exec
"""
    assert _mcp_codes_from_document(document) == ("calendar", "code-exec")


@pytest.mark.unit
def test_a_persona_without_a_manifest_declares_nothing() -> None:
    assert _mcp_codes_from_document("") == ()


@pytest.mark.unit
def test_an_unparsable_manifest_declares_nothing_rather_than_raising() -> None:
    """Grammar is the create-time preflight's job; this must not be a second validator."""
    assert _mcp_codes_from_document("::: not yaml :::") == ()


@pytest.mark.unit
def test_swarm_bot_name_fits_the_platform_limit() -> None:
    name = _swarm_bot_name("a-very-long-run-id-" * 10)
    assert len(name) <= 32

@pytest.mark.unit
def test_the_worker_id_is_what_discriminates_a_binding() -> None:
    """Every later call carries only `bot_id`, so that id must find the binding.

    `applied_by` is the worker id — not the persona name, not the owner — because
    status, chat and delete all reach the platform with the id `create_bot`
    returned and nothing else.
    """
    platform, _passport, _bots, _baas, devices = _platform()

    worker_id = platform.create_bot(
        spec=_spec("analyst"),
        idempotency_key=IdempotencyKey("run-1:agent-a:0"),
        owner_id="owner-1",
    )

    assert devices.rows[0]["applied_by"] == str(worker_id)
    assert devices.rows[0]["entity_id"] == "owner-1", (
        "the owner scopes the binding under the swarm bot"
    )


@pytest.mark.unit
def test_a_worker_is_provisioned_with_the_runs_token() -> None:
    """The worker presents the run's credential, not an empty one.

    An empty token provisions a container that can reach nothing, so this asserts
    the value the platform actually receives — the bug that shipped past every test
    that only counted containers.
    """
    platform, passport, _bots, baas, _devices = _platform()
    passport._token = "run-token-xyz"

    platform.create_bot(
        spec=_spec("analyst"),
        idempotency_key=IdempotencyKey("run-1:agent-a:0"),
        owner_id="owner-1",
    )

    assert baas.created[0]["agent_pass_token"] == "run-token-xyz"
    # And it was read for the run's identity, not for the worker.
    assert passport.token_calls[0][0] == passport.calls[0]["bot_id"]


@pytest.mark.unit
def test_a_run_without_a_token_refuses_to_provision_a_worker() -> None:
    """Loudly, rather than shipping a blank credential."""
    platform, passport, _bots, baas, _devices = _platform()
    passport._token = None

    with pytest.raises(RuntimeError, match="no token"):
        platform.create_bot(
            spec=_spec("analyst"),
            idempotency_key=IdempotencyKey("run-1:agent-a:0"),
            owner_id="owner-1",
        )

    assert baas.created == [], "a worker was provisioned with no credential"
