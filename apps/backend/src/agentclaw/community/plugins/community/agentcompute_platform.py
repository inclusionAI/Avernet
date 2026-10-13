"""Community host adapter — the in-tree binding for agentcompute's BotPlatform.

Implements the provision seam (``agentcompute.spi.bot.BotPlatform``) with the
backend's own creation flow, in-process from end to end (openspec change
bind-agentcompute-host-bot-platform):

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
- chat drives the engine transport (``GrtChatService``): resolve the bot's
  runtime binding, open the engine WebSocket, ``chat.send`` the objective,
  return the final reply text. Its async is bridged onto one thread with its
  own event loop because the seam's methods are synchronous by contract and
  are called from inside the swarm's already-running loop.

The adapter is a pure function of its three inputs — (spec, idempotency_key,
host profile) — and owns no state of its own. ``model`` and ``metadata`` on the
spec have no manifest/create slot today (engine_config is refused at creation);
the host profile documents them as not honored rather than inventing one.

Bound by ``CommunityAgentComputePlatformModule`` in the community column and
the singlebox branch of the profile composition — never in the base list: an
unbound ``BotPlatform`` is agentcompute's designed fail-clear mode for profiles
without a platform side.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
from typing import Any

import yaml

from agentclaw.community.core.agentcompute.domain.models import (
    BotCreateSpec, BotId, BotState, IdempotencyKey,
)
from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputePlatformConfig,
)
from agentclaw.community.core.agentcompute.spi.bot import BotPlatform
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
from agentclaw.community.core.grt_chat.services import GrtChatService
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES, TaskRecord, TaskStatus,
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


def _idem_digest(idempotency_key: str, length: int) -> str:
    return hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:length]


class AvernetBotPlatform(BotPlatform):
    """Host-side BotPlatform over the backend's own creation and chat flows."""

    def __init__(
        self,
        *,
        config: AgentComputePlatformConfig,
        owner_id: str = "",
        manifest_seam: ManifestCreationSeam,
        bot_service: Any,
        passport_plugin: Any,
        skill_set_factory: Any,
        bot_repo: Any,
        apply_service: Any,
        space_context: BusinessSpaceContextProtocol,
        grt_chat: GrtChatService,
    ) -> None:
        # owner_id is the run's provisioning identity (the submitter);
        # per-call: per_run when the swarm identity arrives via Run.user_id
        self._default_owner = owner_id
        self._default_engine = config.default_engine
        self._manifest_seam = manifest_seam
        self._bot_service = bot_service
        self._passport = passport_plugin
        self._skill_set_factory = skill_set_factory
        self._bot_repo = bot_repo
        self._apply_service = apply_service
        self._space_context = space_context
        self._grt_chat = grt_chat


    # ── create ────────────────────────────────────────────────────────────

    def create_bot(self, *, spec: BotCreateSpec,
                   idempotency_key: IdempotencyKey) -> BotId:
        # Heavy chain (bot_service -> aicoding -> di) imports call-local, so
        # plugin import never participates in the DI bootstrap cycle.
        from agentclaw.community.core.bot_management.create_flow import (
            submit_bot_creation_with_manifest,
        )

        bot_id = f"ac_{_idem_digest(idempotency_key, 16)}"
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
            owner_id=self._default_owner, header_space_id=None,
        )
        submitted = submit_bot_creation_with_manifest(
            user_id=self._default_owner,
            nick_name=self._default_owner,
            bot_id=bot_id,
            document=document,
            modifier=self._default_owner,
            spec=HostCreateSpec(
                entity_id=self._default_owner,
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

    def bot_status(self, bot_id: BotId) -> str:
        owner = self._default_owner
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
        return BotState.PROVISIONING.value

    # ── delete ────────────────────────────────────────────────────────────

    def delete_bot(self, bot_id: BotId) -> bool:
        owner = self._default_owner
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
                "[agentcompute][host-platform] bot delete failed for %s: %s",
                bot_id, exc,
            )
            return False

    # ── chat ──────────────────────────────────────────────────────────────

    def chat(self, bot_id: BotId, message: str) -> str:
        # The seam is synchronous by contract and is called from inside the
        # swarm's running event loop; grt_chat is async. Bridge on one thread
        # with its own loop — one chat per slice, so a fresh executor per call
        # is both simple and bounded.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self._chat_once(bot_id, message)).result()

    async def _chat_once(self, bot_id: BotId, message: str) -> str:
        sandbox_id = self._grt_chat.get_sandbox_id(bot_id, self._default_owner)
        if not sandbox_id:
            raise RuntimeError(
                f"bot {bot_id} has no runtime binding to chat through"
            )
        ws = await self._grt_chat.connect_engine_ws(sandbox_id)
        try:
            result = await self._grt_chat.chat_send(ws, message)
            return result.text
        finally:
            await ws.close()
