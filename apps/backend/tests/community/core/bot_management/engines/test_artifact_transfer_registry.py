"""Real default selection must not reach any coding runtime dependencies."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.engines.artifact_transfer import (
    ArtifactTransferRegistry,
    DefaultArtifactTransferStrategy,
)
from agentclaw.community.core.workspace.engines.aicoding import _AICODING_BUILD_PLAN


@pytest.mark.parametrize('engine', ['openclaw', 'teclaw', 'hermes', 'deepseek_harness', 'future'])
def test_unregistered_engine_is_noop_without_touching_runtime(engine):
    coding = Mock()
    registry = ArtifactTransferRegistry({'aicoding': coding, 'claude_code': coding})
    strategy = registry.resolve(engine)
    assert isinstance(strategy, DefaultArtifactTransferStrategy)
    plan = replace(_AICODING_BUILD_PLAN, engine_type=engine)
    provider, path, budget, migration_path = Mock(), Mock(), Mock(), Mock()
    assert strategy.publish(device_id='', plan=plan, provider=provider,
                            target_dir=path, ignore_paths=()) is False
    assert strategy.restore(bot={}, source_version=1, plan=plan, provider=provider,
                            artifact_dir=path, migration_path_base=migration_path,
                            remaining_timeout=budget, migration_error=RuntimeError) is None
    for dependency in (coding, provider, path, budget, migration_path):
        assert dependency.mock_calls == []


def test_registry_selects_per_bot_not_once_for_the_process():
    coding = Mock()
    registry = ArtifactTransferRegistry({'aicoding': coding, 'claude_code': coding})
    assert registry.resolve('aicoding') is coding
    default = registry.resolve('openclaw')
    assert isinstance(default, DefaultArtifactTransferStrategy)
    assert registry.resolve('claude_code') is coding
    assert registry.resolve('future') is default


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('action', ['publish', 'restore'])
def test_selected_failure_is_not_converted_to_default_fallback(engine, action):
    coding = Mock()
    error = RuntimeError('transfer failed')
    getattr(coding, action).side_effect = error
    registry = ArtifactTransferRegistry({'aicoding': coding, 'claude_code': coding})
    with pytest.raises(RuntimeError) as caught:
        getattr(registry.resolve(engine), action)()
    assert caught.value is error


def test_composition_root_registers_only_coding_engines(monkeypatch):
    from agentclaw.community.di.modules import service_bot_module as module
    from agentclaw.community.core.bot_management.engines.aicoding.artifact_transfer import (
        AicodingArtifactTransferStrategy,
    )

    monkeypatch.setattr(module, 'read_user_config', lambda: {})
    device, bindings = Mock(), Mock()
    registry = module.ServiceBotModule().artifact_transfer_registry(
        device_service=device, device_binding_repo=bindings,
        object_storage=Mock(bucket_name='test', endpoint='https://oss.example.test'),
        mount_config=Mock(oss_root='/aidesktop'),
    )
    coding = registry.resolve('aicoding')
    assert isinstance(coding, AicodingArtifactTransferStrategy)
    assert registry.resolve('claude_code') is coding
    assert isinstance(registry.resolve('openclaw'), DefaultArtifactTransferStrategy)
    assert device.mock_calls == bindings.mock_calls == []
