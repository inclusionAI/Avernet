"""Engine-config service — provider-blind read of a publish record's engine config.

The publish-stage engine-config endpoint
(``GET /api/service-bot/publish/{publish_id}/engine-config``) must show the engine
config actually deployed in that publish's container, regardless of which device backs
it (arca / baas / teclaw). This service reads it provider-blind, mirroring
:meth:`IdentityService._read_from_publish_device`: resolve the publish record's active
**stage** binding (``ext.binding`` by status) → ``resolve_for_binding`` →
``dispatch_addressed(namespace=config)`` → ``device_fs.read_file("config/<file>")``.

The config file is addressed by the canonical logical path
``config/teclaw.json`` for every provider — teclaw's ``to_engine_relative`` maps it to
``/config/teclaw.json``; arca/baas resolve their own concrete file
(``openclaw.json`` / ``config.json``) from ``engine_type`` via the dispatcher's
``build_arca_config_mapper`` (the logical leaf is ignored there).

The router resolves and passes ``engine_type`` (this service does no engine guessing),
and passes the already-fetched ``BotPublishRecord`` (no second lookup).

The bot-level read/write below address a bot rather than a publish record. The
read takes a ``stage``: the draft is the bot's own binding, and a service bot's
verify/online runtimes resolve through the shared rule in
``core/engine_runtime/stage.py``. Only the draft accepts a write.
"""
from __future__ import annotations

import json

from injector import inject

from agentclaw.community.core.bot_config_surface.coords import BotConfigCoords
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.config_compose.teclaw_paths import (
    CONFIG_NS,
    TECLAW_ENGINE_CONFIG_FILE,
)
from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.engine_runtime.stage import (
    require_stage_writable,
    resolve_stage_device_context,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    select_stage_bind_id,
)
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.log import get_logger

logger = get_logger()


# The canonical logical path every provider is addressed with.
# TODO(totalfrank): the leaf is always teclaw.json even for openclaw/claude_code —
# teclaw's to_engine_relative requires exactly "config/teclaw.json", while the
# arca/baas mapper ignores the leaf and derives the real filename (openclaw.json /
# config.json) from engine_type. So it's only correct because non-teclaw discards it
# — a smell. Cleaner: make `config` a leaf-less namespace where each provider's mapper
# owns the full filename, so callers pass no provider-specific name. Solve separately.
_CONFIG_LOGICAL_PATH = f"{CONFIG_NS}/{TECLAW_ENGINE_CONFIG_FILE}"


def engine_config_coords_from_record(
    bot_id: str, owner_id: str, *, bot_service
) -> BotConfigCoords:
    """Where the ``engine_config`` category writes, for a bot that exists.

    **This is the ownership guard as well as the address**, and the two are one
    call on purpose: ``bot_service.get_bot(bot_id, owner_id)`` resolves the bot
    only for the named owner, so a bot that is not theirs is indistinguishable
    from one that does not exist, and the record it returns is the same record
    the coordinates are read off. Check and address cannot disagree, because
    there is only one lookup.

    Alone among the five categories this one guards explicitly; ``resources``
    performs no ownership check at all. That difference is preserved rather than
    reconciled — closing it changes who the resources endpoints admit, which is
    a behaviour change and belongs to its own review.

    Raises ``BotNotFoundError`` on both of the paths the router raised it on: an
    unresolvable bot, and a resolvable bot carrying no ``entity_id``.
    """
    return engine_config_coords_from_bot(
        bot_service.get_bot(bot_id, owner_id), bot_id=bot_id, owner_id=owner_id
    )


def engine_config_coords_from_bot(
    bot: dict, *, bot_id: str, owner_id: str
) -> BotConfigCoords:
    """The record-to-address half, for a caller that already holds the record.

    Split out because there are two such callers and they guard differently:
    the engine-config operations resolve the bot *as* their guard (above), while
    the data-init operation fetches it, applies its own bot-type rule, and only
    then needs the address (``openapi_v1/bots/router.py:1495``). Folding the
    fetch in would make that second caller resolve the same bot twice, which is
    a behaviour change dressed as a tidy-up.

    Raises ``BotNotFoundError`` when the record carries no ``entity_id`` — a
    resolvable bot that cannot be addressed.
    """
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    return BotConfigCoords(
        bot_id=bot_id,
        owner_id=owner_id,
        entity_type=bot.get("entity_type") or "staff",
        entity_id=entity_id,
        engine_type=bot.get("active_engine") or DEFAULT_ENGINE_TYPE,
    )


def engine_config_coords_from_spec(
    bot_id: str,
    owner_id: str,
    *,
    entity_id: str,
    entity_type: str | None,
    engine_type: str | None,
) -> BotConfigCoords:
    """The same address, for a bot that does not exist yet.

    Every value comes off the create request, because in the create flow's first
    phase there is no record to read one from. **No ownership guard, and there
    cannot be one**: nothing exists to own. Whether the caller may create a bot
    at all is settled by ``check_create_bot_preflight``
    (``core/bot_management/create_flow.py:494``), beside which W13 validates the
    manifest.

    The two defaults match the record path exactly, so a bot validated at
    preflight and then created resolves to the same coordinates both times.

    No caller until W13 (#1696).
    """
    return BotConfigCoords(
        bot_id=bot_id,
        owner_id=owner_id,
        entity_type=entity_type or "staff",
        entity_id=entity_id,
        engine_type=engine_type or DEFAULT_ENGINE_TYPE,
    )


def _decode_config(content_bytes: bytes | None) -> dict:
    """Decode raw config bytes → dict; ``{}`` for a missing/empty file.

    Lets ``json.JSONDecodeError`` propagate (the caller maps it to a malformed-config
    response) — only an absent/empty file yields an empty result.
    """
    if not content_bytes or not content_bytes.strip():
        return {}
    return json.loads(content_bytes.decode("utf-8"))


class EngineConfigService:
    """Reads and writes a bot's engine config, provider-blind.

    Three addresses, two of them read-only: a publish record (``publish_id``), a
    bot at a named runtime stage, and a bot's draft for the one write.
    """

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
        publish_repo: BotPublishRepositoryProtocol,
        binding_repo: DeviceBindingRepository,
    ):
        self._bot_repo = bot_repo
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher
        # Both only for a read that names a published stage: finding that
        # stage's live runtime among this bot's publish records, and confirming
        # a retained verify binding is still ACTIVE. The draft read and every
        # write touch neither.
        self._publish_repo = publish_repo
        self._binding_repo = binding_repo

    async def read_publish_config(
        self, record: BotPublishRecord, engine_type: str
    ) -> dict:
        """Read the engine config JSON for a publish record's active stage.

        Args:
            record: The publish record (already fetched by the caller).
            engine_type: Concrete engine type resolved by the caller (required —
                selects the arca/baas config file and the per-bot engine dir).

        Returns:
            The parsed config dict on success; ``{}`` **only** when the config file is
            missing or empty on the device — a bot whose config has never been
            written reads as empty, which every ``DeviceFileSystem`` reports as
            ``None``. Every other failure is surfaced (raised), never collapsed into
            an empty result.

        Raises:
            DeviceNotBoundError: there is no resolvable active-stage device binding —
                either no stage ``bind_id`` at all, or the binding row is missing.
            UnknownProviderError / ConnInfoBuildError: the active-stage binding could
                not be resolved/reached — propagated so the caller surfaces a business
                error (not masked as an empty config).
            json.JSONDecodeError: the config file exists but is not valid JSON
                (the caller maps this to a malformed-config error response).

        A device that is unreachable or refuses the read raises out of ``read_file``
        rather than reading as an absent config — absence and unavailability must
        stay distinguishable, or a caller would overwrite a config it never read.
        """
        owner_id = record.owner_id
        bot_id = record.source_bot_id

        # entity coords for the arca/baas config mapper (teclaw ignores them); same
        # resolution as the bot-level engine-config GET.
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        entity_id = (bot or {}).get("entity_id") or owner_id
        entity_type = (bot or {}).get("entity_type") or "staff"

        ext = record.ext or {}
        # Select by record.status; a missing/0 bind_id means there is no resolvable
        # active-stage binding (binding PKs are ≥1, so 0 is never a real binding).
        # This is a real failure, not an empty config — surface it (don't swallow).
        # Deliberately NOT ``engine_runtime.stage.resolve_stage_bind_id``, which
        # the bot-level stage-addressed read below uses. The two answer different
        # questions and this one is keyed by *record*: the caller named a
        # ``publish_id``, and that rule picks the newest record at a status — so
        # routing this path through it would answer a question about release 7
        # from whichever release happens to be newest. The stage module's own
        # docstring draws the same line.
        bind_id = select_stage_bind_id(ext.get("binding", {}), record.status)
        if not bind_id:
            raise DeviceNotBoundError(
                f"EngineConfigService: no active-stage binding for "
                f"publish_id={record.id} (status={record.status})"
            )

        # Resolution errors are NOT swallowed — a binding that can't be resolved/reached
        # is a real failure and must surface to the caller, not masquerade as an empty
        # config (only a genuinely absent file returns {}).
        ctx = self._resolver.resolve_for_binding(int(bind_id), owner_id, bot_id=bot_id)

        device_fs = self._device_fs_dispatcher.dispatch_addressed(
            ctx, namespace=CONFIG_NS, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        content_bytes = await device_fs.read_file(_CONFIG_LOGICAL_PATH)
        return _decode_config(content_bytes)

    # ── bot-level (stage-addressed read, draft-only write) ───────────────────

    def _bot_config_device_fs(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        stage: str,
    ):
        """Resolve the runtime ``stage`` names and build its config-addressing
        DeviceFileSystem via the dispatcher (mirrors
        ``IdentityService._identity_device_fs``).

        The draft raises ``DeviceNotBoundError`` if the bot has no active
        binding, same as the legacy ``for_bot`` path; a published stage raises
        ``EngineStageNotLiveError`` when that stage has no live runtime. Both
        legs, and the reasons behind them, live in
        :func:`resolve_stage_device_context`.
        """
        ctx = resolve_stage_device_context(
            self._resolver,
            self._publish_repo,
            self._binding_repo,
            self._bot_repo,
            bot_id=bot_id,
            owner_id=owner_id,
            stage=stage,
        )
        return self._device_fs_dispatcher.dispatch_addressed(
            ctx, namespace=CONFIG_NS, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )

    async def read_bot_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        stage: str,
    ) -> dict:
        """Read a bot's engine config from the runtime ``stage`` names, provider-blind.

        ``stage`` is required rather than defaulted — see the Service API
        contract for why. ``STAGE_DRAFT`` is the bot's own workspace, what every
        caller read before stages were addressable.

        Returns the parsed dict; ``{}`` only when the file is missing/empty — a bot
        that has never had a config written reads as empty on every provider, not as
        an error. Resolve errors, a device that refuses the read, and
        ``json.JSONDecodeError`` propagate (the caller surfaces them), as does
        ``EngineStageNotLiveError`` for a published stage with no live runtime.
        """
        device_fs = self._bot_config_device_fs(
            bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
            entity_type=entity_type, engine_type=engine_type, stage=stage,
        )
        content_bytes = await device_fs.read_file(_CONFIG_LOGICAL_PATH)
        return _decode_config(content_bytes)

    async def write_bot_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        config: dict,
        stage: str,
    ) -> None:
        """Write a bot's engine config to its own device, provider-blind.

        Only the draft accepts a write. ``stage`` is taken so that a caller
        naming a published runtime is *refused* (``EngineStageReadOnlyError``)
        rather than having their edit quietly applied to the draft — a write
        that would report success for a runtime it never touched. The refusal is
        the first thing that happens, before any row or device is resolved, so
        nothing is written anywhere.

        Bytes are ``json.dumps(config, ensure_ascii=False, indent=2)`` — byte-identical
        to the legacy ``update_engine_config`` serialization. Resolve/write errors
        propagate (the caller surfaces them).
        """
        require_stage_writable(stage)
        device_fs = self._bot_config_device_fs(
            bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
            entity_type=entity_type, engine_type=engine_type, stage=stage,
        )
        payload = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
        await device_fs.write_file(_CONFIG_LOGICAL_PATH, payload)
