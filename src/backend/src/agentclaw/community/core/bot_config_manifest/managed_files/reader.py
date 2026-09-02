"""What the teclaw composer reads from the managed-files store (W8).

Two questions, one object. *Which categories does the platform assert for this
bot?* — the categories its stored manifest declares, as artifact field names,
when the platform-managed switch is on; otherwise none. *What files does the
platform hold for them?* — store-relative refs from the index, in the shapes
the collector already yields, so the composer embeds them the way it embeds
everything else.

The document is parsed the same way apply parses it (``yaml.safe_load`` plus
the orchestrator's ``declared_entries``), so "declared" means exactly what it
means at apply time: a declared-empty category is declared.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import yaml

from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    declared_entries,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestCategory
from agentclaw.community.core.bot_config_manifest.managed_files.store import (
    BOT_DATA_STORE,
    OWNER_ENTITY_TYPE,
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    CATEGORY_SKILLS,
    ManagedFile,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Manifest category → artifact field name.
ARTIFACT_FIELD_OF: dict[ManifestCategory, str] = {
    ManifestCategory.IDENTITY: "identity_files",
    ManifestCategory.RESOURCES: "resources",
    ManifestCategory.SKILLS: "skills",
    ManifestCategory.MCP: "mcp",
}


class ManagedFilesComposeReader:
    """Implements ``PlatformManagedCategoriesReader`` and ``ManagedFilesReader``."""

    def __init__(
        self,
        *,
        store: ManagedFilesStore,
        manifest_service_provider: Callable[[], BotConfigManifestServiceProtocol],
        platform_managed: Callable[[], bool],
    ) -> None:
        self._store = store
        self._manifests = manifest_service_provider
        self._platform_managed = platform_managed

    # ── PlatformManagedCategoriesReader ──────────────────────────────────

    def platform_managed(self, req: ComposeRequest) -> frozenset[str]:
        if not self._platform_managed():
            return frozenset()
        parsed = self._parsed(req)
        if parsed is None:
            return frozenset()
        return frozenset(
            field
            for category, field in ARTIFACT_FIELD_OF.items()
            if category is not ManifestCategory.MCP
            and declared_entries(parsed, category) is not None
        )

    def _parsed(self, req: ComposeRequest) -> Optional[dict[str, Any]]:
        record = self._manifests().get(entity_id=req.entity_id, bot_id=req.bot_id)
        if record is None:
            return None
        try:
            parsed = yaml.safe_load(record.document)
        except yaml.YAMLError:
            logger.warning(
                "[managed_files.reader] stored manifest does not parse: bot_id=%s",
                req.bot_id,
            )
            return None
        return parsed if isinstance(parsed, dict) else None

    # ── ManagedFilesReader ───────────────────────────────────────────────

    def identity_files(self, req: ComposeRequest) -> list[CollectedFile]:
        return [self._as_file(f) for f in self._files(req, CATEGORY_IDENTITY)]

    def resources(self, req: ComposeRequest) -> list[CollectedFile]:
        # A local skill's files ride as resources refs under
        # ``workspace/skills-local/<name>/…`` — the shape the publish gather
        # produces — beside the ``SkillRef`` that names the package.
        return [
            self._as_file(f)
            for f in self._files(req, CATEGORY_RESOURCES) + self._files(req, CATEGORY_SKILLS)
        ]

    def skills(self, req: ComposeRequest) -> list[CollectedSkill]:
        by_name: dict[str, str] = {}
        for f in self._files(req, CATEGORY_SKILLS):
            # The package prefix: the ref path up to and including the skill's
            # directory. Every file of one skill shares it.
            prefix = f.ref_path.split(f"/{f.name}/", 1)[0] + f"/{f.name}"
            by_name.setdefault(f.name, prefix)
        # ``scope="user"`` is the artifact's existing word for a per-bot,
        # user-supplied skill (``skillRef.scope`` admits ``shared`` | ``user``);
        # what is new is that the package now has a store address.
        return [
            CollectedSkill(name=name, scope="user", store=BOT_DATA_STORE, path=prefix)
            for name, prefix in sorted(by_name.items())
        ]

    def _files(self, req: ComposeRequest, category: str) -> list[ManagedFile]:
        return self._store.list(_scope(req), category=category)

    @staticmethod
    def _as_file(f: ManagedFile) -> CollectedFile:
        return CollectedFile(
            name=f.rel_path.rsplit("/", 1)[-1], store=BOT_DATA_STORE, path=f.ref_path
        )


def _scope(req: ComposeRequest) -> ManagedFileScope:
    """The store scope for a compose request — the *owner-based* address.

    The identity and resources materialisers address a bot at
    ``("staff", owner_id)`` — the personal-bot surface's fixed pair
    (``identity_coords_from_record`` / the resources router) — and the
    store-backed ports key the index by the pair they are handed. The
    composer's ``user_id`` *is* the bot's ``owner_id`` (``_compose_request``
    and the device-sync service both set it so), which is what makes the read
    side land on the rows the write side wrote. ``req.entity_id`` is the
    manifest's storage key, a different vocabulary that happens to share the
    name; it is used for the manifest lookup above and never for the index.
    """
    from agentclaw.community.utils.env_utils import get_current_env

    return ManagedFileScope(
        env=get_current_env(),
        entity_type=OWNER_ENTITY_TYPE,
        entity_id=req.user_id,
        bot_id=req.bot_id,
    )


__all__ = ["ARTIFACT_FIELD_OF", "ManagedFilesComposeReader"]
