"""What the teclaw composer reads from the managed-files store (W8).

Two questions, one object. *Which categories does the platform assert for this
bot?* — the categories its stored manifest declares, as artifact field names,
when the platform-managed switch is on; otherwise none. *What files does the
platform hold for them?* — store-relative refs from the store's listing, in
the shapes the collector already yields, so the composer embeds them the way
it embeds everything else.

The document is parsed the same way apply parses it (``yaml.safe_load`` plus
the orchestrator's ``declared_entries``), so "declared" means exactly what it
means at apply time: a declared-empty category is declared.
"""
from __future__ import annotations

from typing import Any, Callable, Collection, Optional

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
    SKILLS_LOCAL_DIR,
    WORKSPACE_NS,
    ManagedFile,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
)
from agentclaw.community.kernel.bot_config import OwnershipCategory
from agentclaw.community.log import get_logger

logger = get_logger()

#: Manifest category → artifact category.
ARTIFACT_FIELD_OF: dict[ManifestCategory, OwnershipCategory] = {
    ManifestCategory.IDENTITY: OwnershipCategory.IDENTITY_FILES,
    ManifestCategory.RESOURCES: OwnershipCategory.RESOURCES,
    ManifestCategory.SKILLS: OwnershipCategory.SKILLS,
    ManifestCategory.MCP: OwnershipCategory.MCP,
}
#: The engine family this store serves. The reader answers for it alone, so
#: the collector never has to know which engine a compose is for.
SERVED_ENGINE = "teclaw"


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

    def platform_managed(self, req: ComposeRequest) -> frozenset[OwnershipCategory]:
        if req.engine_type != SERVED_ENGINE or not self._platform_managed():
            return frozenset()
        parsed = self._parsed(req)
        if parsed is None:
            return frozenset()
        # MCP is not a *file* category and is not decided here: on teclaw the
        # artifact has carried the whole MCP set since W12, so the composer
        # marks it the platform's unconditionally. This answer is about the
        # three categories whose bytes the platform may or may not hold.
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
        return [self._as_file(f) for f in self._files(req, CATEGORY_RESOURCES)]

    def skills(self, req: ComposeRequest) -> list[CollectedSkill]:
        """Every package the store holds, active or not.

        The store keeps a package after the manifest stops declaring it — the
        way a deactivated local skill keeps its files on an ARCA host — so
        the collector intersects this with the bot's active set before it
        emits a ``SkillRef``; the reader does not know the active set.
        """
        by_name: dict[str, str] = {}
        for f in self._files(req, CATEGORY_SKILLS):
            # The package prefix: the scope's ref root plus the layout's own
            # ``workspace/skills-local/<name>``. Built, never searched for —
            # ``teclaw`` and ``workspace`` are legal skill names, and a search
            # for ``/<name>/`` would match the layout's fixed segments first.
            root = f.ref_path[: len(f.ref_path) - len(f.rel_path)]
            by_name.setdefault(f.name, f"{root}{WORKSPACE_NS}/{SKILLS_LOCAL_DIR}/{f.name}")
        # ``scope="user"`` is the artifact's existing word for a per-bot,
        # user-supplied skill (``skillRef.scope`` admits ``shared`` | ``user``);
        # what is new is that the package now has a store address.
        return [
            CollectedSkill(name=name, scope="user", store=BOT_DATA_STORE, path=prefix)
            for name, prefix in sorted(by_name.items())
        ]

    def skill_files(self, req: ComposeRequest, names: Collection[str]) -> list[CollectedFile]:
        """The named packages' files, as resources refs.

        A local skill's files ride as resources refs under
        ``workspace/skills-local/<name>/…`` — the shape the publish gather
        produces — beside the ``SkillRef`` that names the package.
        """
        wanted = set(names)
        return [
            self._as_file(f) for f in self._files(req, CATEGORY_SKILLS) if f.name in wanted
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
    store-backed ports key the store by the pair they are handed. The
    composer's ``user_id`` *is* the bot's ``owner_id`` (``_compose_request``
    and the device-sync service both set it so), which is what makes the read
    side land on the keys the write side wrote. ``req.entity_id`` is the
    manifest's storage key, a different vocabulary that happens to share the
    name; it is used for the manifest lookup above and never for the store.
    """
    return ManagedFileScope(
        entity_type=OWNER_ENTITY_TYPE, entity_id=req.user_id, bot_id=req.bot_id
    )


__all__ = ["ARTIFACT_FIELD_OF", "SERVED_ENGINE", "ManagedFilesComposeReader"]
