"""What the teclaw composer reads from the managed-files store (W8).

Two questions, one object. *Does the platform own this compose?* — yes for
the closing redeliver of a manifest apply and for the first artifact of a
bot that carries a manifest, when the platform-managed switch is on; no for
a runtime edit, for another engine family, and while the switch is off.
*What files does the platform hold for the bot?* — store-relative refs from
the store's listing, in the shapes the collector already yields, so the
composer embeds them the way it embeds everything else.
"""
from __future__ import annotations

from typing import Callable, Collection

from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
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
    ComposeOccasion,
    ComposeRequest,
)
#: The engine family this store serves. The reader answers for it alone, so
#: the collector never has to know which engine a compose is for.
SERVED_ENGINE = "teclaw"


class ManagedFilesComposeReader:
    """Implements ``PlatformOwnershipReader`` and ``ManagedFilesReader``."""

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

    # ── PlatformOwnershipReader ──────────────────────────────────────────

    def platform_owns(self, req: ComposeRequest) -> bool:
        """Ownership follows the operation (engine contract §9.2).

        A manifest apply's closing redeliver is the platform's: it has just
        written every category into its own state. The first artifact of a
        new container is the platform's when the bot carries a manifest —
        the creation job applied it before provisioning — and the engine's
        for a bot created without one, so a template's own files are not
        told to go. A runtime edit is always the engine's.
        """
        if req.engine_type != SERVED_ENGINE or not self._platform_managed():
            return False
        if req.occasion is ComposeOccasion.MANIFEST_APPLY:
            return True
        if req.occasion is ComposeOccasion.PROVISION:
            return self._manifests().get(entity_id=req.entity_id, bot_id=req.bot_id) is not None
        return False

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


__all__ = ["SERVED_ENGINE", "ManagedFilesComposeReader"]
