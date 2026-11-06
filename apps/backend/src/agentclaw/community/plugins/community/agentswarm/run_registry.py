"""The run registry and the worker provisioning loop.

Pairs with :mod:`swarm_identity`: that module owns what an identity *is*, this one
owns *who has one* and how a worker container is created under it.

Two levels of state, deliberately separated:

* the **run identity** — one per run, established on the first persona and reused
  by every later one;
* the **worker** — one container per persona attempt, bound to the run's identity.

The registry is a cache in front of a durable row. The row is what makes a restart
resume instead of re-mint: the swarm bot id is derived from the run id, so a process
that consulted only memory would treat an issued identity as absent and apply for it
a second time against the same Passport key.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from agentclaw.community.core.agentswarm.repository.identity.protocol import (
    SwarmIdentityRecord,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.community.agentswarm.swarm_identity import (
    SwarmIdentity,
    _RunScope,
    swarm_bot_id_for,
    worker_apply_reason,
)

logger = get_logger("agentswarm")


class _Passport(Protocol):
    def apply_agent_passport(
        self, bot_id: str, owner_workno: str, mcp_codes: list[str], **kw: Any
    ) -> Mapping[str, Any] | None: ...


class _Bots(Protocol):
    def create_bot(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class WorkerProvisioned:
    """One persona's container, bound to its run's identity."""

    bot_id: str
    bot_uuid: str
    binding_id: int | None
    agent_code: str


class SwarmIdentityRegistry:
    """Per-run identities, established once and inherited by every worker.

    Thread-safe: the swarm provisions personas concurrently, and a run must not
    mint two identities because two personas raced.

    The in-process map is a cache, not the source of truth. The durable row is what
    makes a restart resume instead of re-apply: the run-derived bot id is stable, so
    a restarted process that consulted only memory would treat an issued identity as
    absent and apply for it again.
    """

    def __init__(
        self,
        *,
        passport: _Passport,
        bot_service: _Bots,
        repository: Any | None = None,
    ) -> None:
        self._passport = passport
        self._bot_service = bot_service
        self._repository = repository
        self._identities: dict[str, SwarmIdentity] = {}
        self._scopes: dict[str, _RunScope] = {}
        self._lock = threading.Lock()

    # ── identity ──────────────────────────────────────────────────────────

    def identity_for(self, run_id: str) -> SwarmIdentity | None:
        with self._lock:
            cached = self._identities.get(run_id)
        if cached is not None:
            return cached
        return self._from_repository(run_id)

    def _from_repository(self, run_id: str) -> SwarmIdentity | None:
        """Adopt the stored identity for a run, if it has one.

        This is the restart path. A stored row means the run already owns an
        AgentPass, so the caller must not apply for another — the run-derived bot id
        would address the same Passport key, and re-applying on an issued identity
        is at best wasted work.
        """
        if self._repository is None:
            return None
        record = self._repository.get(run_id)
        if record is None or record.status != "ISSUED":
            return None
        identity = SwarmIdentity(
            bot_id=record.swarm_bot_id,
            agent_code=record.agent_code,
            mcp_codes=tuple(record.mcp_codes),
            cli_items=tuple(record.cli_items),
        )
        scope = _RunScope()
        scope.merge(record.mcp_codes, record.cli_items)
        with self._lock:
            self._identities.setdefault(run_id, identity)
            self._scopes.setdefault(run_id, scope)
        return identity

    def establish(
        self,
        *,
        run_id: str,
        owner_id: str,
        nick_name: str,
        bot_name: str,
        bot_desc: str,
        engine_type: str,
        mcp_codes: Sequence[str] = (),
        cli_items: Sequence[Mapping[str, Any]] = (),
        bot_type: str = "personal",
        entity_id: str | None = None,
        space_id: int | None = None,
        space_quota: bool = False,
    ) -> SwarmIdentity:
        """Return the run's identity, minting it on first use.

        Mints at most one identity per run: the stored row is consulted first, so a
        restarted process adopts an issued identity instead of applying for a second.
        The lock is held across the check-and-mint, so a concurrent second persona
        waits and then sees the identity rather than minting its own.
        """
        adopted = self.identity_for(run_id)
        if adopted is not None:
            return self._widen(
                run_id=run_id,
                owner_id=owner_id,
                mcp_codes=mcp_codes,
                cli_items=cli_items,
            )

        # The lock is held across the **whole** mint — the apply, the record write
        # and the publish — not just the cache check. Releasing it between the
        # check and the apply lets two personas race: each finds nothing cached,
        # each applies for the same run's identity, and the service is asked for a
        # credential twice.
        #
        # The calls inside it (an apply, then a create that itself takes a quota
        # lease and registers with BCN) are slow, and holding a lock across them
        # means a sibling persona waits for them. That is deliberate and bounded:
        # this happens once per run, the run cannot proceed without an identity, and
        # the alternative — releasing the lock — costs the run a duplicate
        # authorization, which is the expensive thing. Nothing inside re-enters this
        # registry, so there is no self-deadlock.
        with self._lock:
            existing = self._identities.get(run_id)
            if existing is not None:
                self._widen_locked(run_id, owner_id, mcp_codes, cli_items)
                scope = self._scopes[run_id]
                return SwarmIdentity(
                    bot_id=existing.bot_id,
                    agent_code=existing.agent_code,
                    mcp_codes=tuple(scope.mcp_codes),
                    cli_items=tuple(scope.cli_items.values()),
                )

            bot_id = swarm_bot_id_for(run_id)
            scope = _RunScope()
            _grew, merged_mcp, merged_cli = scope.merge(mcp_codes, cli_items)

            applied = self._passport.apply_agent_passport(
                bot_id,
                owner_id,
                list(merged_mcp),
                cli_items=list(merged_cli),
            )
            if not applied:
                raise RuntimeError(f"Passport returned nothing for swarm bot {bot_id}")
            agent_code = applied.get("agent_code")
            token = applied.get("token")
            if not agent_code:
                raise RuntimeError(
                    f"Passport issued no agent code for swarm bot {bot_id}"
                )
            if not token:
                raise RuntimeError(
                    f"Passport issued no token for swarm bot {bot_id}"
                )

            # Record without a container: the swarm bot holds the identity, and
            # every container in the run belongs to a persona (design D7).
            self._bot_service.create_bot(
                user_id=owner_id,
                nick_name=nick_name,
                bot_name=bot_name,
                bot_desc=bot_desc,
                bot_id=bot_id,
                engine_type=engine_type,
                bot_type=bot_type,
                entity_id=entity_id,
                ext={"passport": {"agent_code": str(agent_code), "status": "ISSUED"}},
                space_id=space_id,
                space_quota=space_quota,
                provision=False,
            )

            identity = SwarmIdentity(
                bot_id=bot_id,
                agent_code=str(agent_code),
                mcp_codes=merged_mcp,
                cli_items=merged_cli,
            )
            self._identities[run_id] = identity
            self._scopes[run_id] = scope
            # Persisted **inside** the mint, not after it. The row is what stops a
            # restarted process re-applying for an identity the service already
            # issued; a write that lands after the lock is released is a write that
            # a crash can lose while the identity is already live.
            self._persist(run_id, identity, owner_id=owner_id)

        self._persist(run_id, identity, owner_id=owner_id)
        return identity

    def _persist(self, run_id: str, identity: SwarmIdentity, *, owner_id: str) -> None:
        """Record the identity durably, so a restart resumes rather than re-applies.

        Best-effort on failure: the identity exists in the service either way, and a
        storage fault must not undo a successful application. A missing row costs a
        re-apply on the next restart, which is the behaviour this table exists to
        remove — so the failure is logged rather than swallowed.
        """
        if self._repository is None:
            return
        try:
            self._repository.put(
                SwarmIdentityRecord(
                    run_id=run_id,
                    swarm_bot_id=identity.bot_id,
                    agent_code=identity.agent_code,
                    status="ISSUED",
                    mcp_codes=identity.mcp_codes,
                    cli_items=identity.cli_items,
                    worker_count=0,
                    owner_id=owner_id,
                )
            )
        except NameError:
            # A programming error must not look like a storage fault: a swallowed
            # NameError here silently disabled persistence entirely.
            raise
        except Exception as exc:  # noqa: BLE001 - durability is best-effort here
            logger.warning(
                "[agentswarm][shared-identity] identity %s not persisted: %s",
                identity.bot_id, exc,
            )

    def _widen(
        self,
        *,
        run_id: str,
        owner_id: str,
        mcp_codes: Sequence[str],
        cli_items: Sequence[Mapping[str, Any]],
    ) -> SwarmIdentity:
        """Union a later persona in under the lock, and answer the run's identity."""
        with self._lock:
            self._widen_locked(run_id, owner_id, mcp_codes, cli_items)
            scope = self._scopes[run_id]
            identity = self._identities[run_id]
            return SwarmIdentity(
                bot_id=identity.bot_id,
                agent_code=identity.agent_code,
                mcp_codes=tuple(scope.mcp_codes),
                cli_items=tuple(scope.cli_items.values()),
            )

    def _widen_locked(
        self,
        run_id: str,
        owner_id: str,
        mcp_codes: Sequence[str],
        cli_items: Sequence[Mapping[str, Any]],
    ) -> None:
        """Union a later persona's resources into the run's scope.

        The scope is a full replacement at the service, so it is re-submitted only
        when the newcomer actually adds something. A persona whose resources are
        already covered costs nothing.
        """
        scope = self._scopes.setdefault(run_id, _RunScope())
        grew, merged_mcp, merged_cli = scope.merge(mcp_codes, cli_items)
        if not grew:
            return
        identity = self._identities[run_id]
        self._passport.apply_agent_passport(
            identity.bot_id,
            owner_id,
            list(merged_mcp),
            cli_items=list(merged_cli),
        )
        if self._repository is not None:
            try:
                self._repository.update_scope(
                    run_id, mcp_codes=merged_mcp, cli_items=merged_cli
                )
            except Exception as exc:  # noqa: BLE001 - durability is best-effort
                logger.warning(
                    "[agentswarm][shared-identity] scope for run %s not persisted: %s",
                    run_id, exc,
                )

    # ── workers ───────────────────────────────────────────────────────────

    def bind_worker(
        self,
        *,
        run_id: str,
        owner_id: str,
        persona_key: str,
        worker_id: str,
        bot_uuid: str,
        binding_id: int | None,
        device_repo: Any | None = None,
        entity_id: str | None = None,
        env: str | None = None,
    ) -> WorkerProvisioned:
        """Bind one persona's container to the run's identity.

        The binding row is written when a device repository is supplied; the reason
        carries :data:`SWARM_WORKER_APPLY_REASON_PREFIX` so an operator can tell a
        worker from the bot's own creation binding.
        """
        identity = self.identity_for(run_id)
        if identity is None:
            raise RuntimeError(f"run {run_id} has no identity to bind a worker to")

        if device_repo is not None and binding_id is None:
            binding_id = device_repo.insert_binding(
                entity_id=entity_id or owner_id,
                entity_type="staff",
                device_id=bot_uuid,
                device_provider="baas",
                env=env or "",
                device_props={"swarm_persona": persona_key},
                # ACTIVE, not PENDING: the worker is only ever bound after its
                # container is up, so the binding records a fact rather than a
                # hope. A PENDING row that nothing advances would tell `bot_status`
                # to wait forever on a container that already exists.
                status="ACTIVE",
                apply_reason=worker_apply_reason(identity.bot_id),
                # The **worker id** is the instance discriminator, and it is also
                # the id the caller hands back on every later call (status, chat,
                # delete) — so a lookup by `applied_by=worker_id` needs nothing but
                # the id the seam already carries. `entity_id` stays the run's owner,
                # which is what makes the row resolve under the swarm bot.
                applied_by=worker_id,
            )

        self._count_worker(run_id)
        return WorkerProvisioned(
            bot_id=identity.bot_id,
            bot_uuid=bot_uuid,
            binding_id=binding_id,
            agent_code=identity.agent_code,
        )

    def _count_worker(self, run_id: str) -> None:
        """Record that the run bound one more worker. Best-effort, like the rest."""
        if self._repository is None:
            return
        try:
            self._repository.increment_workers(run_id)
        except Exception as exc:  # noqa: BLE001 - durability is best-effort
            logger.warning(
                "[agentswarm][shared-identity] worker count for run %s not "
                "persisted: %s", run_id, exc,
            )

    def forget(self, run_id: str) -> None:
        """Drop the run's in-process state at teardown.

        The identity itself is destroyed by the run's teardown path, once; this
        only releases the registry entry so a later run does not inherit it.
        """
        with self._lock:
            self._identities.pop(run_id, None)
            self._scopes.pop(run_id, None)

    def destroy_identity(self, run_id: str, *, owner_id: str) -> bool:
        """Destroy the run's AgentPass exactly once, at the end of the run (D8).

        Idempotent, and idempotent **across processes**: the decision is made from
        the stored row, so a restarted process — which has no memory of the run —
        still knows there is nothing left to destroy.

        Failure is retryable. The in-memory entry is dropped only *after* the
        destroy succeeds, so a failed call leaves the run destroyable rather than
        recording a success that never happened.
        """
        with self._lock:
            identity = self._identities.get(run_id)

        if identity is None and self._repository is not None:
            record = self._repository.get(run_id)
            if record is not None and record.status != "DESTROYED":
                identity = SwarmIdentity(
                    bot_id=record.swarm_bot_id, agent_code=record.agent_code
                )
        if identity is None:
            return False

        destroy = getattr(self._passport, "destroy_passport", None)
        if destroy is None:
            return False

        # The destructive call happens before any state is dropped: if it raises,
        # the registry still holds the identity and the row still reads ISSUED, so
        # the next reconciliation attempts it again.
        destroy(identity.bot_id, owner_id)

        with self._lock:
            self._identities.pop(run_id, None)
            self._scopes.pop(run_id, None)
        if self._repository is not None:
            try:
                self._repository.mark_destroyed(run_id)
            except Exception as exc:  # noqa: BLE001 - durability is best-effort
                logger.warning(
                    "[agentswarm][shared-identity] run %s destruction not "
                    "recorded: %s", run_id, exc,
                )
        return True
