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
  bot exists, soft-delete the bot when one does, and never raise in
  teardown once a per-call owner is present — teardown must not change
  the slice outcome.
- identity is per-call, exclusively: every method refuses a blank
  ``owner_id`` (the run's authenticated submitter is the only owner any
  platform call ever acts under; no profile-level or ambient identity
  exists, and the SPI ``owner_id`` string never falls back to one).
- chat drives the engine transport directly: the chat endpoint comes from
  the canonical BaaS ws-info (``BaasService.get_ws_info_by_bot_uuid`` — the
  same resolution every other chat surface uses), and the adapter's
  engine frames (``connect`` handshake, one fail-soft ``sessions.reset``
  of the conversation the upcoming chat.send addresses — the engine's
  blessed fresh-conversation mechanism, since the engine serves only the
  sessions it knows and never auto-creates a chat on a novel key — then
  ``chat.send`` accumulate) are spoken in-process. A refused chat.send (the
  engine answers the outstanding request with ok false or accepted false)
  raises immediately instead of waiting out the whole receive deadline.
  The async-in engine leg runs on the calling thread's
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
spec have no create slot today (engine_config is refused at creation); the host
profile documents them as not honored rather than inventing one. The one
document-shaped input is ``config_manifest``: when the spec carries a full
five-category bot_config_manifest YAML document, the adapter submits it verbatim
(a suitable identity category is the caller's care); blank keeps the composed
identity-only persona document.

Bound by the ``AgentSwarmModule.bot_platform`` DI provider
(``di/modules/agentswarm``), which sits in the composition root's base module
list. Profiles whose platform-side dependencies that provider cannot resolve
get no ``BotPlatform`` at all (``provide_runtime`` resolves ``None`` there) —
an unbound ``BotPlatform`` is agentswarm's designed fail-clear mode for
profiles without a platform side.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import yaml

from agentclaw.community.core.agentswarm.domain.models import (
    BotCreateSpec, BotId, BotState, IdempotencyKey,
    NonRetryableProvisioningError,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform

# The engine-chat transport leg lives in host_chat.py (one cohesive
# concern, one file); re-exported here so external callers, DI wiring
# and the test seams keep importing from ``agentswarm.host``.
from agentclaw.community.plugins.community.agentswarm.host_chat import (
    ChatLegMixin, ChatTimeoutError, _CHAT_TIMEOUT_SECONDS,
    _DEFAULT_MAX_PLATFORM_WORKERS, _ZERO_CHECK_OPEN_API_TOKEN,
    chat_bridge_executor, set_chat_bridge_max_workers,
    shutdown_chat_bridge,
)
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER, CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    AUTHORIZATION_WINDOW_ELAPSED, BOT_COULD_NOT_BE_PROVISIONED,
    DEFAULT_CREATE_DEADLINE_SECONDS,
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
from agentclaw.community.plugin_api.passport import PassportError
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES, TaskStatus,
)
from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")

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
_HASHED_IDEMPOTENCY_ID_DIGITS = 16
#: The create job's ``Fail`` reason when the authorization ended terminally
#: without the service issuing the license (create_job: "Declined, expired,
#: or anything else terminal the service reports"). The reason carries the
#: service's own status after the colon — the only place a genuine human
#: rejection is named.
_AUTHORIZATION_INCOMPLETE_PREFIX = "authorization did not complete:"

@dataclass(frozen=True)
class BotAuthorizationDescriptor:
    """The pending authorization's observable facts.

    Sourced only from the durable creation-job payload — never a second
    copy of Passport state and never a live Passport re-query:
    ``applyAgentPassport`` returns the consent URLs once, at application
    time, and the job payload is the only durable home they have.
    The with-manifest HTTP poll serves the same payload's URLs; this
    descriptor rides the swarm's own status result so run-surface
    consumers can route a human to the official consent page. Approval
    itself happens only on that page — the swarm never approves,
    synthesizes, or stores an approval.

    ``deadline`` is the authorization window's end — the payload's
    ``submitted_at`` plus its frozen ``window_seconds`` — as an ISO-8601
    string, the same spelling the payload itself carries. It is empty
    only for a payload too old to name its submission time, in which
    case the queue's own deadline still bounds the creation.
    """
    state: str
    iframe_url: str
    redirect_url: str
    deadline: str


class BotStatusResult(str):
    """``bot_status``'s answer: the ``BotState`` string, optionally
    carrying the pending-authorization descriptor.

    A plain ``str`` for every existing consumer — equality with
    ``BotState`` values, string matchers, persistence — because the
    ``BotPlatform.bot_status -> str`` SPI contract is unchanged and this
    change must stay additive. The descriptor rides as an attribute the
    run-surface composition reads (``getattr(status, "authorization",
    None)``): present only on ``AWAITING_AUTHORIZATION``, absent on every
    other state, so a consumer never has to test the type to know that a
    state without the gate carries no descriptor.
    """

    __slots__ = ("authorization",)

    def __new__(cls, state: str,
                authorization: BotAuthorizationDescriptor | None = None):
        result = super().__new__(cls, state)
        result.authorization = authorization
        return result


def _idem_digest(idempotency_key: str, length: int) -> str:
    return hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:length]


def bot_id_for(run_id: str, agent_id: str) -> str:
    """Deterministic transient-bot identity for a (run, agent) pair.

    The platform's create derives the same value from the spawn's
    idempotency key, so a cleanup that only knows the run and agent ids
    can still address the bot that was created for it."""
    return f"agent_swarm_{_idem_digest(f'{run_id}:{agent_id}', _HASHED_IDEMPOTENCY_ID_DIGITS)}"


class AvernetBotPlatform(ChatLegMixin, BotPlatform):
    """Host-side BotPlatform over the backend's own creation and chat flows.

    The seam's methods are synchronous by contract. The async caller
    (bot_subagent.AvernetSubagent) offloads every platform leg to a
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
        manifest_seam: ManifestCreationSeam,
        bot_service: Any,
        passport_plugin: Any,
        skill_set_factory: Any,
        bot_repo: Any,
        apply_service: Any,
        space_context: BusinessSpaceContextProtocol,
        baas_service: BaasService,
        max_platform_workers: int = _DEFAULT_MAX_PLATFORM_WORKERS,
        chat_timeout_seconds: float = _CHAT_TIMEOUT_SECONDS,
    ) -> None:
        self._manifest_seam = manifest_seam
        self._bot_service = bot_service
        self._passport = passport_plugin
        self._skill_set_factory = skill_set_factory
        self._bot_repo = bot_repo
        self._apply_service = apply_service
        self._space_context = space_context
        self._baas_service = baas_service
        self._chat_timeout_seconds = chat_timeout_seconds
        set_chat_bridge_max_workers(max_platform_workers)


    # ── create ────────────────────────────────────────────────────────────

    def create_bot(self, *, spec: BotCreateSpec,
                   idempotency_key: IdempotencyKey, owner_id: str = "") -> BotId:
        # Heavy chain (bot_service -> aicoding -> di) imports call-local, so
        # plugin import never participates in the DI bootstrap cycle.
        from agentclaw.community.core.bot_management.create_flow import (
            submit_bot_creation_with_manifest,
        )
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNameExistsError,
        )
        from agentclaw.community.core.bot_management.bot_quota import (
            BotQuotaExceededError,
        )

        owner = (owner_id or "").strip()
        if not owner:
            raise RuntimeError(
                "provisioning requires the run's authenticated owner "
                "(per-call owner_id); no ambient identity exists"
            )
        # Same digest as bot_id_for(): the subagent always passes the
        # idempotency key f"{run_id}:{agent_id}", so a cleanup that only
        # knows the (run, agent) pair addresses the identical bot id.
        bot_id = (
            f"agent_swarm_{_idem_digest(idempotency_key, _HASHED_IDEMPOTENCY_ID_DIGITS)}")
        # One persona per (run, spec): the idempotency key derives the bot id,
        # so retries rebind the same creation. The platform caps bot_name at
        # 32 chars and a spec-derived name can exceed it: bot_name is the
        # id-format digest (28 chars); the composite label rides bot_desc.
        bot_name = bot_id
        bot_desc_label = f"{spec.name}-{_idem_digest(idempotency_key, 8)}"
        # A caller-supplied manifest REPLACES the synthesized persona
        # document (the platform's own preflight validates its grammar);
        # blank keeps identity-only synthesis.
        document = spec.config_manifest.strip() or yaml.safe_dump(
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
        # D16 idempotent resume: prior slice's job already tracking this identity.
        if self._manifest_seam.find_job(entity_id=owner, bot_id=bot_id):
            return BotId(bot_id)
        try:
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
                    bot_desc=(f"{bot_desc_label} — {spec.description}"
                              if (spec.description or "").strip()
                              else bot_desc_label)[:1024],
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
        except BotNameExistsError:
            # D16 respawn: the refused name carries our idempotency key's
            # digest, so the colliding live row IS our identity — adopt it
            # (a same-name row that is not ours cannot exist).
            existing = self._bot_repo.get_by_id_and_entity(bot_id, owner)
            if existing is None:
                raise
            logger.warning(
                "[agentswarm][host-platform] adopting live persona bot %s "
                "after create-name refusal (idempotency_key=%s)",
                bot_id, idempotency_key,
            )
            return BotId(bot_id)
        except BotQuotaExceededError as exc:
            # A Space-scoped quota ceiling fails every retry the same way,
            # so surface the swarm's non-retryable marker (the pool fails
            # the agent without burning the attempt budget).
            raise NonRetryableProvisioningError(str(exc)) from exc
        except ManifestValidationError as exc:
            # A refused manifest document is deterministic: the same
            # document preflight-refuses identically on every attempt,
            # so fail now with the non-retryable marker instead of
            # burning the attempt budget on identical refusals. Narrowly
            # the platform's own validation type (raised inside the
            # submit, before anything is persisted) — quota refusals
            # above keep their classification.
            raise NonRetryableProvisioningError(str(exc)) from exc
        except PassportError:
            # Cross-slice resume: passport already ISSUED — start the job directly.
            auth = self._passport.query_auth_status(bot_id=bot_id, owner_workno=owner)
            if auth and auth.get("status") == "ISSUED":
                self._manifest_seam.start_job(
                    bot_id=bot_id, entity_id=owner, user_id=owner,
                    document_owner=owner,
                    spec={"entity_id": owner, "engine_type": spec.engine or "openclaw",
                          "bot_type": spec.bot_type or "personal", "bot_name": bot_name,
                          "bot_desc": spec.description or "", "nick_name": owner},
                    iframe_url=None, redirect_url=None,
                )
                return BotId(bot_id)
            raise
        return BotId(submitted.bot_id)

    # ── status: the narrow table ──────────────────────────────────────────

    def bot_status(self, bot_id: BotId, owner_id: str = "") -> str:
        owner = (owner_id or "").strip()
        if not owner:
            raise RuntimeError(
                "provisioning requires the run's authenticated owner "
                "(per-call owner_id); no ambient identity exists"
            )
        bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
        job = self._manifest_seam.find_job(entity_id=owner, bot_id=bot_id)

        if job is None:
            # No creation job: either submit has not landed a row yet or an
            # unknown bot id — neither is a terminal verdict, so provisioning.
            return BotState.PROVISIONING.value
        if bot is None and job.status not in TERMINAL_STATUSES:
            # Live job, nothing created yet: first ask whether the creation
            # is parked at the human gate — an application
            # actually PENDING inside its authorization window is the one
            # in-flight state a human can move, and reporting it as generic
            # PROVISIONING hid the only actionable fact (the consent URL)
            # for the whole 600s window.
            awaiting = self._pending_authorization(job=job, bot_id=bot_id, owner=owner)
            if awaiting is not None:
                return awaiting
            # Authorization issued (or not answerable): the authorization
            # window and every pre-container state read the same from here.
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
            if last_error.startswith(_AUTHORIZATION_INCOMPLETE_PREFIX):
                # The authorization's own terminal status, verbatim from the
                # service — classify by what the service said, never by a
                # default: anything but a reported REJECTED is not a human
                # decline, and stamping REJECTED on it attributes to the user
                # a decision they never made.
                tail = last_error[len(_AUTHORIZATION_INCOMPLETE_PREFIX):].strip()
                if tail == "REJECTED":
                    return BotState.AUTHORIZATION_REJECTED.value
                if tail == "EXPIRED":
                    return BotState.AUTHORIZATION_EXPIRED.value
                return BotState.CREATE_FAILED.value
            return BotState.CREATE_FAILED.value

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
            if (bot_status_ not in _BOT_UP
                    and bool((bot.get("device_id") or "").strip())):
                # Mid-boot apply reports may flap FAILED under N-way quota
                # serialization — a live device binding owns the outcome,
                # so wait; an UP row keeps its apply verdict.
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

    # ── the human gate ────────────────────────────────────────────────

    def _pending_authorization(self, *, job, bot_id: BotId,
                               owner: str) -> BotStatusResult | None:
        """The awaiting-human verdict for a live, bot-less creation.

        ``BotStatusResult(AWAITING_AUTHORIZATION)`` scoped by the window
        and carrying the D1 descriptor, or ``None`` — which keeps the
        pre-change PROVISIONING answer — when this creation is not
        actually parked at the gate:

        - application not PENDING: the gate keys off the application's
          own status (``query_auth_status``), never off the creation
          surface or the mere presence of consent URLs in the payload,
          so an ISSUED creation — issued inline by the auto-issue
          fast path or by a human who already approved — proceeds
          through the normal provisioning answer even though its payload
          carries URLs;
        - a Passport that cannot answer is a wait, never a verdict —
          the same fail-soft rule the creation job's own status poll
          follows;
        - PENDING past the window's end: the job retires itself within
          one poll delay (queue deadline as the backstop) and the
          terminal branch below then answers from the job row; until
          then the in-progress answer stands rather than front-running
          the job's own discard.
        """
        if self._application_status(bot_id, owner) != "PENDING":
            return None
        descriptor = self._awaiting_authorization_descriptor(job)
        if descriptor is None:
            return None
        return BotStatusResult(
            BotState.AWAITING_AUTHORIZATION.value, descriptor)

    def _application_status(self, bot_id: BotId, owner: str) -> str | None:
        """The Passport application's own status, fail-soft to ``None``.

        The creation job polls the same provider (``query_auth_status``,
        5s cadence); this read borrows its answers rather than any
        Passport state of our own, and any failure — outage, provider
        without the method, malformed answer — reads as "no answer yet".
        """
        try:
            answer = self._passport.query_auth_status(
                bot_id=bot_id, owner_workno=owner)
        except Exception:  # noqa: BLE001 — a wait, not a verdict
            return None
        if not answer:
            return None
        return str(answer.get("status") or "")

    def _awaiting_authorization_descriptor(
            self, job) -> BotAuthorizationDescriptor | None:
        """The authorization facts from the job payload, in the window.

        ``None`` once the window (``submitted_at`` + the frozen
        ``window_seconds``, exactly the horizons the creation job itself
        enforces) has elapsed. A payload naming no readable submission
        time counts as fresh — the job's own expiry rule — and serves an
        empty deadline rather than inventing a clock.
        """
        payload = getattr(job, "payload", None) or {}
        submitted = str(payload.get("submitted_at") or "")
        try:
            window = int(payload.get("window_seconds")
                         or DEFAULT_CREATE_DEADLINE_SECONDS)
        except (TypeError, ValueError):
            window = DEFAULT_CREATE_DEADLINE_SECONDS
        started = None
        if submitted:
            try:
                started = datetime.fromisoformat(submitted)
            except ValueError:
                logger.warning(
                    "[agentswarm][host-platform] unreadable submitted_at=%r "
                    "in creation payload; leaving the window to the job",
                    submitted,
                )
        if started is not None and (
                datetime.now() - started > timedelta(seconds=window)):
            return None
        deadline = (
            (started + timedelta(seconds=window)).isoformat()
            if started is not None else "")
        return BotAuthorizationDescriptor(
            state=BotState.AWAITING_AUTHORIZATION.value,
            iframe_url=str(payload.get("iframe_url") or ""),
            redirect_url=str(payload.get("redirect_url") or ""),
            deadline=deadline,
        )

    # ── delete ────────────────────────────────────────────────────────────

    def delete_bot(self, bot_id: BotId, owner_id: str = "") -> bool:
        owner = (owner_id or "").strip()
        if not owner:
            # The blank-owner refusal is deliberately raised, not swallowed:
            # an ownerless delete could never address the right persona,
            # and the factory's sweep catches it as a logged miss.
            raise RuntimeError(
                "provisioning requires the run's authenticated owner "
                "(per-call owner_id); no ambient identity exists"
            )
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

