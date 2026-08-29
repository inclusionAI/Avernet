"""Typed configuration contract for Draft revision storage."""

from injector import Binder, Injector, Module

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
)
from agentclaw.community.core.skill_center.services.draft_content_store import (
    OssDraftContentStore,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule
from agentclaw.community.di.modules.skill_center_module import SkillCenterModule
from agentclaw.community.di.modules.testing_skill_center_module import (
    TestingSkillCenterModule,
)
from agentclaw.community.plugins.local.draft_content_store import (
    LocalDraftContentStore,
)
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


class _Objects:
    pass


class _ObjectModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ObjectStoragePlugin, to=_Objects())


def test_draft_content_store_config_uses_frozen_default(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_user_config", lambda: {})

    config = ConfigModule().draft_content_store()

    assert config.base_prefix_template == (
        "aidesktop/aidesktop_{env}/bolt_shared/skills-upload/space-drafts"
    )


def test_draft_content_store_config_reads_explicit_prefix(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "draft_content_store": {
                "base_prefix_template": "sandbox/aidesktop_{env}/space-drafts"
            }
        },
    )

    assert ConfigModule().draft_content_store().base_prefix_template == (
        "sandbox/aidesktop_{env}/space-drafts"
    )


def test_skill_center_provider_builds_the_oss_adapter() -> None:
    injector = Injector([ConfigModule(), SkillCenterModule(), _ObjectModule()])
    store = injector.get(DraftContentStore)

    assert isinstance(store, OssDraftContentStore)
    assert isinstance(store, DraftContentStore)


def test_testing_module_overrides_store_with_the_local_fake() -> None:
    injector = Injector(
        [ConfigModule(), SkillCenterModule(), TestingSkillCenterModule()]
    )

    assert isinstance(injector.get(DraftContentStore), LocalDraftContentStore)
