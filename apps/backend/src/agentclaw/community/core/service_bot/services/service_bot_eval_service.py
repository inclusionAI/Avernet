"""Authorization and release-context resolution for service-Bot evaluation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    resolve_operable_permission_level,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
    ServicePublicationNotFoundError,
)
from agentclaw.community.plugin_api.service_bot_eval import (
    ServiceBotEvalContext,
    ServiceBotEvalFile,
    ServiceBotEvalPlugin,
)
from agentclaw.community.utils.env_utils import get_current_env


def _iso_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


class ServiceBotEvalService:
    """Protect the evaluation plugin with Bot, collaborator, and release binding.

    The caller-supplied owner and version inputs are never authoritative.  The
    service resolves the tenant-scoped Bot, checks the actor, then proves that
    the addressed version belongs to that Bot's current environment.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        publish_repo: BotPublishRepositoryProtocol,
        collaborator_service: CollaboratorService,
        plugin: ServiceBotEvalPlugin,
    ) -> None:
        self._bot_repo = bot_repo
        self._publish_repo = publish_repo
        self._collaborator_service = collaborator_service
        self._plugin = plugin

    def _resolve(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        required_level: PermissionLevel,
        require_draft: bool = False,
        latest_when_missing: bool = False,
    ) -> tuple[dict[str, Any], BotPublishRecord, ServiceBotEvalContext]:
        try:
            bot = dict(
                BotAddressResolver(self._bot_repo).resolve(
                    bot_id=bot_id,
                    legacy_owner_id=owner_id,
                ).bot
            )
        except BotAddressNotFoundError as exc:
            raise ServicePublicationNotFoundError("bot not found") from exc

        level = resolve_operable_permission_level(
            self._collaborator_service,
            bot=bot,
            user_id=actor_id,
            owner_id=str(bot["owner_id"]),
            env=get_current_env(),
        )
        if level < required_level:
            # Keep absent and forbidden indistinguishable on this public boundary.
            raise ServicePublicationNotFoundError("bot not found")
        if bot.get("bot_type") != "service":
            raise ServicePublicationConflictError("bot is not a service bot")

        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        record: BotPublishRecord | None = None
        try:
            requested_version = int(bot_version, 10)
        except (TypeError, ValueError):
            requested_version = -1
        for item in records:
            if int(item.version or 0) == requested_version:
                record = item
                break
        if record is None:
            if not latest_when_missing:
                raise ServicePublicationNotFoundError("publication not found")
            record = None
            for item in records:
                if record is None or (
                    (item.version or 0, item.id or 0)
                    > (record.version or 0, record.id or 0)
                ):
                    record = item
            if record is None:
                raise ServicePublicationConflictError("no actionable publication")
        if require_draft and record.status != PublishStatus.DRAFT.value:
            raise ServicePublicationConflictError("publication is not a draft")

        context = ServiceBotEvalContext(
            bot_id=str(bot["bot_id"]),
            bot_version=str(record.version),
            release_id=str(record.id),
            publish_id=str(record.id),
            operator_id=actor_id,
            latest_bot_modified_at=_iso_time(bot.get("gmt_modified")),
        )
        return bot, record, context

    async def precheck(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
            require_draft=True,
        )
        return await self._plugin.precheck(context, trigger_source="draft_to_validate")

    async def detail(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        return await self._plugin.detail(context)

    async def submit(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        files: Sequence[ServiceBotEvalFile],
        trigger_source: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        return await self._plugin.submit(
            context,
            list(files),
            trigger_source=trigger_source,
        )

    async def get_submission(
        self,
        bot_id: str,
        submission_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            "latest",
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
            latest_when_missing=True,
        )
        return await self._plugin.get_submission(context, submission_id)

    async def history(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        page_no: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        return await self._plugin.history(
            context,
            page_no=max(page_no, 1),
            page_size=min(max(page_size, 1), 100),
        )

    async def sync(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        _, _, context = self._resolve(
            bot_id,
            bot_version,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.MEMBER,
        )
        return await self._plugin.sync(context)
