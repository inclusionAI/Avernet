"""Community host adapter — the in-tree binding for agentswarm's BotPlatform.

Implements the provision seam (``agentswarm.spi.bot.BotPlatform``) in-process, over
the backend's own services.

**A run owns one identity; a persona is a worker under it.** That is the whole
shape, and every method below is written against it:

- create provisions one persona as a container under the run's swarmed bot. The
  first persona of a run establishes the identity (one AgentPass application, one
  bot record, applied with the union of what the roster declares so far); every
  later persona inherits it and applies for nothing. Persona content is stored
  before the container is requested and written into it afterwards — the container
  platform does not materialize files (see ``persona_delivery``).
- status reports **the worker's binding**, not a bot record: a worker has no row of
  its own, so its state is the state of the ``ac_entity_device_binding`` that
  carries it. ``AWAITING_AUTHORIZATION`` is unreachable — a run never parks for a
  human decision.
- delete releases the worker's container and never destroys the run's identity;
  ``end_run`` destroys that once, at the end of the run.
- identity is per-call, exclusively: every method refuses a blank ``owner_id`` (the
  run's authenticated submitter is the only owner any platform call ever acts
  under; no profile-level or ambient identity exists).
- chat drives the engine transport directly: the device comes from the worker's
  binding, ws-info from the canonical BaaS resolution, and the adapter's engine
  frames (``connect`` handshake, one fail-soft ``sessions.reset``, then
  ``chat.send`` accumulate) are spoken in-process. A refused chat.send raises
  immediately instead of waiting out the receive deadline. The wait is bounded, and
  a cancelled chat unwinds cooperatively between receive slices.

The adapter owns no state of its own beyond the per-run identity registry and the
bounded chat bridge; ``model`` and ``metadata`` on the spec have no create slot
today, and ``config_manifest`` supplies the MCP codes a persona contributes to the
run's scope (its identity category is delivered separately).

Bound by the ``AgentSwarmModule.bot_platform`` DI provider
(``di/modules/agentswarm``). Profiles whose platform-side dependencies that provider
cannot resolve get no ``BotPlatform`` at all — an unbound ``BotPlatform`` is
agentswarm's designed fail-clear mode for profiles without a platform side.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import threading
from typing import Any

import yaml

from agentclaw.community.core.agentswarm.domain.models import (
    BotCreateSpec, BotId, BotState, IdempotencyKey,
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
from agentclaw.community.core.content_store.models import ContentScope
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER, CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.community.agentswarm.persona_delivery import (
    deliver_persona,
    store_persona,
)
from agentclaw.community.plugins.community.agentswarm.run_registry import (
    SwarmIdentityRegistry,
)
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger("agentswarm")

_CREATION_TRIGGERS = frozenset({CREATE_PRE_CONTAINER_TRIGGER, CREATE_ON_CONTAINER_TRIGGER})
_BOT_UP = frozenset({"ACTIVE"})
_BOT_DEAD = frozenset({"FAILED", "DELETED", "INACTIVE"})
# Re-exported from ``swarm_identity``: the worker binding family has one
# definition, and it is the caller-instance family (production's shape).
from agentclaw.community.plugins.community.agentswarm.swarm_identity import (
    SWARM_WORKER_APPLY_REASON_PREFIX,  # noqa: F401  re-export
)
_HASHED_IDEMPOTENCY_ID_DIGITS = 16
def _idem_digest(idempotency_key: str, length: int) -> str:
    return hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:length]


def bot_id_for(run_id: str, agent_id: str) -> str:
    """Deterministic transient-bot identity for a (run, agent) pair.

    The platform's create derives the same value from the spawn's
    idempotency key, so a cleanup that only knows the run and agent ids
    can still address the bot that was created for it."""
    return f"agent_swarm_{_idem_digest(f'{run_id}:{agent_id}', _HASHED_IDEMPOTENCY_ID_DIGITS)}"


def run_id_from_idempotency_key(idempotency_key: str) -> str:
    """The run a spawn belongs to, read back off its idempotency key.

    The key is ``f"{run_id}:{agent_id}:{attempt}"`` (bot_subagent), and a run id
    may itself contain colons — so the run id is everything before the last two
    segments, never a naive first-segment split. A key with fewer than two
    separators is treated as its own run: the swarm would rather group two
    spawns than crash a provisioning path on a key shape it did not compose.
    """
    parts = idempotency_key.rsplit(":", 2)
    if len(parts) == 3:
        return parts[0]
    return idempotency_key


def run_sync(coro: Any) -> Any:
    """Run a coroutine from a synchronous call, on the running loop if there is one.

    ``BotPlatform`` is synchronous by contract and the subagent calls it from a
    worker thread via ``asyncio.to_thread``. Persona delivery is one small async
    write, so it is bridged here rather than threading async through the whole
    adapter. A loop already running on this thread is the one case that must not
    block on itself: the delivery then runs on a private loop in a helper thread,
    which is correct and never deadlocks.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _swarm_bot_name(run_id: str) -> str:
    """A platform-safe name for the run's identity record.

    ``bot_name`` is capped at 32 characters by the platform, so the run id rides a
    digest rather than the name itself; ``bot_desc`` carries the readable label.
    """
    return f"agent_swarm_{_idem_digest(run_id, 16)}"


def _mcp_codes_from_document(document: str) -> tuple[str, ...]:
    """The MCP codes a persona's manifest declares, or none.

    Tolerant by design: a persona whose document is blank, unparsable, or carries
    no ``mcp`` category declares no MCP resources, and contributes nothing to the
    run's scope. A malformed document is refused by the create-time preflight
    that owns document grammar; this function must not become a second validator.
    """
    text = (document or "").strip()
    if not text:
        return ()
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return ()
    if not isinstance(parsed, dict):
        return ()
    manifest = parsed.get("manifest")
    if not isinstance(manifest, dict):
        return ()
    entries = manifest.get("mcp")
    if not isinstance(entries, list):
        return ()
    codes: dict[str, None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = entry.get("server_code") or entry.get("code")
        if isinstance(code, str) and code.strip():
            codes.setdefault(code.strip(), None)
    return tuple(codes)


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
        identity_repository: Any = None,
        content_store: Any = None,
        identity_files: Any = None,
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
        #: Persona content: stored in the content-addressed store, written into a
        #: worker through the identity port once its container is up (design D5).
        self._content_store = content_store
        self._identity_files = identity_files
        # One run owns one AgentPass; there is no second provisioning path to
        # select between, so the registry is always built.
        self._run_registry = SwarmIdentityRegistry(
            passport=passport_plugin,
            bot_service=bot_service,
            repository=identity_repository,
        )
        set_chat_bridge_max_workers(max_platform_workers)

    @staticmethod
    def _require_owner(owner_id: str) -> str:
        """The run's authenticated owner, or a refusal — never an ambient identity."""
        owner = (owner_id or "").strip()
        if not owner:
            raise RuntimeError(
                "provisioning requires the run's authenticated owner "
                "(per-call owner_id); no ambient identity exists"
            )
        return owner

    @property
    def _device_binding_repo(self) -> Any:
        """The device-binding repository, reached through the bot service.

        The bot service already owns it (it is what creation writes bindings
        with), so the adapter reads it from there rather than taking a second
        constructor dependency for one call.
        """
        return getattr(self._bot_service, "_device_binding_repo", None)


    # ── create ────────────────────────────────────────────────────────────

    def create_bot(self, *, spec: BotCreateSpec,
                   idempotency_key: IdempotencyKey, owner_id: str = "") -> BotId:
        """Provision one persona as a worker under its run's single identity.

        There is one creation path. A run owns one AgentPass, established by its
        first persona; every later persona inherits it. A worker is a container
        bound to that identity, not a bot with an identity of its own — the
        per-attempt path that minted one was removed with this change.
        """
        return self._create_worker(
            spec=spec, idempotency_key=idempotency_key, owner_id=owner_id)

    def _create_worker(self, *, spec: BotCreateSpec,
                       idempotency_key: IdempotencyKey, owner_id: str) -> BotId:
        """Provision one persona as a worker under its run's single identity.

        The run's AgentPass is established on the first persona and inherited by
        every later one; this call never applies for a second identity. A worker is
        still a container, so it is provisioned through the container platform and
        bound to the swarm bot — which is what keeps the run at one identity and N
        containers.

        Persona content is stored **before** the container is requested and written
        into it **after** it is up: the platform does not materialize files, so a
        persona cannot ride along with ``create_bot``. See :mod:`persona_delivery`.
        """
        owner = self._require_owner(owner_id)
        run_id = run_id_from_idempotency_key(idempotency_key)
        registry = self._run_registry
        if registry is None:  # pragma: no cover - guarded by the constructor
            raise RuntimeError("shared-identity provisioning is not configured")

        space = self._space_context.resolve_current(
            owner_id=owner, header_space_id=None,
        )
        engine = spec.engine or "openclaw"
        identity = registry.establish(
            run_id=run_id,
            owner_id=owner,
            nick_name=owner,
            bot_name=_swarm_bot_name(run_id),
            bot_desc=f"agent swarm run {run_id}"[:1024],
            engine_type=engine,
            mcp_codes=self._persona_mcp_codes(spec, owner),
            bot_type=spec.bot_type or "personal",
            entity_id=owner,
            space_id=space.id if getattr(space, "id", None) is not None else None,
            space_quota=True,
        )

        # The worker id is the **spawn's** identity, not the run's: it is derived from
        # the idempotency key, which the subagent composes as
        # ``f"{run_id}:{agent_id}:{attempt}"``. Hashing the key whole is what makes
        # a same-slice retry mint a fresh worker and a replayed attempt re-derive the
        # crashed slice's id — ``spawn_digest``'s contract, which this must match or
        # a cleanup would address a worker that was never created.
        worker_id = f"agent_swarm_{_idem_digest(idempotency_key, _HASHED_IDEMPOTENCY_ID_DIGITS)}"
        persona_key = spec.name or worker_id

        # Store first: a persona whose content cannot be stored must not get a
        # container that will boot blank.
        stored = self._store_persona(
            identity=identity, owner=owner, spec=spec,
            engine=engine, persona_key=persona_key,
        )

        worker = self._provision_worker_container(
            identity=identity,
            worker_id=worker_id,
            owner=owner,
            spec=spec,
            engine=engine,
        )
        registry.bind_worker(
            run_id=run_id,
            owner_id=owner,
            persona_key=persona_key,
            worker_id=worker_id,
            bot_uuid=worker["bot_uuid"],
            binding_id=worker.get("binding_id"),
            device_repo=self._device_binding_repo,
            entity_id=owner,
            env=get_current_env(),
        )
        if stored is not None:
            self._deliver_persona(
                identity=identity, owner=owner, worker_id=worker_id,
                engine=engine, stored=stored,
            )
        logger.info(
            "[agentswarm][shared-identity] worker provisioned run=%s swarm=%s "
            "worker=%s uuid=%s",
            run_id, identity.bot_id, worker_id, worker["bot_uuid"],
        )
        return BotId(worker_id)

    def _store_persona(self, *, identity: Any, owner: str, spec: BotCreateSpec,
                       engine: str, persona_key: str) -> Any:
        """File this persona's content, or ``None`` when no store is bound."""
        content = getattr(self, "_content_store", None)
        if content is None:
            logger.warning(
                "[agentswarm][shared-identity] no content store bound; persona "
                "%s will boot without content", persona_key,
            )
            return None
        return store_persona(
            content=content,
            scope=ContentScope(
                env=get_current_env(), entity_id=owner, bot_id=identity.bot_id
            ),
            instructions=spec.instructions,
            engine_type=engine,
            identity_file=spec.identity_file,
            modifier=owner,
            persona_key=persona_key,
        )

    def _deliver_persona(self, *, identity: Any, owner: str, worker_id: str,
                         engine: str, stored: Any) -> None:
        """Write the stored persona into the worker's running container.

        A delivery failure is raised — the caller fails this persona and reports it —
        but it never fails the run: the other personas are independent, and the swarm
        retries the attempt. The worker is left provisioned in that case, which is
        deliberate: the container is real and reusable, so the retry rebinds it
        instead of paying for a second one.
        """
        identity_files = getattr(self, "_identity_files", None)
        if identity_files is None:
            logger.warning(
                "[agentswarm][shared-identity] no identity port bound; persona "
                "content for %s was stored but not delivered", worker_id,
            )
            return
        try:
            run_sync(
                deliver_persona(
                    content=self._content_store,
                    identity_files=identity_files,
                    stored=stored,
                    entity_type="staff",
                    entity_id=owner,
                    worker_id=worker_id,
                    owner_id=owner,
                    engine_type=engine,
                )
            )
        except Exception as exc:
            # Name the persona and the worker: the failure is this instance's, and
            # an operator reading the log must be able to tell which one.
            logger.error(
                "[agentswarm][shared-identity] persona delivery failed "
                "worker=%s file=%s: %s",
                worker_id, getattr(stored, "file_type", "?"), exc,
            )
            raise

    @staticmethod
    def persona_delivery_failure(
        *, worker_id: str, file_type: str, error: BaseException
    ) -> str:
        """The one-line report a failed persona write contributes to its run.

        Kept as a function rather than an f-string at the call site so the shape is
        testable: a run reports *which* persona failed and *what* it was writing,
        and never spends those words on a persona that succeeded.
        """
        return f"persona {worker_id} failed writing {file_type}: {error}"

    def _swarm_passport_token(self, *, identity: Any, owner: str) -> str:
        """The run's current AgentPass token, or a refusal.

        Fetched rather than remembered: the token is deliberately not persisted, and
        a cached copy would go stale the moment the identity is reissued. A run whose
        token cannot be read cannot provision a working worker, so this raises
        instead of handing the platform an empty credential.
        """
        query = getattr(self._passport, "query_token", None)
        if query is None:
            raise RuntimeError(
                "the Passport provider cannot answer query_token; a worker "
                "cannot be provisioned without the run's token"
            )
        token = query(identity.bot_id, owner)
        if not token:
            raise RuntimeError(
                f"the run's AgentPass for {identity.bot_id} has no token; "
                f"refusing to provision a worker that could not act"
            )
        return str(token)

    @staticmethod
    def _persona_mcp_codes(spec: BotCreateSpec, owner: str) -> tuple[str, ...]:
        """The MCP codes a persona declares, from its manifest when it carries one.

        A persona may ship a five-category document; only the ``mcp`` category is
        this function's business — the run's scope is the union of what personas
        declare, and persona content is delivered to its worker separately.
        """
        return _mcp_codes_from_document(spec.config_manifest)

    def _provision_worker_container(
        self,
        *,
        identity: Any,
        worker_id: str,
        owner: str,
        spec: BotCreateSpec,
        engine: str,
    ) -> dict[str, Any]:
        """Allocate one container for this persona under the swarm bot."""
        bot_info = {
            "bot_id": worker_id,
            "bot_name": worker_id,
            "entity_id": owner,
            "entity_type": "staff",
            "bot_desc": (spec.description or "")[:1024],
            "active_engine": engine,
            "bot_type": spec.bot_type or "personal",
        }
        # The worker presents the **run's** credential. The token is never stored —
        # it is fetched on demand, the rule every Passport surface follows — so the
        # swarm's agent code is read back here and handed to the platform, which
        # writes it into the sandbox's credentials. Empty is not an option: a worker
        # with no token can reach nothing, and failing loudly beats provisioning a
        # container that cannot act.
        token = self._swarm_passport_token(identity=identity, owner=owner)
        result = self._baas_service.create_bot(
            bot=bot_info,
            owner_id=owner,
            request_id=worker_id,
            device_count=1,
            migration_path=None,
            agent_pass_token=token,
        )
        bot_uuid = (result or {}).get("bot_uuid")
        if not bot_uuid:
            raise RuntimeError(
                f"container platform returned no bot_uuid for worker {worker_id}"
            )
        return {"bot_uuid": bot_uuid, "binding_id": result.get("binding_id")}

    # ── status: the narrow table ──────────────────────────────────────────

    def bot_status(self, bot_id: BotId, owner_id: str = "") -> str:
        """A worker's provisioning state, read from the container it was given.

        There is no creation job under shared identity: the run's identity is
        applied synchronously by the first persona, so a worker has exactly one
        question to answer — is my container up. ``AWAITING_AUTHORIZATION`` is
        therefore unreachable here, which is the point: a run never parks for a
        human decision.
        """
        owner = self._require_owner(owner_id)

        # A worker is an instance of the run's bot, not a bot of its own: its state
        # is the state of the device binding that carries it. A run's bot record is
        # one row for the whole run, so reading it would report every persona the
        # same and could not tell a PENDING instance from an ACTIVE one — the very
        # distinction production's rows carry.
        binding = self._find_worker_binding(bot_id=bot_id, owner=owner)
        if binding is not None:
            status = str(getattr(binding, "status", "") or "").upper()
            if status == "ACTIVE":
                return BotState.READY.value
            if status == "FAILED":
                return BotState.CREATE_FAILED.value
            return BotState.PROVISIONING.value

        bot = self._bot_repo.get_by_id_and_entity(bot_id, owner)
        if bot is None:
            # Provisioning has not landed the binding yet, or the id is unknown —
            # neither is terminal.
            return BotState.PROVISIONING.value

        bot_status_ = str(bot.get("status") or "")
        if bot_status_ in _BOT_DEAD:
            return BotState.CREATE_FAILED.value
        if bot_status_ not in _BOT_UP:
            return BotState.PROVISIONING.value

        report = self._apply_service.last_apply(entity_id=owner, bot_id=bot_id)
        if report is not None and report.trigger in _CREATION_TRIGGERS:
            # The creation's own apply outranks job liveness: the
            # post-container phase runs while the job is still live, so
            # READY/APPLY_FAILED are answerable from the report even then
            # (a later explicit apply — "how is the bot configured now" —
            # is not read here).
            if report.status is ApplyStatus.RUNNING:
                # An active container can precede its post-container apply.
                # Keep polling instead of killing a still-provisioning bot.
                # Kept from REL20261105 when this branch moved onto it.
                return BotState.PROVISIONING.value
            if report.status is ApplyStatus.SUCCEEDED:
                return BotState.READY.value
            return BotState.APPLY_FAILED.value
        # A worker whose container is up has nothing left to wait for.
        return BotState.READY.value

    def _find_worker_binding(self, *, bot_id: BotId, owner: str) -> Any:
        """The binding carrying this worker, or ``None``.

        The worker id *is* the instance discriminator: it is derived from the spawn
        and handed back on every later call, and the binding records it as
        ``applied_by``. So the lookup is exact — no candidate list, no fallback —
        and a worker is never confused with a sibling in the same run.
        """
        finder = getattr(self._device_binding_repo, "find_worker_binding", None)
        if finder is None:
            return None
        return finder(
            owner_id=owner,
            applied_by=str(bot_id),
            env=get_current_env(),
        )

    # ── delete ────────────────────────────────────────────────────────────

    def delete_bot(self, bot_id: BotId, owner_id: str = "") -> bool:
        """Release one worker; the run's identity is destroyed by ``end_run``."""
        return self._delete_worker(bot_id=bot_id, owner_id=owner_id)

    def _delete_worker(self, *, bot_id: BotId, owner_id: str) -> bool:
        """Release one worker, and never touch the run's identity (design D8).

        A worker is a container under the swarm bot, not a bot in its own right, so
        it has no Passport of its own to destroy. Destroying the identity here would
        destroy it for every other worker in the run at the first teardown — the
        exact failure D8 names. The identity is destroyed once, by the run.
        """
        owner = self._require_owner(owner_id)
        try:
            # The worker's container is addressed by the id derived from its spawn.
            # A container that was never created is already in the desired state.
            released = self._release_worker_container(
                worker_id=str(bot_id), owner=owner, reason="swarm worker teardown"
            )
            if not released:
                logger.info(
                    "[agentswarm][shared-identity] worker %s had no live container "
                    "to release", bot_id,
                )
                return True
            # Mark the binding RELEASED. Without it the row stays ACTIVE and
            # `bot_status` keeps answering READY for a worker whose container is
            # gone — the table would describe a worker that no longer exists.
            self._release_worker_binding(bot_id=bot_id, owner=owner)
            return True
        except Exception as exc:  # best-effort by contract: never raise
            logger.warning(
                "[agentswarm][shared-identity] worker teardown failed for %s: %s",
                bot_id, exc,
            )
            return False

    def _release_worker_binding(self, *, bot_id: BotId, owner: str) -> None:
        """Flip the worker's binding to RELEASED, best-effort.

        The binding is the worker's only record, so leaving it ACTIVE after the
        container is gone is how a torn-down worker keeps reading as available.
        """
        release = getattr(self._device_binding_repo, "release_binding", None)
        if release is None:
            return
        binding = self._find_worker_binding(bot_id=bot_id, owner=owner)
        binding_id = getattr(binding, "id", None)
        if not binding_id:
            return
        try:
            release(
                binding_id=int(binding_id),
                release_reason="swarm worker teardown",
                released_by=owner,
            )
        except Exception as exc:  # noqa: BLE001 - teardown is best-effort
            logger.warning(
                "[agentswarm][shared-identity] worker binding %s not released: %s",
                binding_id, exc,
            )

    def _release_worker_container(
        self, *, worker_id: str, owner: str, reason: str
    ) -> bool:
        """Release the platform container a worker was given, if it still exists.

        The container is addressed by the **platform's** uuid, not by the worker id:
        the worker id is ours, ``bot_uuid`` is what the platform minted, and only the
        latter names a container it knows. The binding records it as ``device_id``,
        which is why teardown reads the binding first.

        Best-effort: a container already gone is the desired end state, and a
        platform refusal must not change the slice outcome.
        """
        destroy = getattr(self._baas_service, "destroy_bot", None)
        if destroy is None:
            return False

        binding = self._find_worker_binding(bot_id=BotId(worker_id), owner=owner)
        bot_uuid = str(getattr(binding, "device_id", "") or "").strip()
        if not bot_uuid:
            # No binding: the worker never got a container, so there is nothing
            # to release. Distinguishing this from a failed release matters —
            # the caller reports a miss only for the latter.
            return False

        # ``request_id`` is the platform's idempotency handle for the destroy, so
        # it is derived from the container being destroyed rather than minted: a
        # retried teardown addresses the same request.
        request_id = _idem_digest(f"destroy:{bot_uuid}", 32)
        destroy(bot_uuid=bot_uuid, operator=owner, request_id=request_id)
        return True

    def end_run(self, *, run_id: str, owner_id: str) -> bool:
        """Destroy the run's identity exactly once, after its workers are gone (D8).

        This is the only place a run's AgentPass is destroyed under shared
        identity. It is idempotent across processes because the decision is made
        from the stored row, not from memory. A run that failed before phase one
        has no identity and answers ``False``.
        """
        registry = getattr(self, "_run_registry", None)
        if registry is None:
            return False
        owner = self._require_owner(owner_id)
        identity = registry.identity_for(run_id)
        destroyed = registry.destroy_identity(run_id, owner_id=owner)
        if identity is not None:
            self._release_swarm_record(identity=identity, owner=owner)
        return destroyed

    def _release_swarm_record(self, *, identity: Any, owner: str) -> None:
        """Soft-delete the run's bot row once its identity is gone.

        The row is not freed by destroying the Passport, and it is **not** free to
        leave behind: the bot quota counts every cloud bot an owner holds, and a
        leaked row per run would consume an owner's allowance until creating any
        bot is refused. Teardown of the run is the end of that row's purpose.

        Best-effort: a failure leaves a soft-deletable row rather than breaking the
        run's completion, and the identity is already released by this point.
        """
        delete = getattr(self._bot_service, "delete_bot", None)
        if delete is None:
            return
        try:
            delete(identity.bot_id, owner)
        except Exception as exc:  # noqa: BLE001 - run completion must not fail here
            logger.warning(
                "[agentswarm][shared-identity] run bot row %s not released: %s",
                identity.bot_id, exc,
            )

