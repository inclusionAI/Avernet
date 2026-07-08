"""TestingSkillCenterModule — SQLite / local overrides for skill_center.

Mirrors the override pattern of :class:`TestingDatabaseModule` /
:class:`TestingInfrastructureModule`. Installed by ``modules_for`` for
the ``test`` / ``singlebox`` profiles.

This module only overrides Protocol- and repository-level bindings.
Service constructors run unchanged from :class:`SkillCenterModule`; the
OSS upload dependency they require is satisfied by a
:class:`MockObjectStoragePlugin` bound here.

NOTE: ``DeviceAccessor`` and ``SkillRepoSyncPlugin`` are arguably
infrastructure (Local vs Arca) rather than skill_center bindings. In
practice all current call sites have these aligned with the profile, so
they're bundled into this ``test`` / ``singlebox`` module. They move to
their own per-concern modules when the device/skill clusters decompose
(B6 / B7).
"""
from __future__ import annotations


from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.core.devices.services.local_device_accessor import LocalDeviceAccessor
from agentclaw.community.plugins.local.local_device_lifecycle import LocalDeviceLifecycle
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin

logger = get_logger()


class TestingSkillCenterModule(Module):
    """SQLite + local overrides for skill_center singletons.

    Overrides are infrastructure-level only: skill repositories,
    sync-log repository, ``DeviceAccessor``, ``SkillRepoSyncPlugin``,
    and the OSS upload protocol. Service constructors come from
    :class:`SkillCenterModule` unchanged so tests exercise the same
    composition as production.
    """

    def configure(self, binder: Binder) -> None:
        # Bind the OSS upload protocol to a configurable mock. This
        # shadows ``InfrastructureModule.bot_oss_client`` for the same
        # Protocol key so prod skill_center service constructors run
        # unchanged in test boots — they receive the mock instead of
        # the real OSSStorageManager (which can't be built without
        # MOSN/Mist). Tests that need to assert calls or inject
        # failures fetch this singleton from the injector and mutate
        # it (see ``MockObjectStoragePlugin`` docstring for the pattern).
        binder.bind(
            ObjectStoragePlugin,
            to=MockObjectStoragePlugin,
            scope=singleton,
        )

        # Bind LocalDeviceAccessor to itself as a singleton; the
        # device_plugin() provider below resolves the same instance to bind as
        # the DeviceAccessor Protocol.
        binder.bind(LocalDeviceAccessor, to=LocalDeviceAccessor, scope=singleton)
        # The singlebox device boot/shutdown Lifecycle is a standalone
        # ``plugins/local`` participant (B9 split — it drives other local
        # plugins, so it can't live in ``core`` with the accessor). Bind it as a
        # singleton so ``discover_lifecycle_participants`` finds it.
        binder.bind(LocalDeviceLifecycle, to=LocalDeviceLifecycle, scope=singleton)

    # SkillRepository / SkillSetRepository are no longer overridden:
    # the unified repositories (bound in SkillCenterModule) are a
    # faithful prod port that runs on SQLite too — they only need the
    # SQLite DatabasePlugin injected. One impl, both runtimes.

    # SkillMemberRepository is no longer overridden: the unified
    # repository (bound in SkillCenterModule) runs on SQLite too — it only
    # needs the SQLite DatabasePlugin injected. One impl, both runtimes.

    # SkillCategoryRepository is no longer overridden: the unified
    # repository (bound in SkillCenterModule) runs on SQLite too — it only
    # needs the SQLite DatabasePlugin injected. One impl, both runtimes.

    @singleton
    @provider
    @inject
    def device_plugin(
        self,
        local_plugin: LocalDeviceAccessor,
    ) -> DeviceAccessor:
        """Local ``DeviceAccessor`` — binding 直绑 ``LocalDeviceAccessor``。

        Local 模式下只有 ``LocalDeviceAccessor`` 一种实现,业务 caller 走
        :class:`DeviceContextResolver` + 各 ConnInfoBuilder,不再依赖
        provider 分流。

        ``local_plugin`` 是 ``configure()`` 中绑定的同一 singleton 实例,
        injecting it here (rather than constructing inline) ensures
        ``discover_lifecycle_participants`` returns one instance.
        """
        logger.info("[NEW-ARCH] DeviceAccessor: LocalDeviceAccessor (testing override)")
        return local_plugin

    @singleton
    @provider
    def skill_repo_sync(self) -> SkillRepoSyncPlugin:
        from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin

        logger.info("[NEW-ARCH] SkillRepoSyncPlugin: LocalSkillRepoSyncPlugin (testing override)")
        return LocalSkillRepoSyncPlugin()

    # SkillPropagationLogRepository is no longer overridden: the unified
    # repository (bound in SkillCenterModule) runs on SQLite too — it only
    # needs the SQLite DatabasePlugin injected. One impl, both runtimes.

    # SkillCenterSyncLogRepository is no longer overridden: the unified
    # repository (bound in SkillCenterModule) runs on SQLite too — it only
    # needs the SQLite DatabasePlugin injected. One impl, both runtimes.
