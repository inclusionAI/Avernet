"""Production service-Bot lifecycle projection for the unified inventory."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any, Mapping

from agentclaw.community.core.bot_inventory.protocols import ServiceLifecyclePort
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    DisplayState,
    ServiceLifecycleCard,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.types import (
    can_upgrade_publication_from_records,
)
from agentclaw.community.utils.env_utils import get_current_env


_DEPLOYING = {
    PublishStatus.BUILDING.value,
    PublishStatus.BUILT.value,
    PublishStatus.VALIDATE_PUB.value,
    PublishStatus.ONLINE_PUB.value,
    PublishStatus.FAILED.value,
}
_ONLINE = {PublishStatus.SUCCESS.value}


class ServiceLifecycleView(ServiceLifecyclePort):
    """Project publication rows into rongzhi's inventory state/action contract."""

    def __init__(self, publish_repo: BotPublishRepositoryProtocol) -> None:
        self._publish_repo = publish_repo

    def _records(self, bot: Mapping[str, Any]) -> list[BotPublishRecord]:
        bot_pk = bot.get("id")
        if not isinstance(bot_pk, int):
            return []
        return self._publish_repo.list_by_source_bot(bot_pk, get_current_env())

    @staticmethod
    def _display_state(status: str) -> DisplayState:
        if status == PublishStatus.DRAFT.value:
            return DisplayState.SERVICE_DRAFT
        if status in _DEPLOYING:
            return DisplayState.SERVICE_DEPLOYING
        if status == PublishStatus.VALIDATING.value:
            return DisplayState.SERVICE_PRESTABLE
        if status in _ONLINE:
            return DisplayState.SERVICE_ONLINE
        return DisplayState.SERVICE_OFFLINE

    @staticmethod
    def _product_status(status: str) -> str:
        if status == PublishStatus.DRAFT.value:
            return "draft"
        if status in _DEPLOYING:
            return "deploying"
        if status == PublishStatus.VALIDATING.value:
            return "prestable"
        if status in _ONLINE:
            return "running"
        return "offline"

    @staticmethod
    def _visible(records: Iterable[BotPublishRecord]) -> list[BotPublishRecord]:
        candidates = [
            record
            for record in records
            if record.status != PublishStatus.UPGRADED.value
        ]
        latest_released = max(
            (
                record
                for record in candidates
                if record.status == PublishStatus.RELEASED.value
            ),
            key=lambda item: ((item.version or 0), (item.id or 0)),
            default=None,
        )
        candidates = [
            record
            for record in candidates
            if record.status != PublishStatus.RELEASED.value
            or record is latest_released
        ]
        candidates.sort(
            key=lambda item: ((item.version or 0), (item.id or 0)), reverse=True
        )
        return candidates[:2]

    def display_state(self, *, bot: Mapping[str, Any]) -> DisplayState:
        records = self._visible(self._records(bot))
        if not records:
            return DisplayState.SERVICE_DRAFT
        return self._display_state(records[0].status)

    @staticmethod
    def _record_actions(
        record: BotPublishRecord,
        all_records: Sequence[BotPublishRecord],
    ) -> tuple[BotAction, ...]:
        actions: list[BotAction] = [BotAction.VIEW]

        def add(*items: BotAction) -> None:
            actions.extend(item for item in items if item not in actions)

        if record.status == PublishStatus.DRAFT.value:
            add(BotAction.EDIT, BotAction.PUBLISH_STAGING)
            # The draft / validating runtimes are the owner's own dev-stage
            # machines: an engine-process restart (POST …/engine/restart,
            # stage defaults to draft; the verify card must send ?stage=verify)
            # is a self-service recovery, granted with the card.
            add(BotAction.ENGINE_RESTART)
            if not any(
                item.status == PublishStatus.SUCCESS.value for item in all_records
            ):
                add(BotAction.DELETE)
        elif record.status == PublishStatus.VALIDATING.value:
            add(
                BotAction.PUBLISH_ONLINE,
                BotAction.RESTART,
                BotAction.ENGINE_RESTART,
                BotAction.CANCEL_STAGING,
            )
        elif record.status == PublishStatus.SUCCESS.value:
            add(BotAction.CHAT, BotAction.RESTART)
            if can_upgrade_publication_from_records(record, all_records):
                add(BotAction.UPGRADE)
            add(BotAction.OFFLINE)
        elif record.status == PublishStatus.FAILED.value:
            add(BotAction.RETRY)
        return tuple(actions)

    def allowed_actions(self, *, bot: Mapping[str, Any]) -> Sequence[BotAction]:
        records = self._records(bot)
        visible = self._visible(records)
        actions: list[BotAction] = []
        for record in visible:
            actions.extend(
                action
                for action in self._record_actions(record, records)
                if action not in actions
            )
        return tuple(actions or (BotAction.VIEW,))

    def cards_for_bots(
        self, *, bots: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Sequence[ServiceLifecycleCard]]:
        bots_by_id = {str(bot["bot_id"]): bot for bot in bots if bot.get("bot_id")}
        bot_by_pk = {
            bot["id"]: bot
            for bot in bots_by_id.values()
            if isinstance(bot.get("id"), int) and bot.get("bot_id")
        }
        records_by_pk: dict[int, list[BotPublishRecord]] = defaultdict(list)
        for record in self._publish_repo.list_by_source_bots(
            tuple(bot_by_pk), get_current_env()
        ):
            bot = bot_by_pk.get(record.source_bot_pk)
            if bot is not None and record.source_bot_id == bot.get("bot_id"):
                records_by_pk[record.source_bot_pk].append(record)

        result: dict[str, Sequence[ServiceLifecycleCard]] = {}
        for bot_id, bot in bots_by_id.items():
            bot_pk = bot.get("id")
            records = records_by_pk[bot_pk] if isinstance(bot_pk, int) else []
            has_draft = any(
                record.status == PublishStatus.DRAFT.value for record in records
            )
            live = max(
                (
                    record
                    for record in records
                    if record.status == PublishStatus.SUCCESS.value
                ),
                key=lambda item: ((item.version or 0), (item.id or 0)),
                default=None,
            )
            cards = tuple(
                ServiceLifecycleCard(
                    publication_id=record.id,
                    version=record.version,
                    display_state=self._display_state(record.status),
                    status=self._product_status(record.status),
                    internal_status=record.status,
                    actions=self._record_actions(record, records),
                    live_version=live.version if live else None,
                    has_draft=has_draft,
                )
                for record in self._visible(records)
            )
            result[bot_id] = cards or (
                ServiceLifecycleCard(
                    publication_id=None,
                    version=None,
                    display_state=DisplayState.SERVICE_DRAFT,
                    status="draft",
                    internal_status=str(bot.get("status") or ""),
                    actions=(BotAction.VIEW,),
                ),
            )
        return result
