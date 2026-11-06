"""The run registry: one identity per run, inherited by every worker.

The properties worth pinning are the ones that would turn a swarm of N personas into
N identities again if they broke:

1. **One identity per run** — N personas, one application, however they interleave.
2. **Late personas widen by union, and only when they add something** — because the
   resource scope is a full replacement, an unconditional re-apply per persona would
   be N writes racing on one scope.
3. **A worker is bound to the run's identity, never its own** — and its binding row
   is readable as a worker, not as the bot's own creation or a caller instance.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Mapping

import pytest

from agentclaw.community.plugins.community.agentswarm.run_registry import (
    SwarmIdentityRegistry,
)
from agentclaw.community.plugins.community.agentswarm.swarm_identity import (
    SWARM_WORKER_APPLY_REASON_PREFIX,
    swarm_bot_id_for,
    worker_apply_reason,
)


class _Passport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    def apply_agent_passport(
        self, bot_id: str, owner_workno: str, mcp_codes: list[str], **kw: Any
    ) -> Mapping[str, Any]:
        self._n += 1
        self.calls.append(
            {
                "bot_id": bot_id,
                "owner": owner_workno,
                "mcp_codes": list(mcp_codes),
                "cli_items": list(kw.get("cli_items") or ()),
            }
        )
        return {"token": f"tok-{self._n}", "agent_code": f"ac-{self._n}", "status": "ISSUED"}


class _Bots:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_bot(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return {"bot_id": kwargs["bot_id"], "status": "PENDING"}


class _Devices:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._next = 100

    def insert_binding(self, **kwargs: Any) -> int:
        self._next += 1
        self.rows.append({**kwargs, "id": self._next})
        return self._next


def _registry() -> tuple[SwarmIdentityRegistry, _Passport, _Bots, _Devices]:
    passport, bots, devices = _Passport(), _Bots(), _Devices()
    return SwarmIdentityRegistry(passport=passport, bot_service=bots), passport, bots, devices


def _establish(reg: SwarmIdentityRegistry, *, run_id: str = "run-1", **kw: Any):
    return reg.establish(
        run_id=run_id,
        owner_id="owner-1",
        nick_name="owner",
        bot_name=run_id,
        bot_desc="swarm run",
        engine_type="openclaw",
        **kw,
    )


# ── one identity per run ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_first_persona_mints_exactly_one_identity() -> None:
    reg, passport, bots, _ = _registry()

    identity = _establish(reg, mcp_codes=("calendar",))

    assert len(passport.calls) == 1
    assert len(bots.calls) == 1
    assert identity.mcp_codes == ("calendar",)


@pytest.mark.unit
def test_later_personas_do_not_mint_a_second_identity() -> None:
    reg, passport, bots, _ = _registry()

    first = _establish(reg, mcp_codes=("calendar",))
    second = _establish(reg, mcp_codes=("calendar",))

    assert second.bot_id == first.bot_id
    assert second.agent_code == first.agent_code
    assert len(bots.calls) == 1, "a second record would be a second identity"


@pytest.mark.unit
def test_the_swarm_bot_is_derived_from_the_run_id() -> None:
    """Deterministic, so a replayed run re-adopts rather than re-mints."""
    assert swarm_bot_id_for("run-1") == swarm_bot_id_for("run-1")
    assert swarm_bot_id_for("run-1") != swarm_bot_id_for("run-2")


@pytest.mark.unit
def test_the_record_is_written_without_provisioning() -> None:
    reg, _passport, bots, _ = _registry()

    _establish(reg)

    call = bots.calls[0]
    assert call["provision"] is False, "the swarm bot must not get a container"
    assert call["ext"]["passport"]["status"] == "ISSUED"


# ── widening by union ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_a_persona_adding_a_resource_widens_the_scope() -> None:
    reg, passport, _bots, _ = _registry()

    _establish(reg, mcp_codes=("calendar",))
    widened = _establish(reg, mcp_codes=("code-exec",))

    assert len(passport.calls) == 2, "the newcomer added a resource"
    assert "calendar" in widened.mcp_codes
    assert "code-exec" in widened.mcp_codes
    assert set(passport.calls[-1]["mcp_codes"]) == {"calendar", "code-exec"}


@pytest.mark.unit
def test_a_persona_adding_nothing_does_not_re_apply() -> None:
    """The scope is a full replacement, so a redundant write is a race, not a no-op."""
    reg, passport, _bots, _ = _registry()

    _establish(reg, mcp_codes=("calendar",))
    _establish(reg, mcp_codes=("calendar",))

    assert len(passport.calls) == 1, "no new resource, so no new application"


@pytest.mark.unit
def test_concurrent_first_personas_mint_one_identity() -> None:
    """The swarm provisions personas concurrently; a race must not mint two."""
    reg, passport, bots, _ = _registry()
    results: list[Any] = []
    errors: list[BaseException] = []

    def spawn() -> None:
        try:
            results.append(_establish(reg, mcp_codes=("calendar",)))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=spawn) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len({r.bot_id for r in results}) == 1
    assert len(bots.calls) == 1, "one run writes one record"
    # The assertion that matters: an application is what costs money, consent and
    # a Passport record. Counting bot records alone let a double-apply race pass
    # unnoticed, because a second `create_bot` for the same id is idempotent while
    # a second `apply_agent_passport` is not.
    assert len(passport.calls) == 1, (
        f"a racing run applied {len(passport.calls)} times for its identity"
    )


# ── workers ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_a_worker_binds_to_the_run_identity_and_not_its_own() -> None:
    reg, _passport, _bots, devices = _registry()
    identity = _establish(reg)

    worker = reg.bind_worker(
        run_id="run-1",
        owner_id="owner-1",
        persona_key="analyst",
        worker_id="worker-analyst",
        bot_uuid="BOT-abc",
        binding_id=None,
        device_repo=devices,
        env="dev",
    )

    assert worker.bot_id == identity.bot_id
    assert worker.agent_code == identity.agent_code
    assert worker.bot_uuid == "BOT-abc"
    assert len(devices.rows) == 1


@pytest.mark.unit
def test_a_worker_binding_is_readable_as_a_worker() -> None:
    reg, _passport, _bots, devices = _registry()
    identity = _establish(reg)

    reg.bind_worker(
        run_id="run-1",
        owner_id="owner-1",
        persona_key="analyst",
        worker_id="worker-analyst",
        bot_uuid="BOT-abc",
        binding_id=None,
        device_repo=devices,
        env="dev",
    )

    row = devices.rows[0]
    assert row["apply_reason"] == worker_apply_reason(identity.bot_id)
    assert row["apply_reason"].startswith(SWARM_WORKER_APPLY_REASON_PREFIX)
    assert not row["apply_reason"].startswith("Create bot:")
    assert not row["apply_reason"].startswith("caller_instance:")
    assert row["device_props"]["swarm_persona"] == "analyst"


@pytest.mark.unit
def test_binding_a_worker_for_an_unknown_run_is_refused() -> None:
    reg, _passport, _bots, _devices = _registry()

    with pytest.raises(RuntimeError, match="no identity"):
        reg.bind_worker(
            run_id="never-established",
            owner_id="owner-1",
            persona_key="analyst",
            worker_id="worker-analyst",
            bot_uuid="BOT-abc",
            binding_id=None,
        )


@pytest.mark.unit
def test_forgetting_a_run_releases_only_that_run() -> None:
    reg, _passport, _bots, _ = _registry()
    _establish(reg, run_id="run-1")
    _establish(reg, run_id="run-2")

    reg.forget("run-1")

    assert reg.identity_for("run-1") is None
    assert reg.identity_for("run-2") is not None

@pytest.mark.unit
def test_a_slow_apply_does_not_let_two_personas_both_mint() -> None:
    """The race the old test could not see.

    A real apply is a network call. When the fake answered instantly, two threads
    never overlapped and the test passed while the code released the lock between
    the cache check and the apply. Slowing the apply is what exposes it: if the
    lock does not cover the mint, both threads observe "nothing cached" and both
    apply.
    """
    reg, passport, bots, _ = _registry()

    real_apply = passport.apply_agent_passport

    def slow_apply(*a: Any, **kw: Any):
        # Long enough that every racing thread reaches `establish` while this
        # one is still inside it — which is exactly the window the bug lived in.
        time.sleep(0.05)
        return real_apply(*a, **kw)

    passport.apply_agent_passport = slow_apply  # type: ignore[method-assign]

    results: list[Any] = []
    errors: list[BaseException] = []

    def spawn() -> None:
        try:
            results.append(_establish(reg, mcp_codes=("calendar",)))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=spawn) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(passport.calls) == 1, (
        f"a racing run applied {len(passport.calls)} times for its identity"
    )
    assert len(bots.calls) == 1
    assert len({r.bot_id for r in results}) == 1
