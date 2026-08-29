"""Typed configuration contract for Draft revision storage."""

from injector import Binder, Injector, Module
import pytest

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftContentStoreError,
    DraftContentStoreErrorCode,
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
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.testing.draft_content_store import (
    LocalDraftContentStore,
)
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin


class _Objects:
    pass


class _ObjectModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ObjectStoragePlugin, to=MockObjectStoragePlugin())


class _LegacyObjectModule(Module):
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


def test_draft_content_store_config_rejects_unknown_invalid_and_non_mapping(
    monkeypatch,
) -> None:
    for raw in (
        {"unknown": True},
        "not-a-mapping",
    ):
        monkeypatch.setattr(
            config_module, "_user_config", lambda raw=raw: {"draft_content_store": raw}
        )
        with pytest.raises(ValueError):
            ConfigModule().draft_content_store()

    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {"draft_content_store": {"base_prefix_template": "../{env}"}},
    )
    with pytest.raises(DraftContentStoreError) as error:
        ConfigModule().draft_content_store()
    assert error.value.code is DraftContentStoreErrorCode.INVALID_CONFIGURATION


def test_invalid_draft_content_store_config_fails_application_boot(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {"draft_content_store": {"unknown": True}},
    )

    with pytest.raises(ValueError):
        build_injector(profile=DeployProfile.TEST)


def test_skill_center_provider_builds_the_oss_adapter() -> None:
    injector = Injector([ConfigModule(), SkillCenterModule(), _ObjectModule()])
    store = injector.get(DraftContentStore)

    assert isinstance(store, OssDraftContentStore)
    assert isinstance(store, DraftContentStore)


def test_skill_center_provider_rejects_store_without_immutable_capability() -> None:
    injector = Injector([ConfigModule(), SkillCenterModule(), _LegacyObjectModule()])

    with pytest.raises(ValueError, match="atomic create-if-absent"):
        injector.get(DraftContentStore)


def test_testing_module_overrides_store_with_the_local_fake() -> None:
    injector = Injector(
        [ConfigModule(), SkillCenterModule(), TestingSkillCenterModule()]
    )

    assert isinstance(injector.get(DraftContentStore), LocalDraftContentStore)
