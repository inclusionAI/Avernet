"""Configuration and composition contracts for the Canonical Center Store."""

from injector import Binder, Injector, Module
import pytest

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreConfig,
    CanonicalCenterStoreError,
    CanonicalCenterStoreErrorCode,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.services.canonical_center_store import (
    OssCanonicalCenterVersionStore,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule
from agentclaw.community.di.modules.skill_center_module import SkillCenterModule
from agentclaw.community.di.modules.testing_skill_center_module import (
    TestingSkillCenterModule,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)


class _ObjectModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ObjectStoragePlugin, to=MockObjectStoragePlugin())


class _LegacyObjects:
    pass


class _LegacyObjectModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ObjectStoragePlugin, to=_LegacyObjects())


def test_config_uses_runtime_env_and_frozen_default(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_user_config", lambda: {})
    monkeypatch.setattr(config_module, "get_current_env", lambda: "pre")

    config = ConfigModule().canonical_center_store()

    assert config == CanonicalCenterStoreConfig(env="pre")
    assert config.base_prefix == (
        "aidesktop/aidesktop_pre/bolt_shared/skills-center"
    )
    assert config.control_prefix == (
        "aidesktop/aidesktop_pre/bolt_shared/skills-center-control"
    )


def test_config_reads_explicit_prefix_and_rejects_unknown_or_invalid(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config_module, "get_current_env", lambda: "prod")
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "canonical_center_store": {
                "base_prefix_template": "sandbox/aidesktop_{env}/skills-center"
            }
        },
    )
    config = ConfigModule().canonical_center_store()
    assert config.base_prefix == (
        "sandbox/aidesktop_prod/skills-center"
    )
    assert config.control_prefix == "sandbox/aidesktop_prod/skills-center-control"

    for raw in ({"unknown": True}, "not-a-mapping"):
        monkeypatch.setattr(
            config_module,
            "_user_config",
            lambda raw=raw: {"canonical_center_store": raw},
        )
        with pytest.raises(ValueError):
            ConfigModule().canonical_center_store()

    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "canonical_center_store": {"base_prefix_template": "../{env}"}
        },
    )
    with pytest.raises(CanonicalCenterStoreError) as error:
        ConfigModule().canonical_center_store()
    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION


def test_invalid_config_fails_application_boot(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {"canonical_center_store": {"unknown": True}},
    )

    with pytest.raises(ValueError):
        build_injector(profile=DeployProfile.TEST)


@pytest.mark.parametrize(
    "template",
    [
        "sandbox/{{env}}/skills-center",
        "sandbox/{env!r}/skills-center",
        "sandbox/{env:>10}/skills-center",
        "sandbox/{env}/{env}/skills-center",
        "sandbox/{tenant}/skills-center",
    ],
)
def test_config_rejects_escaped_or_non_exact_env_placeholder(template: str) -> None:
    with pytest.raises(CanonicalCenterStoreError) as error:
        CanonicalCenterStoreConfig(env="pre", base_prefix_template=template)

    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION


def test_provider_builds_oss_adapter_and_rejects_legacy_storage() -> None:
    injector = Injector([ConfigModule(), SkillCenterModule(), _ObjectModule()])
    assert isinstance(
        injector.get(CanonicalCenterVersionStore),
        OssCanonicalCenterVersionStore,
    )

    legacy = Injector(
        [ConfigModule(), SkillCenterModule(), _LegacyObjectModule()]
    )
    with pytest.raises(CanonicalCenterStoreError) as error:
        legacy.get(CanonicalCenterVersionStore)
    assert error.value.code is CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION


def test_testing_module_overrides_store_with_local_fake() -> None:
    injector = Injector(
        [ConfigModule(), SkillCenterModule(), TestingSkillCenterModule()]
    )

    assert isinstance(
        injector.get(CanonicalCenterVersionStore),
        LocalCanonicalCenterVersionStore,
    )
