"""Root-container validation must not lose native publish lifecycle silently."""

import pytest

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules import service_bot_module
from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy


@pytest.mark.parametrize("block", [None, [], "false", {"enabled": "false"}, {"enabled": 0}])
def test_invalid_policy_fails_container_construction(monkeypatch, block):
    real = service_bot_module.read_user_config
    monkeypatch.setattr(
        service_bot_module, "read_user_config",
        lambda: {**real(), "bot_knowledge_publication": block},
    )
    with pytest.raises(ValueError, match="bot_knowledge_publication"):
        build_injector(profile=DeployProfile.TEST)


@pytest.mark.parametrize("enabled", [False, True])
def test_valid_policy_keeps_native_publish_handler(monkeypatch, enabled):
    import asyncio
    from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
        PROGRESS_POLL_TASK, PublishTaskLifecycle,
    )
    from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
    from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants

    real = service_bot_module.read_user_config
    monkeypatch.setattr(
        service_bot_module, "read_user_config",
        lambda: {**real(), "bot_knowledge_publication": {"enabled": enabled}},
    )
    injector = build_injector(profile=DeployProfile.TEST)
    assert injector.get(KnowledgePublicationPolicy).enabled is enabled
    participants = [item for item in discover_lifecycle_participants(injector)
                    if isinstance(item, PublishTaskLifecycle)]
    assert len(participants) == 1
    asyncio.run(participants[0].bootstrap())
    assert injector.get(HandlerRegistry).get(PROGRESS_POLL_TASK) is not None


@pytest.mark.parametrize('yaml_value', ['"false"', '${REVIEW_KNOWLEDGE_ENABLED}'])
def test_real_yaml_and_environment_interpolation_fail_before_lifecycle(
    tmp_path, monkeypatch, yaml_value,
):
    from pathlib import Path
    import shutil

    from agentclaw.community.di.config_bootstrap import register_config_provider

    original_dir = Path.cwd()
    shipped = Path(service_bot_module.__file__).parents[2] / 'configs'
    configs = tmp_path / 'configs'
    configs.mkdir()
    shutil.copyfile(shipped / 'application.yaml', configs / 'application.yaml')
    (configs / 'application-test.yaml').write_text(
        f'user_config:\n  bot_knowledge_publication:\n    enabled: {yaml_value}\n')
    monkeypatch.setenv('REVIEW_KNOWLEDGE_ENABLED', 'false')
    monkeypatch.chdir(tmp_path)
    try:
        register_config_provider(DeployProfile.TEST)
        assert service_bot_module.read_user_config()['bot_knowledge_publication']['enabled'] == 'false'
        with pytest.raises(ValueError, match='must be a boolean'):
            build_injector(profile=DeployProfile.TEST)
    finally:
        monkeypatch.chdir(original_dir)
        register_config_provider(DeployProfile.TEST)


def test_shipped_policy_enables_publication_and_keeps_native_handler():
    import asyncio
    from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
        PROGRESS_POLL_TASK, PublishTaskLifecycle,
    )
    from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
    from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants

    injector = build_injector(profile=DeployProfile.TEST)
    assert injector.get(KnowledgePublicationPolicy).enabled is True
    participants = [item for item in discover_lifecycle_participants(injector)
                    if isinstance(item, PublishTaskLifecycle)]
    assert len(participants) == 1
    asyncio.run(participants[0].bootstrap())
    assert injector.get(HandlerRegistry).get(PROGRESS_POLL_TASK) is not None
