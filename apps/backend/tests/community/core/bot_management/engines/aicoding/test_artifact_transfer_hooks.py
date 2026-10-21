"""The real engine strategy owns all runtime-specific work at both hooks."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.artifact_transfer import (
    AicodingArtifactTransferStrategy, FastdiskPublishError,
)
from agentclaw.community.core.service_bot.build_errors import BotBuildMigrationError
from agentclaw.community.core.workspace.engines.aicoding import _AICODING_BUILD_PLAN


def fixture(engine):
    strategy = object.__new__(AicodingArtifactTransferStrategy)
    strategy.try_publish = Mock(return_value=True)
    strategy.try_restore = Mock(return_value=True)
    provider = Mock(get_base_path=Mock(return_value=f'/home/admin/.{engine}'))
    plan = replace(_AICODING_BUILD_PLAN, engine_type=engine, migration_subpath=engine)
    return strategy, dict(
        bot={'bot_id': 'bot', 'entity_id': 'owner', 'device_id': 'draft'},
        source_version=17, plan=plan, provider=provider,
        artifact_dir=Path(f'/aidesktop/env/bolt_data/staff_owner/bot/17/{engine}'),
        migration_path_base=Mock(return_value='/opt/nfs/bot-data'),
        remaining_timeout=Mock(return_value=1234),
    )


@pytest.mark.parametrize('engine', ['openclaw', 'teclaw', 'hermes', 'deepseek_harness', 'future'])
def test_unsupported_engine_does_not_touch_runtime_or_validate_coding_fields(engine):
    strategy, args = fixture(engine)
    args.update(bot={}, source_version=False)
    assert strategy.restore(**args) is None
    assert strategy.publish(device_id='', plan=args['plan'], provider=args['provider'],
                            target_dir=args['artifact_dir'], ignore_paths=()) is False
    args['provider'].get_base_path.assert_not_called()
    args['migration_path_base'].assert_not_called()
    args['remaining_timeout'].assert_not_called()
    strategy.try_restore.assert_not_called()
    strategy.try_publish.assert_not_called()


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_coding_strategy_assembles_versioned_restore_and_preserves_publish_context(engine):
    strategy, args = fixture(engine)
    result = strategy.restore(**args)
    assert result == {'restore_type': 'migration_path',
                      'artifact_path': str(args['artifact_dir']),
                      'draft_path': f'/home/admin/.{engine}'}
    call = strategy.try_restore.call_args.kwargs
    assert call['device_id'] == 'draft'
    assert call['artifact_path'] == f'/opt/nfs/bot-data/17/{engine}'
    assert call['artifact_dir'] == args['artifact_dir']
    assert call['remaining_timeout'] is args['remaining_timeout']
    args['migration_path_base'].assert_called_once_with(owner_id='owner', bot_id='bot')
    assert strategy.publish(device_id='draft', plan=args['plan'], provider=args['provider'],
                            target_dir=args['artifact_dir'], ignore_paths=('workspace/skip',))
    call = strategy.try_publish.call_args.kwargs
    assert call['source_root'] == f'/home/admin/.{engine}'
    assert call['target_dir'] == args['artifact_dir']
    assert call['ignore_paths'] == ('workspace/skip',)


@pytest.mark.parametrize('version', [True, 0, -1, '17', None])
def test_invalid_coding_version_fails_before_runtime_access(version):
    strategy, args = fixture('aicoding')
    args['source_version'] = version
    with pytest.raises(BotBuildMigrationError, match='无效的历史版本'):
        strategy.restore(**args)
    args['provider'].get_base_path.assert_not_called()
    strategy.try_restore.assert_not_called()


def test_missing_device_keeps_existing_error_contract():
    strategy, args = fixture('aicoding')
    del args['bot']['device_id']
    with pytest.raises(BotBuildMigrationError, match='当前草稿设备'):
        strategy.restore(**args)
    strategy.try_restore.assert_not_called()


def test_only_not_applicable_returns_fallback_and_errors_propagate():
    strategy, args = fixture('aicoding')
    strategy.try_restore.return_value = False
    assert strategy.restore(**args) is None
    error = FastdiskPublishError('restore failed')
    strategy.try_restore.side_effect = error
    with pytest.raises(FastdiskPublishError) as caught:
        strategy.restore(**args)
    assert caught.value is error
