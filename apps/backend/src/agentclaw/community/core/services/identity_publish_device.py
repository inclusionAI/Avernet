"""Read identity files from a publish record's stage device binding.

The publish-read rule is deliberately keyed by the named publish record, not by
the current newest release for a stage. Keeping it separate lets stage-addressed
reads and record-addressed reads preserve their different precedence.
"""

from agentclaw.community.log import get_logger

from agentclaw.community.core.config_compose.teclaw_paths import IDENTITY_NS
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.service_bot.repository.models import (
    select_stage_bind_id,
)


logger = get_logger()


async def read_identity_from_publish_device(
    publish_repo,
    resolver,
    device_fs_dispatcher,
    *,
    publish_id: str,
    entity_type: str,
    entity_id: str,
    bot_id: str,
    file_type: str,
    operator_id: str,
    engine_type: str,
) -> str | None:
    """Read through the publish record's stage device binding.

    Resolves the stage ``bind_id`` (``ext.binding`` online→verify) and reads
    provider-blind via ``resolve_for_binding → dispatch_addressed →
    device_fs.read_file("identity/<file>")`` — covering arca / baas / teclaw
    uniformly (replaces the old ``ARCA_`` target string-sniff). Returns ``None``
    when the publish record / stage binding can't be resolved.
    """
    try:
        record = publish_repo.get_by_id(int(publish_id))
        if not record:
            logger.warning("publish_id=%s not found", publish_id)
            return None
        ext = record.ext or {}
        binding_info = ext.get("binding", {})
        # Select by `record.status`; the final guard then treats a missing/0
        # bind_id as "no stage binding" (binding PKs are >= 1, so 0 is never a
        # real binding).
        bind_id = select_stage_bind_id(binding_info, record.status)
        if not bind_id:
            logger.warning(
                "publish_id=%s stage bind_id missing", publish_id
            )
            return None
        try:
            ctx = resolver.resolve_for_binding(
                int(bind_id), operator_id, bot_id=bot_id
            )
        except (
            DeviceNotBoundError,
            UnknownProviderError,
            ConnInfoBuildError,
        ) as e:
            logger.warning(
                "publish_id=%s bind_id=%s resolve failed: %s",
                publish_id,
                bind_id,
                e,
            )
            return None
        device_fs = device_fs_dispatcher.dispatch_addressed(
            ctx,
            namespace=IDENTITY_NS,
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
        )
        content_bytes = await device_fs.read_file(f"{IDENTITY_NS}/{file_type}")
        return content_bytes.decode("utf-8") if content_bytes else ""
    except Exception as e:
        logger.error(
            "publish read failed for publish_id=%s: %s",
            publish_id,
            e,
        )
        return None
