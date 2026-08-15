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
"""
from __future__ import annotations

import json

import httpx
from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.config_compose.teclaw_paths import (
    CONFIG_NS,
    TECLAW_ENGINE_CONFIG_FILE,
)
from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
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


async def _read_config_bytes(device_fs) -> bytes | None:
    """The engine config file's bytes, or ``None`` when the device has no such file.

    A bot whose engine config has never been written has no file to read, and the
    providers disagree about how they say so: teclaw/local answer ``None``, while
    arca/baas surface the container's HTTP 404 as ``httpx.HTTPStatusError`` — their
    ``read_file`` re-raises *every* status on purpose, because a swallowed 401 once
    read as "file gone" and silently dropped promoted files at draft→verify.

    This domain's contract is "absent means empty" (see the ``{}`` promise on both
    read methods), so the 404 is reconciled here, once, for every caller and every
    provider — the same seam ``IdentityService._device_read`` closes for identity
    files, and for the same reason: without it a bot that simply has no config yet
    answers 500, because ``httpx.HTTPStatusError`` is not in ``ENVELOPE_ERRORS``.

    Every other status still propagates. A proxy 401, a sandbox 5xx, or a device
    that is not reachable is a real failure, and reporting it as an unconfigured
    bot would invite the caller to overwrite a config they never managed to read.
    """
    try:
        return await device_fs.read_file(_CONFIG_LOGICAL_PATH)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


def _decode_config(content_bytes: bytes | None) -> dict:
    """Decode raw config bytes → dict; ``{}`` for a missing/empty file.

    Lets ``json.JSONDecodeError`` propagate (the caller maps it to a malformed-config
    response) — only an absent/empty file yields an empty result.
    """
    if not content_bytes or not content_bytes.strip():
        return {}
    return json.loads(content_bytes.decode("utf-8"))


class EngineConfigService:
    """Reads a service bot's deployed engine config for a publish record, provider-blind."""

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
    ):
        self._bot_repo = bot_repo
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher

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
            missing or empty on the device — including the provider 404 arca/baas
            raise for an absent file, which :func:`_read_config_bytes` reconciles.
            Every other failure is surfaced (raised), never collapsed into an empty
            result.

        Raises:
            DeviceNotBoundError: there is no resolvable active-stage device binding —
                either no stage ``bind_id`` at all, or the binding row is missing.
            UnknownProviderError / ConnInfoBuildError: the active-stage binding could
                not be resolved/reached — propagated so the caller surfaces a business
                error (not masked as an empty config).
            json.JSONDecodeError: the config file exists but is not valid JSON
                (the caller maps this to a malformed-config error response).
            httpx.HTTPStatusError: the device answered with a failing status other
                than 404 — a proxy 401, a sandbox 5xx. Only 404 means "no config".
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
        content_bytes = await _read_config_bytes(device_fs)
        return _decode_config(content_bytes)

    # ── bot-level (draft/current binding) read + write ───────────────────────

    def _bot_config_device_fs(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        engine_type: str,
    ):
        """Resolve the bot's own (draft/current) binding and build its config-addressing
        DeviceFileSystem via the dispatcher (mirrors
        ``IdentityService._identity_device_fs``). Raises ``DeviceNotBoundError`` if the
        bot has no active binding — same as the legacy ``for_bot`` path."""
        ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
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
    ) -> dict:
        """Read a bot's engine config from its own device, provider-blind.

        Returns the parsed dict; ``{}`` only when the file is missing/empty — a bot
        that has never had a config written reads as empty on every provider, not as
        an error (see :func:`_read_config_bytes`). Resolve errors, a non-404 device
        status, and ``json.JSONDecodeError`` propagate (the caller surfaces them).
        """
        device_fs = self._bot_config_device_fs(
            bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
            entity_type=entity_type, engine_type=engine_type,
        )
        content_bytes = await _read_config_bytes(device_fs)
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
    ) -> None:
        """Write a bot's engine config to its own device, provider-blind.

        Bytes are ``json.dumps(config, ensure_ascii=False, indent=2)`` — byte-identical
        to the legacy ``update_engine_config`` serialization. Resolve/write errors
        propagate (the caller surfaces them).
        """
        device_fs = self._bot_config_device_fs(
            bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
            entity_type=entity_type, engine_type=engine_type,
        )
        payload = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
        await device_fs.write_file(_CONFIG_LOGICAL_PATH, payload)
