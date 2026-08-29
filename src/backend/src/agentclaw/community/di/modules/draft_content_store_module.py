"""Composition root for immutable Draft revision storage."""

from __future__ import annotations

from injector import inject, provider, singleton

from agentclaw.community.core.skill_center.draft_content import (
    DraftContentStore,
    DraftContentStoreConfig,
)
from agentclaw.community.core.skill_center.services.draft_content_store import (
    OssDraftContentStore,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


class DraftContentStoreBindings:
    """Provider mixin; test profiles may override with the Local Fake."""

    @singleton
    @provider
    @inject
    def draft_content_store(
        self,
        object_storage: ObjectStoragePlugin,
        config: DraftContentStoreConfig,
    ) -> DraftContentStore:
        return OssDraftContentStore(
            object_storage=object_storage,
            package_validator=SkillPackageValidator(SkillParser()),
            config=config,
        )
