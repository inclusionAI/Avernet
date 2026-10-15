"""Community host adapter — the in-tree binding for agentswarm's BotPlatform.

Implements the provision seam (``agentswarm.spi.bot.BotPlatform``) with the
backend's own creation flow, in-process from end to end (openspec change
bind-agentswarm-host-bot-platform):

- create submits a with-manifest creation: ``submit_bot_creation_with_manifest``
  owns validation (the manifest is preflighted before any Passport is applied),
  a deterministic ``bot_id`` derived from the seam's idempotency key makes a
  retried setup rebind the same creation instead of leaking bots, and the
  persona document is composed — never guessed — from the spec: identity file
  ``type`` from the host profile (engine-gated), ``content`` from the spec's
  instructions, empty stays empty and visible.
- status maps the durable rows (creation job, bot record, newest apply report)
  to the swarm's ``BotState`` vocabulary through a deliberately narrow table:
  it reads only public surfaces of the creation flow and answers only for
  creations this adapter submitted — the HTTP poll's W8 sequence machinery
  (ordinary creates, record-first engines, later explicit applies) is out of
  scope here, and the unit tests pin every branch of the narrow table.
- delete is phase-aware and best-effort: discard the pending creation when no
  bot exists, soft-delete the bot when one does, and never raise — teardown
  must not change the slice outcome.
- chat drives the engine transport directly: the chat endpoint comes from
  the canonical BaaS ws-info (``BaasService.get_ws_info_by_bot_uuid`` — the
  same resolution every other chat surface uses), and the adapter's
  engine frames (``connect`` handshake + ``chat.send`` accumulate) are
  spoken in-process. The async-in engine leg runs on the calling thread's
  own loop: the swarm's subagent drives the sync ``chat`` entry point
  from the module-scoped bounded chat bridge
  (``chat_bridge_executor()``) so one pod never holds more than
  ``agentswarm.executor.max_platform_workers`` engine chats in flight,
  and the wait is bounded (``_CHAT_TIMEOUT_SECONDS``): a hung engine
  WebSocket surfaces as ``ChatTimeoutError`` rather than blocking the
  bridge thread forever. A cancelled chat unwinds cooperatively — the
  recv loop checks the call's ``cancel_event`` between receive slices —
  so bridge threads return to the pool instead of being abandoned.

The adapter is a pure function of its three inputs — (spec, idempotency_key,
host profile) — and owns no state of its own. ``model`` and ``metadata`` on the
spec have no manifest/create slot today (engine_config is refused at creation);
the host profile documents them as not honored rather than inventing one.

Bound by the ``AgentSwarmModule.bot_platform`` DI provider
(``di/modules/agentswarm``), which sits in the composition root's base module
list. Profiles whose platform-side dependencies that provider cannot resolve
get no ``BotPlatform`` at all (``provide_runtime`` resolves ``None`` there) —
an unbound ``BotPlatform`` is agentswarm's designed fail-clear mode for
profiles without a platform side.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import hashlib
import json
import ssl
import threading
import uuid
from typing import Any

import yaml

from agentclaw.community.core.agentswarm.domain.models import (
    BotCreateSpec, BotId, BotState, IdempotencyKey,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER, CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    AUTHORIZATION_WINDOW_ELAPSED, BOT_COULD_NOT_BE_PROVISIONED,
)
from agentclaw.community.core.bot_management.create_context import (
    BotCreateContext, BotCreateDeploymentMode, BotCreateSpec as HostCreateSpec,
    BotCreateTemplateValidationMode,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES, TaskStatus,
)
from agentclaw.community.log import get_logger

logger = get_logger(__name__)

# The publicly creatable engine registry minus the internal one (aicoding);
# the cluster bijection and the claude_code identity-file rule are deployment
# constants, verified once at adapter construction rather than per call.
_CREATABLE_ENGINES = frozenset({
    "moltis", "openclaw", "hermes", "deepseek_harness", "claude_code", "teclaw",
})
_CLAUDE_CODE_IDENTITY = "CLAUDE.md"
_CREATION_TRIGGERS = frozenset({CREATE_PRE_CONTAINER_TRIGGER, CREATE_ON_CONTAINER_TRIGGER})
_BOT_UP = frozenset({"ACTIVE"})
_BOT_DEAD = frozenset({"FAILED", "DELETED", "INACTIVE"})

# Upper bound on one engine chat round trip. It matches the swarm's own
# slice budget (``orchestration.slice_max_seconds``/the ready-poll
# deadline, 300s): a chat that has not answered by then cannot still
# count. Keeps the blocked bridge thread from waiting forever on a hung
# engine WebSocket, and bounds every receive slice in the recv loop so a
# cancelled chat unwinds within one slice even while a socket recv hangs.
_CHAT_TIMEOUT_SECONDS = 300.0

# One receive slice inside the recv loop. Each slice sits well under the
# conversation deadline; the loop re-checks the shared cancel event every
# slice, so a chat cancelled from the asyncio side unwinds within one
# slice of the set() rather than after the full remaining deadline.
_RECV_SLICE_SECONDS = 1.0
_HASHED_IDEMPOTENCY_ID_DIGITS = 16

# One worker per concurrent engine chat the pod may carry: the default for
# direct construction; the composition root sizes the bridge from
# ``agentswarm.executor.max_platform_workers``.
_DEFAULT_MAX_PLATFORM_WORKERS = 4

# Engine chat transport: the in-pod adapter channel and its zero-check
# machine-caller token (OPEN_API:<kind>:<app>; no certified token, and the
# check itself fails open on its own infrastructure errors).
_ENGINE_WS_PORT = 20003
_ENGINE_WS_PATH = "/api/openclaw/ws"
_ZERO_CHECK_OPEN_API_TOKEN = "OPEN_API:app:agentswarm"

# Conversation addressing for a chat whose caller did not derive one: the
# historical fixed session, kept for outside/default callers.
_DEFAULT_SESSION_KEY = "agent:main:default"

# Module-scoped chat bridge (async-run-and-resume D-B): one bounded pool
# whose workers carry sync ``BotPlatform.chat`` legs (one asyncio.run loop
# per chat) off the swarm's event loop. Workers return to the pool after
# every chat — cancelled or timed-out legs included — so repeated timeouts
# cannot grow the live thread set past ``max_platform_workers``; the pool
# only ever shuts down at process exit (atexit → ``shutdown_chat_bridge``).
_BRIDGE_MAX_WORKERS: int | None = None
_BRIDGE_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_BRIDGE_LOCK = threading.Lock()


def set_chat_bridge_max_workers(max_workers: int) -> None:
    """Record the desired bridge size before the pool's first use.

    Called from ``AvernetBotPlatform.__init__`` (the DI provider passes
    ``agentswarm.executor.max_platform_workers``). First construction wins:
    once live workers exist the size stays fixed for the process."""
    global _BRIDGE_MAX_WORKERS
    with _BRIDGE_LOCK:
        if _BRIDGE_EXECUTOR is None:
            _BRIDGE_MAX_WORKERS = max(1, int(max_workers))


def chat_bridge_executor() -> concurrent.futures.ThreadPoolExecutor:
    """The module-scoped platform bridge pool (lazy singleton)."""
    global _BRIDGE_EXECUTOR
    with _BRIDGE_LOCK:
        if _BRIDGE_EXECUTOR is None:
            _BRIDGE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=_BRIDGE_MAX_WORKERS or _DEFAULT_MAX_PLATFORM_WORKERS,
                thread_name_prefix="agentswarm-chat",
            )
        return _BRIDGE_EXECUTOR


def shutdown_chat_bridge() -> None:
    """Process-exit close hook (atexit). During operation nothing calls
    this: workers return to the pool after every leg, cancelled or not,
    and ``shutdown(wait=True, cancel_futures=True)`` would only join at
    the cost of refusing new chats."""
    global _BRIDGE_EXECUTOR, _BRIDGE_MAX_WORKERS
    with _BRIDGE_LOCK:
        executor, _BRIDGE_EXECUTOR = _BRIDGE_EXECUTOR, None
        _BRIDGE_MAX_WORKERS = None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


atexit.register(shutdown_chat_bridge)


class ChatTimeoutError(RuntimeError):
    """The engine chat leg did not answer within ``_CHAT_TIMEOUT_SECONDS``.

    Raised by ``AvernetBotPlatform.chat`` when the bridge leg exceeds the
    bounded wait: the recv loop's own deadline fires and the caller's
    thread is freed with this error. The bridge thread is never abandoned
    — the same deadline unwinds the recv loop inside the pool worker, and
    the worker returns to the bridge pool. A chat cancelled through its
    ``cancel_event`` (slice lost from the asyncio side) unwinds the same
    way, within one receive slice.
    """


def _idem_digest(idempotency_key: str, length: int) -> str:
    return hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:length]


def bot_id_for(run_id: str, agent_id: str) -> str:
    """Deterministic transient-bot identity for a (run, agent) pair.

    The platform's create derives the same value from the spawn's
    idempotency key, so a cleanup that only knows the run and agent ids
    can still address the bot that was created for it."""
    return f"ac_{_idem_digest(f'{run_id}:{agent_id}', _HASHED_IDEMPOTENCY_ID_DIGITS)}"


class AvernetBotPlatform(BotPlatform):
    """Host-side BotPlatform over the backend's own creation and chat flows.

    The seam's methods are synchronous by contract. The async caller
    (avernet_subagent.AvernetSubagent) offloads every platform leg to a
    thread: create/status/delete via ``asyncio.to_thread``, and chat —
    the one leg measured in minutes — via the module-scoped bounded
    ``chat_bridge_executor`` + ``asyncio.futures.wrap_future`` so a
    cancelled coroutine sets the chat's cancel event and the bridge worker
    unwinds cooperatively (async-run-and-resume D-B). No method here can
    block unboundedly while it does block: create submits the durable
    creation job (validate + persist + Passport application — no
    polling), status is two reads, delete is one write, and chat's wait
    is bounded by ``_CHAT_TIMEOUT_SECONDS``.
    """

    def __init__(
        self,
        *,
        owner_id: str = "",
        manifest_seam: ManifestCreationSeam,
        bot_service: Any,
        passport_plugin: Any,
        skill_set_factory: Any,
        bot_repo: Any,
        apply_service: Any,
        space_context: BusinessSpaceContextProtocol,
        baas_service: BaasService,
        max_platform_workers: int = _DEFAULT_MAX_PLATFORM_WORKERS,
    ) -> None:
        # owner_id is the run's provisioning identity (the submitter);
        # per-call: per_run when the swarm identity arrives via Run.user_id
        self._default_owner = owner_id
        self._manifest_seam = manifest_seam
        self._bot_service = bot_service
        self._passport = passport_plugin
        self._skill_set_factory = skill_set_factory
        self._bot_repo = bot_repo
        self._apply_service = apply_service
        self._space_context = space_context
        self._baas_service = baas_service
        self._chat_timeout_seconds = _CHAT_TIMEOUT_SECONDS
        set_chat_bridge_max_workers(max_platform_workers)


    # ── create ────────────────────────────────────────────────────────────

    def create_bot(self, *, spec: BotCreateSpec,
                   idempotency_key: IdempotencyKey, owner_id: str = "") -> BotId:
        # Heavy chain (bot_service -> aicoding -> di) imports call-local, so
        # plugin import never participates in the DI bootstrap cycle.
        from agentclaw.community.core.bot_management.create_flow import (
            submit_bot_creation_with_manifest,
        )

        owner = (owner_id or "").strip() or self._default_owner
        # Same digest as bot_id_for(): the subagent always passes the
        # idempotency key f"{run_id}:{agent_id}", so a cleanup that only
        # knows the (run, agent) pair addresses the identical bot id.
        bot_id = (
            f"ac_{_idem_digest(idempotency_key, _HASHED_IDEMPOTENCY_ID_DIGITS)}")
        # One persona per (run, spec): the same idempotency key derives the same
        # bot identifier, so a retried setup rebinds the same creation instead of
        # minting a second one (and soft-name collisions across concurrent runs
        # are impossible: the name carries the key's digest).
        bot_name = f"{spec.name}-{_idem_digest(idempotency_key, 8)}"
        document = yaml.safe_dump(
            {
                "schema_version": 1,
                "manifest": {
                    "identity": [{
                        "type": spec.identity_file or "SOUL.md",
                        "content": spec.instructions,
                    }],
                },
            },
            sort_keys=False, allow_unicode=True, default_flow_style=False,
        )
        space = self._space_context.resolve_current(
            owner_id=owner, header_space_id=None,
        )
        submitted = submit_bot_creation_with_manifest(
            user_id=owner,
            nick_name=owner,
            bot_id=bot_id,
            document=document,
            modifier=owner,
            spec=HostCreateSpec(
                entity_id=owner,
                engine_type=spec.engine or "openclaw",
                bot_type=spec.bot_type or "personal",
                bot_name=bot_name,
                bot_desc=spec.description or None,
                template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
                engine_properties={},
            ),
            context=BotCreateContext(
                deployment_mode=BotCreateDeploymentMode.CLOUD,
                space_kind=space.kind,
                space_quota=True,
            ),
            bot_service=self._bot_service,
            passport_plugin=self._passport,
            skill_set_factory=self._skill_set_factory,
            manifest_seam=self._manifest_seam,
        )
        return BotId(submitted.bot_id)

    # ── status: the narrow table ──────────────────────────────────────────

    def bot_status(self, bot_id: BotId, owner_id: str = "") -> str:
        owner = (owner_id or "").strip() or self._default_owner
        bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
        job = self._manifest_seam.find_job(entity_id=owner, bot_id=bot_id)

        if job is None:
            # No creation job: either submit has not landed a row yet or an
            # unknown bot id — neither is a terminal verdict, so provisioning.
            return BotState.PROVISIONING.value
        if bot is None and job.status not in TERMINAL_STATUSES:
            # Live job, nothing created yet: the authorization window and
            # every pre-container state read the same from here.
            return BotState.PROVISIONING.value
        if bot is None:
            # Terminal job, no bot: declined, never answered, or the platform
            # could not provision — the job's own answer, never enriched.
            if job.status is TaskStatus.TIMED_OUT:
                return BotState.AUTHORIZATION_EXPIRED.value
            last_error = job.last_error or ""
            if last_error == AUTHORIZATION_WINDOW_ELAPSED:
                return BotState.AUTHORIZATION_EXPIRED.value
            if last_error.startswith(BOT_COULD_NOT_BE_PROVISIONED):
                return BotState.CREATE_FAILED.value
            return BotState.AUTHORIZATION_REJECTED.value

        bot_status_ = str(bot.get("status") or "")
        if bot_status_ in _BOT_DEAD:
            # The container failed, was removed, or was deactivated: a
            # provisioning outcome regardless of what the job is doing.
            return BotState.CREATE_FAILED.value
        report = self._apply_service.last_apply(entity_id=owner, bot_id=bot_id)
        if report is not None and report.trigger in _CREATION_TRIGGERS:
            # The creation's own apply outranks job liveness: the
            # post-container phase runs while the job is still live, so
            # READY/APPLY_FAILED are answerable from the report even then
            # (a later explicit apply — "how is the bot configured now" —
            # is not read here).
            if report.status is ApplyStatus.SUCCEEDED:
                if bot_status_ in _BOT_UP:
                    return BotState.READY.value
                # Record-first engines apply before the container: applied,
                # container still coming.
                return BotState.PROVISIONING.value
            return BotState.APPLY_FAILED.value
        if job.status not in TERMINAL_STATUSES:
            # Live job with no creation report yet: every remaining in-flight
            # state (authorization window, creating, applying) reads the same.
            return BotState.PROVISIONING.value
        if job.status is TaskStatus.SUCCEEDED:
            return BotState.READY.value
        if job.status in TERMINAL_STATUSES and bot_status_ in _BOT_UP:
            # Bot exists and works, job gave up: a configuration-side
            # failure — "CREATE_FAILED" would send the caller to create a
            # duplicate of a running, billable bot.
            return BotState.APPLY_FAILED.value
        # Terminal job, bot parked in a middle state (PENDING/STOPPED —
        # neither up nor dead): the creation job is what drives the bot
        # toward READY, and it has finished, so the bot will never come
        # up on its own. Report the terminal creation failure (the same
        # state as the other terminal-without-a-working-bot cases)
        # instead of PROVISIONING, which would only burn the poller's
        # whole deadline into a generic timeout.
        return BotState.CREATE_FAILED.value

    # ── delete ────────────────────────────────────────────────────────────

    def delete_bot(self, bot_id: BotId, owner_id: str = "") -> bool:
        owner = (owner_id or "").strip() or self._default_owner
        try:
            bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
            if bot is None:
                # Pending (or never landed a bot): undo what submission wrote.
                return bool(self._manifest_seam.discard(
                    entity_id=owner, bot_id=bot_id, owner_id=owner,
                ))
            return bool(self._bot_service.delete_bot(bot_id, owner))
        except Exception as exc:  # best-effort by contract: never raise
            logger.warning(
                "[agentswarm][host-platform] bot delete failed for %s: %s",
                bot_id, exc,
            )
            return False

    # ── chat ──────────────────────────────────────────────────────────────

    def chat(self, bot_id: BotId, message: str, owner_id: str = "",
             session_key: str | None = None,
             cancel_event: threading.Event | None = None) -> str:
        # Sync-out by SPI contract; async-in in the bridge worker that
        # carries this call: the engine leg runs its own asyncio.run loop
        # here and every wait inside it is bounded — the recv loop's
        # ``_CHAT_TIMEOUT_SECONDS`` deadline and receive slices, either of
        # which (or the caller's cancel_event) unwinds the worker back to
        # the bridge pool instead of abandoning it.
        owner = (owner_id or "").strip() or self._default_owner
        try:
            return asyncio.run(self._chat_once(
                bot_id, message, owner, session_key, cancel_event))
        except TimeoutError as exc:
            raise ChatTimeoutError(
                f"engine chat for bot {bot_id} did not finish within "
                f"{_CHAT_TIMEOUT_SECONDS:.0f}s"
            ) from exc

    def _engine_endpoint(self, bot_id: BotId, owner: str) -> tuple[str, str]:
        """Resolve the bot's chat endpoint via BaaS ws-info.

        The sandbox proxy only resolves targets the BaaS provider registry
        knows (bot-uuid-minted targets close the WS 1008 in proxypass_ws),
        so the connection pair always comes from the canonical
        ``/api/v1/bots/{uuid}/ws-info`` resolution — never minted here.
        """
        bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
        if bot is None:
            raise RuntimeError(f"bot {bot_id} not found for chat")
        bot_uuid = (bot.get("device_id") or "").strip()
        if not bot_uuid:
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            )
        try:
            info = self._baas_service.get_ws_info_by_bot_uuid(
                bot_uuid=bot_uuid,
                port=_ENGINE_WS_PORT,
                path=_ENGINE_WS_PATH,
            )
        except Exception as exc:
            logger.warning(
                "[agentswarm][host-platform] baas ws-info failed for "
                "bot=%s uuid=%s: %s", bot_id, bot_uuid, exc,
            )
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            ) from exc
        ws_url = (getattr(info, "ws_url", "") or "").strip()
        token = (getattr(info, "token", "") or "").strip()
        if not ws_url or not token:
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            )
        return ws_url, token

    async def _chat_once(self, bot_id: BotId, message: str, owner: str,
                         session_key: str | None,
                         cancel_event: threading.Event | None) -> str:
        ws_url, token = self._engine_endpoint(bot_id, owner)
        ws = await self._connect_engine(ws_url, token)
        try:
            return await self._chat_send(
                ws, message, token, session_key, cancel_event)
        finally:
            await ws.close()

    async def _connect_engine(self, ws_url: str, token: str):
        import websockets

        ssl_context = None
        if ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    ws_url,
                    additional_headers={"x-proxypass-token": token},
                    ssl=ssl_context,
                    max_size=2 * 1024 * 1024,
                ),
                timeout=30,
            )
        except Exception as e:
            raise ConnectionError(
                f"engine websocket connect failed ({ws_url}): {e}"
            ) from e

        connect_frame = {
            "type": "req",
            "id": str(uuid.uuid4()),
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "cli",
                    "version": "1.0.0",
                    "platform": "MacIntel",
                    "mode": "cli",
                },
                "role": "operator",
                "x-moltis-mcp-token": token,
                "scopes": [
                    "operator.admin",
                    "operator.read",
                    "operator.write",
                ],
            },
        }
        try:
            await ws.send(json.dumps(connect_frame))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(raw)
            if not resp.get("ok", False):
                await ws.close()
                raise ConnectionError(
                    f"engine handshake failed: "
                    f"{resp.get('error', {}).get('message', 'unknown error')}"
                )
            return ws
        except ConnectionError:
            raise
        except Exception as e:
            await ws.close()
            raise ConnectionError(f"engine handshake failed: {e}") from e

    async def _chat_send(self, ws, message: str, token: str,
                         session_key: str | None,
                         cancel_event: threading.Event | None) -> str:
        """One chat.send round: the adapter's zero-check gate requires an
        x-iam-token on every frame. OPEN_API:<kind>:<app> is the form it
        accepts for machine callers (no certified token; the check fails
        open on its own infrastructure errors). ``session_key`` addresses a
        per-attempt engine conversation — the historical fixed session only
        when this call carries none."""
        request_id = str(uuid.uuid4())
        request_frame = {
            "type": "req",
            "id": request_id,
            "method": "chat.send",
            "params": {
                "sessionKey": session_key or _DEFAULT_SESSION_KEY,
                "message": message,
                "deliver": False,
                "idempotencyKey": request_id,
                "x-iam-token": _ZERO_CHECK_OPEN_API_TOKEN,
            },
        }
        await ws.send(json.dumps(request_frame))

        # A cancelled chat exits the loop with the empty sentinel: its
        # caller stopped waiting when the asyncio side was cancelled, so
        # the reply is discarded by definition, and quiet return keeps the
        # unwinding worker's bridge future free of an unretrieved
        # exception that only the dead await would have consumed.
        deadline = asyncio.get_event_loop().time() + self._chat_timeout_seconds
        accumulated_text = ""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return ""
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"[host-platform][chat] timeout waiting for final: id={request_id}"
                )
            try:
                raw = await asyncio.wait_for(
                    ws.recv(),
                    timeout=min(remaining, _RECV_SLICE_SECONDS),
                )
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                raise ConnectionError(f"[host-platform][chat] ws closed: {e}") from e

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "event" or data.get("event") != "chat":
                continue
            payload = data.get("payload", {})
            state = payload.get("state", "")
            if state == "final":
                for c in (payload.get("message", {}) or {}).get("content", []):
                    if c.get("type") == "text":
                        accumulated_text = c.get("text", "")
                return accumulated_text
            if state == "error":
                raise RuntimeError(
                    f"[host-platform][chat] agent error: "
                    f"{payload.get('errorMessage', 'unknown')}"
                )
            if state == "aborted":
                raise RuntimeError("[host-platform][chat] chat aborted")
