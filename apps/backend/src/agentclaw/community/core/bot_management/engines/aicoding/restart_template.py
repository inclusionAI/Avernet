from typing import TYPE_CHECKING, Any

from agentclaw.community.log import get_logger

from ..provisioning import BotProvisioningContext

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.template_service import (
        TemplateService,
    )

    from .strategy import AicodingProvisioningStrategy

logger = get_logger()


def apply_restart_template(
    strategy: 'AicodingProvisioningStrategy',
    ctx: BotProvisioningContext,
    extra_configs: dict[str, Any] | None,
    *,
    template_service: 'TemplateService',
    consuming_engines: frozenset[str],
) -> None:
    if strategy.normalize_engine_type(ctx.active_engine, default='') not in consuming_engines:
        return
    if not isinstance(extra_configs, dict):
        return
    candidate = extra_configs.get('template_config')
    if not isinstance(candidate, dict) or not candidate:
        return
    incoming_version = strategy._template_version_id(candidate)
    if extra_configs.get('confirmed_template_update'):
        persisted = strategy._with_restart_resync_marker(candidate, template_version_id=incoming_version)
    else:
        persisted = candidate
    arguments = dict(
        bot_id=ctx.bot_id,
        template_config=persisted,
        template_type=ctx.template_type,
        active_engine=ctx.active_engine,
    )
    expected_hash = extra_configs.get('expected_template_config_hash')
    if expected_hash is not None:
        saved = template_service.update_template_conditionally(**arguments, expected_hash=expected_hash)
    else:
        saved = template_service.create_or_update_template(**arguments)
    if not saved:
        raise RuntimeError('Failed to persist coding restart template snapshot')
    logger.info(
        '[aicoding.restart] persisted restart template snapshot bot_id=%s version=%s resync_marker=%s',
        ctx.bot_id,
        incoming_version,
        persisted is not candidate,
    )
