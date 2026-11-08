"""Capture is an explicit deployment choice, not a Sink flag or a boot gate."""

from injector import Module
import pytest

from agentclaw.community.core.config.sofa import sofa_config
from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import (
    KnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.persistence_effects import (
    NoKnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.space_knowledge.capture_lifecycle import (
    SpaceKnowledgeCaptureLifecycle,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.space_knowledge_capture_config import (
    SpaceKnowledgeCaptureConfig,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.core.space_knowledge.test_membership_real_database import (
    database as database,
)


@pytest.mark.parametrize(
    "block",
    [None, True, [], "true", {"enabled": "true"}, {"enabled": 1}, {"enabeld": True}],
)
def test_capture_configuration_fails_closed_on_malformed_blocks(
    database, monkeypatch, block
):
    monkeypatch.setitem(
        vars(sofa_config), "user_config", {"space_knowledge_capture": block}
    )

    class ExistingDatabase(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=database)

    injector = build_injector(
        profile=DeployProfile.TEST, extra_modules=[ExistingDatabase()]
    )
    assert injector.get(SpaceKnowledgeCaptureConfig).enabled is False
    lifecycle = injector.get(SpaceKnowledgeCaptureLifecycle)
    assert isinstance(lifecycle._effects, NoKnowledgeSpacePersistenceEffects)


def test_capture_configuration_enables_recording_when_explicit(database, monkeypatch):
    monkeypatch.setitem(
        vars(sofa_config),
        "user_config",
        {"space_knowledge_capture": {"enabled": True}},
    )

    class ExistingDatabase(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=database)

    injector = build_injector(
        profile=DeployProfile.TEST, extra_modules=[ExistingDatabase()]
    )
    assert injector.get(SpaceKnowledgeCaptureConfig).enabled is True
    lifecycle = injector.get(SpaceKnowledgeCaptureLifecycle)
    assert isinstance(lifecycle._effects, KnowledgeSpacePersistenceEffects)
