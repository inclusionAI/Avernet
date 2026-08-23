"""Unified, type-resolved seam for Bot Skill assets.

P1-01 wires the Local adapter end to end.  Repo and Space are deliberately
registered as source kinds here, but their readers remain P1-02/Phase 2 work.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable, Protocol, TYPE_CHECKING

from injector import inject

from agentclaw.community.api.bot_skill_asset_service import (
    BotSkillAssetServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillStorageError,
    SkillParameterValidationError,
)
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.factories import (
    SkillParameterServiceFactory,
)

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )


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


class _LocalSkillStatePort(Protocol):
    async def set_local_skill_active(
        self, *, skill_id: str, actor_id: str, active: bool
    ) -> dict[str, Any]: ...

    async def set_repo_skill_active(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        active: bool,
    ) -> dict[str, Any]: ...


class BotSkillAssetService(BotSkillAssetServiceProtocol):
    """Resolve one public ``skill_id`` before invoking its registered reader."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborators: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
        parameter_service_factory: SkillParameterServiceFactory,
        device_context_resolver_provider: Callable[[], "DeviceContextResolver"],
        local_state_service: _LocalSkillStatePort,
    ) -> None:
        self._skill_repo = skill_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborators
        self._skill_service_factory = skill_service_factory
        self._parameter_service_factory = parameter_service_factory
        self._device_context_resolver_provider = device_context_resolver_provider
        self._local_state_service = local_state_service
        self._adapters: dict[SkillAssetKind, _AssetAdapter] = {
            SkillAssetKind.LOCAL: _LocalAssetAdapter(self),
            SkillAssetKind.REPO: _RepoAssetAdapter(self),
            SkillAssetKind.SPACE: _UnavailableAssetAdapter(),
        }

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
        installed = self._skill_repo.list_bot_installed_skills(
            env=str(bot["env"]), owner_id=_owner_id, bot_id=bot_id
        )
        # ``active`` is a desired-state projection, never an asset attribute.
        # This keeps shared Repo detail aligned with the same Installation fact
        # that Direct activate/deactivate write.
        return {
            **skill,
            "active": any(str(item.get("id")) == skill_id for item in installed),
        }

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

    async def set_active(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
        active: bool,
    ) -> dict[str, Any]:
        skill = self.get_skill(
            skill_id=skill_id,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=user_id,
        )
        if self._kind_for(skill) is SkillAssetKind.REPO:
            return await self._local_state_service.set_repo_skill_active(
                skill_id=skill_id,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=user_id,
                active=active,
            )
        return await self._local_state_service.set_local_skill_active(
            skill_id=skill_id, actor_id=user_id, active=active
        )

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
    def __init__(self, service: BotSkillAssetService) -> None:
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

    def __init__(self, service: BotSkillAssetService) -> None:
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
