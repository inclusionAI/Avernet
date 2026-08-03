"""6.5.4: local in-process :class:`ExecutionPort` doubles for community/singlebox.

These fake the async bot-completion model (plan §6.5 — bot self-reports via SKILL
after completion) so the task loop can be exercised end-to-end without a real
engine/BCS:

- :class:`LocalBotExecutorPort` — a well-behaved bot. On ``dispatch_single_bot``
  the "bot" self-reports ``NODE_ACCEPTED`` back through :meth:`TaskService.on_event`
  (the single-writer assigns ``seq``; no private reach). Two settle modes:
  * ``"instant"`` (default): the self-report fires inside dispatch → the node is
    ``DONE`` by the time the dispatch tick reloads (full self-driving happy path;
    a single-node task closes DEFINED→RUNNING→REVIEWING in one ``start`` call).
  * ``"deferred"``: dispatch enqueues the self-report; call :meth:`pump` to flush
    (faithful async — the bot completes between ticks, not during dispatch).

- :class:`HangingBotExecutor` — a hung bot that never self-reports, not even on
  probe/redispatch. Use it to exercise the scheduler watchdog (6.5): the tick
  phase drives PROBE→REDRIVE→ESCALATE→``FAILED`` with zero kernel change.

Both implement :class:`ExecutionPort` structurally (``runtime_checkable``). They
are community doubles, NOT ``@plugin_impl`` — :class:`ExecutionPort` is an
api-layer business Protocol bound via injector ``@provider`` (Rule 20/21).
"""
from __future__ import annotations

from typing import Any, List

from agentclaw.community.core.task.protocols import DispatchResult, ExecutionPort
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.domain.models import RunMode


class LocalBotExecutorPort(ExecutionPort):
    """Well-behaved local bot: dispatch self-reports ``NODE_ACCEPTED``.

    Holds a reference to :class:`TaskService` so the self-report folds through
    the same public ``on_event`` entrypoint a real bot would hit (router
    ``/events`` or owner-bot 回投). The scheduler is uninvolved in the
    self-report — only :class:`TaskService` folds ``NODE_ACCEPTED`` (node →
    ``DONE``); the next ``tick`` then sees the settled node and advances.
    """

    def __init__(self, task_service: Any, settle_mode: str = "instant") -> None:
        if settle_mode not in {"instant", "deferred"}:
            raise ValueError(
                f"settle_mode must be 'instant' or 'deferred', got {settle_mode!r}"
            )
        self._svc = task_service
        self._settle_mode = settle_mode
        self._pending: List[dict] = []
        # call records (handy for assertions in smoke/integration tests)
        self.single_bots: List[tuple[str, str, str]] = []
        self.coop_groups: List[tuple[str, str, list[str]]] = []
        self.redispatches: List[tuple[str, str, str]] = []
        self.probes: List[tuple[str, str, str]] = []

    def dispatch_single_bot(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        self.single_bots.append((task_id, node_id, bot_id))
        self._schedule_accept(task_id, node_id, bot_id)
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def coop_group(self, task_id: str, node_id: str, bot_ids: list[str]) -> DispatchResult:
        self.coop_groups.append((task_id, node_id, list(bot_ids)))
        # coop group: the lead bot (first) self-reports on behalf of the group.
        if bot_ids:
            self._schedule_accept(task_id, node_id, bot_ids[0])
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.COOP_GROUP)

    def redispatch_node(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        # A re-driven well-behaved bot still completes → self-report.
        self.redispatches.append((task_id, node_id, bot_id))
        self._schedule_accept(task_id, node_id, bot_id)
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def probe(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        # A well-behaved bot that's still RUNNING answers a probe by self-reporting
        # (it had finished but the report was delayed/lost). The scheduler only
        # probes RUNNING nodes, so RUNNING→DONE is a legal fold here.
        self.probes.append((task_id, node_id, bot_id))
        self._schedule_accept(task_id, node_id, bot_id)
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def bbs(self, task_id: str, node_id: str, reason: str = "") -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.BBS)

    # --- internal ------------------------------------------------------------

    def _schedule_accept(self, task_id: str, node_id: str, bot_id: str) -> None:
        envelope = {
            "task_id": task_id,
            "kind": EventKind.NODE_ACCEPTED.value,
            "payload": {"node_id": node_id, "verifier": bot_id},
        }
        if self._settle_mode == "instant":
            self._svc.on_event(envelope)
        else:
            self._pending.append(envelope)

    def pump(self) -> int:
        """Deferred mode: flush all pending self-reports into the task service.

        Returns the number of reports delivered. Instant mode is a no-op (reports
        fire inside dispatch, so nothing is ever enqueued).
        """
        if self._settle_mode != "deferred":
            return 0
        pending = self._pending
        self._pending = []
        for env in pending:
            self._svc.on_event(env)
        return len(pending)


class HangingBotExecutor(ExecutionPort):
    """Hung bot: never self-reports, not even on probe/redispatch.

    Pair with the scheduler watchdog (6.5): a task whose only node is dispatched
    to this executor will be driven PROBE→REDRIVE→ESCALATE→``FAILED`` by the
    tick phase, with zero kernel change. Records dispatch/probe/redispatch calls
    for assertion.
    """

    def __init__(self) -> None:
        self.single_bots: List[tuple[str, str, str]] = []
        self.coop_groups: List[tuple[str, str, list[str]]] = []
        self.redispatches: List[tuple[str, str, str]] = []
        self.probes: List[tuple[str, str, str]] = []

    def dispatch_single_bot(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        self.single_bots.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def coop_group(self, task_id: str, node_id: str, bot_ids: list[str]) -> DispatchResult:
        self.coop_groups.append((task_id, node_id, list(bot_ids)))
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.COOP_GROUP)

    def redispatch_node(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        self.redispatches.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def probe(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        self.probes.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def bbs(self, task_id: str, node_id: str, reason: str = "") -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.BBS)


__all__ = [
    "HangingBotExecutor",
    "LocalBotExecutorPort",
]
