"""Composition bindings for exact-version Canonical Center storage."""

from __future__ import annotations

from injector import inject, provider, singleton

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreConfig,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.services.canonical_center_store import (
    OssCanonicalCenterVersionStore,
)
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


class CanonicalCenterStoreBindings:
    """Provider mixin kept outside the already-large SkillCenterModule."""

    @singleton
    @provider
    @inject
    def canonical_center_version_store(
        self,
        object_storage: ObjectStoragePlugin,
        config: CanonicalCenterStoreConfig,
    ) -> CanonicalCenterVersionStore:
        return OssCanonicalCenterVersionStore(
            object_storage=object_storage,
            config=config,
        )
