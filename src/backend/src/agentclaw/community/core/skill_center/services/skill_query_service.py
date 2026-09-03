"""One query seam for a Bot's skills — listing, detail, content, parameters.

Merges the old ``LocalSkillQueryService`` (desired-state listing/legacy
detail) and the read half of ``BotSkillAssetService`` (type-resolved detail,
content, parameters, legacy reference resolution). No activation writes live
here — those are the two command services'.

Listing and detail answer ``active`` from Installation after the reader's
flush: Installation is the single source of truth, and the flush is what
makes a SkillSet-bridged Skill answerable before anything else wrote its row.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable, Protocol, TYPE_CHECKING

from injector import inject

from agentclaw.community.core.skill_center.skill_query_service_protocol import SkillQueryServiceProtocol
from agentclaw.community.core.bot_config_surface.coords import BotConfigCoords
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillOwnerAmbiguousError,
    LocalSkillStorageError,
    SkillParameterValidationError,
)
from agentclaw.community.core.skill_center.factories import (
    SkillParameterServiceFactory,
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )


def require_addressed_bot(record: dict[str, Any], bot_id: str) -> None:
    """The skill must belong to the bot the address names.

    Without this the ``{bot_id}`` segment on the ``{skill_id}`` operations would
    be decorative — a client could name any bot and reach a skill on another
    one, which is the precise defect bot-first addressing exists to remove. A
    skill id resolves its own bot, so the two can be compared, and a mismatch is
    answered as the skill not existing.

    Masked as a 404 rather than reported as a mismatch, for the same reason the
    rest of the surface masks: a distinguishable "wrong bot" answer confirms the
    skill exists somewhere, which is an enumeration oracle over other people's
    bots.

    Lives here rather than in the router that used to own it: it is a statement
    about a skill record, not about a request, and manifest apply reaching
    skills by id needs the same comparison.
    """
    if str(record["bolt_id"]) != bot_id:
        raise LocalSkillNotFoundError()


def skill_coords_from_record(bot_id: str, owner_id: str) -> BotConfigCoords:
    """Where the ``skills`` category writes, for a bot that exists.

    ``engine_type`` is ``None``: the skill services address a bot by
    ``(bot_id, owner_id)`` and resolve any engine detail themselves, so there is
    no engine for this category to carry.
    """
    return BotConfigCoords(
        bot_id=bot_id,
        owner_id=owner_id,
        entity_type="staff",
        entity_id=owner_id,
        engine_type=None,
    )


def skill_coords_from_spec(bot_id: str, owner_id: str) -> BotConfigCoords:
    """The same address, for a bot that does not exist yet.

    Note what has no equivalent here: :func:`require_addressed_bot` compares a
    skill record against an address, and at preflight a manifest's declared
    skills have no records to compare — they do not exist yet. That validation
    is inherently record-bound and simply does not run on the create path.
    Saying so is better than inventing a check that would always pass.

    No caller until W13 (#1696).
    """
    return skill_coords_from_record(bot_id, owner_id)


class SkillAssetKind(StrEnum):
    LOCAL = "LOCAL"
    REPO = "REPO"
    SPACE = "SPACE"


class _AssetAdapter(Protocol):
    def resolve(
        self,
        *,
        skill: dict[str, Any],
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]: ...


class SkillQueryService(SkillQueryServiceProtocol):
    """Resolve one public ``skill_id`` (or a Bot scope) and answer for it."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborators: CollaboratorServiceProtocol,
        reader: BotCapabilityStateReaderProtocol,
        skill_service_factory: SkillServiceFactory,
        parameter_service_factory: SkillParameterServiceFactory,
        device_context_resolver_provider: Callable[[], "DeviceContextResolver"],
    ) -> None:
        self._skill_repo = skill_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborators
        self._reader = reader
        self._skill_service_factory = skill_service_factory
        self._parameter_service_factory = parameter_service_factory
        self._device_context_resolver_provider = device_context_resolver_provider
        self._adapters: dict[SkillAssetKind, _AssetAdapter] = {
            SkillAssetKind.LOCAL: _LocalAssetAdapter(self),
            SkillAssetKind.REPO: _RepoAssetAdapter(self),
            SkillAssetKind.SPACE: _UnavailableAssetAdapter(),
        }

    # ── Listing ─────────────────────────────────────────────────────

    def list_bot_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
        source: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Page every Skill the Bot has — owned rows and SkillSet-bridged ones.

        The flush runs before the page because ``active`` is a *filter*: it
        decides ``total`` and the page boundary, and a bridged Skill holds no
        Installation row until something writes one.
        """
        bot = self._require_view_access(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )
        # The reader flushes before answering, so the page need not resolve
        # Set membership again.
        member_ids = self._reader.member_skill_ids(bot=bot)
        return self._skill_repo.list_bot_skills(
            bot_id=bot_id,
            user_id=owner_id,
            skill_set_member_ids=member_ids,
            page=page,
            page_size=page_size,
            active=active,
            keyword=keyword,
            source=source,
        )

    # ── Detail ──────────────────────────────────────────────────────

    def get_local_skill(self, *, skill_id: str, actor_id: str) -> dict[str, Any]:
        """The legacy self-addressed Local detail: the Skill selects its Bot."""
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        skill = self._skill_repo.get_by_id(skill_id)
        if not self._is_exact_local_skill(skill):
            if self._is_unresolvable_legacy_local_skill(skill):
                raise LocalSkillOwnerAmbiguousError()
            raise LocalSkillNotFoundError()
        owner_id = str(skill["user_id"])
        bot_id = str(skill["bolt_id"])
        self._require_view_access(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        record = self._skill_repo.get_bot_local_skill(
            skill_id=skill_id, bot_id=bot_id, user_id=owner_id
        )
        if not record:
            raise LocalSkillNotFoundError()
        return record

    def get_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict[str, Any]:
        """Resolve the item once at the Bot-facing control-plane boundary."""
        skill, bot, _owner_id = self._resolve(
            skill_id=skill_id,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )
        # ``active`` is a desired-state projection, never an asset attribute,
        # and the reader answers from Installation after its flush — a
        # SkillSet-bridged Skill is active in detail before any listing ran.
        installed = self._reader.active_skill_assets(
            bot_id=bot_id, owner_id=_owner_id, bot=bot
        )
        return {
            **skill,
            "active": any(asset.skill_id == int(skill_id) for asset in installed),
        }

    # ── Legacy reference resolution ─────────────────────────────────

    def resolve_legacy_skill_id(
        self,
        *,
        skill_reference: str,
        source_path: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> str:
        """Translate Legacy path/link references to the decimal public id.

        Legacy adapters retain their published route shape, but they cannot
        mutate their own path-based state after P1-01.  Resolve the historical
        reference once, then prove it is addressable through this control plane.
        """
        references = [skill_reference, source_path]
        for reference in references:
            if not reference:
                continue
            if reference.isdecimal():
                self.get_skill(
                    skill_id=reference,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    user_id=user_id,
                )
                return reference
            candidates = [reference]
            if not reference.startswith(("git://", "local://", "center://")):
                candidates.append(f"git://{reference.lstrip('/')}")
            for candidate in candidates:
                skill = self._skill_repo.get_by_git_path(candidate)
                if skill is not None:
                    skill_id = str(skill["id"])
                    self.get_skill(
                        skill_id=skill_id,
                        bot_id=bot_id,
                        owner_id=owner_id,
                        user_id=user_id,
                    )
                    return skill_id
            link_name = reference.rsplit("://", 1)[-1].strip("/").replace("/", "_")
            skill = self._skill_repo.get_by_link_name(link_name, bolt_id=bot_id)
            if skill is not None:
                skill_id = str(skill["id"])
                self.get_skill(
                    skill_id=skill_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    user_id=user_id,
                )
                return skill_id
        raise LocalSkillNotFoundError()

    # ── Content & parameters ────────────────────────────────────────

    async def get_content(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> str:
        skill, bot, owner_id = self._resolve(
            skill_id=skill_id,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )
        if self._kind_for(skill) is SkillAssetKind.REPO:
            # Repo assets are read from the global skills-repo corpus, never a
            # Bot workspace and never the historical README fallback.
            content = self._skill_service_factory.create().get_repository_skill_content(
                skill_id
            )
        else:
            content = await self._local_storage(skill, bot, owner_id).read_file(
                "SKILL.md"
            )
        if content is None:
            raise LocalSkillNotFoundError()
        if isinstance(content, str):
            return content
        return SkillParser.decode_content_for_display(content)

    async def get_readme_by_skill(self, *, skill_id: str, actor_id: str) -> str:
        """Read a Local or public Repo Skill without a Bot on the wire.

        Public Repo Skills are environment-shared and are resolved solely by
        their database id. Local Skills still need their device workspace, but
        their Bot is derived from the persisted Skill row and never trusted
        from request input.
        """
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        skill = self._skill_repo.get_by_id(skill_id)
        if skill is None:
            raise LocalSkillNotFoundError()

        kind = self._kind_for(skill)
        if kind is SkillAssetKind.REPO:
            content = self._skill_service_factory.create().get_repository_skill_content(
                skill_id
            )
            if content is None:
                raise LocalSkillNotFoundError()
            return content

        if kind is not SkillAssetKind.LOCAL:
            raise LocalSkillNotFoundError()

        bot_id = str(skill.get("bolt_id") or "")
        if not bot_id:
            raise LocalSkillNotFoundError()
        bot = self._bot_repo.get_unique_by_id(bot_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        owner_id = str(bot.get("owner_id") or "")
        if not owner_id:
            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()

        storage = self._local_storage(skill, bot, owner_id)
        for filename in ("SKILL.md", "README.md"):
            content = await storage.read_file(filename)
            if content:
                if isinstance(content, str):
                    return content
                return SkillParser.decode_content_for_display(content)
        raise LocalSkillNotFoundError()

    async def get_parameters(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict[str, Any]:
        skill, bot, owner_id = self._resolve(
            skill_id=skill_id,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )
        parameter_service = await self._parameter_service_factory.create(
            bot_id=bot_id, user_id=owner_id
        )
        return parameter_service.get_skill_parameters(str(skill["name"]))

    async def replace_parameters(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        skill, bot, owner_id = self._resolve(
            skill_id=skill_id,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )
        manifest = await self.get_content(
            skill_id=skill_id,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )
        self._validate_parameters(manifest, parameters)
        parameter_service = await self._parameter_service_factory.create(
            bot_id=bot_id, user_id=owner_id
        )
        if not await parameter_service.save_skill_parameters(
            str(skill["name"]), parameters
        ):
            raise LocalSkillStorageError()
        return parameters

    # ── Resolution machinery ────────────────────────────────────────

    def _resolve(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        skill = self._skill_repo.get_by_id(skill_id)
        if skill is None:
            raise LocalSkillNotFoundError()
        return self._adapters[self._kind_for(skill)].resolve(
            skill=skill,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )

    def _resolve_local(
        self,
        *,
        skill: dict[str, Any],
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        skill_owner_id = str(skill.get("user_id") or "")
        if (
            not skill_owner_id
            or skill_owner_id != owner_id
            or str(skill.get("bolt_id")) != bot_id
        ):
            raise LocalSkillNotFoundError()
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if user_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, user_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        return skill, bot, owner_id

    def _require_view_access(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        """Refuse an invisible Bot, and hand back the one row it took to say so.

        Returning the Bot rather than ``None`` is what keeps the listing to a
        single Bot read: the engine that scopes its SkillSets and the env its
        Installation rows are keyed on are both on it.
        """
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if actor_id == owner_id:
            return bot
        permission = self._collaborators.check_collaborator_permission(
            bot_id, owner_id, actor_id, PermissionLevel.MEMBER
        )
        if not permission.get("has_permission"):
            raise LocalSkillNotFoundError()
        return bot

    @staticmethod
    def _is_exact_local_skill(skill: dict[str, Any] | None) -> bool:
        return bool(
            skill
            and skill.get("user_id")
            and skill.get("bolt_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )

    @staticmethod
    def _is_unresolvable_legacy_local_skill(skill: dict[str, Any] | None) -> bool:
        """Whether a legacy default Local row lacks its trusted owner field."""
        return bool(
            skill
            and skill.get("bolt_id") == "default"
            and not skill.get("user_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )

    @staticmethod
    def _kind_for(skill: dict[str, Any]) -> SkillAssetKind:
        source = str(skill.get("git_path") or "")
        if source.startswith("local://"):
            return SkillAssetKind.LOCAL
        if source.startswith("git://"):
            return SkillAssetKind.REPO
        if source.startswith("center://"):
            return SkillAssetKind.SPACE
        raise LocalSkillNotFoundError()

    def _local_storage(self, skill: dict[str, Any], bot: dict[str, Any], owner_id: str):
        locator = str(skill["git_path"])[len("local://") :]
        context = self._device_context_resolver_provider().resolve_for_bot(
            str(skill["bolt_id"]), owner_id
        )
        return self._skill_service_factory.local_skill_package_storage_for_locator(
            entity_id=str(bot["entity_id"]),
            owner_id=owner_id,
            bot_id=str(skill["bolt_id"]),
            engine_type=bot.get("active_engine"),
            entity_type=str(bot.get("entity_type") or "staff"),
            is_desktop=bot.get("bot_type") == "desktop",
            is_teclaw=context.provider == "teclaw",
            locator=locator,
        )

    @staticmethod
    def _validate_parameters(manifest: str, parameters: dict[str, Any]) -> None:
        try:
            schema = SkillParser.parse_config(manifest)
        except Exception as exc:
            raise SkillParameterValidationError() from exc
        allowed = {
            str(item["name"])
            for item in schema
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if set(parameters) - allowed:
            raise SkillParameterValidationError()
        for item in schema:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise SkillParameterValidationError()
            if item.get("required") and item["name"] not in parameters:
                raise SkillParameterValidationError()


class _LocalAssetAdapter:
    def __init__(self, service: SkillQueryService) -> None:
        self._service = service

    def resolve(
        self,
        *,
        skill: dict[str, Any],
        bot_id: str,
        owner_id: str,
        user_id: str,
    ):
        return self._service._resolve_local(
            skill=skill,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )


class _RepoAssetAdapter:
    """Authorize a shared governed Repo asset against the addressed Bot."""

    def __init__(self, service: SkillQueryService) -> None:
        self._service = service

    def resolve(
        self,
        *,
        skill: dict[str, Any],
        bot_id: str,
        owner_id: str,
        user_id: str,
    ):
        # Legacy Repo rows are system-owned.  Their repository implementation
        # persists a historical ``bolt_id='default'`` sentinel, so ownership
        # must be determined by the governed source scheme, never that column.
        if skill.get("user_id") or not str(skill.get("git_path") or "").startswith(
            "git://"
        ):
            raise LocalSkillNotFoundError()
        bot = self._service._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if str(bot.get("owner_id") or "") != owner_id:
            raise LocalSkillNotFoundError()
        if user_id != owner_id:
            permission = self._service._collaborators.check_collaborator_permission(
                bot_id, owner_id, user_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        # The HTTP adapter's grant checker is intentionally record-shaped.
        # Enrich a copy only; shared Repo persistence remains bot-independent.
        return {**skill, "bolt_id": bot_id, "user_id": owner_id}, bot, owner_id


class _UnavailableAssetAdapter:
    """Explicit P1-01 registration point for P1-02/Phase-2 asset readers."""

    def resolve(
        self,
        *,
        skill: dict[str, Any],
        bot_id: str,
        owner_id: str,
        user_id: str,
    ):
        raise LocalSkillNotFoundError()
