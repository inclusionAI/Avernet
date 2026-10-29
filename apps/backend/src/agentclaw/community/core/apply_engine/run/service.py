"""Applying a bot's configuration manifest (issue #1472).

Owns the lifecycle around the orchestrator: the lock, the pre-apply check,
the record's two writes, and handing the work to the queue that runs it.

**An apply starts from a state reference, not a document.** ``start_apply``
takes a :class:`~...apply_engine.compiler.StateRef` — a producer's kind and
its reference within it — looks the kind's compiler up in the map it was
built with, and the worker compiles then applies. This
service reads no document and names no construct: what a reference means is
its compiler's business, and the orchestrator applies whatever came out.

**Apply is started, not awaited.** :meth:`start_apply` answers as soon as it can
answer — with an ``apply_id`` — and the orchestrator runs elsewhere. Applying is
device I/O today and network fetching from W5, so a caller holding an HTTP
connection across it would be a caller timing out. It is also the shape W13's
``APPLYING`` poll state needs: a state you can observe only exists if the work is
something you start and then ask about.

**Where "elsewhere" is changed with W13, and the difference is durability.** W4
ran the orchestrator on a daemon thread; it now runs as a ``config_manifest.apply``
task (see ``apply/apply_task.py``). A thread dies with its pod and takes the apply
with it, which stopped being merely untidy once creation began to *depend* on an
apply completing — the startup-script row must exist before the start command is
composed. A task is re-claimed after its lease expires and finishes.

Nothing above the enqueue moved, and that is deliberate: the lock, the
re-validation and the ``RUNNING`` row all still happen on the caller's thread, so
every observable behaviour of ``POST …/config-manifest/apply`` is what it was.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional


from agentclaw.community.core.apply_engine.compiler import (
    CompileRefused,
    DesiredStateCompiler,
    StateCompilers,
    StateRef,
    state_ref_from_payload,
)
from agentclaw.community.core.apply_engine.carry_forward import (
    carry_forward,
)
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.delivery import (
    DeliveryStrategy,
    DeliveryStrategyFactory,
)
from agentclaw.community.core.apply_engine.apply_task import (
    APPLY_TASK_DEADLINE_SECONDS,
    APPLY_TASK_TYPE,
    PHASE_KEY,
    build_apply_task_payload,
    phase_from_payload,
)
from agentclaw.community.core.apply_engine.triggers import require_phase_matches_trigger
from agentclaw.community.core.apply_engine.order import ApplyPhase
from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
from agentclaw.community.core.apply_engine.outcomes import (
    ApplyConstruct,
    ApplyReport,
    ApplyStatus,
)
from agentclaw.community.core.apply_engine.registry import (
    build_materialisers,
)
from agentclaw.community.core.apply_engine.run.termination import (
    parse_started_at,
    record_engine_failure,
    terminate_on_launch_failure,
    terminate_unstartable,
)
from agentclaw.community.core.apply_engine.run.report_codec import (
    report_from_payload,
)
from agentclaw.community.core.apply_engine.run.lock_reaper import (
    APPLY_LOCK_TTL_SECONDS,
    is_abandoned,
    reap_stale_lock,
)
from agentclaw.community.core.apply_engine.run.service_protocol import (
    ApplyAccepted,
    ApplyRunServiceProtocol,
    ApplyInProgressError,
)
from agentclaw.community.core.apply_engine.capabilities import CapabilityResolver
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:  # pragma: no cover - import-time cycle, see below
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )

logger = get_logger()



class ManifestApplyBotMissingError(RuntimeError):
    """The bot a queued apply targets no longer exists."""

    def __init__(self, bot_id: str) -> None:
        super().__init__(f"bot {bot_id} not found for a queued apply")



def _engine_and_bot_type(
    bot: Optional[dict],
    engine_type: Optional[str],
    bot_type: Optional[str],
) -> tuple[str, str]:
    """The two values every capability question is answered from.

    One helper because the record and record-free paths must not drift: a bot
    whose engine is read one way here and another way there would validate
    against one set of capabilities and apply against a different one.
    """
    if bot is not None:
        return str(bot.get("active_engine") or ""), str(bot.get("bot_type") or "")
    return str(engine_type or ""), str(bot_type or "")

class ApplyRunService(ApplyRunServiceProtocol):
    """Start applies, and read what they did."""

    # Deliberately **not** ``@inject``: ``task_queue_provider`` is annotated with a
    # ``TYPE_CHECKING``-only name (the queue module imports the DI container at
    # module scope, so it cannot be imported here), and ``@inject`` would make the
    # injector resolve these hints at construction and fail on it. The DI module
    # builds this service with an explicit provider instead.
    def __init__(
        self,
        capabilities: CapabilityResolver,
        apply_repository: BotConfigManifestApplyRepositoryProtocol,
        lock_repository: BotConfigManifestApplyLockRepositoryProtocol,
        task_queue_provider: Callable[[], "TaskQueueService"],
        bot_repository: BotRepository,
        *,
        delivery_strategies: DeliveryStrategyFactory,
        compilers: StateCompilers,
    ) -> None:
        # Read for capabilities only (``_context``): what a document may be is
        # the Manifest compiler's question now, asked when it compiles.
        self._capabilities = capabilities
        self._applies = apply_repository
        self._locks = lock_repository
        # The queue that now runs the work, and the reader that rebuilds what the
        # payload deliberately does not carry.
        #
        # The queue is **lazy, and must stay that way**:
        # ``task_queue_service`` imports ``community.di`` at module scope, which
        # pulls the whole container graph, which reaches back here — importing it
        # eagerly from this module is a circular import, not a style preference.
        # The repository has no such problem and is injected directly.
        self._task_queue_provider = task_queue_provider
        self._bots = bot_repository
        # W8: the delivery seam, injected already assembled. This service
        # composes no strategy and knows no engine family: which port bundle,
        # phase table and closing step a bot applies through was decided in the
        # composition root, and what arrives here is a lookup keyed by the
        # bot's engine.
        #
        # Required, and the reason it is not defaulted has history: an earlier
        # default of "every bot is ARCA" routed a teclaw apply through the
        # container ports silently. A service that cannot be built without the
        # seam cannot be built wrong.
        self._strategies = delivery_strategies
        # One compiler per state kind, contributed by each
        # compiler's own module. Required for the same reason the seam is: a
        # service with no compiler could start nothing.
        self._compilers = dict(compilers)

    # ── starting ────────────────────────────────────────────────────────────

    def start_apply(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: Optional[dict] = None,
        owner_id: str,
        actor_id: str,
        audit_actor: Optional[str] = None,
        state: StateRef,
        trigger: str = "explicit",
        phase: ApplyPhase | None = None,
        engine_type: Optional[str] = None,
        bot_type: Optional[str] = None,
        carry_from_apply_id: Optional[str] = None,
    ) -> ApplyAccepted:
        """Lock, check, record ``RUNNING``, enqueue the work, return the id.

        The order is the contract. Every refusal happens **before** an id exists,
        so a caller never holds a handle to an apply that never ran.

        ``state`` names what to apply, and its type picks the compiler: an
        unregistered type is a ``ValueError`` before the lock, like a
        phase/trigger mismatch — a programming error at the call site, not a
        state of the bot.

        **What runs where, and why the split is here.** Everything above the
        enqueue is synchronous on the caller's thread — the lock (so a concurrent
        apply still raises ``ApplyInProgressError``), the compiler's
        check (so a stored document that no longer validates still raises *to
        the caller*), the ``apply_id`` and the ``RUNNING`` row. Only the work
        is handed off — the compile included, because it fetches. That
        is what keeps ``POST …/config-manifest/apply``'s contract identical to
        what W4 shipped while the executor underneath it changed from a daemon
        thread to a durable task.

        **The lock spans the handoff**: acquired here, released by the handler
        using the token in the payload. A task that never runs — a deployment
        with the worker disabled — leaves a lock the TTL reaps, which is the same
        outcome a dead thread had.

        ``bot`` is optional. It is ``None`` on exactly one path: W13 applies the
        pre-container phase **before** the bot record is created, and supplies
        ``engine_type`` / ``bot_type`` from the creation request instead.

        ``actor_id`` and ``audit_actor`` are two different things and must not be
        collapsed. ``actor_id`` is the **principal** every downstream
        authorization check is made against — ``can_manage_bot``,
        ``check_mcp_permission_detail``. ``audit_actor`` is a *label* for the
        record's actor column, and for an application caller it is a synthetic
        string (``app:<id>:on-behalf-of:<user>``) that is deliberately not a
        principal. Passing the label as the principal denied every application
        caller: the activation service compared that string against owner and
        collaborator rows and found nobody. It defaults to ``actor_id`` so a
        caller with nothing to distinguish keeps the obvious behaviour.
        """
        require_phase_matches_trigger(trigger, phase)
        compiler = self._compiler_for(state)
        env = get_current_env()
        lock = self._locks.acquire(
            env=env, entity_id=entity_id, bot_id=bot_id, holder_user_id=actor_id
        )
        if lock is None:
            if not reap_stale_lock(
                self._locks, env=env, entity_id=entity_id, bot_id=bot_id
            ):
                raise ApplyInProgressError(bot_id)
            lock = self._locks.acquire(
                env=env, entity_id=entity_id, bot_id=bot_id, holder_user_id=actor_id
            )
            if lock is None:
                raise ApplyInProgressError(bot_id)

        try:
            # Called for its **exception**: what cannot be applied is refused
            # synchronously, to the caller, before an ``apply_id`` exists —
            # exactly as it was when this service validated the document
            # itself. The Manifest compiler re-validates the stored document
            # (its docstring says why a ``PUT``'s validation is not enough);
            # the worker compiles from the same reference again, because the
            # compile fetches and a fetch does not belong on this thread.
            compiler.check(
                self._context(
                    bot_id=bot_id,
                    bot=bot,
                    owner_id=owner_id,
                    actor_id=actor_id,
                    entity_id=entity_id,
                    env=env,
                    engine_type=engine_type,
                    bot_type=bot_type,
                ),
                state,
            )
            apply_id = uuid.uuid4().hex
            started_at = datetime.now()
            self._applies.start(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                apply_id=apply_id,
                trigger=trigger,
                # The audit label, never the principal — this column is the one
                # place the application-formatted value belongs.
                actor=audit_actor or actor_id,
                report=json.dumps(
                    ApplyReport(
                        apply_id=apply_id,
                        bot_id=bot_id,
                        trigger=trigger,
                        status=ApplyStatus.RUNNING,
                        started_at=started_at,
                        state=state,
                    ).as_payload()
                ),
            )
        except Exception:
            # Nothing started, so the lock must not be left behind.
            self._locks.release(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                lock_token=lock.lock_token,
            )
            raise

        try:
            # Handed to the queue, not to a thread. The tenant is read here,
            # inside the request, and carried in the payload: the queue has no
            # tenant column and no request context survives to handler time, so
            # the thread-era ``bind_current_avernet_tenant`` wrapper has nothing
            # to wrap. The handler opens its own scope from the payload.
            self._task_queue_provider().enqueue(
                APPLY_TASK_TYPE,
                build_apply_task_payload(
                    apply_id=apply_id,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_id=actor_id,
                    env=env,
                    tenant=get_current_avernet_tenant(),
                    trigger=trigger,
                    lock_token=lock.lock_token,
                    started_at=started_at.isoformat(),
                    phase=phase,
                    state=state.to_payload(),
                    engine_type=engine_type,
                    bot_type=bot_type,
                    carry_from_apply_id=carry_from_apply_id,
                ),
                APPLY_TASK_DEADLINE_SECONDS,
            )
        except BaseException as exc:
            # The RUNNING row exists but the work never started. Written for a
            # thread that could not be created; an enqueue that fails leaves the
            # identical state, so it keeps the identical answer — and that answer
            # is the better one: without the terminal write a poller sees a
            # lock-less RUNNING row, and the next apply waits out the stale-lock
            # TTL (30 minutes of ApplyInProgressError) for work that never
            # ran. The apply is terminally FAILED here, the lock released for the
            # next attempt, and the caller hears the original failure.
            terminate_on_launch_failure(
                self._applies,
                self._locks,
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                apply_id=apply_id,
                trigger=trigger,
                started_at=started_at,
                lock_token=lock.lock_token,
                state=state,
                exc=exc,
            )
            raise

        return ApplyAccepted(apply_id=apply_id, status=ApplyStatus.RUNNING)

    def _run(
        self,
        *,
        ctx: ApplyContext,
        compiler: DesiredStateCompiler,
        state: StateRef,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phase: ApplyPhase | None = None,
        lock_token: str,
        carry_from_apply_id: Optional[str] = None,
    ) -> None:
        """The background half: compile, apply, terminate the report and the lock.

        Both the terminal write and the lock release are in ``finally`` blocks:
        a raising compiler or orchestrator must not leave a report polling
        forever, and must not leave the bot locked against every future apply.
        The compile runs here, on the worker, because it fetches; a compiler
        that raises (the document stopped validating since the check, a
        promotion that went away) is a failed apply, recorded like any other.
        """
        report: ApplyReport | None = None
        try:
            strategy = self.delivery_for_engine(ctx.engine_type)
            report = asyncio.run(
                self._compile_apply_and_finish(
                    strategy,
                    ctx,
                    compiler,
                    state,
                    apply_id=apply_id,
                    trigger=trigger,
                    started_at=started_at,
                    phase=phase,
                )
            )
        except CompileRefused as exc:
            # Not a bug: the reference could not be compiled now (a promotion
            # active has moved on from, a revision the model refuses). The
            # report says why, in the compiler's own words.
            logger.warning(
                "[manifest_apply] compile refused, apply_id=%s, bot_id=%s: %s", apply_id, ctx.bot_id, exc
            )
            report = ApplyReport(
                apply_id=apply_id,
                bot_id=ctx.bot_id,
                trigger=trigger,
                status=ApplyStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(),
                categories=(),
                state=state,
                notes=(f"compile refused: {exc}",),
            )
        except Exception as exc:  # noqa: BLE001 - a daemon thread has no caller
            logger.exception(
                "[manifest_apply] apply raised, apply_id=%s, bot_id=%s",
                apply_id,
                ctx.bot_id,
            )
            report = ApplyReport(
                apply_id=apply_id,
                bot_id=ctx.bot_id,
                trigger=trigger,
                status=ApplyStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(),
                categories=(),
                state=state,
            )
            record_engine_failure(report, exc)
        else:
            report = carry_forward(
                report,
                ctx=ctx,
                carry_from_apply_id=carry_from_apply_id,
                applies=self._applies,
                to_report=self._to_report,
            )
        finally:
            try:
                if report is not None:
                    self._applies.finish(
                        env=ctx.env,
                        entity_id=ctx.entity_id,
                        bot_id=ctx.bot_id,
                        apply_id=apply_id,
                        status=report.status.value,
                        report=json.dumps(report.as_payload()),
                    )
            finally:
                self._locks.release(
                    env=ctx.env,
                    entity_id=ctx.entity_id,
                    bot_id=ctx.bot_id,
                    lock_token=lock_token,
                )

    def run_apply_task(self, payload: dict) -> None:
        """Execute one apply from its task payload. Called only by the handler.

        Rebuilds what the payload deliberately does not carry — the bot record,
        and the state its reference names (see ``build_apply_task_payload``) —
        then runs the same ``_run`` body the daemon thread used to.

        **The tenant is re-established here, from the payload.** The queue has no
        tenant column and no request context survives to handler time, so
        ``bind_current_avernet_tenant`` cannot help. Getting this wrong fails
        *silently*: ``get_current_avernet_tenant()`` is a total function that
        returns the **default** tenant outside a request rather than raising, so a
        handler that dropped this scope would not crash — it would substitute the
        wrong ``${BOT_TENANT}`` and read and write the manifest tables under the
        wrong tenant. That is an isolation failure with nothing raised anywhere to
        announce it, which is why it is pinned by a test rather than a comment.
        """
        tenant = str(payload.get("tenant") or "")
        with avernet_tenant_scope(tenant):
            bot_id = str(payload["bot_id"])
            try:
                ctx, compiler, state = self._rebuild(payload)
            except Exception as exc:  # noqa: BLE001 - see below
                # The rebuild can fail for reasons a retry cannot fix: the bot
                # went away since the enqueue, or the row is corrupt. Letting that
                # escape would hand the worker an exception it treats as a retry,
                # and the apply would loop until its deadline with the lock still
                # held and the record still RUNNING. Terminate it here instead —
                # the same outcome ``_run`` gives a raising orchestrator.
                logger.exception(
                    "[manifest_apply] could not rebuild apply_id=%s for bot_id=%s",
                    payload.get("apply_id"),
                    bot_id,
                )
                terminate_unstartable(self._applies, self._locks, payload, exc)
                return
            self._run(
                ctx=ctx,
                compiler=compiler,
                state=state,
                apply_id=str(payload["apply_id"]),
                trigger=str(payload["trigger"]),
                started_at=parse_started_at(payload.get("started_at")),
                phase=phase_from_payload(payload.get(PHASE_KEY)),
                lock_token=str(payload["lock_token"]),
                carry_from_apply_id=payload.get("carry_from_apply_id"),
            )

    def _rebuild(self, payload: dict) -> tuple[ApplyContext, DesiredStateCompiler, StateRef]:
        """The context, and the compiler the payload's state reference names."""
        state = state_ref_from_payload(payload.get("state"))
        compiler = self._compiler_for(state)
        entity_id = str(payload["entity_id"])
        bot_id = str(payload["bot_id"])
        # Re-read rather than trust a serialised copy: the record may have
        # moved since the enqueue, and for the pre-container phase there is
        # no record at all yet.
        bot = self._bot_or_none(entity_id=entity_id, bot_id=bot_id)
        if bot is None and not payload.get("engine_type"):
            # No record and nothing to stand in for one. That is not the
            # pre-container phase — it is a bot that went away between the
            # enqueue and now, and applying against defaulted capabilities
            # would be a guess.
            raise ManifestApplyBotMissingError(bot_id)
        ctx = self._context(
            bot_id=bot_id,
            bot=bot,
            owner_id=str(payload["owner_id"]),
            actor_id=str(payload["actor_id"]),
            entity_id=entity_id,
            env=str(payload["env"]),
            engine_type=payload.get("engine_type"),
            bot_type=payload.get("bot_type"),
            # The id the compiler's fetches stamp into every receipt, so the
            # linkage column answers "what did THIS apply fetch" as an
            # indexed read.
            apply_id=str(payload["apply_id"]),
        )
        return ctx, compiler, state

    def _bot_or_none(self, *, entity_id: str, bot_id: str) -> Optional[dict]:
        """The bot record, or ``None`` when it does not exist yet.

        ``None`` is an ordinary state, not an error: W13's pre-container phase
        runs before the record is written, which is the whole reason that phase
        can guarantee the startup-script row exists before the start command is
        composed.
        """
        return self._bots.get_by_id_and_entity(bot_id, entity_id)

    # ── dry run ─────────────────────────────────────────────────────────────

    async def dry_run(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
        state: StateRef,
    ) -> ApplyReport:
        """Compute the plan. Writes nothing, and mints no id.

        The compile is the real one — the same compiler, the same fetch
        bounds, the same baselines — so the plan previewed is the plan the
        apply would make, and the resolutions reported are what the caller is
        previewing. Only the write stage is skipped. A reference the compiler
        refuses is the compiler's own domain error to the caller (the one its
        HTTP surface maps), never the worker's ``CompileRefused``: the
        refusal is unwrapped to the error it carries.
        """
        compiler = self._compiler_for(state)
        ctx = self._context(
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=get_current_env(),
        )
        try:
            compiled = await compiler.compile(ctx, state)
        except CompileRefused as exc:
            domain_error = exc.domain_error
        else:
            report = await self._orchestrator(self.delivery_for_engine(ctx.engine_type)).apply(
                ctx,
                compiled.state,
                # No id: a dry run appears in no history, so there is nothing to
                # address it by. Naming it explicitly rather than minting one keeps
                # "writes nothing" true of the record as well as of the bot.
                apply_id="",
                trigger="dry_run",
                started_at=datetime.now(),
                dry_run=True,
                sources=compiled.sources,
            )
            return dataclasses.replace(report, state=state)
        # Raised outside the handler: the domain error keeps its own chain and
        # does not acquire the worker-only refusal as its context.
        raise domain_error

    # ── reading ─────────────────────────────────────────────────────────────

    def get_apply(
        self, *, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[ApplyReport]:
        """One apply's report. Scoped to the bot, so a foreign id is not found."""
        record = self._applies.get(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            apply_id=apply_id,
        )
        return self._to_report(record, entity_id=entity_id, bot_id=bot_id)

    def last_apply(
        self, *, entity_id: str, bot_id: str, trigger: str | None = None
    ) -> Optional[ApplyReport]:
        """The newest report, optionally for one trigger; no match is ``None``."""
        record = self._applies.latest(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id, trigger=trigger
        )
        return self._to_report(record, entity_id=entity_id, bot_id=bot_id)

    # ── internals ───────────────────────────────────────────────────────────

    def materialised_constructs(self) -> frozenset[ApplyConstruct]:
        """The constructs something can actually apply in this build.

        The keys of the very registry ``_orchestrator`` builds, so a caller
        gating on this and the engine executing the apply can never disagree —
        and W5/W6 widen it by registering a materialiser rather than by anyone
        remembering to update a list. A hand-written set would drift, and the
        drift is only observable as a failed apply on a bot that already exists.
        """
        # Any strategy answers, because the keys are the registry's and the
        # strategy only decides what sits behind each port. ``None`` is the
        # engine authority's ARCA answer, which is what this used to build by
        # hand.
        return frozenset(
            self._build_materialisers(self.delivery_for_engine(None)).keys()
        )

    def _build_materialisers(self, strategy: DeliveryStrategy):
        """The one construction site for the registry.

        Named rather than inlined into ``_orchestrator`` so
        ``materialised_constructs`` reads the *same* registry the engine runs,
        instead of a second list that would drift from it. The ports come from
        the strategy (W8): the registry's *keys* are the same for every family
        — which is why ``materialised_constructs`` may ask any strategy for
        them — while what sits behind each port is the family's.
        """
        return build_materialisers(**strategy.ports().as_kwargs())

    def _orchestrator(self, strategy: DeliveryStrategy) -> ApplyOrchestrator:
        """A fresh orchestrator over the strategy's registry and phase table."""
        return ApplyOrchestrator(
            self._build_materialisers(strategy), steps=strategy.steps_for
        )

    # ── the delivery seam (W8) ──────────────────────────────────────────────

    def delivery_for_engine(self, engine_type: Optional[str]) -> DeliveryStrategy:
        """The strategy a bot of this engine applies through."""
        return self._strategies.for_engine(engine_type)

    def delivery_for_bot(self, bot: dict) -> DeliveryStrategy:
        return self._strategies.for_bot(bot)

    async def _compile_apply_and_finish(
        self,
        strategy: DeliveryStrategy,
        ctx: ApplyContext,
        compiler: DesiredStateCompiler,
        state: StateRef,
        *,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phase: ApplyPhase | None = None,
    ) -> ApplyReport:
        """Compile the reference, walk the constructs, then let the strategy close the apply.

        The closing step runs only after every category has been written and
        only for a real apply; its failure is a note on the report, never a
        raise (§2.7: a manifest problem does not fail what it rode on).
        """
        # A creation half compiles only its own constructs: the pre-container
        # phase must not spend the fetch budget on skills it cannot deliver yet.
        only = None if phase is None else frozenset(step.construct for step in strategy.steps_for(phase))
        compiled = await compiler.compile(ctx, state, only=only)
        report = await self._orchestrator(strategy).apply(
            ctx,
            compiled.state,
            apply_id=apply_id,
            trigger=trigger,
            started_at=started_at,
            phase=phase,
            sources=compiled.sources,
        )
        report = dataclasses.replace(report, state=state)
        try:
            note = await strategy.finish(ctx, report)
        except Exception as exc:  # noqa: BLE001 — recorded, never raised
            logger.exception(
                "[manifest_apply] closing step raised, apply_id=%s, bot_id=%s",
                apply_id,
                ctx.bot_id,
            )
            note = f"delivery could not be closed: {exc.__class__.__name__}"
        if note:
            report = dataclasses.replace(report, notes=report.notes + (note,))
        return report

    def _context(
        self,
        *,
        bot_id: str,
        bot: Optional[dict],
        owner_id: str,
        actor_id: str,
        entity_id: str,
        env: str,
        apply_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        bot_type: Optional[str] = None,
    ) -> ApplyContext:
        """The identity one apply runs under, with or without a bot record.

        ``bot`` is ``None`` on exactly one path: W13 runs the pre-container phase
        **before** the bot is created, so there is no record to read. That case
        supplies ``engine_type`` / ``bot_type`` from the creation request and
        resolves capabilities from those — which is the second entry point W1
        built for this caller, not a parallel implementation. ``ApplyContext.bot``
        is then a minimal stand-in; the two shipped materialisers do not read it,
        and the one that runs in this phase (``script``) reads only the engine,
        env and tenant.
        """
        engine, kind = _engine_and_bot_type(bot, engine_type, bot_type)
        # The stand-in the docstring describes, named rather than inlined so the
        # "no record yet" case reads as one thing.
        record = bot
        if record is None:
            record = {
                "bot_id": bot_id,
                "entity_id": entity_id,
                "active_engine": engine,
                "bot_type": kind,
            }
        return ApplyContext(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=env,
            tenant=get_current_avernet_tenant(),
            engine_type=engine,
            bot_type=kind,
            bot=record,
            capabilities=(
                self._capabilities.capabilities_for_bot(bot)
                if bot is not None
                else self._capabilities.resolve_capabilities(
                    active_engine=engine, bot_type=kind
                )
            ),
            apply_id=apply_id,
        )

    def _compiler_for(self, state: StateRef) -> DesiredStateCompiler:
        """The compiler registered for this reference's kind; a missing one is a wiring error, not a bot state."""
        try:
            return self._compilers[state.kind]
        except KeyError:
            raise ValueError(f"no compiler is registered for state kind {state.kind!r}") from None

    def _to_report(
        self, record: Any, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """Rebuild a stored report, deriving ``FAILED`` for a stranded one.

        A report still ``RUNNING`` whose lock has gone stale belonged to a
        process that died before its ``finally`` could run. Reading it as
        ``FAILED`` is what stops a poller waiting forever, and doing it here —
        at read time — means there is no sweeper process to keep alive.
        """
        if record is None:
            return None
        payload = json.loads(record.report) if record.report else {}
        status = ApplyStatus(record.status)
        if status is ApplyStatus.RUNNING and is_abandoned(
            self._locks, env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        ):
            status = ApplyStatus.FAILED
        return report_from_payload(payload, record=record, status=status)


__all__ = ["APPLY_LOCK_TTL_SECONDS", "ApplyRunService"]
