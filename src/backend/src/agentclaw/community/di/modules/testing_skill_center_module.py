"""TestingSkillCenterModule — SQLite / local overrides for skill_center.

Mirrors the override pattern of :class:`TestingDatabaseModule` /
:class:`TestingInfrastructureModule`. Installed by ``modules_for`` for
the ``test`` / ``singlebox`` profiles.

This module only overrides Protocol- and repository-level bindings.
Service constructors run unchanged from :class:`SkillCenterModule`; the
OSS upload dependency they require is satisfied by a
:class:`MockObjectStoragePlugin` bound here.

``DeviceAccessor`` and device lifecycle bindings live in the profile's device
module. Keeping them out of this shared module lets ``test`` use the local
device double while ``singlebox`` uses the real local BaaS runtime.
"""
from __future__ import annotations


from injector import Binder, Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
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
