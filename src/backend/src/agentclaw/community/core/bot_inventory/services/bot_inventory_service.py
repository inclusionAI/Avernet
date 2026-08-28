"""Transport-agnostic Bot inventory aggregation service."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping

from agentclaw.community.core.bot_inventory.errors import (
    BotInventoryPermissionError,
    BotInventoryUpstreamError,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryAccessPort,
    BotInventoryBotPort,
    BotInventoryTemplatePort,
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
)
from agentclaw.community.core.bot_management.template_public_view import (
    project_template_config_for_public,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import (
    BotLifecycleView,
)
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    BotInventoryItem,
    BotInventoryKind,
    BusinessSpaceRef,
    DeployMode,
    ServiceLifecycleCard,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel


class BotInventoryService:
    def __init__(
        self,
        *,
        bot_service: BotInventoryBotPort,
        desktop_service: DesktopBotInventoryPort,
        access_service: BotInventoryAccessPort,
        business_space: BusinessSpaceContextProtocol,
        lifecycle_view: BotLifecycleView,
        template_port: BotInventoryTemplatePort,
    ) -> None:
        self._bot = bot_service
        self._desktop = desktop_service
        self._access = access_service
        self._business_space = business_space
        self._lifecycle = lifecycle_view
        self._template_port = template_port

    def list_items(
        self,
        *,
        owner_id: str,
        space: BusinessSpaceRef,
        keyword: str | None,
        engine: str | None,
        deploy_mode: DeployMode | None,
        is_service: bool | None = None,
        bot_ids: list[str] | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[BotInventoryItem], int]:
        cards: list[BotInventoryItem] = []
        if deploy_mode in (None, DeployMode.CLOUD):
            cloud_rows = self._list_cloud_rows(
                owner_id=owner_id,
                space=space,
                keyword=keyword,
                engine=engine,
                bot_ids=bot_ids,
            )
            if is_service is True:
                cloud_rows = [
                    row for row in cloud_rows if row.get("bot_type") == "service"
                ]
            elif is_service is False:
                cloud_rows = [
                    row for row in cloud_rows if row.get("bot_type") != "service"
                ]
            service_rows = [
                row for row in cloud_rows if row.get("bot_type") == "service"
            ]
            levels = self._access.get_operable_permission_levels(
                bots=cloud_rows, user_id=owner_id
            )
            cards.extend(
                self._to_item(
                    row,
                    owner_id,
                    space,
                    levels.get(int(row.get("id") or 0), PermissionLevel.NONE),
                )
                for row in cloud_rows
                if row.get("bot_type") != "service"
            )
            service_cards = self._lifecycle.service_cards(bots=service_rows)
            for row in service_rows:
                bot_id = str(row.get("bot_id") or "")
                cards.extend(
                    self._to_service_item(
                        row,
                        owner_id,
                        lifecycle_card,
                        space,
                        levels.get(int(row.get("id") or 0), PermissionLevel.NONE),
                    )
                    for lifecycle_card in service_cards.get(bot_id, ())
                )
        if (
            is_service is not True
            and deploy_mode in (None, DeployMode.LOCAL)
            and (space is None or space.kind == "personal")
        ):
            cards.extend(
                self._to_local_item(row, owner_id, space, PermissionLevel.OWNER)
                for row in self._list_local_rows(
                    owner_id=owner_id,
                    keyword=keyword,
                    engine=engine,
                    bot_ids=bot_ids,
                )
            )
        cards.sort(
            key=lambda c: (
                c.deploy_mode.value,
                c.bot_name,
                c.bot_id,
                -(c.publication_version or 0),
            )
        )
        total = len(cards)
        start = (page - 1) * page_size
        page_items = self._attach_page_templates(cards[start : start + page_size])
        return page_items, total

    def _attach_page_templates(
        self, items: list[BotInventoryItem]
    ) -> list[BotInventoryItem]:
        """Project template_config onto the returned page slice only.

        The fan-out intentionally pulls rows with ``attach_templates=False``
        (one batched template read per 200-row page would tax every listing);
        this is the single place template snapshots enter the read model, and
        it sees only the page the caller will actually see — so the per-request
        template cost is one bounded read over ≤page_size ids.
        """
        bot_ids = [item.bot_id for item in items if item.template_type]
        if not bot_ids:
            return items
        ext_by_bot_id = self._template_port.list_template_configs_by_bot_ids(
            list(bot_ids)
        )
        enriched: list[BotInventoryItem] = []
        for item in items:
            if not item.template_type:
                enriched.append(item)
                continue
            projected = project_template_config_for_public(
                ext_by_bot_id.get(item.bot_id)
            )
            if projected is None and item.template_config is None:
                enriched.append(item)
                continue
            enriched.append(replace(item, template_config=projected))
        return enriched

    def _list_cloud_rows(
        self,
        *,
        owner_id: str,
        space: BusinessSpaceRef,
        keyword: str | None,
        engine: str | None,
        bot_ids: list[str] | None = None,
    ) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
        fetch_size = 200
        page = 1
        total: int | None = None
        while True:
            result = self._bot.list_bots_by_conditions(
                owner_id=owner_id if space.kind == "personal" else None,
                space_id=space.space_id if space.kind == "team" else None,
                bot_name=keyword,
                engine=engine,
                status=None,
                bot_ids=bot_ids,
                # Inventory cards carry no template_config (verified: neither
                # BotInventoryItem nor the router mapping reads it), so skip
                # the batched template read on every pulled page.
                attach_templates=False,
                page=page,
                page_size=fetch_size,
            )
            page_items = list(result.get("items", []))
            rows.extend(page_items)
            if total is None:
                raw_total = result.get("total")
                total = raw_total if isinstance(raw_total, int) else None
            if not page_items:
                break
            if total is not None and len(rows) >= total:
                break
            if len(page_items) < fetch_size:
                break
            page += 1
        visible: list[Mapping[str, Any]] = []
        for row in rows:
            if row.get("bot_type") not in (None, "", "personal", "service"):
                continue
            try:
                self._business_space.assert_bot_visible_in_current_space(
                    bot=row,
                    owner_id=str(row.get("owner_id") or owner_id),
                    current_space=space,
                )
            except BotInventoryPermissionError:
                continue
            visible.append(row)
        return visible

    def _list_local_rows(
        self,
        *,
        owner_id: str,
        keyword: str | None,
        engine: str | None,
        bot_ids: list[str] | None = None,
    ) -> list[Mapping[str, Any]]:
        try:
            rows = list(self._desktop.list_user_bots(owner_id))
        except Exception as exc:
            _raise_if_desktop_service_error(exc)
            raise
        if bot_ids is not None:
            allowed = frozenset(bot_ids)
            rows = [r for r in rows if str(r.get("bot_id") or "") in allowed]
        if keyword:
            rows = [r for r in rows if keyword in str(r.get("bot_name") or "")]
        if engine:
            rows = [
                r
                for r in rows
                if (r.get("active_engine") or r.get("engine_type") or r.get("engine"))
                == engine
            ]
        return rows

    def _to_item(
        self,
        row: Mapping[str, Any],
        owner_id: str,
        current_space: BusinessSpaceRef | None,
        level: PermissionLevel,
    ) -> BotInventoryItem:
        bot_type = str(row.get("bot_type") or "personal")
        if bot_type == "desktop":
            return self._to_local_item(row, owner_id, current_space, level)
        return self._to_cloud_item(row, owner_id, current_space, level)

    def _to_cloud_item(
        self,
        row: Mapping[str, Any],
        owner_id: str,
        current_space: BusinessSpaceRef | None,
        level: PermissionLevel,
    ) -> BotInventoryItem:
        return self._build_item(
            row=row,
            owner_id=owner_id,
            level=level,
            kind=BotInventoryKind.PERSONAL_CLOUD,
            deploy_mode=DeployMode.CLOUD,
            current_space=current_space,
        )

    def _to_local_item(
        self,
        row: Mapping[str, Any],
        owner_id: str,
        current_space: BusinessSpaceRef | None,
        level: PermissionLevel,
    ) -> BotInventoryItem:
        return self._build_item(
            row=row,
            owner_id=owner_id,
            level=level,
            kind=BotInventoryKind.LOCAL,
            deploy_mode=DeployMode.LOCAL,
            current_space=current_space,
        )

    def _to_service_item(
        self,
        row: Mapping[str, Any],
        owner_id: str,
        lifecycle_card: ServiceLifecycleCard,
        current_space: BusinessSpaceRef | None,
        level: PermissionLevel,
    ) -> BotInventoryItem:
        return self._build_item(
            row=row,
            owner_id=owner_id,
            level=level,
            kind=BotInventoryKind.SERVICE,
            deploy_mode=DeployMode.CLOUD,
            lifecycle_card=lifecycle_card,
            current_space=current_space,
        )

    def _build_item(
        self,
        *,
        row: Mapping[str, Any],
        owner_id: str,
        level: PermissionLevel,
        kind: BotInventoryKind,
        deploy_mode: DeployMode,
        lifecycle_card: ServiceLifecycleCard | None = None,
        current_space: BusinessSpaceRef | None = None,
    ) -> BotInventoryItem:
        ext = _as_mapping(row.get("ext"))
        normalized = {**dict(row), "ext": ext}
        if lifecycle_card is None:
            display_state = self._lifecycle.display_state(bot=normalized, kind=kind)
            actions, disabled = self._lifecycle.allowed_actions(
                bot=normalized, kind=kind
            )
            raw_status = str(row.get("status") or "")
        else:
            display_state = lifecycle_card.display_state
            actions = lifecycle_card.actions
            disabled = {}
            raw_status = lifecycle_card.status
        actions, disabled = self._actions_for_level(
            kind=kind,
            actions=tuple(actions),
            disabled=dict(disabled),
            level=level,
        )
        bot_id = str(row.get("bot_id") or "")
        publication_id = lifecycle_card.publication_id if lifecycle_card else None
        return BotInventoryItem(
            bot_id=bot_id,
            bot_name=str(row.get("bot_name") or ""),
            bot_desc=str(row.get("bot_desc") or ""),
            engine=str(
                row.get("active_engine")
                or row.get("engine_type")
                or row.get("engine")
                or ""
            ),
            bot_type=str(row.get("bot_type") or "personal"),
            kind=kind,
            deploy_mode=deploy_mode,
            display_state=display_state,
            status=raw_status,
            owner_entity_id=str(
                row.get("owner_id") or row.get("entity_id") or owner_id
            ),
            space=self._business_space.bot_space(
                bot={**dict(row), "ext": ext},
                owner_id=owner_id,
                current_space=current_space,
            ),
            avatar_url=_optional_str(ext.get("avatar_url") or row.get("avatar_url")),
            machine_id=_optional_str(ext.get("machine_id") or row.get("machine_id")),
            mount_path=_optional_str(ext.get("mount_path") or row.get("mount_path")),
            passport_id=_passport_id(ext),
            actions=actions,
            disabled_actions=disabled or None,
            card_id=(
                f"service:{bot_id}:{publication_id}"
                if publication_id is not None
                else bot_id
            ),
            publication_id=publication_id,
            publication_version=(lifecycle_card.version if lifecycle_card else None),
            live_version=(lifecycle_card.live_version if lifecycle_card else None),
            internal_status=(
                lifecycle_card.internal_status if lifecycle_card else None
            ),
            template_type=_optional_str(row.get("template_type")),
        )

    @staticmethod
    def _actions_for_level(
        *,
        kind: BotInventoryKind,
        actions: tuple[BotAction, ...],
        disabled: dict[str, str],
        level: PermissionLevel,
    ) -> tuple[tuple[BotAction, ...], dict[str, str]]:
        if level >= PermissionLevel.OWNER:
            return actions, disabled
        if kind is BotInventoryKind.SERVICE and level >= PermissionLevel.MEMBER:
            allowed = tuple(
                action
                for action in actions
                if action is not BotAction.DELETE
                and not (
                    action is BotAction.UPGRADE and level < PermissionLevel.ADMIN
                )
            )
            if BotAction.DELETE in actions:
                disabled.setdefault(
                    BotAction.DELETE.value, "Bot Owner permission required"
                )
            if BotAction.UPGRADE in actions and level < PermissionLevel.ADMIN:
                disabled.setdefault(
                    BotAction.UPGRADE.value, "Bot Admin permission required"
                )
            return allowed, disabled
        # MEMBER is defined as "edit content only": editing (skills/skill-sets,
        # whose endpoints gate on PermissionLevel.MEMBER) stays available to
        # collaborators on every card kind, while the owner-scoped actions
        # (restart/delete/update...) remain disabled. NONE keeps view-only.
        kept = [
            action
            for action in actions
            if action is BotAction.EDIT and level >= PermissionLevel.MEMBER
        ]
        for action in actions:
            if action is BotAction.VIEW or action in kept:
                continue
            disabled.setdefault(action.value, "Bot editor permission required")
        return (BotAction.VIEW, *kept), disabled


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _passport_id(ext: Mapping[str, Any]) -> str | None:
    passport = ext.get("passport")
    if isinstance(passport, Mapping):
        return _optional_str(passport.get("agent_code") or passport.get("agent_id"))
    return None


def _raise_if_desktop_service_error(exc: Exception) -> None:
    if exc.__class__.__name__ in {"DesktopBotServiceError", "DesktopBotOrphanError"}:
        raise BotInventoryUpstreamError("desktop service failed") from exc
