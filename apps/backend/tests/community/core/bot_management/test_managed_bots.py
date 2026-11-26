from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.bot_management.managed_bots import ManagedBotService
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.di.openapi_bot_operations_config import OpenApiBotOperationsConfig
from agentclaw.community.core.bot_management.snapshot_guard import template_snapshot_hash


def test_trust_is_scoped_by_environment_tenant_and_verified_app():
    config = OpenApiBotOperationsConfig(frozenset({('pre', 'team', 'manager')}))
    assert config.allows(env='pre', tenant='team', app_name='manager')
    assert not config.allows(env='prod', tenant='team', app_name='manager')
    assert not config.allows(env='pre', tenant='other', app_name='manager')
    assert not config.allows(env='pre', tenant='team', app_name='stranger')
    assert not OpenApiBotOperationsConfig().allows(env='pre', tenant='team', app_name='manager')


@pytest.mark.asyncio
async def test_restart_reuses_lifecycle_with_string_confirmation_and_complete_snapshot():
    repository = Mock()
    repository.snapshot.return_value = {'template_key': 'coding', 'template_version_id': 3}
    lifecycle = Mock()
    lifecycle.get_bot.return_value = {'bot_type': 'service', 'active_engine': 'aicoding'}
    lifecycle.restart_bot_async = AsyncMock(return_value={'bot_id': 'bot'})
    service = ManagedBotService(repository, lifecycle)
    snapshot = {'template_key': 'coding', 'template_version_id': 4, 'envs': {'CUSTOM': 'yes'}}

    result = await service.restart(bot_id='bot', owner_id='owner', template_key='coding',
                                   template_config=snapshot,
                                   expected_template_config_hash=template_snapshot_hash(repository.snapshot.return_value))

    assert result == {'bot_id': 'bot'}
    lifecycle.restart_bot_async.assert_awaited_once_with(
        bot_id='bot', user_id='owner', extra_configs={
            'template_config': snapshot, 'confirmed_template_update': 'true',
            'expected_template_config_hash': template_snapshot_hash(repository.snapshot.return_value),
        },
    )


@pytest.mark.asyncio
async def test_scope_mismatch_never_restarts_or_degrades():
    repository = Mock()
    repository.snapshot.return_value = None
    lifecycle = Mock(restart_bot_async=AsyncMock())
    service = ManagedBotService(repository, lifecycle)

    with pytest.raises(BotAddressNotFoundError):
        await service.restart(bot_id='bot', owner_id='owner', template_key='wrong')
    lifecycle.restart_bot_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_restart_does_not_invent_template_config():
    repository = Mock()
    repository.snapshot.return_value = {'template_key': 'coding'}
    lifecycle = Mock(restart_bot_async=AsyncMock(return_value={}))
    lifecycle.get_bot.return_value = {'bot_type': 'personal'}
    await ManagedBotService(repository, lifecycle).restart(
        bot_id='bot', owner_id='owner', template_key='coding',
    )
    lifecycle.restart_bot_async.assert_awaited_once_with(bot_id='bot', user_id='owner', extra_configs=None)


@pytest.mark.asyncio
async def test_reject_cross_template_snapshot_even_for_trusted_app():
    repository = Mock()
    repository.snapshot.return_value = {'template_key': 'coding'}
    lifecycle = Mock(restart_bot_async=AsyncMock())
    with pytest.raises(ValueError):
        await ManagedBotService(repository, lifecycle).restart(
            bot_id='bot', owner_id='owner', template_key='coding',
            template_config={'template_key': 'other'},
        )
    lifecycle.restart_bot_async.assert_not_awaited()
