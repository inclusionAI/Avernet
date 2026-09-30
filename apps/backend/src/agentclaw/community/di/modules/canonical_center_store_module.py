"""Composition bindings for exact-version Canonical Center storage."""

from __future__ import annotations

from injector import inject, provider, singleton

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterStoreConfig,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentDistribution,
    CenterContentURLSigner,
)
from agentclaw.community.core.skill_center.services.center_content_distribution import (
    CanonicalCenterContentDistribution,
    CenterContentDistributionConfig,
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
    def center_content_url_signer(
        self, object_storage: ObjectStoragePlugin
    ) -> CenterContentURLSigner:
        return object_storage

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

    @singleton
    @provider
    @inject
    def center_content_distribution(
        self,
        canonical_store: CanonicalCenterVersionStore,
        object_storage: ObjectStoragePlugin,
        url_signer: CenterContentURLSigner,
        config: CanonicalCenterStoreConfig,
    ) -> CenterContentDistribution:
        return CanonicalCenterContentDistribution(
            canonical_store=canonical_store,
            object_storage=object_storage,
            url_signer=url_signer,
            config=CenterContentDistributionConfig(env=config.env),
        )
