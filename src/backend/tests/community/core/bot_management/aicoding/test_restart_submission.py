"""The engine owns durable restart selection, not Bot/Caller services."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.engines.registry import resolve_restart_strategy


@pytest.mark.parametrize('engine', ['openclaw', 'teclaw', 'qoder', 'unknown'])
@pytest.mark.parametrize('scope', ['bot', 'caller'])
def test_default_engines_do_not_submit(engine, scope):
    ctx, strategy = resolve_restart_strategy({'active_engine': engine})
    queue = Mock()
    assert strategy.submit_restart(ctx, scope=scope, task_queue=queue, payload={}) is False
    queue.enqueue.assert_not_called()


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('scope,task_type,payload,key', [
    ('bot', 'bot_management.restart', {'bot_id': 'b', 'user_id': 'u'}, 'bot-restart:b:u'),
    ('caller', 'expert_chat.caller_restart',
     {'bot_id': 'b', 'user_id': 'u', 'owner_id': 'o'}, 'caller-restart:u:b:o'),
])
def test_coding_submission_preserves_wire_contract(engine, scope, task_type, payload, key):
    ctx, strategy = resolve_restart_strategy({'active_engine': engine})
    queue = Mock()
    assert strategy.submit_restart(ctx, scope=scope, task_queue=queue, payload=payload) is True
    queue.enqueue.assert_called_once_with(
        task_type, payload, deadline_seconds=3600, idempotency_key=key,
    )


def test_enqueue_failure_is_not_reported_as_pending():
    ctx, strategy = resolve_restart_strategy({'active_engine': 'aicoding'})
    queue = Mock()
    queue.enqueue.side_effect = RuntimeError('queue unavailable')
    with pytest.raises(RuntimeError, match='queue unavailable'):
        strategy.submit_restart(ctx, scope='bot', task_queue=queue,
                                payload={'bot_id': 'b', 'user_id': 'u'})


def test_library_call_without_queue_retains_synchronous_path():
    ctx, strategy = resolve_restart_strategy({'active_engine': 'aicoding'})
    assert strategy.submit_restart(ctx, scope='bot', task_queue=None, payload={}) is False


def test_caller_worker_uses_original_continuation():
    from unittest.mock import AsyncMock
    from agentclaw.community.core.bot_management.engines.aicoding.caller_restart_task import CallerRestartTaskHandler
    from agentclaw.community.core.task_queue.types import Complete

    restart = AsyncMock(return_value={'need_poll': True})
    handler = CallerRestartTaskHandler(restart=restart)
    assert isinstance(handler.handle({'user_id': 'u', 'bot_id': 'b', 'owner_id': 'o'}), Complete)
    restart.assert_awaited_once_with(user_id='u', bot_id='b', owner_id='o', force_upgrade=True)


def test_bot_worker_uses_existing_restart():
    from agentclaw.community.core.bot_management.engines.aicoding.bot_restart_task import BotRestartTaskHandler
    from agentclaw.community.core.task_queue.types import Complete

    service = Mock()
    handler = BotRestartTaskHandler(bot_service_provider=lambda: service)
    assert isinstance(handler.handle({'bot_id': 'b', 'user_id': 'u'}), Complete)
    service.restart_bot.assert_called_once_with(
        bot_id='b', user_id='u', nick_name=None, extra_configs=None,
    )


def test_worker_failure_does_not_report_success():
    from agentclaw.community.core.bot_management.engines.aicoding.caller_restart_task import CallerRestartTaskHandler
    from agentclaw.community.core.task_queue.types import Fail
    from unittest.mock import AsyncMock

    restart = AsyncMock(side_effect=RuntimeError('backup failed'))
    handler = CallerRestartTaskHandler(restart=restart)
    assert isinstance(handler.handle({'bot_id': 'b', 'user_id': 'u', 'owner_id': 'o'}), Fail)
