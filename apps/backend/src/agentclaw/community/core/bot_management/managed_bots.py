import asyncio
from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.bot_service_protocol import (
    BotServiceProtocol,
)
from agentclaw.community.core.bot_management.engines.registry import resolve_status_ext
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.bot_management.managed_bots_protocol import (
    ManagedBotServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.bot_management.snapshot_guard import (
    ManagedBotInputError,
    template_snapshot_hash,
)
from agentclaw.community.core.repository.protocols.bot.template_bots import (
    TemplateBotRepositoryProtocol,
)


class ManagedBotService(ManagedBotServiceProtocol):
    @inject
    def __init__(self, repository: TemplateBotRepositoryProtocol, lifecycle: BotServiceProtocol) -> None:
        self.repository = repository
        self.lifecycle = lifecycle

    def query(
        self,
        *,
        template_key: str,
        offset: int,
        limit: int,
        search: str = '',
        stage: str = '',
        outdated_before: int | None = None,
    ) -> dict[str, Any]:
        return self.repository.query(
            template_key=template_key,
            offset=offset,
            limit=limit,
            search=search,
            stage=stage,
            outdated_before=outdated_before,
        )

    def snapshot(self, *, bot_id: str, owner_id: str, template_key: str) -> dict[str, Any]:
        snapshot = self.repository.snapshot(bot_id=bot_id, owner_id=owner_id, template_key=template_key)
        if snapshot is None:
            raise BotAddressNotFoundError('no Bot in the requested template scope')
        return snapshot

    def snapshot_context(self, *, bot_id: str, owner_id: str, template_key: str) -> dict[str, Any]:
        snapshot = self.snapshot(bot_id=bot_id, owner_id=owner_id, template_key=template_key)
        bot = self.lifecycle.get_bot(bot_id, owner_id)
        return {
            'template_config': snapshot,
            'template_config_hash': template_snapshot_hash(snapshot),
            'bot_name': bot.get('bot_name'),
            'active_engine': bot.get('active_engine'),
            'template_type': bot.get('template_type'),
            'bot_type': bot.get('bot_type'),
        }

    async def restart(
        self,
        *,
        bot_id: str,
        owner_id: str,
        template_key: str,
        template_config: dict[str, Any] | None = None,
        expected_template_config_hash: str | None = None,
    ) -> dict[str, Any]:
        await asyncio.to_thread(self.snapshot, bot_id=bot_id, owner_id=owner_id, template_key=template_key)
        if template_config is not None and template_config.get('template_key') != template_key:
            raise ManagedBotInputError('restart snapshot must belong to the requested template')
        if template_config is not None:
            if expected_template_config_hash is None:
                raise ManagedBotInputError('expected snapshot hash is required for a snapshot update')
        bot = await asyncio.to_thread(self.lifecycle.get_bot, bot_id, owner_id)
        if bot.get('bot_type') == 'desktop' or bot.get('active_engine') == 'teclaw':
            raise ManagedBotInputError('this Bot has a separately managed lifecycle')
        if template_config is not None and bot.get('active_engine') not in ('aicoding', 'claude_code'):
            raise ManagedBotInputError('this engine cannot update a coding template snapshot')
        extra_configs = None if template_config is None else {
            'template_config': template_config,
            'confirmed_template_update': 'true',
            'expected_template_config_hash': expected_template_config_hash,
        }
        return await self.lifecycle.restart_bot_async(
            bot_id=bot_id,
            user_id=owner_id,
            extra_configs=extra_configs,
        )

    def statuses(self, *, template_key: str, targets: list[tuple[str, str]]) -> list[dict[str, Any]]:
        items = []
        for bot in self.repository.statuses(template_key=template_key, targets=targets):
            item = {
                'bot_id': bot['bot_id'], 'owner_id': bot['owner_id'], 'status': bot.get('status') or '',
                'active_engine': bot.get('active_engine'),
                'is_ready': is_bot_ready(bot),
            }
            ext = resolve_status_ext(bot, response_ext={}, public=True)
            if ext:
                item['ext'] = ext
            items.append(item)
        return items
