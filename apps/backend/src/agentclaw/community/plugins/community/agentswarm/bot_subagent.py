"""Bot-kind subagent driven through the in-process BotPlatform.

No socket leaves this module: the composition root binds the platform's
platform adapter, the subagent talks to it directly. This replaces the
removed HTTP implementation, which dialed backend_base_url over httpx for
endpoints that do not exist on this backend's surface and 404'd in
production-shaped runs.

Async-run-and-resume: every synchronous platform leg rides a thread so the
worker loop never blocks (create/status/delete via ``asyncio.to_thread``,
each bounded by a configurable ``asyncio.wait_for`` timeout; chat via the
host's module-scoped bounded bridge + ``asyncio.futures.wrap_future``). A
cancelled chat hands its bridge worker a shared ``threading.Event`` — the
engine recv loop checks it between receive slices — so in-flight engine
rounds unwind cooperatively and the worker returns to the pool instead of
being abandoned. The chat call carries no ``session_key`` — the platform
addresses the engine-served conversation and resets it per leg (host.py's
reset-before-chat), so a re-executed turn (plain retry or post-crash
respawn) never inherits the orphaned turn's history (``resumeEnabled``/
``chat.resume`` remains the documented follow-up for mid-turn survival,
explicitly not implemented here).
"""
from __future__ import annotations

import asyncio
import functools
import json
import threading
import time

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentSpec, BotCreateSpec, BotState, IdempotencyKey, Run,
    SubagentResult, TaskEnvelope, NonRetryableProvisioningError, spawn_digest,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.plugin_api.agentswarm.subagent import (
    ParkedAwaitingAuthorization,
)
from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")

# Terminal authorization answers: a human rejection or an elapsed authorization window is the
# authorization's own verdict — retrying would re-enter a fresh gate, so
# they fail the attempt through the non-retryable marker, unlike the
# platform-side terminal failures (CREATE_FAILED/APPLY_FAILED) which keep
# their retryable classification below.
_AUTHORIZATION_TERMINAL = frozenset({
    BotState.AUTHORIZATION_REJECTED.value,
    BotState.AUTHORIZATION_EXPIRED.value,
})

_TERMINAL_FAILURE = frozenset({
    BotState.AUTHORIZATION_REJECTED.value,
    BotState.AUTHORIZATION_EXPIRED.value,
    BotState.CREATE_FAILED.value,
    BotState.APPLY_FAILED.value,
})

# Per-leg bounds (async-run-and-resume 2.2): ``asyncio.wait_for`` over each
# offloaded call, so a wedged platform leg fails the attempt — and frees the
# loop for the slice, the lease heartbeat included — instead of hanging the
# run. The ready-poll budget (``_poll_ready(timeout=...)``) still bounds the
# whole provisioning phase.
_CREATE_CALL_TIMEOUT_SECONDS = 120.0
_STATUS_CALL_TIMEOUT_SECONDS = 30.0
_DELETE_CALL_TIMEOUT_SECONDS = 60.0


class AvernetSubagent:

    def __init__(self, spec: AgentSpec, run: Run,
                 platform: BotPlatform,
                 agent_id: AgentId, *,
                 attempt: int = 1,
                 create_call_timeout: float = _CREATE_CALL_TIMEOUT_SECONDS,
                 status_call_timeout: float = _STATUS_CALL_TIMEOUT_SECONDS,
                 delete_call_timeout: float = _DELETE_CALL_TIMEOUT_SECONDS,
                 ready_poll_timeout: float = 300.0,
                 setup_delay_seconds: float = 0.0,
                 ) -> None:
        self._spec = spec
        self._run = run
        self._platform = platform
        self._agent_id = agent_id
        self._attempt = attempt
        self._bot_id: str | None = None
        self.name = spec.name
        # Executor surfaces (D-J, additive SPI): the per-attempt platform
        # persona of this spawn — the minted identity after setup returns,
        # empty before. A same-slice retry constructs a fresh subagent with
        # a different attempt and therefore a different persona id.
        self.executor_id = ""
        self.executor_name = spec.name
        self._create_call_timeout = create_call_timeout
        self._status_call_timeout = status_call_timeout
        self._delete_call_timeout = delete_call_timeout
        self._ready_poll_timeout = ready_poll_timeout
        self._setup_delay_seconds = setup_delay_seconds
        self._prov_delay_fired = False
        # Per-chat cancel registry: the in-flight chat's cooperative-unwind
        # event, reachable by ``halt()`` from off the awaiting coroutine.
        self._chat_registry_lock = threading.Lock()
        self._active_chat_cancel: threading.Event | None = None

    async def setup(self) -> None:
        # Park continuation: a re-invoked setup after a park
        # resumes the SAME persona's provisioning — the identity leg ran
        # before the park and its bot row is the one the authorization is
        # pending for, so re-creating it would mint a duplicate application
        # (or trip the host's duplicate-name refusal). Create runs at most
        # once per bound identity; every park recheck from here is a cheap
        # status poll.
        if self._bot_id is None:
            if self._setup_delay_seconds > 0:
                await asyncio.sleep(self._setup_delay_seconds)
            # Per-attempt persona (D-J): the idempotency key carries the in-slice
            # retry index, so a torn-down attempt's row (live or soft-deleted)
            # can never collide with the retry's create; a cross-slice replay of
            # the same attempt number re-derives the crashed slice's key and the
            # host's adopt leg resolves the live row. One persona per attempt,
            # fresh context per attempt (D23).
            self._bot_id = await asyncio.wait_for(
                asyncio.to_thread(
                    self._platform.create_bot,
                    spec=BotCreateSpec(
                        name=self._spec.name,
                        description=self._spec.role,
                        instructions=self._spec.instructions,
                        model=self._spec.model,
                        engine=self._spec.engine,
                        bot_type=self._spec.bot_type,
                        identity_file=self._spec.identity_file,
                        metadata=dict(self._spec.metadata),
                        config_manifest=self._spec.config_manifest,
                    ),
                    # Platform identity follows the spawn's durable identity and
                    # the attempt: agent_id is stable across D16 respawns (rebind
                    # via adopt, not duplicate) and unique between two same-named
                    # spawns; attempt fresh-mints retries. spec.name would collide
                    # same-named spawns, so there is no name fallback.
                    idempotency_key=IdempotencyKey(
                        f"{self._run.run_id}:{self._agent_id}:{self._attempt}"),
                    owner_id=self._run.owner_id,
                ),
                timeout=self._create_call_timeout,
            )
            if not (self._bot_id or "").strip():
                # A blank identifier is a contract violation, not a slow boot:
                # fail now instead of polling bot_status(None) to the deadline.
                raise RuntimeError(
                    f"platform returned invalid bot_id: {self._bot_id!r}")
        state = await self._poll_ready(
            self._bot_id, timeout=self._ready_poll_timeout)
        if state != BotState.READY.value:
            raise RuntimeError(f"bot {self._bot_id} did not reach READY: {state}")
        self.executor_id = self._bot_id
        # The platform derives the persona name from the same key digest
        # (f"{spec.name}-{digest8}"); mirror it so the journaled executor
        # name is the identity the host actually provisioned under.
        self.executor_name = (
            f"{self._spec.name}-"
            f"{spawn_digest(str(self._run.run_id), str(self._agent_id), self._attempt, 8)}"
        )

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        if self._bot_id is None:
            raise RuntimeError("setup not completed")
        prompt = f"objective={envelope.objective!r} boundaries={envelope.boundaries!r}"
        # No session_key crosses the seam: the platform addresses the
        # engine-served conversation and resets it per leg (host.py), so a
        # retried or respawned turn opens a fresh conversation instead of
        # continuing the orphaned turn's history.
        cancel = threading.Event()
        self._register_active_chat(cancel)
        leg = functools.partial(
            self._platform.chat,
            bot_id=self._bot_id, message=prompt,
            owner_id=self._run.owner_id,
            cancel_event=cancel,
        )
        # Chat rides the module-scoped bounded bridge (host.py), not the raw
        # default executor: the pool caps how many engine chats the pod may
        # hold, and wrap_future bridges its thread future into this loop —
        # cancelling this coroutine only cancels PENDING work; the RUNNING
        # worker exits cooperatively through the event (.recv slices) or at
        # the platform's own deadline. Either way it returns to the pool.
        from agentclaw.community.plugins.community.agentswarm.host import (
            chat_bridge_executor,
        )
        bridge_future = chat_bridge_executor().submit(leg)
        try:
            text = await asyncio.futures.wrap_future(bridge_future)
        except asyncio.CancelledError:
            cancel.set()
            raise
        finally:
            self._register_active_chat(None)
        if not text and cancel.is_set():
            # The cancelled unwrap returns the empty sentinel with no
            # exception to carry; decoding it here would publish an
            # aborted turn as a successful empty result (a D-I/D23
            # corruption shape), so unwind as an error instead.
            raise RuntimeError("engine chat leg unwound after cancellation")
        try:
            output = json.loads(text)
        except Exception:
            output = {"text": text}
        return SubagentResult(output=output)

    async def teardown(self) -> None:
        # Best-effort by contract: a failed delete never fails the slice,
        # the platform's own TTL/lifecycle owns the orphan. The delete leg
        # is offloaded and bounded like every other platform call; its
        # timeout (like its failures) only ends up in the warning log.
        if self._bot_id is not None:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self._platform.delete_bot, self._bot_id,
                        owner_id=self._run.owner_id,
                    ),
                    timeout=self._delete_call_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[agentswarm][bot] bot delete failed: %s", exc)
            finally:
                self._bot_id = None

    async def halt(self) -> None:
        self._cancel_active_chat()
        await self.teardown()

    async def _poll_ready(self, bot_id: str, timeout: float = 300.0,
                          interval: float = 1.0) -> str:
        deadline = time.monotonic() + timeout
        state = ""
        while time.monotonic() < deadline:
            state = await asyncio.wait_for(
                asyncio.to_thread(
                    self._platform.bot_status, bot_id,
                    owner_id=self._run.owner_id,
                ),
                timeout=self._status_call_timeout,
            )
            if state == BotState.READY.value:
                return state
            if state == BotState.AWAITING_AUTHORIZATION.value:
                # The human gate: reset the provisioning-delay marker so
                # the post-gate boot simulation fires once per gate cycle.
                self._prov_delay_fired = False
                # The human gate: the passport application is
                # pending inside its window and only a human can move it, so
                # burning the poll budget against this deadline is guaranteed
                # waste. Park now — the pool rechecks this same continuation
                # cheaply and parks time never touches the setup clock. The
                # window's own deadline (in the status answer's authorization
                # descriptor when the platform serves one) is the park's bound.
                parked = "bot {} parked awaiting human authorization".format(
                    bot_id)
                authorization = getattr(state, "authorization", None)
                if authorization is not None:
                    deadline = str(getattr(authorization, "deadline", "") or "")
                    if deadline:
                        parked += f" (window deadline {deadline})"
                raise ParkedAwaitingAuthorization(parked, bot_id=bot_id)
            if state in _AUTHORIZATION_TERMINAL:
                # The authorization answered with its own terminal verdict:
                # a retry would re-open a fresh gate the same human just
                # declined (or let yet another window lapse), so classify
                # non-retryable and let the pool stop the attempt loop.
                # The identity + verdict ride the marker so the pool can
                # record the terminal state on the agent row
                # without parsing this message.
                raise NonRetryableProvisioningError(
                    f"bot {bot_id} creation failed: {state}",
                    bot_id=bot_id, bot_state=state,)
            if (state not in _TERMINAL_FAILURE
                    and state != ""
                    and not self._prov_delay_fired
                    and self._setup_delay_seconds > 0):
                # PRE parity: after the passport resolves (gate clears),
                # the persona's container takes ~180s to boot on ARCA.
                # Locally the container is up in <5s, so inject the
                # observed PRE provisioning depth here — once per gate
                # resolution — to exercise the real setup budget.
                self._prov_delay_fired = True
                logger.info(
                    "[agentswarm][bot] simulating PRE provisioning: "
                    "%.0fs for bot %s", self._setup_delay_seconds, bot_id)
                await asyncio.sleep(self._setup_delay_seconds)
            if state in _TERMINAL_FAILURE:
                # A terminal-failure state will not become READY by polling
                # longer, so stop the poll now; the pool decides whether the
                # failure is worth another attempt.
                raise RuntimeError(f"bot {bot_id} creation failed: {state}")
            await asyncio.sleep(interval)
        # A persona still not READY after the whole window is wedged behind
        # an upstream that never settles (a quota-scope lock held by
        # concurrent creates, a soft-deleted row blocking re-insert). The
        # same wait would repeat identically, so this is deterministic —
        # fail the attempt rather than retry through the full window again.
        raise NonRetryableProvisioningError(
            f"bot {bot_id} did not become READY within {timeout:.0f}s "
            f"(last state={state!r})")

    def _register_active_chat(self, cancel: threading.Event | None) -> None:
        with self._chat_registry_lock:
            self._active_chat_cancel = cancel

    def _cancel_active_chat(self) -> None:
        with self._chat_registry_lock:
            if self._active_chat_cancel is not None:
                self._active_chat_cancel.set()