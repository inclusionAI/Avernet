"""A run's identity survives a process restart.

The registry is a cache; ``ac_agent_swarm_identity`` is the truth. Without it a
restarted process would treat an issued identity as absent and apply again against
the same Passport key — the swarm bot id is derived from the run id, so the second
apply addresses the identity that already exists.

These tests use an in-memory repository that records writes, so they pin the flow
rather than the SQL. The ORM model's shape is pinned separately by the schema
contract test the ac_agent_swarm_* family already has.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from agentclaw.community.core.agentswarm.repository.identity.protocol import (
    SwarmIdentityRecord,
)
from agentclaw.community.plugins.community.agentswarm.run_registry import (
    SwarmIdentityRegistry,
)


class _Repo:
    """The row store, as much of it as the registry reads and writes."""

    def __init__(self) -> None:
        self.rows: dict[str, SwarmIdentityRecord] = {}
        self.destroyed: list[str] = []

    def get(self, run_id: str) -> SwarmIdentityRecord | None:
        return self.rows.get(run_id)

    def put(self, record: SwarmIdentityRecord) -> SwarmIdentityRecord:
        # Upsert on run_id: a replay must not replace an honoured agent code.
        return self.rows.setdefault(record.run_id, record)

    def update_scope(
        self, run_id: str, *, mcp_codes: Sequence[str], cli_items: Sequence[Mapping[str, Any]]
    ) -> bool:
        row = self.rows.get(run_id)
        if row is None:
            return False
        self.rows[run_id] = SwarmIdentityRecord(
            run_id=row.run_id,
            swarm_bot_id=row.swarm_bot_id,
            agent_code=row.agent_code,
            status=row.status,
            mcp_codes=tuple(mcp_codes),
            cli_items=tuple(cli_items),
            worker_count=row.worker_count,
            owner_id=row.owner_id,
        )
        return True

    def increment_workers(self, run_id: str) -> bool:
        row = self.rows.get(run_id)
        if row is None:
            return False
        self.rows[run_id] = SwarmIdentityRecord(
            run_id=row.run_id,
            swarm_bot_id=row.swarm_bot_id,
            agent_code=row.agent_code,
            status=row.status,
            mcp_codes=row.mcp_codes,
            cli_items=row.cli_items,
            worker_count=row.worker_count + 1,
            owner_id=row.owner_id,
        )
        return True

    def mark_destroyed(self, run_id: str) -> bool:
        row = self.rows.get(run_id)
        if row is None:
            return False
        self.destroyed.append(run_id)
        self.rows[run_id] = SwarmIdentityRecord(
            run_id=row.run_id,
            swarm_bot_id=row.swarm_bot_id,
            agent_code=row.agent_code,
            status="DESTROYED",
            mcp_codes=row.mcp_codes,
            cli_items=row.cli_items,
            worker_count=row.worker_count,
            owner_id=row.owner_id,
        )
        return True


class _Passport:
    def __init__(self) -> None:
        self.applies: list[dict[str, Any]] = []
        self.destroys: list[str] = []
        self._n = 0

    def apply_agent_passport(
        self, bot_id: str, owner: str, mcp_codes: list[str], **kw: Any
    ) -> Mapping[str, Any]:
        self._n += 1
        self.applies.append({"bot_id": bot_id, "mcp": list(mcp_codes)})
        return {"token": f"t{self._n}", "agent_code": f"ac-{self._n}", "status": "ISSUED"}

    def destroy_passport(self, bot_id: str, owner: str) -> None:
        self.destroys.append(bot_id)


class _Bots:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_bot(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return {"bot_id": kwargs["bot_id"], "status": "PENDING"}


def _registry(repo: _Repo | None) -> tuple[SwarmIdentityRegistry, _Passport, _Bots]:
    passport, bots = _Passport(), _Bots()
    return (
        SwarmIdentityRegistry(passport=passport, bot_service=bots, repository=repo),
        passport,
        bots,
    )


def _establish(reg: SwarmIdentityRegistry, *, run_id: str = "run-1", mcp=("calendar",)):
    return reg.establish(
        run_id=run_id,
        owner_id="owner-1",
        nick_name="owner",
        bot_name=run_id,
        bot_desc="swarm run",
        engine_type="openclaw",
        mcp_codes=mcp,
    )


# ── durability ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_an_established_identity_is_written_to_the_row() -> None:
    repo = _Repo()
    reg, _passport, _bots = _registry(repo)

    identity = _establish(reg)

    row = repo.get("run-1")
    assert row is not None
    assert row.swarm_bot_id == identity.bot_id
    assert row.agent_code == identity.agent_code
    assert row.status == "ISSUED"
    assert row.mcp_codes == ("calendar",)


@pytest.mark.unit
def test_a_restarted_process_adopts_instead_of_re_applying() -> None:
    """The whole point: no memory, no second application."""
    repo = _Repo()
    first, _p1, _b1 = _registry(repo)
    identity = _establish(first)

    # A new process: empty cache, same store.
    second, passport2, bots2 = _registry(repo)
    adopted = _establish(second)

    assert adopted.bot_id == identity.bot_id
    assert adopted.agent_code == identity.agent_code
    assert passport2.applies == [], "an issued identity must not be applied for again"
    assert bots2.calls == [], "and must not be recorded a second time"


@pytest.mark.unit
def test_a_restarted_process_resumes_the_persisted_scope() -> None:
    repo = _Repo()
    first, _p, _b = _registry(repo)
    _establish(first, mcp=("calendar",))

    second, _p2, _b2 = _registry(repo)
    resumed = _establish(second, mcp=("calendar",))

    assert "calendar" in resumed.mcp_codes


@pytest.mark.unit
def test_no_repository_still_works_in_process() -> None:
    """The table is an addition, not a prerequisite."""
    reg, passport, _bots = _registry(None)

    identity = _establish(reg)

    assert identity.bot_id
    assert len(passport.applies) == 1


@pytest.mark.unit
def test_a_widened_scope_is_persisted() -> None:
    repo = _Repo()
    reg, _passport, _bots = _registry(repo)

    _establish(reg, mcp=("calendar",))
    _establish(reg, mcp=("code-exec",))

    row = repo.get("run-1")
    assert row is not None
    assert set(row.mcp_codes) == {"calendar", "code-exec"}


@pytest.mark.unit
def test_each_worker_is_counted_on_the_row() -> None:
    repo = _Repo()
    reg, _passport, _bots = _registry(repo)
    _establish(reg)

    for i in range(3):
        reg.bind_worker(
            run_id="run-1", owner_id="owner-1", persona_key=f"p{i}",
            worker_id=f"worker-{i}", bot_uuid=f"BOT-{i}", binding_id=None,
        )

    row = repo.get("run-1")
    assert row is not None and row.worker_count == 3


# ── destruction (decision C: once, at run end) ──────────────────────────────


@pytest.mark.unit
def test_destroying_the_run_destroys_the_identity_once() -> None:
    repo = _Repo()
    reg, passport, _bots = _registry(repo)
    identity = _establish(reg)

    assert reg.destroy_identity("run-1", owner_id="owner-1") is True
    assert passport.destroys == [identity.bot_id]
    assert repo.get("run-1").status == "DESTROYED"


@pytest.mark.unit
def test_a_second_destroy_is_a_no_op() -> None:
    repo = _Repo()
    reg, passport, _bots = _registry(repo)
    _establish(reg)

    reg.destroy_identity("run-1", owner_id="owner-1")
    assert reg.destroy_identity("run-1", owner_id="owner-1") is False
    assert len(passport.destroys) == 1


@pytest.mark.unit
def test_a_restarted_process_does_not_destroy_twice() -> None:
    """The row, not memory, decides — a fresh process must still be idempotent."""
    repo = _Repo()
    first, _p1, _b1 = _registry(repo)
    _establish(first)
    first.destroy_identity("run-1", owner_id="owner-1")

    second, passport2, _b2 = _registry(repo)
    assert second.destroy_identity("run-1", owner_id="owner-1") is False
    assert passport2.destroys == []


@pytest.mark.unit
def test_releasing_a_worker_never_destroys_the_identity() -> None:
    """The D8 trap: one worker's teardown must not take the run's identity."""
    repo = _Repo()
    reg, passport, _bots = _registry(repo)
    _establish(reg)

    reg.bind_worker(
        run_id="run-1", owner_id="owner-1", persona_key="a", worker_id="worker-a", bot_uuid="BOT-a",
        binding_id=None,
    )
    # Worker teardown is the host's business; the registry offers no per-worker
    # destroy at all, and this pins that absence as the guarantee.
    assert not hasattr(reg, "destroy_worker")
    assert passport.destroys == []

@pytest.mark.unit
def test_a_failed_destroy_is_retryable() -> None:
    """A destroy that raised must leave the run destroyable, not recorded as done.

    The first version popped the registry entry before calling the service, so a
    failed call lost the identity from memory while the row still read ISSUED —
    the next attempt found nothing and the Passport leaked.
    """
    repo = _Repo()
    reg, passport, _bots = _registry(repo)
    identity = _establish(reg)

    real_destroy = passport.destroy_passport
    attempts: list[str] = []

    def failing_once(bot_id: str, owner: str) -> None:
        attempts.append(bot_id)
        if len(attempts) == 1:
            raise RuntimeError("passport service unavailable")
        real_destroy(bot_id, owner)

    passport.destroy_passport = failing_once  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="unavailable"):
        reg.destroy_identity("run-1", owner_id="owner-1")

    # The row still says ISSUED, and the second attempt succeeds.
    assert repo.get("run-1").status == "ISSUED"
    assert reg.destroy_identity("run-1", owner_id="owner-1") is True
    assert attempts == [identity.bot_id, identity.bot_id]
    assert repo.get("run-1").status == "DESTROYED"
